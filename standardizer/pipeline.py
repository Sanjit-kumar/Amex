"""
Three-level pipeline, each level using the *uploading user's* credentials:

  L1 vision  (default Gemini)  : images / scanned PDFs  -> transcribed text
  L2 text    (default Claude)  : any text from any format -> loose records (source's own keys)
  L3 convert (default Grok)    : loose records -> standard schema + per-field confidence/source

Fast path: CSV / Excel / JSON whose headers we recognise skip L2 and L3 LLM calls entirely
(deterministic mapping is cheaper and exact). Everything then passes deterministic validation.
"""
import csv
import io
import json
import re
from pathlib import Path

import config
import llm
import storage
from mapping import SCHEMA_DOC, coerce, map_loose, recognises
from validators import validate

IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
             ".gif": "image/gif", ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff", ".heic": "image/heic"}
CHUNK_CHARS = 12000
CONVERT_BATCH = 25

VISION_PROMPT = ("Transcribe ALL text in this document faithfully, preserving rows, columns, labels, dates, times, "
                 "amounts and card details exactly as printed. For tables, output one row per line with cells "
                 "separated by ' | '. Do not summarise, interpret or correct anything.")

TEXT_PROMPT = """The text below comes from a payment-transaction record uploaded by a merchant (restaurant, hotel, shop, etc).
Find every payment transaction in it. Return ONLY a JSON array; one object per transaction, using the keys exactly as
the source labels them (do not rename or normalise values). Include every detail present (merchant, date, time, amount,
currency, card info, bank, id, reference...). If a merchant name appears once for the whole document, repeat it in each
object. If there are no transactions return [].

TEXT:
"""

CONVERT_PROMPT = """Convert these loose transaction records into the standard schema below.

STANDARD SCHEMA (all keys required; use null when truly absent, never guess):
%s

For each input record return an object: {"data": {<all schema keys>}, "confidence": {<key>: 0.0-1.0 for each non-null key},
"source": {<key>: the exact text snippet from the input that the value came from}}.
Never output a full card number; keep only the last 4 digits. Return ONLY a JSON array, same length and order as the input.

INPUT RECORDS:
"""


# ---------- parsers ----------
def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    return raw.decode("latin-1")


def _csv_rows(text: str) -> list[dict]:
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [r for r in csv.DictReader(io.StringIO(text), dialect=dialect) if any((v or "").strip() for v in r.values() if isinstance(v, str))]


def _xlsx_rows(path: Path) -> tuple[list[dict], str]:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    rows, lines = [], []
    for ws in wb.worksheets:
        data = [list(r) for r in ws.iter_rows(values_only=True) if any(c is not None and str(c).strip() for c in r)]
        if not data:
            continue
        lines.append(f"## Sheet: {ws.title}")
        lines += [" | ".join("" if c is None else str(c) for c in r) for r in data]
        header = [str(h) if h is not None else "" for h in data[0]]
        if recognises(header):
            rows += [dict(zip(header, r)) for r in data[1:]]
    return rows, "\n".join(lines)


def _json_rows(text: str):
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict):
        lists = [v for v in obj.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        obj = lists[0] if lists else [obj]
    return [r for r in obj if isinstance(r, dict)] if isinstance(obj, list) else None


def _flatten(d: dict, prefix="") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "_"))
        else:
            out[key] = v
    return out


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)


def _chunks(text: str, size: int = CHUNK_CHARS):
    lines, cur, n = text.splitlines(), [], 0
    for ln in lines:
        if n + len(ln) > size and cur:
            yield "\n".join(cur)
            cur, n = [], 0
        cur.append(ln)
        n += len(ln) + 1
    if cur:
        yield "\n".join(cur)


# ---------- levels ----------
def level1_vision(user: str, fid: int, name: str, mime: str, content: bytes) -> str:
    r = config.resolve(user, "vision")
    if r["mock"]:
        raise RuntimeError(f"Image/scan reading needs a '{r['provider']}' key for user '{user}' - none configured (config.json)")
    storage.log(fid, f"L1 vision: {r['provider']}/{r['model']} reading {name} with {user}'s credentials")
    return llm.complete(r["provider"], r["key"], r["model"], VISION_PROMPT, [(mime, content)])


def level2_text(user: str, fid: int, text: str) -> list[dict]:
    r = config.resolve(user, "text")
    records: list[dict] = []
    if r["mock"]:
        storage.log(fid, "L2 text: MOCK engine (no key for this user) - only 'Key: Value' blocks understood")
        for block in re.split(r"\n\s*\n", text):
            rec = dict(m.groups() for m in re.finditer(r"^\s*([A-Za-z][\w /#.-]{0,30}?)\s*[:=]\s*(.+?)\s*$", block, re.M))
            if rec:
                records.append(rec)
        return records
    storage.log(fid, f"L2 text: {r['provider']}/{r['model']} with {user}'s credentials")
    for i, chunk in enumerate(_chunks(text), 1):
        out = llm.parse_json(llm.complete(r["provider"], r["key"], r["model"], TEXT_PROMPT + chunk))
        records += [x for x in (out if isinstance(out, list) else [out]) if isinstance(x, dict)]
    return records


def level3_convert(user: str, fid: int, loose: list[dict], deterministic: bool):
    """Returns list of (data, conf, source) in standard-field space (not yet coerced)."""
    r = config.resolve(user, "convert")
    if deterministic or r["mock"]:
        if r["mock"] and not deterministic:
            storage.log(fid, "L3 convert: MOCK engine (no key for this user) - alias-based mapping")
        return [map_loose(rec) for rec in loose]
    storage.log(fid, f"L3 convert: {r['provider']}/{r['model']} with {user}'s credentials")
    out = []
    for i in range(0, len(loose), CONVERT_BATCH):
        batch = loose[i:i + CONVERT_BATCH]
        res = llm.parse_json(llm.complete(r["provider"], r["key"], r["model"],
                                          CONVERT_PROMPT % SCHEMA_DOC + json.dumps(batch, default=str)))
        res = res if isinstance(res, list) else [res]
        if len(res) != len(batch):
            raise RuntimeError(f"Converter returned {len(res)} records for {len(batch)} inputs")
        for item in res:
            out.append((item.get("data") or {}, item.get("confidence") or {}, item.get("source") or {}))
    return out


# ---------- orchestration ----------
def process_file(fid: int, user: str):
    f = storage.get_file(fid)
    try:
        path = Path(f["path"])
        ext = path.suffix.lower()
        content = path.read_bytes()
        storage.log(fid, f"Stored raw file ({len(content)} bytes, untouched). Detected type: {ext or 'unknown'}")
        text, loose, deterministic = "", None, False

        if ext in IMAGE_EXT:
            text = level1_vision(user, fid, f["name"], IMAGE_EXT[ext], content)
        elif ext == ".pdf":
            text = _pdf_text(path)
            if len(text.strip()) < 30:
                storage.log(fid, "PDF has no text layer - treating as a scan")
                text = level1_vision(user, fid, f["name"], "application/pdf", content)
        elif ext in (".xlsx", ".xlsm"):
            rows, text = _xlsx_rows(path)
            if rows:
                loose, deterministic = rows, True
        elif ext in (".csv", ".tsv"):
            text = _read_text(path)
            rows = _csv_rows(text)
            if rows and recognises(rows[0].keys()):
                loose, deterministic = rows, True
        elif ext == ".json":
            text = _read_text(path)
            rows = _json_rows(text)
            if rows:
                rows = [_flatten(r) for r in rows]
                if recognises(rows[0].keys()):
                    loose, deterministic = rows, True
        else:
            text = _read_text(path)

        storage.update_file(fid, extracted_text=text)
        if deterministic:
            storage.log(fid, f"Recognised headers - deterministic mapping of {len(loose)} rows (no LLM needed for L2/L3)")
        else:
            if not text.strip():
                raise RuntimeError("No readable content found in file")
            loose = level2_text(user, fid, text)
        if not loose:
            raise RuntimeError("No transactions found in file")

        mapped = level3_convert(user, fid, loose, deterministic)
        coerced = [coerce(*m) for m in mapped]
        datas = [c[0] for c in coerced]
        for i, (d, c, s, notes) in enumerate(coerced):
            siblings = datas[:i] + datas[i + 1:]
            storage.add_record(fid, i, d, c, s, validate(d, c, notes, siblings), "AI")
        storage.log(fid, f"Created {len(coerced)} standardized record(s) - waiting for human approval")
        storage.update_file(fid, status="review")
    except Exception as e:  # surface every failure to the UI instead of dying silently
        storage.log(fid, f"FAILED: {e}")
        storage.update_file(fid, status="failed", error=str(e))
