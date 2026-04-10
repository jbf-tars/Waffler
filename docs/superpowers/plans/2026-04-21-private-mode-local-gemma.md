# Private Mode — Local Gemma 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an opt-in Private Mode that routes transcription and cleanup to local machine resources (local Whisper + Gemma 4 E4B via Ollama), with zero cloud calls and a hard-fail policy. Existing cloud / API-key flow is unchanged.

**Architecture:** One new module (`src/local_backend.py`) encapsulates all Ollama HTTP calls. Existing `transcribe_whisper.py` and `style_openai.py` gain a `private_mode` branch — when true, force local; when false, existing behavior unchanged. UI adds one new Settings card with detection panel + toggle.

**Tech Stack:** Python 3.11+, `requests` (existing), `mlx-whisper` / `faster-whisper` (existing local Whisper path), Ollama HTTP API (`localhost:11434`), pywebview bridge to HTML/JS UI.

**Spec:** [docs/superpowers/specs/2026-04-21-private-mode-local-gemma-design.md](../specs/2026-04-21-private-mode-local-gemma-design.md)

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/local_backend.py` | **new** | All Ollama HTTP interaction (detection, pull, inference). Sole owner of `http://localhost:11434` URL and model name. |
| `src/errors.py` | **new** | Shared exception types (`LocalUnavailableError`). Tiny file so both `local_backend` and the callers can import without circular deps. |
| `src/settings_store.py` | **new** | Thin helper module that wraps `~/.waffler-hosted/settings.json` read/write. Currently settings are read ad-hoc in multiple files; centralizing keeps the private-mode flag consistent and makes it testable. |
| `src/transcribe_whisper.py` | modify | Add `private_mode` branch at the top of `transcribe_sync()`. When true: force local backend, raise `LocalUnavailableError` if unavailable, do NOT fall back to cloud. |
| `src/style_openai.py` | modify | Add `"local"` provider alongside `groq`/`gemini`/`openai`. When `private_mode=True`, call `local_backend.clean_text()`. |
| `app.py` | modify | Add 5 JS-callable methods: `get_private_mode_status`, `set_private_mode`, `check_ollama_now`, `pull_gemma_model`, `open_ollama_app`. |
| `ui/index.html` | modify | Add Private Mode card in Settings. Add lock icon in header (hidden by default). |
| `ui/app.js` | modify | Wire up detection panel, download progress streaming, toggle state, header icon visibility. |
| `tests/test_local_backend.py` | **new** | Unit tests for `local_backend` using `unittest.mock.patch` on `requests`. |
| `tests/test_private_mode_routing.py` | **new** | Regression tests asserting cloud flow is unchanged when `private_mode=False`. |
| `tests/test_settings_store.py` | **new** | Unit tests for settings read/write helper. |
| `src/__init__.py` | modify (release) | Version bump to `3.10.0`. |

All changes are additive except version bump. The existing cloud-mode code paths are untouched — they simply gain a `private_mode` check that short-circuits to the new behavior when true.

---

## Phase 1 — Foundation (errors + settings helper)

### Task 1: `LocalUnavailableError` exception class

**Files:**
- Create: `src/errors.py`
- Test: `tests/test_errors.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_errors.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from errors import LocalUnavailableError

def test_local_unavailable_inherits_runtime():
    err = LocalUnavailableError("ollama not running")
    assert isinstance(err, RuntimeError)
    assert str(err) == "ollama not running"
```

- [ ] **Step 2: Run — expect ImportError (module does not exist yet)**

```bash
python -m pytest tests/test_errors.py -v
```

- [ ] **Step 3: Implement**

```python
# src/errors.py
"""Shared exception types for Waffler.

Kept in its own module so `local_backend.py` and its callers
(`transcribe_whisper.py`, `style_openai.py`) can import without
circular-dependency risk.
"""


class LocalUnavailableError(RuntimeError):
    """Raised when Private Mode is active but a local resource (Ollama,
    Gemma model, or local Whisper) is unavailable. Must NEVER be silently
    swallowed in favor of a cloud fallback — the caller should surface a
    user-visible error."""
    pass
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/test_errors.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/errors.py tests/test_errors.py
git commit -m "feat: add LocalUnavailableError for Private Mode failures"
```

---

### Task 2: `settings_store` helper

**Files:**
- Create: `src/settings_store.py`
- Test: `tests/test_settings_store.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_settings_store.py
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import settings_store


def test_load_missing_file_returns_empty_dict(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "missing.json")
    assert settings_store.load() == {}


def test_load_reads_existing_file(monkeypatch, tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"private_mode": True}))
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", p)
    assert settings_store.load() == {"private_mode": True}


def test_set_persists_key(monkeypatch, tmp_path):
    p = tmp_path / "s.json"
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", p)
    settings_store.set_key("private_mode", True)
    assert json.loads(p.read_text())["private_mode"] is True


def test_set_preserves_other_keys(monkeypatch, tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"language": "en"}))
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", p)
    settings_store.set_key("private_mode", True)
    data = json.loads(p.read_text())
    assert data == {"language": "en", "private_mode": True}


def test_private_mode_default_false(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "missing.json")
    assert settings_store.is_private_mode() is False
```

- [ ] **Step 2: Run — expect fail (module missing)**

```bash
python -m pytest tests/test_settings_store.py -v
```

- [ ] **Step 3: Implement**

```python
# src/settings_store.py
"""Centralized read/write for ~/.waffler-hosted/settings.json.

Other modules should call `load()` or `is_private_mode()` rather than
reading the JSON file directly. This gives us one place to validate,
cache, and version the settings schema.
"""

import json
from pathlib import Path

SETTINGS_FILE = Path.home() / ".waffler-hosted" / "settings.json"


def load() -> dict:
    """Return the full settings dict. Missing file or parse error → {}."""
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        pass
    return {}


def set_key(key: str, value) -> None:
    """Set a single key and persist, preserving other keys."""
    data = load()
    data[key] = value
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))


def is_private_mode() -> bool:
    """True if Private Mode is toggled on. Defaults to False."""
    return bool(load().get("private_mode", False))
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/test_settings_store.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/settings_store.py tests/test_settings_store.py
git commit -m "feat: add settings_store helper with is_private_mode()"
```

---

## Phase 2 — Local Backend Module

### Task 2.5: Sanity-check the Gemma 4 Ollama tag (before coding)

**Why this exists:** the spec asserts the Ollama tag is `gemma4:e4b`, but Ollama tags are published by Google/community and occasionally differ from expected (`gemma4:4b`, `gemma4-e4b`, etc.). If we hard-code the wrong tag, every `check_model_installed()` / `pull_model()` call silently reports "not installed" and downloads fail. Verifying the real tag takes 30 seconds; discovering the typo after UI wiring takes much longer.

- [ ] **Step 1: Check the real tag**

```bash
# With Ollama running locally:
curl -s https://registry.ollama.ai/v2/library/gemma4/tags/list | python -m json.tool | head -40
# OR (easier, on any machine with Ollama):
ollama search gemma4 2>&1 | head -20
```

- [ ] **Step 2: Confirm `gemma4:e4b` appears**

If present → continue as planned.
If absent → note the actual tag name, and update `DEFAULT_MODEL` in `src/local_backend.py` + every reference in tests + UI copy ("Gemma 4 E4B") accordingly. No commit needed for this step, just verification.

---

### Task 3: `check_ollama_running()`

**Files:**
- Create: `src/local_backend.py`
- Test: `tests/test_local_backend.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_local_backend.py
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import local_backend


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


def test_check_ollama_running_true_on_200():
    with patch("local_backend.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(200, {"version": "0.1.0"})
        assert local_backend.check_ollama_running() is True
        mock_get.assert_called_once_with(
            "http://localhost:11434/api/version", timeout=0.5
        )


def test_check_ollama_running_false_on_connection_error():
    import requests
    with patch("local_backend.requests.get") as mock_get:
        mock_get.side_effect = requests.ConnectionError()
        assert local_backend.check_ollama_running() is False


def test_check_ollama_running_false_on_timeout():
    import requests
    with patch("local_backend.requests.get") as mock_get:
        mock_get.side_effect = requests.Timeout()
        assert local_backend.check_ollama_running() is False


def test_check_ollama_running_false_on_500():
    with patch("local_backend.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(500)
        assert local_backend.check_ollama_running() is False
```

- [ ] **Step 2: Run — expect fail (module missing)**

```bash
python -m pytest tests/test_local_backend.py -v
```

- [ ] **Step 3: Implement**

```python
# src/local_backend.py
"""Ollama backend for Waffler's Private Mode.

This module is the ONLY place in Waffler that knows Ollama's URL,
port, model names, or API shape. If we ever swap Ollama for another
local runtime (llama.cpp, MLX LM, etc.), only this file changes.

All public functions raise LocalUnavailableError (from src.errors) on
any local-resource failure. They NEVER fall back to cloud.
"""

import requests

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma4:e4b"


def check_ollama_running() -> bool:
    """Is the Ollama daemon reachable? Returns bool, never raises."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/version", timeout=0.5)
        return resp.status_code == 200
    except requests.RequestException:
        return False
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/test_local_backend.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/local_backend.py tests/test_local_backend.py
git commit -m "feat(local_backend): add check_ollama_running"
```

---

### Task 4: `check_model_installed()`

**Files:**
- Modify: `src/local_backend.py`
- Modify: `tests/test_local_backend.py`

- [ ] **Step 1: Add failing test**

```python
# Append to tests/test_local_backend.py

def test_check_model_installed_true_when_tag_present():
    with patch("local_backend.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(200, {
            "models": [{"name": "gemma4:e4b"}, {"name": "llama3:8b"}]
        })
        assert local_backend.check_model_installed() is True


def test_check_model_installed_false_when_tag_missing():
    with patch("local_backend.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(200, {
            "models": [{"name": "llama3:8b"}]
        })
        assert local_backend.check_model_installed() is False


def test_check_model_installed_false_on_error():
    import requests
    with patch("local_backend.requests.get") as mock_get:
        mock_get.side_effect = requests.ConnectionError()
        assert local_backend.check_model_installed() is False


def test_check_model_installed_custom_name():
    with patch("local_backend.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(200, {
            "models": [{"name": "custom:7b"}]
        })
        assert local_backend.check_model_installed("custom:7b") is True
```

- [ ] **Step 2: Run — expect fail (function missing)**

```bash
python -m pytest tests/test_local_backend.py -v
```

- [ ] **Step 3: Implement**

```python
# Append to src/local_backend.py

def check_model_installed(name: str = DEFAULT_MODEL) -> bool:
    """Is the given model already pulled into Ollama? Returns bool, never raises."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        if resp.status_code != 200:
            return False
        models = resp.json().get("models", [])
        return any(m.get("name") == name for m in models)
    except requests.RequestException:
        return False
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/test_local_backend.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/local_backend.py tests/test_local_backend.py
git commit -m "feat(local_backend): add check_model_installed"
```

---

### Task 5: `pull_model()` with progress streaming

**Files:**
- Modify: `src/local_backend.py`
- Modify: `tests/test_local_backend.py`

- [ ] **Step 1: Add failing test**

```python
# Append to tests/test_local_backend.py
import json as _json


def _ndjson_response(lines):
    """Build a fake streaming response yielding NDJSON lines."""
    resp = MagicMock()
    resp.status_code = 200
    resp.iter_lines.return_value = [_json.dumps(l).encode() for l in lines]
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: None
    return resp


def test_pull_model_streams_progress():
    progress = []

    def on_prog(pct):
        progress.append(pct)

    with patch("local_backend.requests.post") as mock_post:
        mock_post.return_value = _ndjson_response([
            {"status": "downloading", "total": 1000, "completed": 0},
            {"status": "downloading", "total": 1000, "completed": 250},
            {"status": "downloading", "total": 1000, "completed": 500},
            {"status": "downloading", "total": 1000, "completed": 1000},
            {"status": "success"},
        ])
        local_backend.pull_model("gemma4:e4b", on_progress=on_prog)

    assert progress == [0.0, 25.0, 50.0, 100.0]


def test_pull_model_raises_on_connection_error():
    import requests
    from errors import LocalUnavailableError
    with patch("local_backend.requests.post") as mock_post:
        mock_post.side_effect = requests.ConnectionError()
        try:
            local_backend.pull_model("gemma4:e4b", on_progress=lambda p: None)
            assert False, "should have raised"
        except LocalUnavailableError:
            pass


def test_pull_model_ignores_progress_without_total():
    """Ollama sometimes emits status-only lines (e.g. 'pulling manifest').
    These should not invoke on_progress."""
    progress = []
    with patch("local_backend.requests.post") as mock_post:
        mock_post.return_value = _ndjson_response([
            {"status": "pulling manifest"},
            {"status": "success"},
        ])
        local_backend.pull_model("gemma4:e4b", on_progress=progress.append)

    assert progress == []
```

- [ ] **Step 2: Run — expect fail**

```bash
python -m pytest tests/test_local_backend.py -v
```

- [ ] **Step 3: Implement**

```python
# Append to src/local_backend.py
import json as _json
from typing import Callable

from errors import LocalUnavailableError


def pull_model(name: str, on_progress: Callable[[float], None]) -> None:
    """Download a model via Ollama's streaming /api/pull endpoint.

    Calls `on_progress(percent)` each time the `completed/total` ratio
    advances. Raises LocalUnavailableError on network failure.
    """
    try:
        with requests.post(
            f"{OLLAMA_URL}/api/pull",
            json={"name": name, "stream": True},
            stream=True,
            timeout=None,  # stream can legitimately take a long time
        ) as resp:
            if resp.status_code != 200:
                raise LocalUnavailableError(
                    f"ollama /api/pull returned {resp.status_code}"
                )
            for raw in resp.iter_lines():
                if not raw:
                    continue
                try:
                    chunk = _json.loads(raw)
                except Exception:
                    continue
                total = chunk.get("total")
                completed = chunk.get("completed")
                if total and completed is not None:
                    on_progress(100.0 * completed / total)
    except requests.RequestException as e:
        raise LocalUnavailableError(f"ollama unreachable: {e}") from e
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/test_local_backend.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/local_backend.py tests/test_local_backend.py
git commit -m "feat(local_backend): add pull_model with streaming progress"
```

---

### Task 6: `clean_text()` — the core cleanup call

**Files:**
- Modify: `src/local_backend.py`
- Modify: `tests/test_local_backend.py`

- [ ] **Step 1: Add failing test**

```python
# Append to tests/test_local_backend.py

def test_clean_text_returns_response_body():
    with patch("local_backend.requests.post") as mock_post:
        mock_post.return_value = FakeResponse(200, {
            "choices": [{"message": {"content": "cleaned text here"}}],
        })
        out = local_backend.clean_text("user prompt here")
        assert out == "cleaned text here"
        args, kwargs = mock_post.call_args
        assert args[0] == "http://localhost:11434/v1/chat/completions"
        body = kwargs["json"]
        assert body["model"] == "gemma4:e4b"
        assert body["temperature"] == 0
        assert body["messages"] == [{"role": "user", "content": "user prompt here"}]
        assert kwargs["timeout"] == 30


def test_clean_text_raises_on_connection_error():
    import requests
    from errors import LocalUnavailableError
    with patch("local_backend.requests.post") as mock_post:
        mock_post.side_effect = requests.ConnectionError()
        try:
            local_backend.clean_text("hi")
            assert False, "should have raised"
        except LocalUnavailableError:
            pass


def test_clean_text_raises_on_non_200():
    from errors import LocalUnavailableError
    with patch("local_backend.requests.post") as mock_post:
        mock_post.return_value = FakeResponse(500, text="boom")
        try:
            local_backend.clean_text("hi")
            assert False, "should have raised"
        except LocalUnavailableError:
            pass


def test_clean_text_raises_on_timeout():
    import requests
    from errors import LocalUnavailableError
    with patch("local_backend.requests.post") as mock_post:
        mock_post.side_effect = requests.Timeout()
        try:
            local_backend.clean_text("hi")
            assert False, "should have raised"
        except LocalUnavailableError:
            pass
```

- [ ] **Step 2: Run — expect fail**

```bash
python -m pytest tests/test_local_backend.py -v
```

- [ ] **Step 3: Implement**

```python
# Append to src/local_backend.py

def clean_text(prompt: str) -> str:
    """Run the cleanup prompt through Gemma 4 E4B via Ollama.

    `prompt` is the already-formatted string — `prompts/normal.txt` with
    the transcript substituted. We send it as a single user message. The
    model is configured for deterministic output (temperature=0).

    Raises LocalUnavailableError on any transport or HTTP failure.
    """
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/v1/chat/completions",
            json={
                "model": DEFAULT_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise LocalUnavailableError(
                f"ollama chat returned {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except requests.RequestException as e:
        raise LocalUnavailableError(f"ollama unreachable: {e}") from e
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/test_local_backend.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/local_backend.py tests/test_local_backend.py
git commit -m "feat(local_backend): add clean_text via OpenAI-compatible endpoint"
```

---

## Phase 3 — Wire into Transcription

### Task 7: Private-mode branch in `transcribe_whisper.transcribe_sync()`

**Files:**
- Modify: `src/transcribe_whisper.py`
- Create: `tests/test_private_mode_routing.py`

- [ ] **Step 1: Write failing regression test**

```python
# tests/test_private_mode_routing.py
"""Verify Private Mode routing decisions.

Two halves:
  1. When private_mode=False, behavior is byte-for-byte unchanged from v3.9.0.
  2. When private_mode=True, transcription forces local backend and refuses
     to fall back to cloud.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _stub_wav_bytes():
    """1 second of silence as a minimal valid WAV (so _pad_audio_with_silence
    doesn't choke)."""
    import io, wave, struct
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 16000, *([0] * 16000)))
    return buf.getvalue()


def test_private_mode_false_uses_groq_when_configured(monkeypatch):
    """Regression: private_mode=False MUST behave exactly like v3.9.0."""
    import settings_store
    monkeypatch.setattr(settings_store, "is_private_mode", lambda: False)

    from transcribe_whisper import WhisperTranscriber
    t = WhisperTranscriber(api_key="", groq_api_key="fake-key")
    t._groq_client = MagicMock()
    t._groq_client.audio.transcriptions.create.return_value = "hello world"

    result = t.transcribe_sync(_stub_wav_bytes())
    assert result == "hello world"
    t._groq_client.audio.transcriptions.create.assert_called_once()


def test_private_mode_true_forces_local_and_never_calls_groq(monkeypatch):
    import settings_store
    monkeypatch.setattr(settings_store, "is_private_mode", lambda: True)

    from transcribe_whisper import WhisperTranscriber
    from errors import LocalUnavailableError
    t = WhisperTranscriber(api_key="", groq_api_key="fake-key")
    t._groq_client = MagicMock()
    # No local backend loaded → should raise, NOT call Groq

    try:
        t.transcribe_sync(_stub_wav_bytes())
        assert False, "should have raised"
    except LocalUnavailableError:
        pass

    # Critical: Groq was never called even though a key was present
    t._groq_client.audio.transcriptions.create.assert_not_called()
```

- [ ] **Step 2: Run — expect fail (no private-mode branch yet)**

```bash
python -m pytest tests/test_private_mode_routing.py -v
```

- [ ] **Step 3: Modify `transcribe_sync()` to add the branch**

**Scope of change:** ONLY the backend-dispatcher block changes. Everything AFTER `raw = ...` is assigned stays exactly as-is (hallucination strip, vocab-echo check, return). Do not touch code below the dispatcher.

Exact BEFORE state (current code, around lines 292–307):

```python
def transcribe_sync(self, audio_bytes: bytes):
    audio_bytes = _pad_audio_with_silence(audio_bytes)

    if self._backend == "groq":
        try:
            raw = self._transcribe_groq(audio_bytes)
        except Exception as e:
            print(f"⚠️  Groq transcription failed ({e}), falling back to OpenAI")
            if self.client:
                raw = self._transcribe_api(audio_bytes)
            else:
                raise
    elif self._backend == "mlx":
        raw = self._transcribe_mlx(audio_bytes)
    elif self._backend == "faster":
        raw = self._transcribe_faster(audio_bytes)
    else:
        raw = self._transcribe_api(audio_bytes)

    cleaned = _strip_hallucinations(raw)
    # ... rest of method unchanged (vocab echo, return) ...
```

Exact AFTER state — replace only that dispatcher block:

```python
def transcribe_sync(self, audio_bytes: bytes):
    audio_bytes = _pad_audio_with_silence(audio_bytes)

    # ── Private Mode: force local backend, never touch cloud ─────────
    import settings_store
    from errors import LocalUnavailableError
    if settings_store.is_private_mode():
        if _mlx_whisper is not None:
            raw = self._transcribe_mlx(audio_bytes)
        elif _faster_whisper is not None:
            raw = self._transcribe_faster(audio_bytes)
        else:
            raise LocalUnavailableError(
                "Private Mode is on but no local Whisper backend is loaded. "
                "Set LOCAL_WHISPER=1 and restart, or disable Private Mode."
            )
    elif self._backend == "groq":
        try:
            raw = self._transcribe_groq(audio_bytes)
        except Exception as e:
            print(f"⚠️  Groq transcription failed ({e}), falling back to OpenAI")
            if self.client:
                raw = self._transcribe_api(audio_bytes)
            else:
                raise
    elif self._backend == "mlx":
        raw = self._transcribe_mlx(audio_bytes)
    elif self._backend == "faster":
        raw = self._transcribe_faster(audio_bytes)
    else:
        raw = self._transcribe_api(audio_bytes)

    cleaned = _strip_hallucinations(raw)
    # ... rest of method unchanged — DO NOT modify below this point ...
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/test_private_mode_routing.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/transcribe_whisper.py tests/test_private_mode_routing.py
git commit -m "feat(transcribe): honor private_mode — force local, never fallback to cloud"
```

---

## Phase 4 — Wire into Cleanup

### Task 8: Private-mode branch in `style_openai`

**Files:**
- Modify: `src/style_openai.py`
- Modify: `tests/test_private_mode_routing.py`

**Key facts about the actual code (verified by reading `src/style_openai.py`):**
- The method is `OpenAIStyler.style(transcript)` — NOT `style_text`.
- It returns a **tuple** `(styled_text: str, usage: dict)` — NOT a dict.
- Cloud dispatch checks `self._use_groq` (bool flag set in `__init__`) — NOT `self._groq_client`.
- Strip call inside `style()` uses `self._last_raw`, which is set to `transcript` at line 95.
- Existing `usage` dict shape from Groq path: `{"input_tokens": N, "output_tokens": N, "api_used": True, "provider": "groq", ...}`.

The private-mode branch must preserve the same `(text, usage_dict)` return shape so existing callers are unaffected.

- [ ] **Step 1: Add failing cleanup tests**

```python
# Append to tests/test_private_mode_routing.py

def test_cleanup_private_mode_false_uses_groq_when_configured(monkeypatch):
    """Regression: private_mode=False cleanup routes to Groq as before."""
    import settings_store
    monkeypatch.setattr(settings_store, "is_private_mode", lambda: False)

    with patch("style_openai.OpenAIStyler._style_groq") as mock_groq, \
         patch("local_backend.clean_text") as mock_local:
        mock_groq.return_value = ("cloud result", {"input_tokens": 1, "output_tokens": 1, "api_used": True})

        from style_openai import OpenAIStyler
        styler = OpenAIStyler(groq_api_key="fake")
        styler._use_groq = True  # matches dispatcher check at line 128

        text, usage = styler.style("hi there, please clean this up for me")
        assert text == "cloud result"
        assert mock_groq.called
        assert not mock_local.called


def test_cleanup_private_mode_true_uses_local_and_never_calls_cloud(monkeypatch):
    import settings_store
    monkeypatch.setattr(settings_store, "is_private_mode", lambda: True)

    with patch("style_openai.OpenAIStyler._style_groq") as mock_groq, \
         patch("style_openai.OpenAIStyler._style_openai") as mock_openai, \
         patch("style_openai.OpenAIStyler._style_gemini") as mock_gemini, \
         patch("local_backend.clean_text", return_value="local cleaned") as mock_local:

        from style_openai import OpenAIStyler
        styler = OpenAIStyler(groq_api_key="fake")
        styler._use_groq = True

        text, usage = styler.style("hi there, please clean this up for me")

        assert text == "local cleaned"
        assert usage["provider"] == "local"
        assert usage["api_used"] is False  # "no cloud call"
        assert mock_local.called
        # None of the cloud paths were invoked
        assert not mock_groq.called
        assert not mock_openai.called
        assert not mock_gemini.called
```

NB: `_is_simple()` at the top of `style()` can short-circuit and skip the LLM entirely. Use a test input that's long/complex enough to fail `_is_simple()` (the example above is comfortably over the threshold).

- [ ] **Step 2: Run — expect fail**

```bash
python -m pytest tests/test_private_mode_routing.py -v
```

- [ ] **Step 3: Modify `style_openai.py`**

In `OpenAIStyler.style()`, add a private-mode branch **after** the prompt is built (line 121, where `prompt = self.prompt_template.format(...)` finishes) and **before** the cloud dispatcher (line 128, `if self._use_groq:`).

Exact before-block (around line 126):

```python
        self._vocab_system_extra = (
            f" If any of these words were intended by the speaker, use these exact spellings: {', '.join(vocab)}."
            if vocab else ""
        )

        # Priority 1: Try Groq (much faster), fall back to OpenAI
        if self._use_groq:
            ...
```

Insert **between** the `_vocab_system_extra` assignment and the `if self._use_groq:` check:

```python
        self._vocab_system_extra = (
            f" If any of these words were intended by the speaker, use these exact spellings: {', '.join(vocab)}."
            if vocab else ""
        )

        # ── Private Mode: route cleanup to local Gemma via Ollama ─────
        # When on, NEVER fall back to cloud — raise on local failure.
        import settings_store
        if settings_store.is_private_mode():
            import local_backend
            styled = local_backend.clean_text(prompt)
            styled = self._strip_hallucinations(styled, self._last_raw)
            usage = {
                "input_tokens": 0,
                "output_tokens": 0,
                "api_used": False,
                "provider": "local",
                "model": local_backend.DEFAULT_MODEL,
                "duration_ms": int((time.time() - start_time) * 1000),
            }
            return styled, usage

        # Priority 1: Try Groq (much faster), fall back to OpenAI
        if self._use_groq:
            ...
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/test_private_mode_routing.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/style_openai.py tests/test_private_mode_routing.py
git commit -m "feat(style): honor private_mode — route cleanup to local Gemma, no cloud fallback"
```

---

## Phase 5 — App.py Bridge

### Task 9: `get_private_mode_status()` + `set_private_mode()`

**Files:**
- Modify: `app.py`
- Test: `tests/test_app_private_mode_api.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_app_private_mode_api.py
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))  # repo root so we can import app.py
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_get_private_mode_status_returns_shape(monkeypatch, tmp_path):
    import settings_store
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "s.json")

    with patch("local_backend.check_ollama_running", return_value=True), \
         patch("local_backend.check_model_installed", return_value=False):
        from app import Api
        api = Api.__new__(Api)
        status = api.get_private_mode_status()

    assert status == {
        "private_mode": False,
        "ollama_running": True,
        "model_installed": False,
    }


def test_set_private_mode_persists(monkeypatch, tmp_path):
    import settings_store
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "s.json")

    from app import Api
    api = Api.__new__(Api)
    api.set_private_mode(True)

    assert settings_store.is_private_mode() is True
```

- [ ] **Step 2: Run — expect fail**

```bash
python -m pytest tests/test_app_private_mode_api.py -v
```

- [ ] **Step 3: Add methods to `app.py`**

In `app.py`, find the `Api` class (or whatever the pywebview-exposed API class is called — look for `expose=...` or the JS-callable methods like `get_vocab`, `save_settings`). Add:

```python
def get_private_mode_status(self) -> dict:
    """Return current Private Mode status for the Settings UI."""
    import settings_store
    import local_backend
    return {
        "private_mode": settings_store.is_private_mode(),
        "ollama_running": local_backend.check_ollama_running(),
        "model_installed": local_backend.check_model_installed(),
    }

def set_private_mode(self, enabled: bool) -> None:
    """Persist the Private Mode toggle."""
    import settings_store
    settings_store.set_key("private_mode", bool(enabled))

def check_ollama_now(self) -> dict:
    """Re-run detection. Same shape as get_private_mode_status."""
    return self.get_private_mode_status()

def open_ollama_app(self) -> None:
    """Launch the Ollama desktop app (platform-specific)."""
    import sys, subprocess
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Ollama"])
        elif sys.platform == "win32":
            # Windows: launch via shell — resolves shortcut / PATH entry
            subprocess.Popen(["cmd", "/c", "start", "", "ollama"], shell=False)
    except Exception as e:
        print(f"⚠️  open_ollama_app failed: {e}")
```

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/test_app_private_mode_api.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app_private_mode_api.py
git commit -m "feat(app): expose private-mode status + toggle + ollama-open to JS"
```

---

### Task 10: `pull_gemma_model()` with progress reporting

**Files:**
- Modify: `app.py`
- Modify: `tests/test_app_private_mode_api.py`

The pull runs on a background thread (same pattern as the existing `updater.py` download). Progress is exposed via a `get_model_pull_progress()` method, which the JS polls.

- [ ] **Step 1: Write failing test**

```python
# Append to tests/test_app_private_mode_api.py
import time


def test_pull_gemma_model_runs_in_background_and_reports_progress(monkeypatch):
    progress_events = [25.0, 50.0, 100.0]

    def fake_pull(name, on_progress):
        for p in progress_events:
            on_progress(p)
            time.sleep(0.01)

    with patch("local_backend.pull_model", side_effect=fake_pull):
        from app import Api
        api = Api.__new__(Api)
        api.pull_gemma_model()

        # Poll until done (max 2s)
        deadline = time.time() + 2
        while time.time() < deadline:
            prog = api.get_model_pull_progress()
            if prog.get("done"):
                break
            time.sleep(0.02)

    final = api.get_model_pull_progress()
    assert final["done"] is True
    assert final["percent"] == 100.0
    assert final.get("error") is None
```

- [ ] **Step 2: Run — expect fail**

```bash
python -m pytest tests/test_app_private_mode_api.py::test_pull_gemma_model_runs_in_background_and_reports_progress -v
```

- [ ] **Step 3: Implement**

In `app.py` add to `Api`:

```python
def __init__(self, ...):
    # ... existing init ...
    self._pull_state = {"percent": 0.0, "done": False, "error": None, "running": False}
    self._pull_thread = None

def pull_gemma_model(self) -> None:
    """Kick off Gemma 4 pull on a background thread."""
    import threading, local_backend
    if self._pull_state.get("running"):
        return  # already in flight

    self._pull_state = {"percent": 0.0, "done": False, "error": None, "running": True}

    def worker():
        try:
            local_backend.pull_model(
                local_backend.DEFAULT_MODEL,
                on_progress=lambda p: self._pull_state.update({"percent": p}),
            )
            self._pull_state.update({"percent": 100.0, "done": True, "running": False})
        except Exception as e:
            self._pull_state.update({"error": str(e), "done": True, "running": False})

    self._pull_thread = threading.Thread(target=worker, daemon=True)
    self._pull_thread.start()

def get_model_pull_progress(self) -> dict:
    """Poll-style accessor for the JS UI."""
    return dict(self._pull_state)
```

NB: For the test to work with `__new__`, we need to initialize `_pull_state` in `pull_gemma_model` too. The implementation above does this on each call — fine.

- [ ] **Step 4: Run — expect PASS**

```bash
python -m pytest tests/test_app_private_mode_api.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app_private_mode_api.py
git commit -m "feat(app): add background pull_gemma_model + progress poll endpoint"
```

---

## Phase 6 — UI (Settings card + header icon)

UI tasks don't have automated tests — manual verification in browser and then smoke test via running Waffler. Each step is still small and committable.

### Task 11: HTML — Settings card skeleton

**Files:**
- Modify: `ui/index.html`

- [ ] **Step 1: Locate the Settings section**

Open `ui/index.html` and find the existing Settings page structure. Find where the About section lives. Insert the Private Mode card directly above or below About, following the same CSS class naming the rest of Settings uses.

- [ ] **Step 2: Add the card HTML**

```html
<!-- Private Mode card — inserted in Settings section -->
<section class="settings-card" id="private-mode-card">
  <h3>Private Mode</h3>
  <p class="help">
    Keep everything on your machine. Audio is transcribed and cleaned locally — zero cloud calls.
  </p>

  <div class="status-row" id="ollama-row">
    <span class="status-label">Ollama</span>
    <span class="status-state" id="ollama-state">Checking…</span>
    <button id="ollama-action-btn" class="btn-secondary">Refresh</button>
  </div>

  <div class="status-row" id="model-row">
    <span class="status-label">Gemma 4 E4B</span>
    <span class="status-state" id="model-state">Checking…</span>
    <button id="model-action-btn" class="btn-secondary">Refresh</button>
  </div>

  <div class="progress-row hidden" id="model-pull-progress">
    <progress id="model-pull-bar" max="100" value="0"></progress>
    <span id="model-pull-label">0%</span>
  </div>

  <div class="toggle-row">
    <label class="switch">
      <input type="checkbox" id="private-mode-toggle" disabled />
      <span class="slider"></span>
    </label>
    <span id="private-mode-toggle-label">Private Mode off</span>
    <span class="toggle-hint" id="private-mode-hint">(finish setup above to enable)</span>
  </div>
</section>

<!-- Lock icon in header — hidden by default -->
<span id="private-mode-header-icon" class="header-icon hidden" title="Private Mode on — all processing local">🔒</span>
```

Use whatever CSS classes match the existing Settings layout; if none exist, add minimal styles inline via a `<style>` block at the top of the file.

- [ ] **Step 3: Manual check — load Waffler**

```bash
python app.py
```

Verify: the Settings page shows the Private Mode card. Toggle is disabled. States say "Checking…".

- [ ] **Step 4: Commit**

```bash
git add ui/index.html
git commit -m "feat(ui): add Private Mode card skeleton to Settings"
```

---

### Task 12: JS — detection + state rendering

**Files:**
- Modify: `ui/app.js`

- [ ] **Step 1: Add the detection + render function**

```javascript
// In ui/app.js, near other settings code

async function refreshPrivateModeStatus() {
  const status = await window.pywebview.api.get_private_mode_status();
  renderPrivateModeStatus(status);
  return status;
}

function renderPrivateModeStatus(s) {
  // Ollama row
  const ollamaState = document.getElementById("ollama-state");
  const ollamaBtn = document.getElementById("ollama-action-btn");
  if (s.ollama_running) {
    ollamaState.textContent = "✓ Installed, running";
    ollamaBtn.textContent = "Refresh";
    ollamaBtn.onclick = refreshPrivateModeStatus;
  } else {
    ollamaState.textContent = "✗ Not installed or not running";
    ollamaBtn.textContent = "Install Ollama →";
    ollamaBtn.onclick = () => window.open("https://ollama.com/download", "_blank");
  }

  // Model row
  const modelState = document.getElementById("model-state");
  const modelBtn = document.getElementById("model-action-btn");
  if (s.model_installed) {
    modelState.textContent = "✓ Ready";
    modelBtn.textContent = "Refresh";
    modelBtn.onclick = refreshPrivateModeStatus;
  } else {
    modelState.textContent = "✗ Not downloaded";
    modelBtn.textContent = "Download (3GB)";
    modelBtn.onclick = startModelPull;
  }
  modelBtn.disabled = !s.ollama_running;  // can't pull without ollama

  // Toggle
  const toggle = document.getElementById("private-mode-toggle");
  const hint = document.getElementById("private-mode-hint");
  const label = document.getElementById("private-mode-toggle-label");
  const canEnable = s.ollama_running && s.model_installed;
  toggle.disabled = !canEnable;
  toggle.checked = s.private_mode;
  hint.style.display = canEnable ? "none" : "";
  label.textContent = s.private_mode
    ? "● Private Mode on — all processing local"
    : "Private Mode off";

  // Header lock icon
  const headerIcon = document.getElementById("private-mode-header-icon");
  headerIcon.classList.toggle("hidden", !s.private_mode);
}

// Call on Settings page load
document.addEventListener("DOMContentLoaded", () => {
  // Only run if on Settings page
  if (document.getElementById("private-mode-card")) {
    refreshPrivateModeStatus();
  }
});
```

- [ ] **Step 2: Manual test**

```bash
python app.py
```

Open Settings. Verify detection runs once and status rows populate correctly. If Ollama is running + model absent, Download button is enabled. If Ollama is not running, Download button is disabled.

- [ ] **Step 3: Commit**

```bash
git add ui/app.js
git commit -m "feat(ui): detect and render Private Mode status"
```

---

### Task 13: JS — download flow with progress

**Files:**
- Modify: `ui/app.js`

- [ ] **Step 1: Implement `startModelPull`**

```javascript
async function startModelPull() {
  await window.pywebview.api.pull_gemma_model();

  const progressRow = document.getElementById("model-pull-progress");
  const bar = document.getElementById("model-pull-bar");
  const label = document.getElementById("model-pull-label");
  progressRow.classList.remove("hidden");

  const poll = setInterval(async () => {
    const p = await window.pywebview.api.get_model_pull_progress();
    bar.value = p.percent || 0;
    label.textContent = Math.round(p.percent || 0) + "%";

    if (p.done) {
      clearInterval(poll);
      progressRow.classList.add("hidden");
      if (p.error) {
        alert("Model download failed: " + p.error);
      }
      await refreshPrivateModeStatus();
    }
  }, 500);
}
```

- [ ] **Step 2: Manual test**

Requires Ollama installed + running. Clear model if present: `ollama rm gemma4:e4b`. Open Waffler settings. Click Download. Verify progress bar fills; completion refreshes state to ✓ Ready.

- [ ] **Step 3: Commit**

```bash
git add ui/app.js
git commit -m "feat(ui): stream model download progress via polling"
```

---

### Task 14: JS — toggle handler

**Files:**
- Modify: `ui/app.js`

- [ ] **Step 1: Wire the toggle**

```javascript
document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("private-mode-card")) {
    refreshPrivateModeStatus();
    document.getElementById("private-mode-toggle").addEventListener("change", async (e) => {
      await window.pywebview.api.set_private_mode(e.target.checked);
      await refreshPrivateModeStatus();
    });
  }
});
```

- [ ] **Step 2: Manual test**

With Ollama + model ready, toggle Private Mode on. Verify:
- Label changes to "● Private Mode on — all processing local"
- Lock icon appears in header
- Restart Waffler, setting is remembered (verify `cat ~/.waffler-hosted/settings.json` shows `"private_mode": true`).

- [ ] **Step 3: Commit**

```bash
git add ui/app.js
git commit -m "feat(ui): persist Private Mode toggle changes"
```

---

## Phase 7 — Integration verification + error surfaces

### Task 15: Toast messages for Private Mode failures

**Files:**
- Modify: `ui/app.js` (or wherever the transcription-error toast lives)
- Modify: `app.py` (error message propagation)

- [ ] **Step 1: Find how transcription errors currently surface**

Search existing code for how current errors (e.g. Groq failures) produce toasts. Usually there's an `on_error` callback or an `alert_user()` helper in `app.py` and a matching JS listener.

- [ ] **Step 2: Map each `LocalUnavailableError` message to user-friendly text**

In the error-surface layer (most likely in `app.py` where the pipeline is orchestrated), wrap the transcribe+style pipeline:

```python
try:
    transcript = self.transcriber.transcribe_sync(audio_bytes)
    styled, usage = self.styler.style(transcript)
    # ... paste ...
except LocalUnavailableError as e:
    msg = str(e)
    if "ollama" in msg.lower() and "unreachable" in msg.lower():
        user_msg = "Private mode active but Ollama isn't running. Open Settings to check."
    elif "not found" in msg.lower() or "not loaded" in msg.lower():
        user_msg = "Gemma 4 model not found. Download it from Settings → Private Mode."
    elif "timeout" in msg.lower():
        user_msg = "Local AI is slow to respond. Try again or disable Private Mode."
    else:
        user_msg = f"Private mode error: {e}. Check logs."
    self._show_toast(user_msg)
    # IMPORTANT: do NOT retry with cloud. Just drop.
```

- [ ] **Step 3: Manual test the 3 failure modes**

1. Kill Ollama (`pkill ollama` on Mac), hit hotkey → toast says "Ollama isn't running..."
2. `ollama rm gemma4:e4b`, hit hotkey → toast says "model not found..."
3. With both OK, Private Mode on, hotkey should succeed (sanity check).

- [ ] **Step 4: Commit**

```bash
git add app.py ui/app.js
git commit -m "feat: user-friendly toast messages for Private Mode failures"
```

---

### Task 16: Run full test suite

- [ ] **Step 1: Run all new tests + existing ones**

```bash
cd /c/Users/james/waffler
python -m pytest tests/test_errors.py tests/test_settings_store.py tests/test_local_backend.py tests/test_private_mode_routing.py tests/test_app_private_mode_api.py -v
```

Expected: all pass.

- [ ] **Step 2: Smoke-test the existing cloud flow manually**

1. Open Waffler with Private Mode off (default).
2. Record a phrase with Groq key configured.
3. Verify transcription + cleanup work exactly as before v3.10.0.

- [ ] **Step 3: Smoke-test Private Mode end-to-end**

1. Install Ollama, `ollama pull gemma4:e4b`.
2. Start Waffler with `LOCAL_WHISPER=1`.
3. Open Settings → Private Mode card shows ✓ ✓. Toggle on.
4. Record a phrase. Verify cleanup happens locally (network-monitor the process: no outgoing HTTPS to `api.groq.com` or `api.openai.com`).

- [ ] **Step 4: Commit any last-minute fixes**

---

## Phase 8 — Release v3.10.0

### Task 17: Version bump + tag

- [ ] **Step 1: Bump `__version__` in `src/__init__.py`**

```python
__version__ = "3.10.0"
```

- [ ] **Step 2: Update `src/data/release.ts` on the website side** (do this in the waffler-website repo in a separate commit after GitHub Release is live)

- [ ] **Step 3: Commit and tag**

```bash
git add src/__init__.py
git commit -m "release: v3.10.0 — Private Mode (local Gemma 4 via Ollama)"
git tag v3.10.0
git push origin main
git push origin v3.10.0
```

- [ ] **Step 4: Watch CI**

```bash
gh run list --limit 4
```

Wait for both Mac + Windows builds to succeed (~4 min). Mac DMG should be signed + notarized + stapled (existing CI unchanged).

- [ ] **Step 5: Verify release**

```bash
gh release view v3.10.0 --json assets -q '.assets[].name'
```

Expected: `Waffler-3.10.0-mac.dmg` and `Waffler-Setup-3.10.0.exe`.

- [ ] **Step 6: Update website release.ts**

In `waffler-website/src/data/release.ts`, bump to `3.10.0` and commit.

---

## Definition of Done

- [ ] All tests from Phase 1–5 pass locally.
- [ ] Regression tests explicitly assert cloud mode is unaffected.
- [ ] Manual smoke test: Private Mode works end-to-end on Mac (your test machine).
- [ ] Manual smoke test: Private Mode off still uses Groq (no behavior change vs v3.9.0).
- [ ] v3.10.0 released, signed/notarized Mac DMG + Windows EXE available.
- [ ] Website download links bumped to v3.10.0.
- [ ] No cloud HTTP call ever fires while Private Mode is on.
