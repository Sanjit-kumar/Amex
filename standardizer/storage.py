"""SQLite metadata + raw file store + versioned standardized records + curated CSV export."""
import csv
import io
import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from config import DATA
from mapping import FIELDS

RAW = DATA / "raw"
CURATED = DATA / "curated"
DB = DATA / "amex.db"
_lock = threading.RLock()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def conn():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    RAW.mkdir(parents=True, exist_ok=True)
    CURATED.mkdir(parents=True, exist_ok=True)
    with _lock, conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS files(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, mime TEXT, path TEXT, owner TEXT,
            status TEXT, log TEXT DEFAULT '[]', extracted_text TEXT, error TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS records(
            id INTEGER PRIMARY KEY AUTOINCREMENT, file_id INTEGER, idx INTEGER,
            status TEXT DEFAULT 'pending', current_version INTEGER DEFAULT 1, decided_by TEXT, decided_at TEXT);
        CREATE TABLE IF NOT EXISTS versions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER, version INTEGER,
            data TEXT, conf TEXT, source TEXT, issues TEXT, edited_by TEXT, note TEXT, created TEXT);
        """)


# ---------- files ----------
def save_raw(name: str, mime: str, content: bytes, owner: str) -> int:
    folder = RAW / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True)
    path = folder / Path(name).name
    path.write_bytes(content)  # raw file is stored untouched and never modified
    with _lock, conn() as c:
        cur = c.execute("INSERT INTO files(name,mime,path,owner,status,created) VALUES(?,?,?,?,?,?)",
                        (Path(name).name, mime, str(path), owner, "processing", _now()))
        return cur.lastrowid


def get_file(fid: int):
    with conn() as c:
        return c.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()


def log(fid: int, msg: str):
    with _lock, conn() as c:
        row = c.execute("SELECT log FROM files WHERE id=?", (fid,)).fetchone()
        entries = json.loads(row["log"]) + [{"t": _now(), "msg": msg}]
        c.execute("UPDATE files SET log=? WHERE id=?", (json.dumps(entries), fid))


def update_file(fid: int, **kw):
    cols = ", ".join(f"{k}=?" for k in kw)
    with _lock, conn() as c:
        c.execute(f"UPDATE files SET {cols} WHERE id=?", (*kw.values(), fid))


def list_files():
    with conn() as c:
        rows = c.execute("SELECT * FROM files ORDER BY id DESC").fetchall()
        out = []
        for r in rows:
            counts = {s: n for s, n in c.execute(
                "SELECT status, COUNT(*) FROM records WHERE file_id=? GROUP BY status", (r["id"],))}
            out.append({"id": r["id"], "name": r["name"], "owner": r["owner"], "status": r["status"],
                        "created": r["created"], "error": r["error"], "counts": counts})
        return out


# ---------- records ----------
def add_record(fid: int, idx: int, data: dict, conf: dict, source: dict, issues: list, by: str) -> int:
    with _lock, conn() as c:
        rid = c.execute("INSERT INTO records(file_id,idx) VALUES(?,?)", (fid, idx)).lastrowid
        c.execute("INSERT INTO versions(record_id,version,data,conf,source,issues,edited_by,note,created) VALUES(?,?,?,?,?,?,?,?,?)",
                  (rid, 1, json.dumps(data), json.dumps(conf), json.dumps(source), json.dumps(issues), by, "AI output", _now()))
        return rid


def _version_row(c, rid):
    return c.execute("""SELECT v.* FROM versions v JOIN records r ON r.id=v.record_id
                        WHERE r.id=? AND v.version=r.current_version""", (rid,)).fetchone()


def get_records(fid: int):
    with conn() as c:
        out = []
        for r in c.execute("SELECT * FROM records WHERE file_id=? ORDER BY idx", (fid,)):
            v = _version_row(c, r["id"])
            out.append({"id": r["id"], "idx": r["idx"], "status": r["status"], "version": r["current_version"],
                        "decided_by": r["decided_by"], "data": json.loads(v["data"]), "conf": json.loads(v["conf"]),
                        "source": json.loads(v["source"]), "issues": json.loads(v["issues"]), "edited_by": v["edited_by"]})
        return out


def list_all_records():
    """Every standardized record across all files (newest first) for the review list."""
    with conn() as c:
        rows = c.execute("""SELECT r.id, r.file_id, r.idx, r.status, r.current_version, r.decided_by, f.name fname, f.created fcreated,
                                   v.data, v.conf, v.issues, v.edited_by
                            FROM records r JOIN files f ON f.id=r.file_id
                            JOIN versions v ON v.record_id=r.id AND v.version=r.current_version
                            ORDER BY r.file_id DESC, r.idx""").fetchall()
    return [{"id": r["id"], "file_id": r["file_id"], "file": r["fname"], "idx": r["idx"], "status": r["status"],
             "version": r["current_version"], "decided_by": r["decided_by"], "edited_by": r["edited_by"],
             "data": json.loads(r["data"]), "conf": json.loads(r["conf"]), "issues": json.loads(r["issues"])} for r in rows]


def get_record(rid: int):
    with conn() as c:
        r = c.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
        if not r:
            return None
        v = _version_row(c, rid)
        return {"id": rid, "file_id": r["file_id"], "status": r["status"], "version": r["current_version"],
                "data": json.loads(v["data"]), "conf": json.loads(v["conf"]), "source": json.loads(v["source"])}


def add_version(rid: int, data: dict, conf: dict, source: dict, issues: list, by: str, note: str) -> int:
    """Edits never overwrite: each one is a new version, and the AI's original (v1) is kept."""
    with _lock, conn() as c:
        r = c.execute("SELECT current_version FROM records WHERE id=?", (rid,)).fetchone()
        n = c.execute("SELECT MAX(version) m FROM versions WHERE record_id=?", (rid,)).fetchone()["m"] + 1
        c.execute("INSERT INTO versions(record_id,version,data,conf,source,issues,edited_by,note,created) VALUES(?,?,?,?,?,?,?,?,?)",
                  (rid, n, json.dumps(data), json.dumps(conf), json.dumps(source), json.dumps(issues), by, note, _now()))
        # An edit sends an approved/rejected record back for re-approval.
        c.execute("UPDATE records SET current_version=?, status='pending', decided_by=NULL, decided_at=NULL WHERE id=?", (n, rid))
        c.execute("UPDATE files SET status='review' WHERE id=(SELECT file_id FROM records WHERE id=?)", (rid,))
    export_curated()
    return n


def versions(rid: int):
    with conn() as c:
        return [{"version": v["version"], "edited_by": v["edited_by"], "note": v["note"], "created": v["created"],
                 "data": json.loads(v["data"])} for v in c.execute("SELECT * FROM versions WHERE record_id=? ORDER BY version", (rid,))]


def decide(rid: int, status: str, by: str):
    with _lock, conn() as c:
        c.execute("UPDATE records SET status=?, decided_by=?, decided_at=? WHERE id=?", (status, by, _now(), rid))
        fid = c.execute("SELECT file_id FROM records WHERE id=?", (rid,)).fetchone()["file_id"]
        pending = c.execute("SELECT COUNT(*) n FROM records WHERE file_id=? AND status='pending'", (fid,)).fetchone()["n"]
        c.execute("UPDATE files SET status=? WHERE id=?", ("done" if pending == 0 else "review", fid))
    export_curated()


# ---------- curated (approved) output ----------
def curated_csv() -> str:
    cols = ["record_id", "source_file", *FIELDS, "approved_by", "approved_at"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    with conn() as c:
        rows = c.execute("""SELECT r.id rid, f.name fname, r.decided_by, r.decided_at, v.data FROM records r
                            JOIN files f ON f.id=r.file_id
                            JOIN versions v ON v.record_id=r.id AND v.version=r.current_version
                            WHERE r.status='approved' ORDER BY r.id""").fetchall()
    for r in rows:
        d = json.loads(r["data"])
        w.writerow([r["rid"], r["fname"], *[d.get(f, "") if d.get(f) is not None else "" for f in FIELDS], r["decided_by"], r["decided_at"]])
    return buf.getvalue()


def export_curated():
    """Keep the curated store (data/curated/approved_transactions.csv) in sync with approvals."""
    CURATED.mkdir(parents=True, exist_ok=True)
    (CURATED / "approved_transactions.csv").write_text(curated_csv(), encoding="utf-8", newline="")
