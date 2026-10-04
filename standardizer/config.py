"""Config loader. Per-user AI credentials live in config.json (gitignored)."""
import json
import os
from pathlib import Path

BASE = Path(__file__).parent
DATA = BASE / "data"
CONFIG_PATH = BASE / "config.json"


def load() -> dict:
    # Re-read every call so edits to config.json apply without a restart.
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def users() -> list[str]:
    return sorted(load().get("users", {}))


def _resolve_secret(v: str | None) -> str | None:
    if not v:
        return None
    if v.startswith("env:"):
        return os.environ.get(v[4:]) or None
    return v


def resolve(user: str, stage: str) -> dict:
    """Return {provider, model, key, mock} for a user and pipeline stage."""
    cfg = load()
    st = cfg["stages"][stage]
    provider = st["provider"]
    if provider == "claude_code":  # local CLI uses the machine's Claude Code login; no per-user key
        return {"provider": provider, "model": st.get("model", ""), "key": "cli", "mock": False}
    key = _resolve_secret(cfg.get("users", {}).get(user, {}).get(provider, {}).get("api_key"))
    if key:
        return {"provider": provider, "model": st["model"], "key": key, "mock": False}
    if cfg.get("mock_when_no_key", False):
        return {"provider": provider, "model": st["model"], "key": None, "mock": True}
    raise RuntimeError(f"User '{user}' has no API key configured for '{provider}' (stage '{stage}')")


def status(user: str) -> list[dict]:
    """Non-secret view of which stage uses which provider, and whether the user has a key."""
    out = []
    for stage in load()["stages"]:
        try:
            r = resolve(user, stage)
            out.append({"stage": stage, "provider": r["provider"], "model": r["model"], "mock": r["mock"], "ok": True})
        except RuntimeError as e:
            out.append({"stage": stage, "provider": load()["stages"][stage]["provider"], "model": "", "mock": False, "ok": False, "error": str(e)})
    return out
