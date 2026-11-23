"""The llama.cpp server engine for src/local_llm.py, on Windows.

The Mac's engine, llama-cpp-python, has no Windows build Waffler can ship
(PyPI builds it from source there, which needs a compiler). llama.cpp's own
Windows releases carry ``llama-server.exe``, built for the processor and, with
NVIDIA's libraries (src/gpu_pack.py), for an NVIDIA card. This engine starts
it for the one model file on 127.0.0.1 at a free port, with ``--fit on`` so
that llama.cpp itself puts as many layers on the card as its free memory takes
(1 GB left over) and the rest on the processor. It asks through the server's
OpenAI-style chat endpoint and reads the reply word by word; a reply is
stopped at the next word by closing the connection, which the server takes
as "stop". ``close()`` ends the process.

The server keeps what it has read in its one slot (``cache_prompt``), so the
clean-up's long rules are read once and not again for every dictation, as
with llama-cpp-python on the Mac.

Nothing listens beyond this computer: the server is bound to 127.0.0.1 only.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

try:
    from data_paths import data_dir
except ImportError:  # imported as src.local_llm_server
    from src.data_paths import data_dir

# As the Mac's engine (src/local_llm_llamacpp.py): one part of a meeting plus
# its notes, or the clean-up's long prompt plus a long dictation.
N_CTX = 16384
LOAD_TIMEOUT_S = 180.0              # a 5 GB model from a cold disk
SERVER_ENV = "WAFFLER_LLAMA_SERVER"
CPU_ENV = "WAFFLER_LLM_DEVICE"          # "cpu": keep everything on the processor
_SERVER_NAME = "llama-server.exe" if sys.platform == "win32" else "llama-server"
FIT_SPARE_MB = 1024                 # left free on the card for everything else


class ContextTooLong(Exception):
    """What was asked does not fit the model's window."""


class ServerFailed(Exception):
    """The server could not be started, or stopped answering."""


def find_server() -> Optional[Path]:
    """The llama-server to run: one named by $WAFFLER_LLAMA_SERVER, else the one
    in <data folder>/llama. None when there is none."""
    named = os.environ.get(SERVER_ENV, "").strip()
    for path in ([Path(named)] if named else []) + [data_dir() / "llama" / _SERVER_NAME]:
        try:
            if path.is_file():
                return path
        except OSError:
            pass
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http():
    import requests
    return requests


class LlamaServerEngine:
    def __init__(self, model_file: Path, server: Optional[Path] = None, log_file: Optional[Path] = None):
        self._server = server or find_server()
        if self._server is None:
            raise ServerFailed("llama-server is not on this computer")
        self._port = _free_port()
        self._base = f"http://127.0.0.1:{self._port}"
        self._log = log_file or (data_dir() / "llama-server.log")
        env = dict(os.environ)
        # NVIDIA's libraries, when Waffler fetched them (src/gpu_pack.py), and
        # anything shipped beside the server itself.
        extra = [str(data_dir() / "gpu"), str(self._server.parent)]
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
        # -lv 4: the log then says how many layers went on the card (read below).
        cmd = [str(self._server), "-m", str(model_file), "--host", "127.0.0.1", "--port", str(self._port),
               "-c", str(N_CTX), "-np", "1", "--fit", "on", "--fit-target", str(FIT_SPARE_MB),
               "-fa", "auto", "--seed", "7", "-lv", "4", "--log-file", str(self._log)]
        if os.environ.get(CPU_ENV, "").strip().lower() == "cpu":
            cmd += ["-ngl", "0"]            # the processor only, as on a PC with no card
        try:
            self._log.parent.mkdir(parents=True, exist_ok=True)
            self._log.unlink(missing_ok=True)
        except OSError:
            pass
        self._proc = subprocess.Popen(
            cmd, cwd=str(self._server.parent), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.load_s = None
        t0 = time.monotonic()
        try:
            self._wait_ready(t0)
        except Exception:
            self.close()
            raise
        self.load_s = round(time.monotonic() - t0, 2)
        self.device, self.gpu_layers = self._read_offload()

    def _wait_ready(self, t0: float) -> None:
        http = _http()
        while time.monotonic() - t0 < LOAD_TIMEOUT_S:
            if self._proc.poll() is not None:
                raise ServerFailed(f"llama-server stopped while loading (exit {self._proc.returncode})")
            try:
                r = http.get(self._base + "/health", timeout=2)
                if r.status_code == 200:
                    return
            except Exception:       # noqa: BLE001 - not listening yet
                pass
            time.sleep(0.25)
        raise ServerFailed("llama-server did not get ready in time")

    def _read_offload(self) -> tuple:
        """(device, layers on the card) from the server's own log. A card is
        in use only when a model buffer sits on it ("CUDA0 model buffer
        size"): the processor build also says "offloaded 43/43 layers to GPU",
        meaning its own repacked copy (CPU_REPACK). ("cpu", 0) otherwise."""
        try:
            text = self._log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "unknown", None
        card = re.search(r"\b(CUDA|Vulkan|ROCm|SYCL|Metal)\d*\s+model buffer size", text)
        if not card:
            return "cpu", 0
        m = re.search(r"offloaded (\d+)/(\d+) layers to GPU", text)
        return card.group(1).lower(), (int(m.group(1)) if m else None)

    def chat(self, system: str, messages: list, max_tokens: int, temperature: float,
             should_stop=None, on_text=None) -> dict:
        """One reply: ``{"text", "input_tokens", "output_tokens",
        "finish_reason"}``, as the Mac's engine gives it. ``should_stop()`` is
        asked after every word; yes ends the reply there ("stopped")."""
        if self._proc is None or self._proc.poll() is not None:
            raise ServerFailed("llama-server is not running")
        body = {"messages": [{"role": "system", "content": system}, *messages],
                "max_tokens": int(max_tokens), "temperature": float(temperature), "seed": 7,
                "stream": True, "cache_prompt": True, "stream_options": {"include_usage": True},
                "chat_template_kwargs": {"enable_thinking": False}}
        text, pieces, finish, usage = "", 0, "stop", {}
        http = _http()
        with http.post(self._base + "/v1/chat/completions", json=body, stream=True,
                       timeout=(5, 600)) as r:
            # The stream is UTF-8, but says no charset, and requests then
            # reads text/* as Latin-1: "£8,400" came back as "Â£8,400".
            r.encoding = "utf-8"
            if r.status_code != 200:
                said = r.text[:500].lower()
                if "context" in said and ("exceed" in said or "too long" in said or "size" in said):
                    raise ContextTooLong(said)
                raise ServerFailed(f"llama-server answered {r.status_code}")
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                usage = chunk.get("usage") or usage
                choice = (chunk.get("choices") or [{}])[0]
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    text += piece
                    pieces += 1
                    if on_text is not None:
                        on_text(text)
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                if should_stop is not None and should_stop():
                    finish = "stopped"
                    break               # leaving the block closes the connection: the server stops
        return {"text": text.strip(), "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or pieces), "finish_reason": finish}

    def close(self) -> None:
        proc, self._proc = getattr(self, "_proc", None), None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:           # noqa: BLE001
            try:
                proc.kill()
            except Exception:       # noqa: BLE001
                pass
