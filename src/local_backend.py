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
