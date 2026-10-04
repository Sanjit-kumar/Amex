"""End-to-end tests through the real FastAPI app (mock AI mode: no keys needed)."""
import io
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    import storage
    monkeypatch.setattr(storage, "RAW", tmp_path / "raw")
    monkeypatch.setattr(storage, "CURATED", tmp_path / "curated")
    monkeypatch.setattr(storage, "DB", tmp_path / "amex.db")
    storage.init()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "stages": {"vision": {"provider": "gemini", "model": "m"}, "text": {"provider": "claude", "model": "m"},
                   "convert": {"provider": "grok", "model": "m"}},
        "mock_when_no_key": True,
        "users": {"alice": {"claude": {"api_key": ""}}, "bob": {}}}))
    monkeypatch.setattr(config, "CONFIG_PATH", cfg)
    import app
    return TestClient(app.app)


def upload(c, name, content, user="alice"):
    r = c.post("/api/upload", files={"files": (name, content)}, headers={"X-User": user})
    assert r.status_code == 200, r.text
    fid = r.json()["ids"][0]
    for _ in range(50):  # background task runs after the response
        d = c.get(f"/api/files/{fid}").json()
        if d["status"] != "processing":
            return d
        time.sleep(0.1)
    raise AssertionError("processing never finished")


def test_requires_known_user(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"X-User": "nobody"}).status_code == 401
    assert client.get("/api/me", headers={"X-User": "alice"}).json()["stages"][0]["mock"] is True


def test_csv_fast_path_standardizes_and_flags(client):
    csv_text = ("Merchant,Date,Total,Card,Currency\n"
                "Rose Garden Florist,03/04/2026,$45.50,4111 1111 1111 1111,USD\n"
                "Grand Hotel,2026-03-15,\"1,250.00\",Visa ending 4242,USD\n"
                "Bad Row,not-a-date,abc,1234,USD\n")
    d = upload(client, "orders.csv", csv_text.encode())
    assert d["status"] == "review", d
    r1, r2, r3 = d["records"]
    assert r1["data"]["transaction_date"] == "2026-03-04" and r1["data"]["amount"] == 45.5
    assert r1["data"]["card_last4"] == "1111" and r1["data"]["card_network"] == "Visa"
    assert "4111" not in json.dumps(r1["data"]).replace("1111", "")  # PAN never stored beyond last4
    assert any("Ambiguous" in i["msg"] for i in r1["issues"])
    assert r2["data"]["amount"] == 1250.0 and r2["data"]["card_last4"] == "4242" and r2["data"]["card_network"] == "Visa"
    assert {i["field"] for i in r3["issues"] if i["severity"] == "error"} >= {"transaction_date", "amount"}


def test_free_text_goes_through_mock_l2(client):
    txt = "Merchant: Blue Bistro\nDate: March 3, 2026\nTotal: 88.20\nCurrency: USD\nCard: ending 9999\n"
    d = upload(client, "receipt.txt", txt.encode())
    assert d["status"] == "review", d
    assert d["records"][0]["data"]["merchant_name"] == "Blue Bistro"
    assert d["records"][0]["data"]["transaction_date"] == "2026-03-03"


def test_json_nested(client):
    j = {"transactions": [{"merchant": {"name": "Corner Shop"}, "date": "2026-02-01", "amount": 10, "currency": "USD"}]}
    d = upload(client, "t.json", json.dumps(j).encode())
    assert d["status"] == "review", d
    assert d["records"][0]["data"]["merchant_name"] == "Corner Shop"


def test_image_without_vision_key_fails_clearly(client):
    d = upload(client, "scan.png", b"\x89PNG fake")
    assert d["status"] == "failed" and "gemini" in d["error"]


def test_edit_versions_approve_export(client):
    d = upload(client, "o.csv", b"Merchant,Date,Total,Card,Currency\nShop,2026-01-31,10.00,1111,USD\nShop2,2026-01-31,11.00,2222,USD\n")
    rid = d["records"][0]["id"]
    h = {"X-User": "alice"}
    r = client.put(f"/api/records/{rid}", json={"data": {**d["records"][0]["data"], "amount": "12.34", "merchant_name": "Shop Fixed"}}, headers=h)
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["data"]["amount"] == 12.34
    vs = client.get(f"/api/records/{rid}/versions").json()
    assert vs[0]["data"]["amount"] == 10.0 and vs[1]["data"]["amount"] == 12.34  # AI original preserved
    assert client.post(f"/api/records/{rid}/approve", headers=h).status_code == 200
    csv_out = client.get("/api/export.csv").text
    assert "Shop Fixed" in csv_out and "Shop2" not in csv_out  # only approved rows are exported
    # editing an approved record sends it back to pending and out of the curated export
    client.put(f"/api/records/{rid}", json={"data": {**r.json()["data"], "amount": 13}}, headers=h)
    assert "Shop Fixed" not in client.get("/api/export.csv").text


def test_cannot_approve_record_with_errors(client):
    d = upload(client, "o.csv", b"Merchant,Date,Total,Card,Currency\nShop,garbage,10.00,1111,USD\n")
    r = client.post(f"/api/records/{d['records'][0]['id']}/approve", headers={"X-User": "alice"})
    assert r.status_code == 422


def test_duplicate_detection(client):
    row = "Shop,2026-01-31,10.00,1111,USD\n"
    d = upload(client, "o.csv", ("Merchant,Date,Total,Card,Currency\n" + row * 2).encode())
    assert all(any("duplicate" in i["msg"] for i in r["issues"]) for r in d["records"])


def test_review_list_and_bulk(client):
    upload(client, "a.csv", b"Merchant,Date,Total,Card,Currency\nShopA,2026-01-31,10.00,1111,USD\nShopB,2026-01-31,11.00,2222,USD\nBad,garbage,5,3333,USD\n")
    h = {"X-User": "alice"}
    rs = client.get("/api/records").json()
    assert len(rs) == 3 and {r["file"] for r in rs} == {"a.csv"} and all(r["status"] == "pending" for r in rs)
    r = client.post("/api/records/bulk", json={"ids": [x["id"] for x in rs], "action": "approve"}, headers=h).json()
    assert len(r["done"]) == 2 and len(r["skipped"]) == 1  # the record with errors is never bulk-approved
    st = {x["data"]["merchant_name"]: x["status"] for x in client.get("/api/records").json()}
    assert st == {"ShopA": "approved", "ShopB": "approved", "Bad": "pending"}
