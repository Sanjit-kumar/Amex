"""One-command launcher:  python start.py

Installs missing dependencies, creates config.json on first run (auto-detecting how to reach an AI model),
starts the local server and opens the UI in the browser. Nothing else to set up.
"""
import getpass
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).parent
os.chdir(HERE)
sys.path.insert(0, str(HERE))

MODULES = {"fastapi": "fastapi", "uvicorn": "uvicorn", "httpx": "httpx", "openpyxl": "openpyxl",
           "pypdf": "pypdf", "multipart": "python-multipart", "pydantic": "pydantic"}


def ensure_deps():
    missing = [pkg for mod, pkg in MODULES.items() if importlib.util.find_spec(mod) is None]
    if missing:
        print(f"Installing missing packages: {', '.join(missing)} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "-r", str(HERE / "requirements.txt")])


def ensure_config():
    """Create config.json the first time. Pick the AI route that works on this machine."""
    path = HERE / "config.json"
    if path.exists():
        return
    user = re.sub(r"[^A-Za-z0-9_.-]", "", getpass.getuser()) or "user"
    if shutil.which("claude"):
        provider, users, mock, how = "claude_code", {user: {}}, False, "the Claude Code CLI found on this machine (no API key needed)"
    elif os.environ.get("ANTHROPIC_API_KEY"):
        provider, users, mock, how = "claude", {user: {"claude": {"api_key": "env:ANTHROPIC_API_KEY"}}}, False, "ANTHROPIC_API_KEY from the environment"
    else:
        provider, users, mock, how = "claude", {user: {"claude": {"api_key": ""}}}, True, "NOTHING FOUND - running in MOCK mode (structured files only). Add an API key in config.json for real AI"
    cfg = {
        "_comment": "provider 'claude_code' = local Claude Code CLI. Use 'claude' / 'gemini' / 'grok' with users.<name>.<provider>.api_key "
                    "(a literal or 'env:VAR_NAME') to use API keys instead. See config.example.json.",
        "stages": {s: {"provider": provider, "model": ""} for s in ("vision", "text", "convert")},
        "mock_when_no_key": mock,
        "users": users,
    }
    if provider == "claude":
        for s in cfg["stages"].values():
            s["model"] = "claude-sonnet-5-5"
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"Created config.json for user '{user}' using {how}.")


def free_port(start: int) -> int:
    for p in range(start, start + 20):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise SystemExit("No free port found between %d and %d" % (start, start + 19))


def main():
    ensure_deps()
    ensure_config()
    port = free_port(int(os.environ.get("PORT", "8000")))
    url = f"http://localhost:{port}"
    print(f"\n  Transaction Standardizer running at {url}\n  Press Ctrl+C to stop.\n")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
