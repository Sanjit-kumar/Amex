# Transaction Standardizer (prototype)

Merchants upload payment-transaction files in any format. AI levels convert them to one standard schema, a human
compares raw vs. standardized in the browser, edits, and approves. Approved rows land in the curated store.

## Run
    run.bat                      (or: pip install -r requirements.txt && python -m uvicorn app:app --port 8000)
    open http://localhost:8000
    python -m pytest tests -q    (end-to-end tests, no API keys needed)

## Two pages
- **Upload** (`/`): drop files; the AI levels extract and standardize automatically. Shows progress only.
- **Review & Approve** (`/review.html`): one list of every standardized transaction from all uploads. Filter by
  status, search, bulk-approve clean rows, or click a row to see raw vs. standardized side by side, edit, approve/reject.

## Per-user AI credentials: config.json
Pick your name in the UI; your uploads are processed with *your* keys. Edit `config.json` (gitignored, re-read on
every request, no restart needed). A key is a literal string or `env:VAR_NAME`.

    "stages":  vision / text / convert  -> which provider + model runs each level
    "users":   { "<name>": { "gemini": {"api_key": ""}, "claude": {...}, "grok": {...} } }
    "mock_when_no_key": true   -> demo mode when a user has no key (UI shows a banner). Set false in real use
                                  so a missing key is an error rather than a silent fallback.

Provider `claude_code` runs the local Claude Code CLI (your Claude Code login, no API key) - used for local testing.
Model names in `stages` are placeholders - set them to whatever your Amex-approved endpoints expose.

## Levels (pipeline.py)
| Level | Default | Job |
|---|---|---|
| L1 vision  | Gemini | images / scanned PDFs -> transcribed text |
| L2 text    | Claude | any text -> loose records, source's own keys |
| L3 convert | Grok   | loose records -> standard schema + per-field confidence + source snippet |

Fast path: CSV / Excel / JSON with recognisable headers skip L2/L3 LLM calls (deterministic mapping, exact and free).
Validation (validators.py) is always plain code: required fields, date/amount/currency/last-4, Luhn, duplicates,
low confidence. Records with errors cannot be approved.

## Storage (data/, gitignored)
- `data/raw/<id>/<file>`            original upload, never modified
- `data/amex.db`                    files, records, **every version** (AI original = v1, each edit = new version)
- `data/curated/approved_transactions.csv`   regenerated whenever approvals/edits change

## Known prototype limits
- Identity is a user dropdown; replace `current_user()` in app.py with Amex SSO.
- API keys sit in plain text in config.json; use `env:` references or a secrets manager in production.
- Raw files are stored as received (may contain full card numbers); the *standardized* data keeps last 4 only.
- Field-to-source highlighting finds the first matching text, so repeated values (e.g. "USD") may highlight the wrong spot.
- The Gemini/Claude/Grok REST calls are written to each vendor's documented API but have NOT been run against live keys.
- No bounding-box overlay on images yet (needs the vision model to return coordinates).
