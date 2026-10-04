"""FastAPI backend + static browser UI.  Run:  python -m uvicorn app:app --port 8000"""
import mimetypes
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import pipeline
import storage
from mapping import FIELDS, coerce
from validators import validate

app = FastAPI(title="Transaction Standardizer")
storage.init()


def current_user(x_user: str | None) -> str:
    # Prototype identity: the UI sends the chosen user. Production should replace this with Amex SSO.
    if not x_user or x_user not in config.users():
        raise HTTPException(401, "Unknown user - pick a user listed in config.json")
    return x_user


@app.get("/api/users")
def users():
    return config.users()


@app.get("/api/me")
def me(x_user: str | None = Header(None)):
    u = current_user(x_user)
    return {"user": u, "stages": config.status(u)}


@app.post("/api/upload")
async def upload(bg: BackgroundTasks, files: list[UploadFile] = File(...), x_user: str | None = Header(None)):
    u = current_user(x_user)
    ids = []
    for up in files:
        fid = storage.save_raw(up.filename or "upload", up.content_type or "", await up.read(), u)
        bg.add_task(pipeline.process_file, fid, u)
        ids.append(fid)
    return {"ids": ids}


@app.get("/api/files")
def files():
    return storage.list_files()


@app.get("/api/files/{fid}")
def file_detail(fid: int):
    f = storage.get_file(fid)
    if not f:
        raise HTTPException(404)
    import json
    return {"id": fid, "name": f["name"], "owner": f["owner"], "status": f["status"], "error": f["error"],
            "log": json.loads(f["log"]), "extracted_text": f["extracted_text"] or "",
            "ext": Path(f["name"]).suffix.lower(), "records": storage.get_records(fid)}


@app.get("/api/files/{fid}/raw")
def raw(fid: int):
    f = storage.get_file(fid)
    if not f:
        raise HTTPException(404)
    mime = mimetypes.guess_type(f["name"])[0] or "application/octet-stream"
    return FileResponse(f["path"], media_type=mime, headers={"Content-Disposition": f'inline; filename="{f["name"]}"'})


@app.post("/api/files/{fid}/reprocess")
def reprocess(fid: int, bg: BackgroundTasks, x_user: str | None = Header(None)):
    u = current_user(x_user)
    f = storage.get_file(fid)
    if not f:
        raise HTTPException(404)
    with storage.conn() as c:
        if c.execute("SELECT COUNT(*) FROM records WHERE file_id=? AND status!='pending'", (fid,)).fetchone()[0]:
            raise HTTPException(409, "File has approved/rejected records - cannot reprocess")
        c.execute("DELETE FROM versions WHERE record_id IN (SELECT id FROM records WHERE file_id=?)", (fid,))
        c.execute("DELETE FROM records WHERE file_id=?", (fid,))
    storage.update_file(fid, status="processing", error=None)
    bg.add_task(pipeline.process_file, fid, u)
    return {"ok": True}


class Edit(BaseModel):
    data: dict
    note: str = "Human edit"


@app.put("/api/records/{rid}")
def edit(rid: int, body: Edit, x_user: str | None = Header(None)):
    u = current_user(x_user)
    rec = storage.get_record(rid)
    if not rec:
        raise HTTPException(404)
    old = rec["data"]
    # Re-run the same coercion/validation the AI output went through, so edits can't bypass checks.
    new_raw = {k: v for k, v in body.data.items() if k in FIELDS}
    new_raw = {k: (None if isinstance(v, str) and v.strip() == "" else v) for k, v in new_raw.items()}
    changed = {k for k in FIELDS if new_raw.get(k) != old.get(k)}
    conf = {**rec["conf"], **{k: 1.0 for k in changed}}  # a human-confirmed value is full confidence
    data, conf, source, notes = coerce({k: v for k, v in new_raw.items() if v is not None}, conf, rec["source"])
    siblings = [r["data"] for r in storage.get_records(rec["file_id"]) if r["id"] != rid]
    issues = validate(data, conf, notes, siblings)
    n = storage.add_version(rid, data, conf, source, issues, u, body.note)
    return {"version": n, "data": data, "conf": conf, "issues": issues}


@app.get("/api/records")
def all_records():
    return storage.list_all_records()


class Bulk(BaseModel):
    ids: list[int]
    action: str


@app.post("/api/records/bulk")
def bulk(body: Bulk, x_user: str | None = Header(None)):
    """Approve/reject many records at once. Records that still have validation errors are skipped, not approved."""
    u = current_user(x_user)
    if body.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject")
    done, skipped = [], []
    for rid in body.ids:
        rec = storage.get_record(rid)
        if not rec:
            continue
        if body.action == "approve":
            siblings = [r["data"] for r in storage.get_records(rec["file_id"]) if r["id"] != rid]
            if any(i["severity"] == "error" for i in validate(rec["data"], rec["conf"], [], siblings)):
                skipped.append(rid)
                continue
        storage.decide(rid, "approved" if body.action == "approve" else "rejected", u)
        done.append(rid)
    return {"done": done, "skipped": skipped}


@app.post("/api/records/{rid}/{action}")
def decide(rid: int, action: str, x_user: str | None = Header(None)):
    u = current_user(x_user)
    if action not in ("approve", "reject", "reopen"):
        raise HTTPException(404)
    rec = storage.get_record(rid)
    if not rec:
        raise HTTPException(404)
    if action == "approve":
        from validators import validate as v
        siblings = [r["data"] for r in storage.get_records(rec["file_id"]) if r["id"] != rid]
        if any(i["severity"] == "error" for i in v(rec["data"], rec["conf"], [], siblings)):
            raise HTTPException(422, "Record has validation errors - fix them before approving")
    storage.decide(rid, {"approve": "approved", "reject": "rejected", "reopen": "pending"}[action], u)
    return {"ok": True}


@app.get("/api/records/{rid}/versions")
def record_versions(rid: int):
    return storage.versions(rid)


@app.get("/api/export.csv")
def export():
    return PlainTextResponse(storage.curated_csv(), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="approved_transactions.csv"'})


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
