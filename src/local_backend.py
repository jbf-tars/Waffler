"""Ollama backend for Waffler's Private Mode.

This module is the ONLY place in Waffler that knows Ollama's URL,
port, model names, or API shape. If we ever swap Ollama for another
local runtime (llama.cpp, MLX LM, etc.), only this file changes.

Error-handling contracts (per function):
  - Detection functions (check_ollama_running, check_model_installed)
    return bool and NEVER raise.
  - Action functions (pull_model, clean_text) raise LocalUnavailableError
    on network / HTTP / timeout failure.

No function ever falls back to cloud — that's enforced at the caller level.
"""

import requests

OLLAMA_URL = "http://localhost:11434"

MODEL_INFO = {
    "name": "gemma4:e4b",
    "display_name": "Gemma 4 E4B",
    "download_size_gb": 9.6,
    "min_ram_gb": 16,
}

# Backward-compatible alias — existing callers may use DEFAULT_MODEL.
DEFAULT_MODEL = MODEL_INFO["name"]


def check_ollama_running() -> bool:
    """Is the Ollama daemon reachable? Returns bool, never raises."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/version", timeout=0.5)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def check_model_installed(name: str = DEFAULT_MODEL) -> bool:
    """Is the given model already pulled into Ollama? Returns bool, never raises."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        if resp.status_code != 200:
            return False
        models = resp.json().get("models", [])
        if not isinstance(models, list):
            return False
        return any(m.get("name") == name for m in models)
    except requests.RequestException:
        return False


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


def clean_text(prompt: str) -> str:
    """Run the cleanup prompt through Gemma 4 E4B via Ollama.

    `prompt` is the already-formatted string — `prompts/normal.txt` with
    the transcript substituted. We send it as a single user message. The
    model is configured for deterministic output (temperature=0).

    The 9.6GB Gemma 4 E4B can take 30-90 seconds to load into memory on
    first use after the app starts (or after Ollama's 5-minute keep_alive
    expires). Ollama returns HTTP 500 with "loading model" during that
    window — we retry with backoff rather than fail hard. After the
    first warm call, subsequent requests complete in <1 second.

    Raises LocalUnavailableError on any transport or HTTP failure.
    """
    import time as _time
    _MAX_RETRIES = 4
    _RETRY_DELAY = 3  # seconds between retries while model loads

    last_err = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/v1/chat/completions",
                json={
                    "model": DEFAULT_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
                timeout=180,  # covers cold-start model load + inference
            )
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
                except (ValueError, KeyError, IndexError, TypeError) as e:
                    raise LocalUnavailableError(
                        f"ollama returned malformed response: {e}"
                    ) from e

            # "Model still loading" — retryable transient.
            body = resp.text[:400]
            if resp.status_code == 500 and "loading model" in body.lower():
                last_err = f"loading model (attempt {attempt + 1}/{_MAX_RETRIES})"
                if attempt < _MAX_RETRIES - 1:
                    _time.sleep(_RETRY_DELAY)
                    continue
                raise LocalUnavailableError(
                    "ollama is still loading the model after several retries — "
                    "try again in a moment."
                )

            raise LocalUnavailableError(
                f"ollama chat returned {resp.status_code}: {body}"
            )
        except requests.Timeout as e:
            last_err = str(e)
            if attempt < _MAX_RETRIES - 1:
                _time.sleep(_RETRY_DELAY)
                continue
            raise LocalUnavailableError(f"ollama timed out: {e}") from e
        except requests.RequestException as e:
            raise LocalUnavailableError(f"ollama unreachable: {e}") from e

    # Should never reach here (loop either returns or raises), but just in case:
    raise LocalUnavailableError(f"ollama clean_text gave up: {last_err}")
