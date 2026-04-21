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
DEFAULT_MODEL = "gemma4:e4b"


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
