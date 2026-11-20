"""API keys, kept in the operating system's credential store.

Windows Credential Manager on Windows, Keychain on macOS (through `keyring`).
Keys are never written to the database, a settings file or a log, and the
API only ever reports whether a key is set and its last four characters.

Lookup order for a provider: the credential store, then the environment
variable (`GROQ_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`NATTER_CUSTOM_API_KEY`). The environment is what a headless hub uses.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SERVICE = "Natter"
PROVIDERS = {
    "groq": "GROQ_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "custom": "NATTER_CUSTOM_API_KEY",
}

_memory: dict[str, str] = {}   # used when no credential store exists (tests, bare servers)


def _file() -> Path | None:
    """A headless hub has no credential store. When NATTER_KEYS_FILE names a
    file, keys set through the app are kept there (owner-only), so they
    survive a restart. The desktop apps never use this."""
    name = os.environ.get("NATTER_KEYS_FILE")
    return Path(name) if name else None


def _file_load() -> None:
    f = _file()
    if f and f.exists() and not _memory.get("__loaded__"):
        import json
        try:
            _memory.update({k: v for k, v in json.loads(f.read_text(encoding="utf-8")).items()
                            if isinstance(v, str)})
        except ValueError:
            pass
        _memory["__loaded__"] = "1"


def _file_save() -> None:
    f = _file()
    if not f:
        return
    import json
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump({k: v for k, v in _memory.items() if k != "__loaded__"}, out)
    os.replace(tmp, f)


def store_kind() -> str:
    """Where a key typed into Settings ends up: for the wording on screen."""
    if _keyring():
        return "keychain" if sys.platform == "darwin" else "credential_manager"             if sys.platform == "win32" else "keyring"
    return "server_file" if _file() else "memory"


def _keyring():
    if os.environ.get("NATTER_NO_KEYRING") == "1":
        return None
    try:
        import keyring
        from keyring.backends.fail import Keyring as Fail
        if isinstance(keyring.get_keyring(), Fail):
            return None
        return keyring
    except Exception:
        return None


def get_key(provider: str) -> str:
    if provider not in PROVIDERS:
        return ""
    kr = _keyring()
    if not kr:
        _file_load()
    try:
        value = kr.get_password(SERVICE, provider) if kr else _memory.get(provider)
    except Exception:
        value = None
    return (value or os.environ.get(PROVIDERS[provider]) or "").strip()


def set_key(provider: str, value: str) -> None:
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider: {provider}")
    value = (value or "").strip()
    if not value:
        return delete_key(provider)
    kr = _keyring()
    if kr:
        kr.set_password(SERVICE, provider, value)
    else:
        _file_load()
        _memory[provider] = value
        _file_save()


def delete_key(provider: str) -> None:
    kr = _keyring()
    try:
        if kr:
            kr.delete_password(SERVICE, provider)
    except Exception:
        pass
    if not kr:
        _file_load()
    if _memory.pop(provider, None) is not None and not kr:
        _file_save()


def status() -> dict:
    """{provider: {"set": bool, "hint": "…abcd", "source": "store"|"env"|""}}. Never the key."""
    out = {}
    if not _keyring():
        _file_load()
    for p, env in PROVIDERS.items():
        kr = _keyring()
        try:
            stored = (kr.get_password(SERVICE, p) if kr else _memory.get(p)) or ""
        except Exception:
            stored = ""
        value = stored or os.environ.get(env, "")
        out[p] = {"set": bool(value), "hint": ("…" + value[-4:]) if len(value) >= 12 else "",
                  "source": "store" if stored else ("env" if value else "")}
    return out


def waffler_env_path() -> Path:
    return Path.home() / ".waffler-hosted" / ".env"


def import_from_waffler() -> list[str]:
    """Copy the keys Waffler already holds (its .env) into the credential
    store. Returns the providers copied; never returns or logs a key."""
    path = waffler_env_path()
    if not path.exists():
        return []
    copied = []
    wanted = {"GROQ_API_KEY": "groq", "OPENAI_API_KEY": "openai", "ANTHROPIC_API_KEY": "anthropic"}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        provider = wanted.get(name.strip())
        if provider and value and not value.lower().startswith("your_"):
            set_key(provider, value)
            copied.append(provider)
    return copied
