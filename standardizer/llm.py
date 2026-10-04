"""Thin REST clients for Gemini, Claude and Grok. The caller passes in the *user's* key."""
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

TIMEOUT = httpx.Timeout(180.0, connect=15.0)


class LLMError(RuntimeError):
    pass


def _post(url: str, headers: dict, body: dict) -> dict:
    r = httpx.post(url, headers=headers, json=body, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise LLMError(f"{url.split('/')[2]} returned {r.status_code}: {r.text[:300]}")
    return r.json()


def _gemini(key, model, prompt, files):
    parts = [{"text": prompt}]
    for mime, data in files:
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
    j = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
              {"x-goog-api-key": key}, {"contents": [{"parts": parts}], "generationConfig": {"temperature": 0}})
    try:
        return "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise LLMError(f"Gemini returned no content: {json.dumps(j)[:300]}")


def _claude(key, model, prompt, files):
    content = []
    for mime, data in files:
        kind = "document" if mime == "application/pdf" else "image"
        content.append({"type": kind, "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(data).decode()}})
    content.append({"type": "text", "text": prompt})
    j = _post("https://api.anthropic.com/v1/messages",
              {"x-api-key": key, "anthropic-version": "2023-06-01"},
              {"model": model, "max_tokens": 16000, "temperature": 0, "messages": [{"role": "user", "content": content}]})
    return "".join(b.get("text", "") for b in j.get("content", []))


def _grok(key, model, prompt, files):
    content = [{"type": "text", "text": prompt}]
    for mime, data in files:
        if mime.startswith("image/"):
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}})
    j = _post("https://api.x.ai/v1/chat/completions", {"Authorization": f"Bearer {key}"},
              {"model": model, "temperature": 0, "messages": [{"role": "user", "content": content}]})
    try:
        return j["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise LLMError(f"Grok returned no content: {json.dumps(j)[:300]}")


def _claude_code(key, model, prompt, files):
    """Local testing: run the Claude Code CLI (uses the logged-in Claude Code subscription, no API key).
    Images/PDFs are written to a temp folder and read by Claude Code's Read tool."""
    exe = shutil.which("claude")
    if not exe:
        raise LLMError("Claude Code CLI ('claude') not found on PATH")
    with tempfile.TemporaryDirectory(prefix="cc_") as tmp:
        names = []
        for i, (mime, data) in enumerate(files):
            ext = mimetypes.guess_extension(mime) or ".bin"
            p = Path(tmp) / f"input_{i}{ext}"
            p.write_bytes(data)
            names.append(p.name)
        if names:
            prompt = f"The input file(s) are in the current directory: {', '.join(names)}. Read them with the Read tool.\n\n{prompt}"
        cmd = [exe, "-p", "--output-format", "text", "--no-session-persistence", "--allowedTools", "Read"]
        if model:
            cmd += ["--model", model]
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE")}
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           cwd=tmp, env=env, timeout=600)
        if r.returncode != 0:
            raise LLMError(f"claude CLI failed ({r.returncode}): {(r.stderr or r.stdout)[:300]}")
        return r.stdout


_PROVIDERS = {"gemini": _gemini, "claude": _claude, "grok": _grok, "claude_code": _claude_code}


def complete(provider: str, key: str, model: str, prompt: str, files: list[tuple[str, bytes]] | None = None) -> str:
    if provider not in _PROVIDERS:
        raise LLMError(f"Unknown provider '{provider}'")
    return _PROVIDERS[provider](key, model, prompt, files or [])


def parse_json(text: str):
    """Pull the first JSON array/object out of a model reply (tolerates code fences / chatter)."""
    t = re.sub(r"```(?:json)?", "", text).strip()
    starts = [i for i in (t.find("["), t.find("{")) if i != -1]
    if not starts:
        raise LLMError("Model reply contained no JSON")
    t = t[min(starts):]
    try:
        return json.JSONDecoder().raw_decode(t)[0]
    except json.JSONDecodeError as e:
        raise LLMError(f"Model reply was not valid JSON: {e}")
