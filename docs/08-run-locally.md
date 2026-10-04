# 8. Run it locally

[&larr; Roadmap and open questions](07-roadmap-and-open-questions.md) &middot; [Home](README.md)

## Quick start (nothing to configure)

```bash
git clone https://github.com/Sanjit-kumar/Amex.git
cd Amex/standardizer
python start.py          # Windows: double-click run.bat   |   Mac/Linux: ./start.sh
```

`start.py` does everything:

1. Installs any missing Python packages.
2. Creates `config.json` on first run and **auto-detects how to reach an AI model**:
   - the Claude Code CLI on the machine: used with no API key (`claude_code`);
   - otherwise an `ANTHROPIC_API_KEY` environment variable: used as the Claude API key;
   - otherwise mock mode, which still handles structured files (CSV, Excel, JSON) and shows a banner.
3. Starts the local web server (the backend and both pages are one program, so there is nothing else to run).
4. Opens <http://localhost:8000> in the browser. If port 8000 is busy it uses the next free one and prints the address.

Stop it with Ctrl+C. Requires Python 3.10 or newer. Nothing else.

### Changing how the AI runs (optional)

The auto-detected setup is fine for trying it out. To use a different model or API keys, edit `config.json` and set the provider for each of the three stages:

| Goal | `stages.*.provider` | Needs |
|---|---|---|
| Local testing, no API key | `claude_code` | Claude Code CLI logged in |
| Anthropic API | `claude` | `users.<name>.claude.api_key` |
| Google API | `gemini` | `users.<name>.gemini.api_key` |
| xAI API | `grok` | `users.<name>.grok.api_key` |

The user listed under `users` must match the user selected in the UI. Set `"mock_when_no_key": false`.

## Try it

1. Open the Upload page and drop `samples/florist_orders.csv`. It is standardized instantly with no AI.
2. Drop `samples/bistro_receipts.txt` or `samples/receipt_photo.png` to exercise the AI levels.
3. Go to **Review and Approve**, open a record, compare, fix, approve.
4. Find the result in `data/curated/approved_transactions.csv` or use **Export approved CSV**.

## Where data lives

| Path (under `standardizer/`) | Contents |
|---|---|
| `data/raw/<id>/<file>` | Original uploads, untouched |
| `data/amex.db` | SQLite: files, records, all versions |
| `data/curated/approved_transactions.csv` | Approved records only |

All of `data/` and `config.json` are gitignored.

## Tests

```bash
python -m pytest tests -q
```

The 9 tests drive the real FastAPI app in a temporary folder with mock mode on, so they need no keys or network. They
cover CSV fast path and validation flags, free text, nested JSON, an image with no vision key, edit versioning with
approve and export, the approval gate, duplicate detection, and the review list with bulk approve.

## API reference

All endpoints except `/api/users` require the header `X-User: <name>` (a prototype stand-in for SSO).

| Method and path | Purpose |
|---|---|
| `GET /api/users` | Names in `config.json` |
| `GET /api/me` | Current user and which provider each stage uses |
| `POST /api/upload` | Multipart upload of one or more files. Returns file ids and starts processing |
| `GET /api/files` | All uploads with status and record counts |
| `GET /api/files/{id}` | One file: log, extracted text, records |
| `GET /api/files/{id}/raw` | The original file |
| `POST /api/files/{id}/reprocess` | Re-run the pipeline. Refused if any record is decided |
| `GET /api/records` | Every record across all files, for the review list |
| `PUT /api/records/{id}` | Save edits. Re-validated and stored as a new version |
| `POST /api/records/{id}/approve` | Approve. 422 if the record has errors |
| `POST /api/records/{id}/reject` | Reject |
| `POST /api/records/{id}/reopen` | Back to pending |
| `POST /api/records/bulk` | `{ids, action}` for approve or reject. Records with errors are skipped |
| `GET /api/records/{id}/versions` | Full version history |
| `GET /api/export.csv` | Approved records as CSV |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Banner says a key is missing | The stage uses `claude`, `gemini` or `grok` and the user has no key. Add it, or switch the stage to `claude_code` |
| Env var key not picked up | Environment variables load when the server starts. Restart the server |
| Image or scanned PDF fails with "needs a key" | Mock mode cannot read images. Configure a real vision provider |
| A file stays "AI working" for a long time | The local CLI takes about 15 to 60 seconds per level. Check `server.log` and `server.err` if it never finishes |
| Hard refresh needed after an update | Browsers cache `common.js` and `style.css`. Press Ctrl+F5 |
