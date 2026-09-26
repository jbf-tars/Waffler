"""Run the real dictation pipeline from app.py with fakes.

app.py imports pywebview, audio hardware and the provider SDKs at module
scope, so, as in tests/test_bookkeeping_never_fails_dictation.py, the
functions under test are lifted out of its source and run in a namespace
where every module-level name they use is either the real module (the
watchdog, the unsent rules, the history writers) or a fake (the page, the
tray, the log). The fakes stand in for everything with a side effect: no
network, no keys, no real clipboard, no overlay process, no private data.

Not a test module itself (no test_ prefix); imported by the tests.
"""
import ast
import io
import json
import math
import os
import struct
import sys
import threading
import time
import types
import wave
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import atomic_json  # noqa: E402
import cleanup_pause  # noqa: E402
import pipeline_watchdog as pw  # noqa: E402
import recent_audio  # noqa: E402
import tray_state  # noqa: E402
import unsent  # noqa: E402
import user_messages  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_PIPELINE = next(n for n in _TREE.body
                 if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")

PIPELINE_METHODS = (
    "_process", "on_hotkey_release", "_on_overlay_cancel", "_on_overlay_cancel_request",
    "_on_toast_action", "_show_no_audio_toast", "_apply_snippets",
    "_transcribe_with_provenance", "_unstyled", "_speech_provider_name", "_show_journal",
    "_run_is_current", "_set_listener_processing", "_ui_begin", "_ui_working",
    "_ui_offer", "_ui_withdraw_offer", "_ui_finished", "_ui_stuck",
    "_save_unsent_recording", "_handle_failed_transcription", "_count_unsent",
    "_replace_unsent_entry", "resend_unsent", "delete_unsent", "_drain_unsent",
    "_drain_unsent_soon", "_on_hotkey_cancel", "_fill_unsent_card", "_in_flight",
    "_collect_late_words", "_late_words_arrived", "_keep_late_recording",
)
MODULE_DEFS = ("ensure_data_dir", "load_history", "save_history", "append_history",
               "append_history_safely", "_MIN_TAP_SPEECH_S")


def _module_defs(ns):
    nodes = [n for n in _TREE.body
             if (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in MODULE_DEFS)
             or (isinstance(n, ast.FunctionDef) and n.name in MODULE_DEFS)]
    exec(compile(ast.Module(nodes, []), "<app.py>", "exec"), ns)


def _methods(ns):
    nodes = [n for n in _PIPELINE.body
             if isinstance(n, ast.FunctionDef) and n.name in PIPELINE_METHODS]
    found = {n.name for n in nodes}
    missing = set(PIPELINE_METHODS) - found
    assert not missing, f"not in WafflerPipeline: {sorted(missing)}"
    exec(compile(ast.Module(nodes, []), "<app.py WafflerPipeline>", "exec"), ns)


# ── audio ──────────────────────────────────────────────────────────────────

def speech_wav(seconds: float = 3.0, rate: int = 16000) -> bytes:
    """A mono 16-bit WAV of a loud warbling tone: enough "speech" for the
    pipeline's silence and tap checks."""
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        t = i / rate
        v = 6000 * math.sin(2 * math.pi * 220 * t) * (0.6 + 0.4 * math.sin(2 * math.pi * 3 * t))
        frames += struct.pack("<h", int(v))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


# ── fakes ──────────────────────────────────────────────────────────────────

class Hang:
    """A step that blocks until released (or for ``seconds``), like a
    provider that never answers."""

    def __init__(self, seconds: float = 30.0):
        self.seconds = seconds
        self.release = threading.Event()
        self.entered = threading.Event()

    def wait(self):
        self.entered.set()
        self.release.wait(self.seconds)


class FakeAudio:
    """``hang`` makes stop() wait like a recorder whose lock is held while a
    device is rebuilt."""

    def __init__(self, wav: bytes, hang=None):
        self.wav = wav
        self.hang = hang
        self.stops = 0

    def stop(self):
        self.stops += 1
        if self.hang is not None:
            self.hang.wait()
        return self.wav

    def force_rebuild(self):
        pass


class FakeTranscriber:
    """Groq-only by default, like the recommended setup."""

    def __init__(self, text="ok so ship it on monday", hang=None, error=None):
        self.text = text
        self.hang = hang
        self.error = error
        self.calls = 0
        self._backend = "groq"
        self._groq_client = object()
        self.client = None
        self._cloud_order = ["groq", "openai"]
        self.last_asr_response = ""
        self.last_asr_filtered = False
        self.last_speech_seconds = 0.0
        self.last_retry_fired = False
        self.last_retry_rejected = False
        self._last_cloud_provider = ""

    def transcribe_sync(self, audio_bytes):
        self.calls += 1
        if self.hang is not None:
            self.hang.wait()
        if self.error is not None:
            raise self.error
        self.last_asr_response = self.text
        self.last_speech_seconds = 3.0
        self._last_cloud_provider = "groq"
        return self.text


class FakeStyler:
    def __init__(self, hang=None, error=None, fallback_reason=None):
        self.hang = hang
        self.error = error
        self.fallback_reason = fallback_reason
        self.calls = 0

    def style(self, transcript):
        self.calls += 1
        if self.hang is not None:
            self.hang.wait()
        if self.error is not None:
            raise self.error
        if self.fallback_reason:
            # Every clean-up provider failed: the words as said, and why.
            return transcript, {"input_tokens": 0, "output_tokens": 0, "api_used": False,
                                "provider": "basic_clean", "fallback_reason": self.fallback_reason}
        styled = transcript[:1].upper() + transcript[1:] + "."
        return styled, {"input_tokens": 10, "output_tokens": 8, "api_used": True,
                        "provider": "groq"}

    def _basic_clean(self, text):
        return text.strip()

    def _format_email_layout(self, text):
        return text


class FakeClipboard:
    def __init__(self, paste_hang=None, copy_ok=True):
        self.copies = []
        self.pastes = []
        self.paste_hang = paste_hang
        self.copy_ok = copy_ok

    def copy(self, text):
        self.copies.append(text)
        return self.copy_ok

    def auto_paste(self, window=None):
        if self.paste_hang is not None:
            self.paste_hang.wait()
        self.pastes.append(self.copies[-1] if self.copies else None)

    def get_focused_window(self):
        return None


class FakeOverlay:
    """Records every call, like the overlay controller's public API."""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def record(*a, **k):
            with self.lock:
                self.calls.append((name, a, k))
            return True
        return record

    def names(self):
        with self.lock:
            return [c[0] for c in self.calls]

    def toasts(self):
        with self.lock:
            return [k for n, a, k in self.calls if n == "show_toast"]


class FakeListener:
    def __init__(self):
        self.processing = []

    def set_processing(self, active):
        self.processing.append(active)

    def reset_state(self):
        pass


class Page:
    """Everything the pipeline sends to the window."""

    def __init__(self):
        self.statuses = []
        self.items = []
        self.updates = []
        self.scripts = []
        self.pauses = []


# ── the pipeline ───────────────────────────────────────────────────────────

class Pipeline:
    """A WafflerPipeline made of the real methods and the fakes above."""


def make_pipeline(data_dir: Path, *, transcriber=None, styler=None, clipboard=None,
                  wav=None, tick_s=0.05):
    ns = {}
    page = Page()
    logged = []

    ns.update(
        json=json, os=os, time=time, threading=threading, datetime=datetime,
        _pw=pw, _unsent=unsent, _tray_state=tray_state,
        DATA_DIR=data_dir, HISTORY_FILE=data_dir / "history.json",
        _history_lock=threading.Lock(),
        write_json_atomic=lambda p, d: atomic_json.write_json_atomic(p, d, sleep=lambda s: None),
        _log_to_file=logged.append,
        notify_js_status=page.statuses.append,
        notify_js_new_item=page.items.append,
        notify_js_item_updated=lambda uid, item: page.updates.append((uid, item)),
        notify_js_window_visible=lambda v: None,
        _post_js=page.scripts.append,
        _set_tray_state=lambda s: None,
        _tray_state_now=tray_state.IDLE,
        record_usage_safely=lambda *a, **k: None,
        _transcripts_loggable=lambda: False,
        _window=None, _window_hidden=False,
        _platform=types.SimpleNamespace(system=lambda: "Windows"),
        cleanup_skipped_message=user_messages.cleanup_skipped_message,
        limit_reached_message=user_messages.limit_reached_message,
        _recent_audio=recent_audio, _cleanup_pause=cleanup_pause,
        _set_cleanup_pause=page.pauses.append,
    )
    from transcribe_whisper import _speech_seconds
    ns["_speech_seconds"] = _speech_seconds
    _module_defs(ns)
    _methods(ns)

    p = Pipeline()
    for name in PIPELINE_METHODS:
        setattr(p, name, types.MethodType(ns[name], p))
    p.ns, p.page, p.logged = ns, page, logged
    p.audio = FakeAudio(wav if wav is not None else speech_wav(3.0))
    p.transcriber = transcriber or FakeTranscriber()
    p.styler = styler or FakeStyler()
    p.clipboard = clipboard or FakeClipboard()
    p.overlay = FakeOverlay()
    p.hotkey_listener = FakeListener()
    p.is_recording = False
    p._is_paused = False
    p._processing_lock = threading.Lock()
    p._processing_cancelled = threading.Event()
    p._processing_id = 1
    p._current_press_id = 1
    p._recording_start_time = time.time() - 3.0
    p._recording_session = 1
    p._prev_window = None
    p._mic_dropout = None
    p._unsent_lock = threading.Lock()
    p._drain_lock = threading.Lock()
    p._unsent_waiting = 0
    p._unsent_in_flight = {}
    p._in_flight_lock = threading.Lock()
    p._watchdog = pw.PipelineWatchdog(
        on_begin=p._ui_begin, on_working=p._ui_working, on_offer=p._ui_offer,
        on_withdraw_offer=p._ui_withdraw_offer, on_finished=p._ui_finished,
        on_stuck=p._ui_stuck, is_current=p._run_is_current, log=logged.append,
        tick_s=tick_s,
    )
    return p


def run_process(p, generation=1):
    """Start _process on its own thread, as on_hotkey_release does."""
    t = threading.Thread(target=p._process, args=(generation,), daemon=True)
    t.start()
    return t


def wait_for(predicate, timeout=5.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return bool(predicate())


def history(p):
    f = p.ns["HISTORY_FILE"]
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def fast_limits(monkeypatch, **over):
    """Shrink the watchdog's limits so a "30 s hang" test takes a second."""
    limits = dict(OFFER_MIN_S=0.4, OFFER_MIN_STAGE_AGE_S=0.1,
                  TRANSCRIBE_DEADLINE_MIN_S=1.5, TRANSCRIBE_DEADLINE_BASE_S=1.5,
                  TRANSCRIBE_DEADLINE_PER_AUDIO_S=0.0, TRANSCRIBE_DEADLINE_MAX_S=1.5,
                  STYLE_DEADLINE_S=1.5, PASTE_DEADLINE_S=0.6, PREPARE_DEADLINE_S=2.0,
                  SAVE_LIMIT_S=5.0, STUCK_GRACE_S=0.5)
    limits.update(over)
    for k, v in limits.items():
        monkeypatch.setattr(pw, k, v)
