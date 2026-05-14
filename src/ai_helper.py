"""
Waffler AI Debug Helper
=======================

Lightweight in-app helper that answers "why is X broken?" questions by
sending the user's *redacted* logs + settings to a chat model and
streaming the response back.

Architecture (Option B — see feature/ai-helper branch design doc)
-----------------------------------------------------------------
This module is BYOK by design. It does NOT call any Waffler-owned
backend — every request goes directly from the user's machine to one of
the chat providers the user has already configured for transcription
styling (Groq / Cerebras / OpenAI). This mirrors the rest of Waffler's
"bring your own keys" philosophy:

    * No new keys to set up.
    * No Waffler-side rate limits, server, or cost.
    * The helper uses whichever provider is fastest available right now.

Provider preference order is the inverse of the styler's order — Cerebras
first because Qwen-3 235B is the strongest at structured analysis among
the free/cheap tiers, then Groq's Llama 3.3 70B (also strong, free tier
fine for a handful of help queries), then OpenAI as last resort.

Redaction
---------
Every log line and settings snapshot is run through ``_redact()`` before
being sent. Specifically:

    * API keys matching ``gsk_…``, ``csk-…``, ``sk-…`` patterns are
      replaced with their prefix + ``[REDACTED]`` so the model can still
      reason about "you have a Groq key" without seeing the secret.
    * The user's home directory path is replaced with ``~/`` so we don't
      leak ``/Users/<username>/`` (which often *is* a real name).
    * Email addresses are masked with ``<email>``.

If a redaction misses something, that secret would leak to the chat
provider — same threat model as the rest of the styler pipeline, which
sends transcripts to the same providers. The redaction here is defence
in depth, not the primary boundary.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional


# Where Waffler's runtime data lives. Mirrors `app.py:get_data_directory`
# but redeclared here so this module can stand alone without importing
# from the giant app.py (avoids circular-import risk).
_DATA_DIR = Path.home() / ".waffler-hosted"


# ── Redaction patterns ───────────────────────────────────────────────

_KEY_PATTERNS = [
    # Groq: "gsk_" + ~50 chars
    (re.compile(r"gsk_[A-Za-z0-9_\-]{20,}"), "gsk_[REDACTED]"),
    # Cerebras: "csk-" + ~50 chars
    (re.compile(r"csk-[A-Za-z0-9_\-]{20,}"), "csk-[REDACTED]"),
    # OpenAI: "sk-..." or "sk-proj-..." — match both
    (re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"), "sk-[REDACTED]"),
    # Anthropic: "sk-ant-..."
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "sk-ant-[REDACTED]"),
    # Generic email
    (re.compile(r"[\w._%+\-]+@[\w.\-]+\.[A-Za-z]{2,}"), "<email>"),
]


def _redact(text: str) -> str:
    """Strip API keys, the user's home-dir path, and emails from text.

    Order matters: run the key patterns *before* the home-dir scrub so
    we don't accidentally re-include a leaked key as part of a path.
    """
    if not text:
        return text
    out = text
    for pat, repl in _KEY_PATTERNS:
        out = pat.sub(repl, out)
    # Home dir → ~/ so we don't leak the user's account name. Path.home()
    # is the cross-platform way; on Windows this is ``C:\Users\<name>``
    # and we replace either Windows or POSIX form.
    home = str(Path.home())
    if home:
        out = out.replace(home, "~")
        # Also handle the POSIX-style form in case logs were normalised
        # somewhere upstream.
        out = out.replace(home.replace("\\", "/"), "~")
    return out


# ── Context gathering ────────────────────────────────────────────────


def _tail_file(path: Path, max_lines: int = 100) -> str:
    """Read the last ``max_lines`` lines of a text file safely.

    Returns an empty string if the file doesn't exist or can't be read.
    Doesn't slurp the entire file when the log is huge — reads from the
    end via a small seek-back buffer.
    """
    if not path.exists():
        return ""
    try:
        # Quick implementation: read up to 64KB from the end, take the
        # last `max_lines` complete lines from it. 64KB ≈ 800 lines of
        # typical Waffler log output, way more than max_lines.
        size = path.stat().st_size
        chunk = min(size, 64 * 1024)
        with open(path, "rb") as f:
            f.seek(max(0, size - chunk))
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception:
        return ""


def _load_settings_snapshot() -> dict:
    """Read settings.json and return a SAFE summary (no raw keys)."""
    path = _DATA_DIR / "settings.json"
    if not path.exists():
        return {"settings_file": "missing"}
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return {"settings_file": f"unreadable: {e}"}

    def _mask(key: str) -> str:
        val = data.get(key) or ""
        if not val:
            return "not set"
        # Show a tiny prefix so the model knows the key exists but
        # never the secret itself.
        return f"set ({val[:4]}…)"

    return {
        "groq_key":       _mask("groq_key"),
        "cerebras_key":   _mask("cerebras_key"),
        "openai_key":     _mask("api_key"),
        "hotkey_keys":    data.get("hotkey_keys"),
        "auto_paste":     data.get("auto_paste", True),
        "prompt_style":   data.get("prompt_style", "normal"),
        "dialect":        data.get("dialect", "auto"),
        "local_whisper":  data.get("local_whisper", False),
    }


def _load_recent_usage() -> dict:
    """Summarise recent usage from usage.json without exposing raw entries."""
    path = _DATA_DIR / "usage.json"
    if not path.exists():
        return {"usage": "no data yet"}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"usage": "unreadable"}
    if not data:
        return {"usage": "no entries"}
    # Take the last 30 entries and aggregate per-provider
    recent = data[-30:]
    per_provider: dict[str, int] = {}
    failures = 0
    for entry in recent:
        prov = entry.get("provider") or "unknown"
        per_provider[prov] = per_provider.get(prov, 0) + 1
        if entry.get("type") == "gpt" and entry.get("cost_usd", 0) == 0:
            failures += 1  # crude proxy: zero-cost gpt entries are fallbacks
    return {
        "recent_count": len(recent),
        "per_provider": per_provider,
        "fallback_to_basic_clean": failures,
    }


def gather_context() -> dict:
    """Bundle the redacted log tails, settings summary, and usage summary
    into a single dict the AI assistant can reason over.

    Everything in the returned dict is safe to send to a third-party
    chat model — secrets are already stripped.
    """
    import platform as _plat

    app_log = _redact(_tail_file(_DATA_DIR / "app.log", 100))
    crash_log = _redact(_tail_file(_DATA_DIR / "crash.log", 50))
    hotkey_log = _redact(_tail_file(_DATA_DIR / "hotkey.log", 30))

    ctx: dict = {
        "platform": _plat.system(),
        "platform_version": _plat.platform(),
        "settings": _load_settings_snapshot(),
        "usage_recent": _load_recent_usage(),
        "log_tail_app": app_log or "(empty)",
        "log_tail_crash": crash_log or "(no recent crash)",
        "log_tail_hotkey": hotkey_log or "(empty)",
    }
    # Best-effort: include the running version if discoverable.
    try:
        from src import __version__ as _v  # type: ignore
        ctx["waffler_version"] = _v
    except Exception:
        pass
    return ctx


# ── Preset prompts ──────────────────────────────────────────────────


PRESETS: dict[str, str] = {
    "slow": (
        "My dictation feels slow. Look at the recent app.log entries and "
        "usage breakdown — what's actually the bottleneck? Network? A specific "
        "provider being throttled? Local Whisper being used when an API key "
        "would be faster? Give me one or two concrete next steps."
    ),
    "rate_limit": (
        "I'm getting rate-limit toasts. Check my settings and recent usage "
        "to figure out which provider is rate-limiting me and why. Suggest "
        "a fallback configuration that would have avoided this."
    ),
    "failed_recording": (
        "My most recent recording didn't work as expected. Look at the "
        "tail of app.log to figure out what happened — was it transcription "
        "failure, styling fallback, an auth error, network? What should I do?"
    ),
    "setup_check": (
        "Audit my Waffler setup based on the settings and platform info "
        "you have. Is my hotkey sensible? Do I have a fallback key? Is "
        "anything misconfigured? Just give me a short bulleted health check."
    ),
}


# ── Prompt template ─────────────────────────────────────────────────


_SYSTEM_PROMPT = (
    "You are Waffler's in-app debug assistant. Waffler is a voice-to-text "
    "app that records audio when the user holds a hotkey, sends it to a "
    "transcription provider (Groq / Cerebras / OpenAI Whisper), passes the "
    "transcript to a styling LLM (Groq Llama / Cerebras Qwen / OpenAI gpt-4.1-mini), "
    "and pastes the polished text into the user's active app.\n\n"
    "The user has given you a CONTEXT block containing:\n"
    "  - Their current settings (API keys are masked — only 'set' / 'not set')\n"
    "  - A summary of recent usage per provider\n"
    "  - The tail of their app.log, crash.log, and hotkey.log (with secrets redacted)\n\n"
    "Your job is to read the context, diagnose the user's question, and reply with:\n"
    "  - A short plain-English explanation of what's going on (2-3 sentences max)\n"
    "  - 1-3 concrete, numbered next steps the user can take inside Waffler\n"
    "  - Where relevant, point at specific Settings tabs ('Settings → API Keys', etc.)\n\n"
    "RULES:\n"
    "  - Be direct. No filler, no 'I think', no apologies.\n"
    "  - Don't mention 'the logs you provided' or 'based on the context' — just answer.\n"
    "  - If the context doesn't actually contain evidence for the user's complaint, "
    "say so plainly and ask one targeted clarifying question.\n"
    "  - Never recommend the user share their API keys with anyone.\n"
    "  - Keep total response under 200 words unless the user explicitly asks for detail."
)


# ── Helper class ────────────────────────────────────────────────────


class AIHelper:
    """Lightweight wrapper around the styler's existing chat clients.

    The styler instance is passed in so we reuse already-constructed
    OpenAI/Groq/Cerebras clients — no double-initialisation cost, no
    extra key reads. ``answer()`` is the single public method.
    """

    def __init__(self, styler):
        self._styler = styler

    def answer(self, question: str, *, preset_id: Optional[str] = None) -> dict:
        """Send the user's question + redacted context to a chat model.

        Returns:
            {
              "ok": True,
              "answer": "<assistant reply>",
              "provider": "groq" | "cerebras" | "openai",
              "latency_ms": 1234,
            }
            or {"ok": False, "error": "..."} on failure.
        """
        # Resolve the actual question text. If the caller supplied a
        # preset_id, use the canned prompt and append the user's own text
        # if they typed anything additional.
        canned = PRESETS.get(preset_id or "", "") if preset_id else ""
        user_question = (canned + "\n\n" + (question or "")).strip()
        if not user_question:
            return {"ok": False, "error": "Empty question"}

        ctx = gather_context()
        ctx_json = json.dumps(ctx, indent=2)[:8000]  # cap at 8KB just in case

        # Build the prompt. Context first (so the model sees evidence
        # before the question), then the question itself.
        user_prompt = (
            f"CONTEXT (redacted; secrets already stripped):\n"
            f"```json\n{ctx_json}\n```\n\n"
            f"QUESTION:\n{user_question}"
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ]

        # Try providers in order: Cerebras (smartest at structured analysis
        # among the fast tiers), then Groq, then OpenAI. The styler exposes
        # ``_cerebras_client`` / ``client`` (OpenAI) and we look for Groq
        # via ``_groq_client`` if it exists in this styler version.
        attempts = []

        # Cerebras
        cb_client = getattr(self._styler, "_cerebras_client", None)
        cb_model = getattr(self._styler, "_cerebras_model", "qwen-3-235b-a22b-instruct-2507")
        if cb_client is not None and getattr(self._styler, "_use_cerebras", False):
            attempts.append(("cerebras", cb_client, cb_model))

        # Groq
        gq_client = getattr(self._styler, "_groq_client", None)
        gq_model = getattr(self._styler, "_groq_model", "llama-3.3-70b-versatile")
        if gq_client is not None and getattr(self._styler, "_use_groq", False):
            attempts.append(("groq", gq_client, gq_model))

        # OpenAI fallback
        oa_client = getattr(self._styler, "client", None)
        oa_model = getattr(self._styler, "_openai_model", "gpt-4o-mini")
        if oa_client is not None:
            attempts.append(("openai", oa_client, oa_model))

        if not attempts:
            return {
                "ok": False,
                "error": (
                    "No chat provider is configured. Open Settings → API Keys "
                    "and paste in a Groq / Cerebras / OpenAI key — the helper "
                    "uses the same keys as the styler."
                ),
            }

        last_error: Optional[str] = None
        for provider_name, client, model in attempts:
            t0 = time.time()
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.2,   # low: factual debugging, not creative
                    max_tokens=600,
                )
                text = resp.choices[0].message.content.strip()
                latency = int((time.time() - t0) * 1000)
                return {
                    "ok": True,
                    "answer": text,
                    "provider": provider_name,
                    "model": model,
                    "latency_ms": latency,
                }
            except Exception as e:
                last_error = f"{provider_name}: {str(e)[:200]}"
                continue

        return {
            "ok": False,
            "error": (
                "All configured providers failed. Last error: "
                f"{last_error or 'unknown'}"
            ),
        }
