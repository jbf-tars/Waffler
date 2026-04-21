# Private Mode — Local Gemma 4 Design Spec

**Date:** 2026-04-21
**Status:** Draft — awaiting implementation plan
**Target version:** v3.10.0

## 1. Problem

Waffler today routes all transcription and cleanup through cloud APIs (Groq Whisper + Groq Llama 3.3 70B by default; OpenAI and Gemini as alternatives). Users who want to use the app without any network round-trip — either for privacy, offline use, or to avoid the bring-your-own-key step — have no option.

Google's Gemma 4 (released April 2, 2026) is an Apache 2.0-licensed open-weight model family. The E4B variant (4.5B effective parameters, 8B total with embeddings, 9.6GB on disk as distributed by Ollama) runs well on any modern Mac or PC with 16GB+ RAM and is well-suited to the cleanup task Waffler performs.

## 2. Goal

Ship a **Private Mode** toggle in Settings that routes both transcription and cleanup to local machine resources:

- **Transcription:** local Whisper (already supported in Waffler via the `LOCAL_WHISPER=1` env path).
- **Cleanup:** local Gemma 4 E4B via Ollama.

When Private Mode is ON, **no byte of audio or text leaves the user's machine**. When it is OFF (the default), Waffler's behavior is byte-for-byte identical to v3.9.0 — the existing API-key / cloud flow is unaffected.

## 3. Non-Goals

- **Not replacing cloud mode.** Private Mode is strictly opt-in. Cloud mode remains the default.
- **Not supporting arbitrary local models.** v1 supports exactly one model: `gemma4:e4b`. Model selection is not exposed to the user.
- **Not bundling Ollama.** Users install Ollama themselves via `ollama.com/download`. Waffler only detects it.
- **Not auto-installing Ollama.** No `brew install` / `winget install` attempts. Detection + link only.
- **Not providing cloud fallback when local fails.** If Private Mode is on and local tools fail, the transcription is dropped with a clear error. We never silently reach for cloud APIs — that would break the privacy promise.
- **Not supporting Intel Macs.** Waffler already requires Apple Silicon; Private Mode inherits that.

### 3.1 Platform support for Private Mode

Private Mode is supported on both platforms Waffler targets:
- **macOS (Apple Silicon only):** local Whisper via `mlx-whisper`, local cleanup via Ollama.
- **Windows (x64):** local Whisper via `faster-whisper`, local cleanup via Ollama.

Local Whisper already works on both — the infrastructure exists behind `LOCAL_WHISPER=1`. Private Mode simply forces that code path when enabled.

## 4. Target Audience

Users who understand AI and open-source models. They are comfortable installing a secondary app (Ollama), triggering a ~10GB model download, and troubleshooting local inference if something misbehaves. The UI should be honest and informative, not hand-holding.

## 5. Architecture

### 5.1 Component split

| File | Role | Change |
|---|---|---|
| `src/local_backend.py` | **New.** Only module aware of Ollama's HTTP API and endpoint URL. | Create |
| `src/transcribe_whisper.py` | Route to local Whisper when Private Mode on; refuse cloud fallback in that mode. | Add private-mode branch |
| `src/style_openai.py` | Add `"local"` provider option alongside `groq`/`gemini`/`openai`. | Additive |
| `app.py` | Expose JS-callable methods for status check, toggle, and model download with progress. | Additive |
| `ui/index.html` + `ui/app.js` | Add Private Mode card to Settings with detection panel, action buttons, toggle. | Additive |
| `prompts/normal.txt` | Start shared. Branch to `prompts/normal_local.txt` only if Gemma adherence is poor in testing. | Possibly additive |

### 5.2 `local_backend.py` interface

Four functions. This file is the **only** one that knows Ollama exists; everything else treats it as "the local provider." If we ever swap Ollama for llama.cpp, only this file changes.

```python
def check_ollama_running() -> bool:
    """HTTP GET http://localhost:11434/api/version, 500ms timeout."""

def check_model_installed(name: str = "gemma4:e4b") -> bool:
    """HTTP GET /api/tags, look for model in response."""

def pull_model(name: str, on_progress: Callable[[float], None]) -> None:
    """Stream POST /api/pull, call on_progress(percent) as chunks arrive."""

def clean_text(prompt: str) -> str:
    """POST /v1/chat/completions (Ollama's OpenAI-compatible endpoint).
    `prompt` is the fully formatted string — `prompts/normal.txt` with the
    transcript already substituted, matching the signature of the existing
    `style_openai._style_groq(prompt, ...)` method. Sent as a single user
    message: {role: "user", content: prompt}. model=gemma4:e4b,
    temperature=0, 30s timeout. Raises LocalUnavailableError on connection
    error, timeout, or non-200 response."""
```

All HTTP calls use the `requests` library (already a Waffler dependency) for consistency — no `httpx` or other new dependencies.

### 5.3 Routing decisions

A single source of truth for whether Private Mode is active: `settings.json → "private_mode": bool` (default `false`).

- `transcribe_whisper.transcribe_sync()` reads `private_mode` at call time. If true: force `_backend` to `"mlx"` (Mac) or `"faster"` (Windows). If the required local backend isn't loaded, raise `LocalUnavailableError`.
- `style_openai.OpenAIStyler.style()` reads `private_mode` at call time. If true: call `local_backend.clean_text()`; on any failure, raise `LocalUnavailableError`. (Note: the method is named `style`, not `clean` or `style_text`.)
- Neither code path falls back to cloud when `private_mode=True`.

### 5.4 State isolation

The change is purely additive. When `private_mode=False`:
- `transcribe_whisper._backend` selection logic is unchanged.
- `style_openai._style_groq/gemini/openai()` methods are unchanged.
- No new HTTP calls happen. No Ollama detection. No local model loading.

A regression test asserts this explicitly (see §9).

## 6. UI / UX

### 6.1 Settings card layout

Added below the existing About section. No changes to existing sections.

```
┌─ Private Mode ──────────────────────────────────────┐
│ Keep everything on your machine. Audio is           │
│ transcribed and cleaned locally — zero cloud calls. │
│                                                     │
│  Ollama        ✓ Installed, running      [Refresh]  │
│  Gemma 4 E4B   ✗ Not downloaded   [Download (9.6GB)]  │
│                                                     │
│  [  ○   ] Private Mode off                          │
│          (finish setup above to enable)             │
└─────────────────────────────────────────────────────┘
```

### 6.2 Detection states

**Ollama row:**

| State | Display | Button |
|---|---|---|
| Not installed | `✗ Not installed` | `Install Ollama →` opens `https://ollama.com/download` |
| Installed but not running | `⚠ Installed but not running` | `Start Ollama` — shells out to `open -a Ollama` (macOS) or launches the Windows shortcut |
| Running | `✓ Installed, running` | `Refresh` |

**Model row:**

| State | Display | Button |
|---|---|---|
| Not present | `✗ Not downloaded` | `Download (9.6GB)` |
| Downloading | `⬇ 42%` with progress bar | `Cancel` |
| Present | `✓ Ready` | `Refresh` |

### 6.3 Toggle behavior

- Disabled (greyed) until **Ollama=running AND model=present**. Tooltip: "Finish setup above to enable."
- When on: label flips to `● Private Mode on — all processing local`.
- When on: small lock icon appears in the main Waffler window header so the user always knows Private Mode is active at a glance.

### 6.4 Detection cadence

- Detection runs once when the Settings page is opened.
- Manual `Refresh` button re-runs on demand.
- **No** background polling.

### 6.5 First-run behavior

Private Mode defaults to **off**. First-time users see the card but it does not nag them or interrupt onboarding. The existing API-key flow is unchanged.

## 7. Runtime Flow

### 7.1 With Private Mode ON

```
hotkey pressed
  ↓
record audio   (existing code, unchanged)
  ↓
transcribe_whisper.transcribe_sync(audio_bytes)
  ├── read private_mode = True from settings
  ├── force _backend = "mlx" (Mac) | "faster" (Windows)
  ├── if local backend not loaded → raise LocalUnavailableError
  ├── _pad_audio_with_silence(audio_bytes)   [existing v3.8.8 helper]
  └── run local Whisper → raw transcript
  ↓
style_openai.OpenAIStyler.style(transcript)
  ├── read private_mode = True
  ├── call local_backend.clean_text(prompt, transcript)
  │     ├── if !check_ollama_running() → LocalUnavailableError
  │     ├── POST http://localhost:11434/v1/chat/completions
  │     │   { model: "gemma4:e4b", temperature: 0,
  │     │     messages: [{role:"user", content: prompt}] }
  │     └── return cleaned text
  └── on LocalUnavailableError → bubble to caller
  ↓
paste cleaned text   (existing code, unchanged)
```

### 7.2 With Private Mode OFF

Identical to v3.9.0. No new code paths execute. Guaranteed by the private-mode branch being the first check in each modified function, and by the regression test in §9.

## 8. Error Handling

All Private Mode failures produce a user-visible toast **and drop the transcription**. There is no cloud fallback.

| Failure | Toast text |
|---|---|
| Ollama not running when hotkey pressed | `Private mode active but Ollama isn't running. Open Settings to check.` |
| Model missing | `Gemma 4 model not found. Download it from Settings → Private Mode.` |
| Ollama HTTP timeout (>30s) | `Local AI is slow to respond. Try again or disable Private Mode.` |
| Local Whisper backend not loaded | `Local transcription not available on this platform.` |
| Any other local exception | `Private mode error: <message>. Check logs.` |

All errors additionally log to `~/.waffler-hosted/app.log` following the pattern of existing error handling. **None trigger a cloud fallback.**

## 9. Testing Strategy

### 9.1 Unit tests (`local_backend.py`)

Mock `requests` responses; verify each state:
- `check_ollama_running()` returns True on 200, False on connection error, False on timeout.
- `check_model_installed()` returns True when model in `/api/tags` JSON, False otherwise.
- `pull_model()` calls `on_progress` with monotonically increasing percentages.
- `clean_text()` returns response body on 200; raises `LocalUnavailableError` on connection error, timeout, or non-200.

### 9.2 Regression test (existing cloud flow)

**Critical.** One new test: with `private_mode=False`, run the full transcribe-then-clean pipeline against mocked Groq/OpenAI clients. Assert the code path taken and the HTTP calls made exactly match what v3.9.0 made. This test is the contract that Private Mode doesn't break the existing feature.

### 9.3 Integration smoke test (local only, skipped in CI)

One end-to-end test that:
1. Confirms a real Ollama instance is reachable at `localhost:11434`.
2. Pulls `gemma4:e4b` if not already present.
3. Runs one cleanup round-trip on a sample transcript.
4. Asserts the output is non-empty, reasonably close to the input, and doesn't contain preamble.

Skipped in CI (needs Ollama installed on the runner). Developers run it locally.

### 9.4 Manual testing

- Turn on Private Mode on a Mac with Ollama installed. Record a few phrases. Verify cleanup quality is acceptable.
- Kill Ollama mid-session. Hit hotkey. Verify the correct toast appears and nothing is sent to cloud.
- Turn Private Mode off. Verify cloud flow still works unchanged.

## 10. Rollout

- Ship as v3.10.0. Minor version bump (new user-facing feature, not a bugfix).
- Docs: one-paragraph update on the website's privacy page noting Private Mode as an option. Update the FAQ with an entry: "How do I use Waffler without an API key?"
- No breaking changes. Users who don't toggle Private Mode see zero difference.

## 11. Open Questions / Future Work

- **Prompt tuning for Gemma.** First release ships the shared `prompts/normal.txt`. If Gemma 4 E4B doesn't follow it well in practice, a branch `prompts/normal_local.txt` is added in a follow-up. Not part of v3.10.0 unless initial testing shows it's required.
- **Model choice.** v1 is E4B-only. If there's user demand for the 31B Dense variant (or others) for higher quality on beefy machines, we can add a picker in a future version.
- **Ollama-independent runtime.** If Ollama becomes unreliable or a better runtime emerges (MLX, llama.cpp bundled), we swap it out by rewriting only `src/local_backend.py`.
