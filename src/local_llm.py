"""A language model on this computer: it tidies dictated text and writes
meeting notes with no key, and no network once the model is here.

An optional second download beside the speech model (src/local_stt.py), off
until it is downloaded. It is used by the clean-up (src/style_openai.py) and
by meeting notes and Ask (src/meetings/llm.py) when it is here and there is no
key, or when the person chose it over their key (``writing_mode: "local"`` in
settings.json). It never falls back to a cloud service: a failure is a plain
sentence, and a dictation is then pasted as it was said.

The engine is llama.cpp through ``llama-cpp-python``
(src/local_llm_llamacpp.py), which is not imported until a request is made.
docs/OFFLINE-TRANSCRIPTION.md ("Tidying and notes on this computer") has the
measurements behind the model and the machines it is offered on.

The design is the speech model's: the file lives in ``<data folder>/models/
<name>/``, its download is resumed where it stopped, checked against the size
and SHA-256 written below and can be cancelled; the model is loaded at the
first request and released after ``WAFFLER_LOCAL_LLM_IDLE_S`` seconds without
one (five minutes unless set, 0: never). One request runs at a time. A
dictation goes ahead of meeting notes, and notes that are being written give
way to it at the next word and are written again afterwards.
"""
from __future__ import annotations

import atexit
import importlib.util
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Callable, NamedTuple, Optional

try:
    import local_stt as _stt
except ImportError:  # imported as src.local_llm
    from src import local_stt as _stt

# One module, whichever way it is imported (as in src/local_stt.py).
for _alias in ("local_llm", "src.local_llm"):
    sys.modules.setdefault(_alias, sys.modules[__name__])

# ── the setting ──────────────────────────────────────────────────────────────

MODE_SETTING = "writing_mode"       # who tidies text and writes notes when there is a key too
MODE_CLOUD = "cloud"
MODE_LOCAL = "local"
MODES = (MODE_CLOUD, MODE_LOCAL)

# What the clean-up reports as its provider when this model did it. Not
# "local": src/quality.py reads that as a clean-up that never ran.
PROVIDER = "ondevice"

MODE_UNKNOWN = "Waffler doesn't know that choice for tidying text. Choose this computer or your key."
NOT_DOWNLOADED = ("The writing model for this computer isn't downloaded yet. "
                  "Download it in Settings, then try again.")
NOT_INSTALLED = "This copy of Waffler can't tidy text or write notes on this computer."
NOT_SUITED = ("The writing model isn't offered on this computer: it needs an Apple Silicon Mac "
              "with 16 GB of memory or more.")
DOWNLOAD_FAILED = ("The writing model couldn't be downloaded. Check you're online and try again: "
                   "it carries on from where it stopped.")
DOWNLOAD_DAMAGED = "The download was damaged, so it was thrown away. Try again."
DOWNLOAD_NO_SPACE = "There isn't enough free disk space for the writing model."
DOWNLOAD_BUSY = "The writing model is already downloading."
LOAD_FAILED = ("The writing model on this computer couldn't be opened. Remove it in Settings "
               "and download it again.")
FAILED = "The writing model on this computer couldn't finish. Nothing was sent anywhere."
TOO_SLOW = "The writing model on this computer took too long."
TOO_LONG = "That is too long for the writing model on this computer."


class LocalLLMError(Exception):
    """A failure with a sentence fit to show the person. ``kind`` is
    'unavailable' (no engine in this build), 'not_downloaded', 'download',
    'deadline', 'too_long' or 'failed'."""

    def __init__(self, message: str, kind: str = "failed"):
        super().__init__(message)
        self.kind = kind


def mode_of(stored) -> str:
    """``"local"`` only when settings.json says exactly that; else ``"cloud"``."""
    if isinstance(stored, dict) and stored.get(MODE_SETTING) == MODE_LOCAL:
        return MODE_LOCAL
    return MODE_CLOUD


def prefers_local(stored=None) -> bool:
    """Did the person choose this computer over their key? Read from
    settings.json at the time of asking when ``stored`` is not given."""
    return mode_of(_stt._read_settings() if stored is None else stored) == MODE_LOCAL


def settings_problem(payload) -> Optional[str]:
    """A plain sentence when a save names a choice Waffler does not have."""
    if isinstance(payload, dict) and MODE_SETTING in payload and payload[MODE_SETTING] not in MODES:
        return MODE_UNKNOWN
    return None


def should_use(has_key: bool, stored=None) -> bool:
    """Is this model the one to ask? Yes when it is here and there is no key
    at all, or the person chose it. With a key and no choice made, nothing
    changes for them. Never raises."""
    try:
        if not is_available() or not is_ready():
            return False
        return (not has_key) or prefers_local(stored)
    except Exception:               # noqa: BLE001
        return False


# ── the model ────────────────────────────────────────────────────────────────

class ModelSpec(NamedTuple):
    label: str
    files: tuple

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


# One model, pinned to a commit of its repository with its size and SHA-256, so
# what is loaded is exactly what was measured. Google's own 4-bit build of
# Gemma 4 E4B (trained to be run at 4 bits; Apache 2.0). Measured on an M5 with
# 16 GB: a 60-word dictation tidied in 1.8 s with the app's real prompt and
# nothing invented in 28 samples; a 1,400-word meeting's notes in 22 s; about
# 3.6 GB of memory while loaded. The smaller E2B added sign-offs nobody said.
DEFAULT_MODEL = "gemma-4-e4b-q4"
MODELS: dict = {
    DEFAULT_MODEL: ModelSpec("Gemma 4 E4B", (_stt.ModelFile(
        "gemma-4-E4B_q4_0-it.gguf",
        "https://huggingface.co/google/gemma-4-E4B-it-qat-q4_0-gguf/resolve/"
        "4b4a2c1d584be7264f87aac328a1bc739ce81b6c/gemma-4-E4B_q4_0-it.gguf",
        5154941280, "676c35070db6dbe52f93e9c864ee0fba4eddea94b9c875d9cb10daff453fbaee"),)),
}

_READY_MARK = ".ready"
_available: list = []


def is_available() -> bool:
    """Is the engine in this build? (Does not import it.)"""
    if not _available:
        try:
            _available.append(importlib.util.find_spec("llama_cpp") is not None)
        except (ImportError, ValueError):
            _available.append(False)
    return _available[0]


def selected_model() -> str:
    return DEFAULT_MODEL


def model_dir(name: str) -> Path:
    return _stt.models_dir() / name


def _is_ready(name: str) -> bool:
    """The file is here, whole, and was verified when it was downloaded."""
    spec = MODELS.get(name)
    if spec is None:
        return False
    folder = model_dir(name)
    try:
        if not (folder / _READY_MARK).is_file():
            return False
        return all((folder / f.name).stat().st_size == f.size for f in spec.files)
    except OSError:
        return False


def is_ready() -> bool:
    return _is_ready(selected_model())


# ── does this computer suit it? ──────────────────────────────────────────────

SUITS_APPLE_SILICON = "apple_silicon"
LOW_MEMORY = "low_memory"
NOT_MEASURED = "not_measured"
# A 16 GB Mac reports a little under 16.
MIN_RAM_GB = 15.5


def describe_machine():
    """This computer as the speech model describes it (src/local_stt.py
    Machine), with its memory read on Apple Silicon too: memory is what
    decides here."""
    m = _stt.describe_machine()
    if m.ram_gb is None:
        try:
            m = m._replace(ram_gb=_stt._ct2()._total_ram_gb())
        except Exception:           # noqa: BLE001 - unknown memory promises nothing
            pass
    return m


def machine_verdict(machine=None) -> dict:
    """Is this computer offered the writing model? ``{"suits", "reason"}``.
    Stricter than the speech model's verdict, because this model is ten times
    its size:

    * Apple Silicon with 16 GB or more: yes (``"apple_silicon"``). It was
      measured on one, and about 3.6 GB while loaded leaves the Mac usable
      beside the speech model.
    * Apple Silicon with less, or memory that could not be read: no
      (``"low_memory"``). On 8 GB it would push everything else out.
    * Windows and Intel Macs: no (``"not_measured"``). Nothing here has run
      it on one, and it is not in those builds.

    Pure when given a Machine; with none it describes this one."""
    m = machine or describe_machine()
    if m.system == "Darwin" and m.arch == "arm64":
        if m.ram_gb is not None and m.ram_gb >= MIN_RAM_GB:
            return {"suits": True, "reason": SUITS_APPLE_SILICON}
        return {"suits": False, "reason": LOW_MEMORY}
    return {"suits": False, "reason": NOT_MEASURED}


# ── downloading (the speech model's downloader, with this module's own state) ─

_SPACE_MARGIN = 500 * 1024 * 1024

_dl_lock = threading.Lock()                  # guards _dl
_dl: dict = {"active": False, "model": "", "done": 0, "total": 0, "error": "", "cancelled": False}
_dl_cancel = threading.Event()


def _http_get(url: str, headers: dict):
    """One streamed GET. Replaced in tests."""
    import requests
    return requests.get(url, headers=headers, stream=True, timeout=(15, 60))


def _begin_download(name: str) -> Optional[dict]:
    """Mark the download as running. None when it is ours to run, else the
    answer to give."""
    if MODELS.get(name) is None or not is_available():
        return {**model_status(), "ok": False, "error": NOT_INSTALLED}
    if not machine_verdict()["suits"]:
        return {**model_status(), "ok": False, "error": NOT_SUITED}
    with _dl_lock:
        if _dl["active"]:
            return {**_status_locked(_dl["model"]), "ok": False, "error": DOWNLOAD_BUSY}
        _dl.update(active=True, model=name, done=_stt._dir_size(model_dir(name)),
                   total=MODELS[name].size, error="", cancelled=False)
        _dl_cancel.clear()
    return None


def _run_download(name: str, progress_cb) -> dict:
    """The download itself, after _begin_download. Never raises."""
    error, cancelled = "", False
    try:
        _download(name, MODELS[name], progress_cb)
    except _stt._Cancelled:
        cancelled = True
    except _stt.LocalSTTError as e:     # the shared downloader's own sentences, said for this model
        error = DOWNLOAD_DAMAGED if str(e) == _stt.DOWNLOAD_DAMAGED else DOWNLOAD_FAILED
    except LocalLLMError as e:
        error = str(e)
    except OSError as e:
        error = DOWNLOAD_NO_SPACE if getattr(e, "errno", None) == 28 else DOWNLOAD_FAILED
    except Exception:               # noqa: BLE001 - never let a download take a thread down
        error = DOWNLOAD_FAILED
    finally:
        with _dl_lock:
            _dl.update(active=False, error=error, cancelled=cancelled)
    with _dl_lock:
        status = _status_locked(name)
    return {**status, "ok": status["state"] == "ready", "cancelled": cancelled}


def _download(name: str, spec: ModelSpec, progress_cb) -> None:
    folder = model_dir(name)
    folder.mkdir(parents=True, exist_ok=True)
    last = [None]
    if _is_ready(name):
        _note_progress(spec.size, spec.size, progress_cb, last)
        return
    (folder / _READY_MARK).unlink(missing_ok=True)
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        free = None
    if free is not None and free < spec.size - _stt._dir_size(folder) + _SPACE_MARGIN:
        raise LocalLLMError(DOWNLOAD_NO_SPACE, "download")
    per_file: dict = {f.name: _stt._bytes_here(folder, f) for f in spec.files}

    def note(file_name: str, n: int) -> None:
        per_file[file_name] = n
        _note_progress(sum(per_file.values()), spec.size, progress_cb, last)

    for f in spec.files:
        if _dl_cancel.is_set():
            raise _stt._Cancelled()
        _stt._fetch(f, folder, note, _dl_cancel, get=lambda url, headers: _http_get(url, headers))
    (folder / _READY_MARK).write_text(
        json.dumps({"model": name, "files": {f.name: f.size for f in spec.files}}), encoding="utf-8")
    _note_progress(spec.size, spec.size, progress_cb, last)


def _note_progress(done: int, total: int, progress_cb, last: list) -> None:
    with _dl_lock:
        _dl.update(done=done, total=total)
    percent = int(done * 100 / total) if total else 0
    if progress_cb is not None and percent != last[0]:
        last[0] = percent
        try:
            progress_cb(percent, done, total)
        except Exception:           # noqa: BLE001 - the page's trouble is not the download's
            pass


def download(progress_cb: Optional[Callable[[int, int, int], None]] = None) -> dict:
    """Download the model and wait for it. Safe to call again after a failure
    or a cancel: a file cut short carries on from where it stopped, and it is
    checked against its size and SHA-256 before it is used. Returns
    model_status() with ``ok``; never raises."""
    name = selected_model()
    refused = _begin_download(name)
    if refused is not None:
        return refused
    return _run_download(name, progress_cb)


def start_download(progress_cb=None, done_cb=None) -> dict:
    """Start the download on a thread of its own and return at once.
    ``done_cb(result)`` gets what download() would have returned. ``started``
    is False when one was running already."""
    name = selected_model()
    refused = _begin_download(name)
    if refused is not None:
        busy = refused.get("error") == DOWNLOAD_BUSY
        return {**refused, "ok": busy, "started": False, "error": "" if busy else refused["error"]}

    def run():
        result = _run_download(name, progress_cb)
        if done_cb is not None:
            try:
                done_cb(result)
            except Exception:       # noqa: BLE001
                pass
    with _dl_lock:
        status = _status_locked(name)
    threading.Thread(target=run, daemon=True, name="LocalLLMDownload").start()
    return {**status, "ok": True, "started": True}


def cancel_download() -> bool:
    """Stop the download. What arrived is kept for the next try."""
    with _dl_lock:
        if not _dl["active"]:
            return False
        _dl_cancel.set()
        return True


def _wait_for_download(timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _dl_lock:
            if not _dl["active"]:
                return
        time.sleep(0.02)


def remove_model() -> dict:
    """Delete the model from this computer (a download in progress is stopped
    first, and the model is released from memory). Returns ``freed_bytes``."""
    name = selected_model()
    if cancel_download():
        _wait_for_download()
    _cancel_idle_timer()
    _gate.acquire(background=False)
    try:
        _release("removed")
        folder = model_dir(name)
        freed = _stt._dir_size(folder)
        shutil.rmtree(folder, ignore_errors=True)
    finally:
        _gate.release()
    with _dl_lock:
        _dl.update(done=0, total=0, error="", cancelled=False)
    return {**model_status(), "ok": not model_dir(name).exists(), "freed_bytes": freed}


def _status_locked(name: str) -> dict:
    """Holds _dl_lock."""
    spec = MODELS.get(name)
    total = spec.size if spec else 0
    downloading = _dl["active"] and _dl["model"] == name
    ready = (not downloading) and _is_ready(name)
    size_on_disk = _stt._dir_size(model_dir(name))
    if downloading:
        state, done = "downloading", _dl["done"]
    elif ready:
        state, done = "ready", total
    else:
        state, done = "not_downloaded", min(size_on_disk, total)
    try:
        verdict = machine_verdict()
    except Exception:               # noqa: BLE001 - a machine that cannot be read is not offered it
        verdict = {"suits": False, "reason": "unknown"}
    return {
        "available": is_available(),          # is the engine in this build?
        "model": name,
        "label": spec.label if spec else name,
        "state": state,                       # 'not_downloaded' | 'downloading' | 'ready'
        "percent": int(done * 100 / total) if total else 0,
        "bytes_done": done,
        "bytes_total": total,
        "size_on_disk": size_on_disk,
        "error": "" if (downloading or ready) else (_dl["error"] if _dl["model"] == name else ""),
        "cancelled": bool(_dl["cancelled"]) and _dl["model"] == name and not downloading and not ready,
        "loaded": _engine is not None,
        "suits_machine": bool(verdict.get("suits")),
        "machine_reason": str(verdict.get("reason") or ""),
        "load_s": _last["load_s"],
        "reply_s": _last["reply_s"],
    }


def model_status() -> dict:
    """For the page: not downloaded, downloading (with ``percent``) or ready,
    the size, and whether this computer is offered it. Cheap enough to poll."""
    with _dl_lock:
        return _status_locked(selected_model())


# ── one request at a time ────────────────────────────────────────────────────

_gate = _stt._Gate()
_engine = None                               # the loaded engine; only touched holding _gate
_engine_key: Optional[str] = None
_last: dict = {"device": None, "load_s": None, "reply_s": None, "unloaded": ""}
_last_used = 0.0
_now = time.monotonic
_MAX_RESTARTS = 40                           # how often notes give way before they are given up

DEFAULT_IDLE_UNLOAD_S = 300.0
_idle_unload_s = _stt._env_float("WAFFLER_LOCAL_LLM_IDLE_S", DEFAULT_IDLE_UNLOAD_S)
_idle_lock = threading.Lock()
_idle_timer: Optional[threading.Timer] = None

# The clean-up's prompt is long (about 5,600 tokens) and the same for every
# dictation, so it is read once and kept: a dictation then costs only its own
# words. Anything else the model is asked (notes, a question) pushes it out,
# so it is read again afterwards, behind any dictation that is waiting.
_primer: Optional[Callable[[], tuple]] = None
_primed = False
_prime_lock = threading.Lock()
_prime_thread: Optional[threading.Thread] = None


def _load_engine(name: str):
    """Open the model. Replaced in tests. The engine has ``chat(system,
    messages, max_tokens, temperature, should_stop, on_text)`` and ``close()``."""
    try:
        import local_llm_llamacpp as mod
    except ImportError:  # imported as src.local_llm
        from src import local_llm_llamacpp as mod
    return mod.LlamaCppEngine(model_dir(name) / MODELS[name].files[0].name)


def is_loaded() -> bool:
    """Is the model in memory now?"""
    return _engine is not None


def _release(why: str) -> bool:
    """Give the model's memory back. Holds _gate."""
    global _engine, _engine_key, _primed
    if _engine is None:
        return False
    try:
        _engine.close()
    except Exception:               # noqa: BLE001
        pass
    _engine, _engine_key, _primed = None, None, False
    _last["unloaded"] = why
    return True


def unload() -> bool:
    """Release the model from memory now. True when one was loaded."""
    _cancel_idle_timer()
    _gate.acquire(background=False)
    try:
        return _release("on request")
    finally:
        _gate.release()


# Set as the program ends: whatever is being written stops at the next word.
_exiting = threading.Event()
_EXIT_WAIT_S = 20.0


def _release_at_exit() -> None:
    """Close the model before the program ends, whatever it is doing.
    llama.cpp aborts the whole process if its Metal device is torn down with
    a model still open (measured: quitting while a request ran ended in a
    crash report). So a running request is told to stop, which it does at
    the next word, and this waits for its turn; reading a long prompt cannot
    be interrupted and takes a few seconds at most."""
    _exiting.set()
    _cancel_idle_timer()
    end = time.monotonic() + _EXIT_WAIT_S
    while not _gate.acquire(background=False, blocking=False):
        if time.monotonic() >= end:
            return
        time.sleep(0.02)
    try:
        _release("exit")
    finally:
        _gate.release()


atexit.register(_release_at_exit)


def set_idle_unload(seconds: Optional[float]) -> None:
    """The idle period after which the model is released; 0 or None keeps it."""
    global _idle_unload_s
    _idle_unload_s = float(seconds or 0)
    _arm_idle_timer()


def _cancel_idle_timer() -> None:
    global _idle_timer
    with _idle_lock:
        if _idle_timer is not None:
            _idle_timer.cancel()
            _idle_timer = None


def _arm_idle_timer(delay: Optional[float] = None) -> None:
    global _idle_timer
    with _idle_lock:
        if _idle_timer is not None:
            _idle_timer.cancel()
            _idle_timer = None
        if _idle_unload_s > 0 and _engine is not None:
            _idle_timer = threading.Timer(_idle_unload_s if delay is None else delay, _idle_fire)
            _idle_timer.daemon = True
            _idle_timer.name = "LocalLLMIdle"
            _idle_timer.start()


def _idle_fire() -> None:
    """Timer thread: release the model if nothing was asked for the idle
    period. It takes its turn like any caller, so it never pulls the model
    from under a request."""
    with _idle_lock:
        if threading.current_thread() is not _idle_timer:
            return                  # superseded
    if not _gate.acquire(background=True, blocking=False):
        _arm_idle_timer(min(1.0, max(0.05, _idle_unload_s)))
        return
    try:
        if _engine is None or _idle_unload_s <= 0:
            return
        remaining = _idle_unload_s - (_now() - _last_used)
        if remaining > 0.01:
            _arm_idle_timer(remaining)
            return
        _release("idle")
    finally:
        _gate.release()


def _ensure_engine(name: str) -> None:
    """Load the model if it is not in memory. Holds _gate."""
    global _engine, _engine_key, _primed
    if _engine is not None and _engine_key == name:
        return
    _release("replaced")
    t0 = time.perf_counter()
    try:
        _engine = _load_engine(name)
    except LocalLLMError:
        raise
    except Exception as e:          # noqa: BLE001
        raise LocalLLMError(LOAD_FAILED, "failed") from e
    _engine_key, _primed = name, False
    _last["load_s"] = round(time.perf_counter() - t0, 3)
    _last["device"] = getattr(_engine, "device", None)


def set_primer(primer: Optional[Callable[[], tuple]]) -> None:
    """``primer()`` gives ``(system, user)`` of a clean-up request: the prompt
    to read ahead of time (src/style_openai.py sets it)."""
    global _primer
    _primer = primer


def _prime() -> bool:
    """Read the clean-up's prompt so the next dictation does not wait for it.
    Takes its turn behind any dictation. Never raises."""
    global _primed, _last_used
    primer = _primer
    if primer is None:
        return False
    _gate.acquire(background=True)
    try:
        if _engine is None or _primed or _exiting.is_set():
            return _primed
        system, user = primer()
        _engine.chat(system, [{"role": "user", "content": user}], 1, 0.0)
        _primed = True
        return True
    except Exception:               # noqa: BLE001 - the next dictation reads it itself
        return False
    finally:
        _last_used = _now()
        _gate.release()
        _arm_idle_timer()


def _prime_soon() -> None:
    global _prime_thread
    if _primer is None or _primed:
        return
    with _prime_lock:
        if _prime_thread is not None and _prime_thread.is_alive():
            return
        _prime_thread = threading.Thread(target=_prime, daemon=True, name="LocalLLMPrime")
        _prime_thread.start()


def _wait_for_prime(timeout: float = 10.0) -> None:
    thread = _prime_thread
    if thread is not None:
        thread.join(timeout)


def warm() -> bool:
    """Load the model and read the clean-up's prompt, so the first dictation
    does not pay for either (about 2.5 s and 3.5 s on an M5). Does nothing
    unless the model is here. Never raises. Blocks: call it on a thread."""
    try:
        if not is_available() or not is_ready():
            return False
        _gate.acquire(background=True)
        try:
            _ensure_engine(selected_model())
        finally:
            _gate.release()
            _arm_idle_timer()
        _prime()
        return True
    except Exception:               # noqa: BLE001
        return False


def chat(system: str, messages: list, max_tokens: int = 1024, temperature: float = 0.1,
         background: bool = False, deadline: Optional[float] = None, on_text=None,
         kind: str = "other") -> dict:
    """One reply from the model on this computer: ``{"text", "input_tokens",
    "output_tokens", "finish_reason"}``.

    ``background`` is for work nobody is waiting on (a meeting's notes): a
    dictation that arrives goes first, and notes already being written stop at
    the next word and are written again afterwards. ``deadline`` is a
    ``time.monotonic()`` instant: past it the reply is stopped and this raises
    (kind ``"deadline"``). ``on_text(words so far)`` is called as the reply is
    written. ``kind`` is ``"cleanup"`` for a dictation's clean-up, whose long
    prompt is kept read between dictations.

    Thread-safe. Raises LocalLLMError; never falls back to a cloud service."""
    global _last_used, _primed
    if not is_available():
        raise LocalLLMError(NOT_INSTALLED, "unavailable")
    name = selected_model()
    if not _is_ready(name):
        raise LocalLLMError(NOT_DOWNLOADED, "not_downloaded")

    def late() -> bool:
        return deadline is not None and _now() >= deadline

    for _attempt in range(_MAX_RESTARTS):
        if late():
            raise LocalLLMError(TOO_SLOW, "deadline")
        why: list = []

        def should_stop() -> bool:
            if _exiting.is_set():
                why.append("exit")
                return True
            if late():
                why.append("deadline")
                return True
            if background and _gate.foreground_waiting():
                why.append("gave way")
                return True
            return False

        _gate.acquire(background=background)
        try:
            if _exiting.is_set():
                raise LocalLLMError(FAILED, "failed")
            if late():                      # the turn came too late
                raise LocalLLMError(TOO_SLOW, "deadline")
            _ensure_engine(name)
            t1 = time.perf_counter()
            try:
                reply = _engine.chat(system, messages, max_tokens, temperature,
                                     should_stop=should_stop, on_text=on_text)
            except LocalLLMError:
                raise
            except Exception as e:          # noqa: BLE001
                if type(e).__name__ == "ContextTooLong":
                    raise LocalLLMError(TOO_LONG, "too_long") from e
                raise LocalLLMError(FAILED, "failed") from e
            _primed = kind == "cleanup"
            _last.update(reply_s=round(time.perf_counter() - t1, 3), unloaded="")
        finally:
            _last_used = _now()
            _gate.release()
            _arm_idle_timer()
        if reply.get("finish_reason") == "stopped":
            if "exit" in why:
                raise LocalLLMError(FAILED, "failed")
            if "deadline" in why:
                raise LocalLLMError(TOO_SLOW, "deadline")
            continue                        # gave way to a dictation: write it again
        if kind != "cleanup":
            _prime_soon()
        return reply
    raise LocalLLMError(FAILED, "failed")
