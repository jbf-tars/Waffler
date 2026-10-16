#!/usr/bin/env python3
"""
Waffler — macOS Desktop UI
Entry point: pywebview window + background hotkey/pipeline thread
"""

import sys
import os

# ── Thread caps, before anything can import NumPy ─────────────────────
# NumPy's OpenBLAS starts an idle worker thread per CPU core the moment it is
# imported, and reserves memory for each. Waffler only uses NumPy for RMS
# sums on short audio buffers, which never touch BLAS. Measured on a 28-thread
# PC: "import numpy" committed 754 MB across 27 threads, against 15 MB and 4
# threads with these set. Both the main process and the overlay paid it. The
# frozen builds also set them in hooks/rthook_thread_caps.py, which runs
# before this file; setdefault keeps any value a user set on purpose.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
             "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "1")

import io
import json
import queue
import time
import threading
import atexit
import copy
import contextlib
import pyperclip
import faulthandler
from pathlib import Path
from datetime import datetime, date

# Enable faulthandler to catch segfaults and write tracebacks to a file
try:
    # Same folder as src/data_paths.data_dir(), which cannot be imported yet
    # because src/ is not on sys.path at this point.
    _crash_log = open(Path(os.environ.get("WAFFLER_DATA_DIR", "").strip()
                           or Path.home() / ".waffler-hosted") / "crash.log", "a",
                      encoding="utf-8")
    faulthandler.enable(file=_crash_log)

    def close_crash_log():
        """Close crash log file on shutdown"""
        try:
            _crash_log.close()
        except:
            pass

    atexit.register(close_crash_log)
except Exception:
    faulthandler.enable()

# ── Safe stdout/stderr (Windows cp1252 can't handle emoji — force UTF-8) ──
def _fix_stream(stream):
    """Return a UTF-8 text stream, or a silent fallback."""
    if stream is None or not hasattr(stream, 'write'):
        return io.StringIO()
    try:
        # Python 3.7+ — cleanest: reconfigure existing stream in-place
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
            return stream
    except Exception:
        pass
    try:
        # Wrap the underlying binary buffer with UTF-8
        if hasattr(stream, 'buffer'):
            return io.TextIOWrapper(stream.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass
    return io.StringIO()

sys.stdout = _fix_stream(sys.stdout)
sys.stderr = _fix_stream(sys.stderr)

# ── Path setup ─────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


# ── Overlay Mode Handler ──────────────────────────────────────────────
# When launched with --overlay flag, run the overlay subprocess instead
# of the main app. This allows PyInstaller to freeze both entry points.
# It runs BEFORE the main app's imports on purpose: the overlay needs only
# its UI toolkit (Tk on Windows, AppKit on a Mac), but it used to import
# pywebview, NumPy, sounddevice and the OpenAI and Groq SDKs first, which
# cost it about a second of start-up and the same memory as the main process.
if '--overlay' in sys.argv:
    import platform as _plat
    if _plat.system() == "Windows":
        import overlay_process_windows
        overlay_process_windows.main()
    else:
        import overlay_process
        overlay_process.main()
    sys.exit(0)

import webview

from config import Config
from audio import AudioRecorder
import platform as _platform
if _platform.system() == "Windows":
    from windows_hotkey import WindowsHotkeyListener
else:
    from smart_hotkey import SmartHotkeyListener
from transcribe_whisper import WhisperTranscriber, _speech_seconds
from style_openai import OpenAIStyler
from clipboard import ClipboardManager
import pasteboard as _pasteboard
import text_context as _text_context
from overlay import RecordingOverlay
from permissions_manager import PermissionsManager
from audio_devices import (
    list_input_devices,
    get_selected_device_index,
    set_selected_device_index,
    get_selected_device_name,
)
from app_detection import get_active_app
from log_util import transcript_for_log
from atomic_json import write_json_atomic, read_json_for_update
import pipeline_watchdog as _pw
import unsent as _unsent
import tray_state as _tray_state
import first_run as _first_run
import journal_data as _journal
import recent_audio as _recent_audio
import privacy_data as _privacy
import cleanup_pause as _cleanup_pause
import languages as _languages
import mac_permissions as _mac_perms
import snippets as _snippets
import style_layer as _style_layer
import notifier as _notifier
import sounds as _sound_cues
import hold_hint as _hold_hint
import usage_log as _usage_log
import history_ids as _history_ids
import history_meta as _history_meta
import history_ops as _history_ops
import history_export as _history_export
import take_marker as _take_marker
import action_hotkeys as _action_hotkeys
import transforms as _transforms
import transform_action as _transform_action
import command_mode as _command_mode
import command_action as _command_action
from login_item import LoginItem, HIDDEN_FLAG as _HIDDEN_FLAG
from user_messages import (
    CLIPBOARD_CHOICE_UNKNOWN,
    DOWNLOAD_PAGE,
    SOUNDS_UNKNOWN,
    TIPS_UNKNOWN,
    UPDATE_DOWNLOAD_FAILED,
    UPDATE_INSTALL_FAILED,
    UPDATE_NO_INSTALLER,
    UPDATE_WAITING,
    classify_request_error,
    cleanup_skipped_message,
    hold_hint_message,
    key_check_error,
    last_transcript_title,
    limit_reached_message,
    paste_timed_out_message,
    update_check_error,
)


# ── Data Directory ────────────────────────────────────────────────────
def get_data_directory():
    """Get the data directory for Waffler (~/.waffler-hosted/, or
    $WAFFLER_DATA_DIR when set; see src/data_paths.py)."""
    from data_paths import data_dir as _resolve_data_dir
    data_dir = _resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# ── History File ──────────────────────────────────────────────────────
DATA_DIR = get_data_directory()
HISTORY_FILE = DATA_DIR / "history.json"
USAGE_FILE = DATA_DIR / "usage.json"          # the older record; moved into the next one
USAGE_LOG_FILE = DATA_DIR / _usage_log.FILE   # usage.jsonl: one row per line

# Serialises the load→append→save read-modify-write on history.json. The file
# write itself is atomic (os.replace), but the read-modify-write around it is
# not — two threads (a processing thread + clear_history from the JS bridge,
# or two overlapping recordings) could otherwise interleave and lose entries.
_history_lock = threading.Lock()
# The same for usage.json: a dictation and a Try again (or a superseded
# dictation still finishing) can record usage at the same moment.
_usage_lock = threading.Lock()
# And for settings.json, which bridge calls on separate threads read,
# change and save (see Api._editing_settings).
_settings_lock = threading.RLock()
# And for snippets.json: the page saves the whole list on every change.
_snippets_lock = threading.Lock()

# ── Pricing ────────────────────────────────────────────────────────────────
# Rates are keyed by the MODEL actually called, not merely by provider. The old
# constants had drifted from what the app runs, in both directions, so the
# Usage panel was confidently wrong:
#
#   * Groq cleanup priced as Llama 3.3 70B ($0.59/$0.79) long after the app
#     moved to openai/gpt-oss-120b ($0.15/$0.60): overstated about 4x.
#   * Groq transcription used $0.168/hour against a published $0.111/hour.
#   * OpenAI cleanup used gpt-4o-mini rates while the app calls gpt-4.1-mini  # doc-drift-ok (the superseded rate)
#     ($0.40/$1.60): understated about 2.7x.
#   * OpenAI transcription used whisper-1's $0.006/min while the app calls
#     gpt-4o-mini-transcribe at $0.003/min: overstated 2x.
#   * Cerebras had no branch at all and was billed at OpenAI's rates.
#
# Each entry records where the figure came from and when it was checked, so a
# stale rate is distinguishable from a current one. `verified` is False where
# the provider publishes no per-token rate; the UI shows those as estimates
# rather than implying precision we do not have.
_RATES_CHECKED = "2026-09-09"

MODEL_RATES = {
    "groq": {
        "gpt":     {"model": "openai/gpt-oss-120b", "in_per_1m": 0.15,
                    "out_per_1m": 0.60, "verified": True,
                    "source": "console.groq.com/docs/models"},
        # Groq bills every transcription request as at least 10 seconds of
        # audio: "Minimum Billed Length: 10 seconds. If you submit a request
        # less than this, you will still be billed for 10 seconds."
        # (console.groq.com/docs/speech-to-text, checked 2026-09-25). A 3 s
        # dictation is therefore billed as 10 s. _usage_cost applies this to
        # the cost only; the stored duration_seconds stays the real length.
        "whisper": {"model": "whisper-large-v3", "per_hour": 0.111,
                    "min_billed_seconds": 10.0,
                    "min_billed_source": "console.groq.com/docs/speech-to-text",
                    "verified": True, "source": "console.groq.com/docs/models"},
    },
    "openai": {
        "gpt":     {"model": "gpt-4.1-mini", "in_per_1m": 0.40,
                    "out_per_1m": 1.60, "verified": True,
                    "source": "developers.openai.com/api/docs/pricing"},
        # No minimum here on purpose: OpenAI's pricing page lists
        # gpt-4o-mini-transcribe at an estimated $0.003/min and documents no
        # minimum billed length per request (checked 2026-09-25).
        "whisper": {"model": "gpt-4o-mini-transcribe", "per_minute": 0.003,
                    "verified": True,
                    "source": "developers.openai.com/api/docs/pricing"},
    },
    "cerebras": {
        # Cerebras publishes no per-token rate on cerebras.ai/pricing or its
        # inference docs (both checked 2026-09-09; the docs URL redirects to
        # the pricing page, which lists only tier prices). Rather than invent a
        # figure this mirrors the same model's published Groq rate and is
        # flagged unverified so the UI can label it an estimate.
        "gpt":     {"model": "gpt-oss-120b", "in_per_1m": 0.15,
                    "out_per_1m": 0.60, "verified": False,
                    "source": "estimated from the same model on Groq; "
                              "Cerebras does not publish per-token pricing"},
    },
}


def _rate_for(provider: str, kind: str) -> dict:
    """Rate spec for a provider/kind, falling back to OpenAI's published rate."""
    return (MODEL_RATES.get(provider, {}).get(kind)
            or MODEL_RATES["openai"].get(kind, {}))


def _usage_cost(rate: dict, entry_type: str, duration_seconds: float = None,
                input_tokens: int = 0, output_tokens: int = 0) -> float:
    """Dollar cost of one API call under ``rate`` (a MODEL_RATES entry).

    Pure arithmetic, separate from record_usage so the tests and
    scripts/recost_usage.py price an entry exactly the way the app does.
    """
    if entry_type == "whisper":
        if not duration_seconds or duration_seconds <= 0:
            return 0.0
        # A provider's per-request minimum (Groq: 10 s) is what it bills, so
        # it is what the cost uses, even though the clip was shorter.
        billed = max(float(duration_seconds),
                     float(rate.get("min_billed_seconds", 0.0)))
        if "per_hour" in rate:
            return billed / 3600.0 * rate["per_hour"]
        return billed / 60.0 * rate.get("per_minute", 0.0)
    if entry_type == "gpt":
        return ((input_tokens / 1_000_000) * rate.get("in_per_1m", 0.0)
                + (output_tokens / 1_000_000) * rate.get("out_per_1m", 0.0))
    return 0.0


def _entry_rate_is_estimate(entry: dict) -> bool:
    """True when a usage entry's cost rests on an unpublished (estimated) rate.

    Entries written since 3.14.95 carry ``rate_verified``. Older ones do not,
    so for those the current rate table decides: every Cerebras row is an
    estimate, because Cerebras publishes no per-token price.
    """
    if "rate_verified" in entry:
        return not entry["rate_verified"]
    provider = (entry.get("provider") or "openai").lower()
    return not _rate_for(provider, entry.get("type") or "gpt").get("verified", False)


def ensure_data_dir():
    """Ensure the data directory exists"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_history() -> list:
    """history.json for reading (the Journal, counts, exports). [] when it
    is missing or cannot be read. Never use this before a save_history: use
    _load_history_for_update, which will not let a failed read empty it."""
    ensure_data_dir()
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _load_history_for_update() -> list:
    """history.json before it is rewritten. Call with _history_lock held.

    A file that cannot be parsed is kept as history.unreadable-<time>.json
    instead of being replaced by one new entry. One that cannot be opened
    (locked by antivirus, backup or sync software) raises, so nothing is
    written; on the dictation path append_history_safely then returns False
    and the words are still pasted."""
    ensure_data_dir()
    history, kept = read_json_for_update(HISTORY_FILE, list)
    if kept is not None:
        _log_to_file(f"[history] history.json could not be read; kept as {kept.name}")
    return history


def save_history(history: list):
    """Save transcription history with an atomic write. The replace is
    retried while another handle briefly locks the file (Windows)."""
    ensure_data_dir()
    write_json_atomic(HISTORY_FILE, history)


# The parsed history and its counts, kept until history.json changes
# (src/journal_data.py). The window asks for pages and counts often; it
# used to read and rescan the whole file every time.
_history_cache = _journal.HistoryCache(HISTORY_FILE, load_history)

# Vocabulary, Try a sentence: the longest text it takes.
TRY_VOCAB_MAX_CHARS = 2000


# History retention (Settings, Privacy and data; src/privacy_data.py). Keep
# everything unless the user chose 30, 90 or 365 days. Applied at start-up,
# when the choice changes, and once a day on the next dictation.
_history_retention_day = None


def _history_keep_days() -> int:
    """The chosen retention from settings.json; 0 (keep all) if unreadable."""
    try:
        sf = DATA_DIR / "settings.json"
        stored = json.loads(sf.read_text(encoding="utf-8-sig")) if sf.exists() else {}
    except Exception:
        return 0
    return _privacy.history_keep_days(stored if isinstance(stored, dict) else {})


def _retain_history(history: list, force: bool = False) -> list:
    """History without the dictations older than the chosen retention.
    Runs once a day unless forced. Call with _history_lock held."""
    global _history_retention_day
    today = datetime.now().date()
    if not force and _history_retention_day == today:
        return history
    _history_retention_day = today
    days = _history_keep_days()
    kept, removed = _privacy.prune_history(history, days)
    if removed:
        _log_to_file(f"[history] {removed} dictation(s) older than {days} days removed "
                     f"(Settings, Privacy and data)")
    return kept


def append_history(item: dict):
    """Atomically append one entry to history.json. Use this instead of a bare
    load→append→save so concurrent writers don't clobber each other."""
    with _history_lock:
        history = _retain_history(_load_history_for_update())
        # Every new entry gets an id (src/history_ids.py). The caller's own
        # dict gets it, so the window is told about the entry with its id.
        _history_ids.stamp(item, history)
        history.append(item)
        save_history(history)


# ── Usage Tracking ──────────────────────────────────────────────────────
def load_usage() -> list:
    """Every usage row, oldest first (src/usage_log.py). A usage.json from
    from an older Waffler is moved into usage.jsonl first, once."""
    ensure_data_dir()
    try:
        if USAGE_FILE.exists():
            with _usage_lock:
                _usage_log.try_import_legacy(USAGE_FILE, USAGE_LOG_FILE, _log_to_file)
        return _usage_log.read(USAGE_LOG_FILE)
    except Exception:
        return []


def save_usage(usage: list):
    """Replace the whole usage record (Settings, reset). The write is atomic
    and the replace is retried while another handle briefly locks the file
    (Windows). Not for the dictation path: record_usage adds one row."""
    ensure_data_dir()
    with _usage_lock:
        # An old usage.json goes first, or the next read would bring it back.
        _usage_log.try_import_legacy(USAGE_FILE, USAGE_LOG_FILE, _log_to_file)
        _usage_log.write_all(USAGE_LOG_FILE, usage)


def _move_old_usage():
    """Start-up: move an older usage.json into usage.jsonl (src/usage_log.py)
    on a thread of its own, so that no dictation pays for it. Safe to stop at
    any point and to repeat; logs counts only."""
    try:
        if USAGE_FILE.exists():
            with _usage_lock:
                _usage_log.try_import_legacy(USAGE_FILE, USAGE_LOG_FILE, _log_to_file)
    except Exception as e:
        _log_to_file(f"[usage] move at start-up failed: {type(e).__name__}")


def _recover_interrupted_takes():
    """Start-up: a take that was being turned into text when Waffler last
    stopped (a crash, a power cut, a force quit) comes back as a Not sent card
    with Try again, if its recording was kept (src/take_marker.py). The usual
    case, no mark, costs one file check. Never raises; logs counts only."""
    try:
        if not (DATA_DIR / _take_marker.FILE).exists():
            return
        found = _take_marker.recover(DATA_DIR, load_history(), _log_to_file)
        saved = True
        for entry in found:
            saved = append_history_safely(entry) and saved
        if saved:
            # What the mark held is dealt with. If a card could not be saved
            # the mark stays, and the next start tries again (a take already
            # in the Journal is not added twice).
            _take_marker.forget_all(DATA_DIR)
        _log_to_file(f"[marker] {len(found)} interrupted take(s) came back as Not sent cards")
    except Exception as e:
        _log_to_file(f"[marker] recovery skipped: {type(e).__name__}")


def record_usage(entry_type: str, duration_seconds: float = None,
                 input_tokens: int = 0, output_tokens: int = 0,
                 provider: str = "openai", purpose: str = None):
    """Record an API usage entry with cost calculation. ``purpose`` names a
    feature other than dictation ("transform", "command"): its rows are priced
    like any other but are not a dictation (see get_usage_stats)."""
    rate = _rate_for(provider, entry_type)
    cost_usd = _usage_cost(rate, entry_type, duration_seconds,
                           input_tokens, output_tokens)

    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "type": entry_type,
        "provider": provider,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 6),
        # Which model this was billed as, and whether the rate is published.
        # Historic rows lack these, which is why old Cerebras entries cannot
        # be recomputed with confidence.
        "model": rate.get("model", ""),
        "rate_verified": bool(rate.get("verified", False)),
    }
    if duration_seconds is not None:
        entry["duration_seconds"] = round(duration_seconds, 3)
    if purpose:
        entry["purpose"] = str(purpose)

    # One line added to the end of usage.jsonl: the cost does not grow with
    # the record (it used to rewrite the whole file, twice per dictation,
    # before the paste). An old usage.json is moved in first, once.
    with _usage_lock:
        ensure_data_dir()
        _usage_log.try_import_legacy(USAGE_FILE, USAGE_LOG_FILE, _log_to_file)
        _usage_log.append(USAGE_LOG_FILE, entry)
    return entry


# ── Bookkeeping on the dictation path ─────────────────────────────────────
# Usage and history are records ABOUT a dictation. Neither may fail one.
# A usage.json write that raised used to fall into _process's generic
# handler: no paste, a "Something went wrong" toast, and the words only on
# the clipboard. A history.json failure after the paste then overwrote the
# clipboard with the raw transcript. "Access is denied" did this to 6 real
# dictations.

def record_usage_safely(*args, **kwargs):
    """record_usage for the dictation path: logs a failure, never raises."""
    try:
        return record_usage(*args, **kwargs)
    except Exception as e:
        _log_to_file(f"[usage] not recorded ({type(e).__name__}: {e})")
        return None


def _words_kept_message(on_clipboard: bool, in_journal: bool, pasted: bool = False) -> str:
    """The error toast's sentence, from where the words really are. After a
    paste that went in, the clipboard is the user's own again (it is put
    back), so the words are in their app and the Journal."""
    if pasted:
        return ("Your text was pasted and is in the Journal." if in_journal
                else "Your text was pasted.")
    if on_clipboard and in_journal:
        return "Your words are on the clipboard and in the Journal."
    if on_clipboard:
        return "Your words are on the clipboard."
    if in_journal:
        return "Your words are in the Journal."
    return "That dictation didn't go through. Please try again."


def _update_would_interrupt(pipeline) -> str:
    """What a restart to update would cut off, or "" when nothing would:
    a recording, a dictation still being processed, or a Not sent
    recording being sent."""
    if pipeline is None:
        return ""
    try:
        if getattr(pipeline, "is_recording", False):
            return "recording"
        watchdog = getattr(pipeline, "_watchdog", None)
        if watchdog is not None and watchdog.active():
            return "a dictation is being processed"
        rewrite = getattr(pipeline, "_transforms", None)
        if rewrite is not None and rewrite.busy():
            return "a transform is running"
        spoken = getattr(pipeline, "_commands", None)
        if spoken is not None and spoken.busy():
            return "a command is running"
        for name in ("_drain_lock", "_unsent_lock"):
            lock = getattr(pipeline, name, None)
            if lock is not None and lock.locked():
                return "a recording is being sent"
    except Exception:
        return ""
    return ""


def append_history_safely(item: dict) -> bool:
    """append_history for the dictation path: True when saved. Logs a
    failure and returns False instead of raising."""
    try:
        append_history(item)
        return True
    except Exception as e:
        _log_to_file(f"[history] not saved ({type(e).__name__}: {e})")
        return False


# ── PyWebView API ─────────────────────────────────────────────────────
class Api:
    """Exposed to JS via pywebview.api.*"""

    def get_app_version(self) -> str:
        """Return the app version string."""
        from src import __version__
        return __version__

    def check_for_updates(self) -> dict:
        """Check GitHub releases for a newer version.

        Fetches the full releases list (not /releases/latest, which returns
        404 on this repo because the 'latest' flag has never been set on any
        release). Picks the highest-semver non-draft, non-prerelease tag.
        Always returns current_version; returns an error field on failure.
        """
        from src import __version__
        current_version = __version__

        # Surface a silently-failed update through the call the UI already
        # makes on startup, rather than adding a new bridge method. Set by the
        # startup reconciliation; read once so the warning does not persist
        # after the user has seen it.
        _failed = globals().pop("_UPDATE_FAILURE_NOTICE", None)

        def parse_ver(v: str):
            # Extract the leading numeric dotted version, tolerating tag
            # suffixes like "v3.14.63-hotfix" or "3.14.63b". The old
            # int()-on-every-part threw on any such tag → coerced to (0,) →
            # wrong "latest" pick / spurious infinite "update available".
            import re as _re
            m = _re.match(r"v?(\d+(?:\.\d+)*)", v or "")
            if not m:
                return (0,)
            try:
                return tuple(int(x) for x in m.group(1).split("."))
            except Exception:
                return (0,)

        try:
            import requests
            r = requests.get(
                "https://api.github.com/repos/jbf-tars/waffler/releases",
                timeout=10,
                headers={"Accept": "application/vnd.github.v3+json"},
                params={"per_page": 20},
            )
            if r.status_code != 200:
                # 403 is GitHub's rate limit for unauthenticated checks. The
                # status goes to the log; the user gets a sentence.
                _log_to_file(f"[update] check failed: GitHub returned HTTP {r.status_code}")
                return {
                    "update_available": False,
                    "current_version": current_version,
                    "error": update_check_error(),
                }
            releases = r.json()
            if not isinstance(releases, list) or not releases:
                _log_to_file("[update] check failed: no releases in the GitHub response")
                return {
                    "update_available": False,
                    "current_version": current_version,
                    "error": update_check_error(),
                }

            # Filter out drafts and prereleases, pick highest semver
            candidates = [
                rel for rel in releases
                if not rel.get("draft") and not rel.get("prerelease") and rel.get("tag_name")
            ]
            if not candidates:
                _log_to_file("[update] check failed: no published releases")
                return {
                    "update_available": False,
                    "current_version": current_version,
                    "error": update_check_error(),
                }

            latest_release = max(candidates, key=lambda rel: parse_ver(rel["tag_name"]))
            latest_version = latest_release["tag_name"].lstrip("v")

            if parse_ver(latest_version) > parse_ver(current_version):
                import platform as _plat
                suffix = ".dmg" if _plat.system() == "Darwin" else ".exe"
                # Only a real platform asset is a valid download target —
                # never fall back to html_url (the release *web page*), which
                # would download HTML and then try to "install" it. If no
                # asset matches, download_url stays "" and JS falls back to
                # opening release_url in the browser.
                download_url = ""
                for asset in latest_release.get("assets", []):
                    if asset.get("name", "").endswith(suffix):
                        download_url = asset.get("browser_download_url", "")
                        break
                return {
                    "update_available": True,
                    "latest_version": latest_version,
                    "current_version": current_version,
                    "download_url": download_url,
                    "release_url": latest_release.get("html_url", ""),
                    # No installer for this platform in the release: the UI
                    # opens the release page instead of trying to download.
                    "no_installer": not download_url,
                    "no_installer_message": "" if download_url else UPDATE_NO_INSTALLER,
                }
            return {
                "update_available": False,
                "current_version": current_version,
                "latest_version": latest_version,
                "last_update_failed": _failed,
            }
        except Exception as e:
            _log_to_file(f"[update] check failed: {e}")
            return {
                "update_available": False,
                "current_version": current_version,
                "error": update_check_error(e),
            }

    def start_update_download(self, url: str) -> dict:
        """Begin downloading the update installer in the background.
        JS polls get_update_progress() to render a progress bar.

        This method is reachable from the webview JS bridge, so the URL is
        validated against a GitHub-release allowlist — otherwise a crafted
        call (or a tampered check_for_updates response) could make us download
        an arbitrary file. We accept only https GitHub release-asset URLs; the
        downloaded file is additionally signature-verified before it is ever
        executed (see updater.install_and_restart)."""
        from urllib.parse import urlparse
        if not (url or "").strip():
            # The release had no installer for this computer (check_for_updates
            # sets no_installer). Not an attack, so don't say "untrusted".
            _log_to_file("[update] download requested with no installer URL")
            return {"ok": False, "error": UPDATE_NO_INSTALLER, "download_page": DOWNLOAD_PAGE}
        p = urlparse(url or "")
        host = (p.hostname or "").lower()
        host_ok = host == "github.com" or host.endswith(".githubusercontent.com")
        path_ok = host != "github.com" or "/releases/download/" in p.path
        if p.scheme != "https" or not host_ok or not path_ok:
            _log_to_file(f"[update] refused untrusted download URL: {url[:120]}")
            return {"ok": False, "error": UPDATE_DOWNLOAD_FAILED, "download_page": DOWNLOAD_PAGE}
        try:
            from src import updater
            _log_to_file(f"[update] start_download requested: {url[:120]}")
            updater.start_download(url)
            return {"ok": True}
        except Exception as e:
            _log_to_file(f"[update] start_download failed: {e}")
            return {"ok": False, "error": UPDATE_DOWNLOAD_FAILED, "download_page": DOWNLOAD_PAGE}

    def get_update_progress(self) -> dict:
        """Poll current download state. Returns active / bytes / total / done / path / error."""
        from src import updater
        return updater.get_progress()

    def install_update_and_restart(self, installer_path: str) -> dict:
        """Launch the installer detached and exit the app so the upgrade can
        replace files.

        Only the file WE downloaded is accepted — the path must match the one
        updater recorded in get_progress(), so a bridge call can't point the
        installer at an arbitrary attacker-chosen path. (The installer is also
        code-signature-verified before it runs, in updater.install_and_restart.)"""
        try:
            from src import updater
            recorded = (updater.get_progress() or {}).get("path") or ""
            if not recorded or os.path.abspath(installer_path) != os.path.abspath(recorded):
                _log_to_file("[update] refused install of unrecognised path")
                return {"ok": False, "error": UPDATE_INSTALL_FAILED, "download_page": DOWNLOAD_PAGE}
            # Restarting exits at once (and the Windows helper force-closes
            # Waffler), so a dictation being recorded or cleaned up, or a
            # Not sent recording being sent, would be lost. Wait for it.
            busy = _update_would_interrupt(_pipeline)
            if busy:
                _log_to_file(f"[update] install waits: {busy}")
                return {"ok": False, "busy": True, "error": UPDATE_WAITING}
            updater.install_and_restart(installer_path)
            return {"ok": True}  # usually unreachable — process exits
        except Exception as e:
            _log_to_file(f"[update] install failed: {e}")
            return {"ok": False, "error": UPDATE_INSTALL_FAILED, "download_page": DOWNLOAD_PAGE}

    def get_history(self, limit=None, offset=0, query="", before_id=None) -> list:
        """Journal entries, newest first: all of them, or one page.

        ``limit`` and ``offset`` page through them (the window draws about
        50 at a time and asks for more as you scroll); ``query`` keeps only
        entries whose clean text or transcript contains it. ``before_id``
        pages from the entry with that id instead of by offset: only
        entries older than it, which stays right when entries are added or
        deleted between two pages.

        Not sent entries get their live state: the recording's id, whether
        its file is still there, and whether Waffler will still send it by
        itself (entries older than a day, or out of tries, will not). An
        entry whose recording is still among the kept ones gets
        ``can_redo`` (for "Redo this take")."""
        items = _journal.page(_history_cache.items(), limit, offset, query,
                              _history_ids.parse(before_id))
        unsent_dir = DATA_DIR / _unsent.UNSENT_DIRNAME
        kept_audio = None
        out = []
        for item in items:
            if isinstance(item, dict) and item.get("failed"):
                item = dict(item)
                uid = _unsent.entry_id(item)
                if uid and _unsent.resolve_file(unsent_dir, uid) is None:
                    uid = ""        # the file has gone; the card can only be deleted
                item["unsent_id"] = uid
                item["will_retry"] = bool(uid) and _unsent.will_auto_retry(item)
            elif isinstance(item, dict) and item.get("audio"):
                if kept_audio is None:
                    kept_audio = _recent_audio.names(DATA_DIR)
                if item["audio"] in kept_audio:
                    item = dict(item)
                    item["can_redo"] = True
            out.append(item)
        return out

    def copy_item(self, text: str):
        """Copy text to clipboard."""
        try:
            pyperclip.copy(text)
            print(f"[clipboard] Copied {len(text)} chars")
            return True
        except Exception as e:
            print(f"[clipboard] Error: {e}")
            import traceback
            traceback.print_exc()
            return False

    def paste_last(self) -> dict:
        """Paste the last transcript into the app in front, the same safe
        paste as a dictation. {"ok", "outcome"} or {"ok": False, "error"}.
        Behind the menu bar item; a shortcut for it comes with the shortcut
        editor."""
        if _pipeline is None:
            return {"ok": False, "error": "not_ready"}
        return _pipeline.paste_last()

    def copy_last(self) -> dict:
        """Put the last transcript on the clipboard."""
        if _pipeline is None:
            return {"ok": False, "error": "not_ready"}
        return _pipeline.copy_last()

    def get_stats(self) -> dict:
        """The Journal's counts and the day streak (src/journal_data.py):
        today, this week, this month and all time, in words and dictations,
        and ``entries`` (every entry, Not sent ones included).

        Worked out once per change to history.json, not on every call.
        Streak: consecutive days, ending today, with at least one entry; if
        today has none yet, yesterday anchors it, so a streak doesn't snap
        to 0 at midnight before the first dictation of the day."""
        return _history_cache.stats(date.today())

    # ── Mode / Prompt API ─────────────────────────────────────────────

    def get_modes(self) -> list:
        """Return available prompt modes with display names.

        Only "Normal" is currently active. Email + Bullets modes are
        planned but disabled in the UI until the prompt-tuning work is
        finished — see settings dropdown which shows them as
        "coming soon" non-clickable options.
        """
        return [
            {"id": "normal", "name": "Normal", "desc": "Keeps everything, cleans grammar, handles emails, lists, and corrections"},
        ]

    def get_current_mode(self) -> str:
        """Return the currently active prompt mode id, falling back to
        'normal' if the persisted choice is no longer in the active
        set (e.g. an old install had 'email' selected before email mode
        was rolled back to 'coming soon' in v3.14.5)."""
        valid = {m["id"] for m in self.get_modes()}
        current = (_pipeline.styler.prompt_style if _pipeline
                   else (_config.prompt_style if _config else "normal"))
        return current if current in valid else "normal"

    def set_mode(self, mode_id: str) -> dict:
        """Switch to a different prompt mode and persist the choice so it
        survives an app restart."""
        valid = {m["id"] for m in self.get_modes()}
        if mode_id not in valid:
            return {"ok": False, "error": f"Unknown mode: {mode_id}"}
        try:
            if _pipeline:
                _pipeline.styler.prompt_style = mode_id
                _pipeline.styler.prompt_template = _pipeline.styler._load_prompt_template()
            # Persist the choice — without this, the setting reverts to
            # whatever config.prompt_style is on next launch.
            try:
                with self._editing_settings() as stored:
                    stored["prompt_style"] = mode_id
            except Exception as e:
                _log_to_file(f"set_mode: persist failed (in-memory change still applied): {e}")
            return {"ok": True, "mode": mode_id}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_active_app(self) -> dict:
        """Return the currently active app and suggested prompt style."""
        try:
            return get_active_app()
        except Exception as e:
            return {"name": "Unknown", "suggested_style": "normal", "error": str(e)}

    # ── Audio Device API ──────────────────────────────────────────────

    def get_audio_devices(self) -> list:
        """Return available audio input devices for the UI selector."""
        return list_input_devices()

    def get_selected_device(self) -> dict:
        """Return {index, name} of the currently selected device."""
        idx  = get_selected_device_index()
        name = get_selected_device_name()
        return {"index": idx, "name": name}

    def get_fn_key_state(self) -> dict:
        """Return current hotkey press state (Fn on Mac, Win+Ctrl on Windows)."""
        global _wizard_step2_monitor
        try:
            # Check wizard Step 2 monitor first (used during setup)
            if _wizard_step2_monitor:
                # On Windows, the wizard step-2 monitor IS a
                # WindowsHotkeyListener which exposes is_combo_active
                # directly as a property — there is no inner ._monitor.
                # Previous code only looked for ._monitor._fn_pressed and
                # always returned False on Windows, so the wizard's
                # "press your hotkey" step could never auto-advance.
                if hasattr(_wizard_step2_monitor, 'is_combo_active'):
                    is_pressed = bool(_wizard_step2_monitor.is_combo_active)
                    return {"ok": True, "pressed": is_pressed}
                # macOS: SmartHotkeyListener wraps an inner monitor.
                monitor = getattr(_wizard_step2_monitor, '_monitor', None)
                if monitor:
                    is_pressed = getattr(monitor, '_fn_pressed', None)
                    if is_pressed is None:
                        is_pressed = getattr(monitor, '_hotkey_active', False)
                    _log_to_file(f"[get_fn_key_state] wizard monitor: pressed={is_pressed}")
                    return {"ok": True, "pressed": bool(is_pressed)}
                else:
                    _log_to_file("[get_fn_key_state] wizard monitor exists but _monitor is None")
                    return {"ok": True, "pressed": False}

            # Check main hotkey listener (used during normal operation)
            if hasattr(self, 'hotkey_listener') and self.hotkey_listener:
                if _platform.system() == "Windows":
                    is_pressed = getattr(self.hotkey_listener, 'is_combo_active', False)
                else:
                    monitor = getattr(self.hotkey_listener, '_monitor', None)
                    if monitor:
                        # FnKeyMonitor uses _fn_pressed, MacHotkeyMonitor uses _hotkey_active
                        is_pressed = getattr(monitor, '_fn_pressed', None)
                        if is_pressed is None:
                            is_pressed = getattr(monitor, '_hotkey_active', False)
                    else:
                        is_pressed = False
                return {"ok": True, "pressed": bool(is_pressed)}
            return {"ok": True, "pressed": False}
        except Exception as e:
            _log_to_file(f"[get_fn_key_state] ERROR: {e}")
            return {"ok": False, "error": str(e), "pressed": False}

    def set_audio_device(self, device_index: int) -> dict:
        """Persist selected audio device and update the recorder."""
        try:
            set_selected_device_index(int(device_index))
            if _pipeline:
                _pipeline.set_device(int(device_index))
            return {"ok": True, "name": get_selected_device_name()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_mic_level(self) -> dict:
        """Settings, General: the microphone's level now, for the meter by
        its name. From the always-open stream the dictations use (it keeps
        the half-second before each press), so it shows what Waffler hears;
        nothing is recorded or sent. ``live`` False: no stream yet (Waffler
        is starting, or has no key). ``current`` False: a newly picked
        microphone that Waffler switches to on the next dictation."""
        try:
            audio = getattr(_pipeline, "audio", None) if _pipeline else None
            if audio is None:
                return {"ok": True, "live": False, "level": 0.0, "current": True}
            return dict(audio.input_level(), ok=True)
        except Exception as e:
            _log_to_file(f"get_mic_level failed: {e}")
            return {"ok": False, "live": False, "level": 0.0, "current": True}

    def get_languages(self) -> list:
        """The dictation languages for the Settings picker, [{code, name}]:
        Auto-detect, English, then the rest (src/languages.py)."""
        return [{"code": c, "name": n} for c, n in _languages.LANGUAGES]

    def preview_style(self, style="auto", category="other") -> dict:
        """The example Settings, Style shows under a choice: the kind of
        app's sample sentence run through the real style rules
        (src/style_post.py), so the page never has a second copy of them.
        {"ok": True, "text": the example, "sample": the sentence as it is}."""
        try:
            import style_post
            kind = category if isinstance(category, str) and category in style_post.SAMPLES else "other"
            return {"ok": True, "text": style_post.preview(style, kind), "sample": style_post.SAMPLES[kind]}
        except Exception as e:
            _log_to_file(f"[style] example not made ({type(e).__name__})")
            return {"ok": False, "error": "Couldn't make the example."}

    def get_vocab(self) -> list:
        """Return the user's custom vocabulary list."""
        from transcribe_whisper import load_vocab
        return load_vocab()

    def set_vocab(self, words: list, sounds_like=None) -> dict:
        """Save the user's custom vocabulary list (vocab.json, UTF-8, written
        atomically). The list is tidied first (transcribe_whisper.clean_vocab)
        and checked against the limits; the saved list comes back as
        ``words`` so the page shows exactly what was kept. The next
        dictation reads it, so no restart is needed.

        ``sounds_like`` ({word: [spellings]}, 3.15) replaces what each word
        sounds like (vocab_sounds.json); left out, the saved spellings stay
        with the words still listed. The saved spellings come back as
        ``sounds_like``."""
        from transcribe_whisper import save_vocab, load_sounds_like
        result = save_vocab(words) if sounds_like is None else save_vocab(words, sounds_like)
        if result.get("log"):
            _log_to_file(f"[vocab] {result.pop('log')}")
        if result.get("ok") and "sounds_like" not in result:
            result["sounds_like"] = load_sounds_like(result.get("words") or [])
        return result

    def get_vocab_book(self) -> dict:
        """The Vocabulary page: each word with what it sounds like, how many
        dictations it corrected and when it last did, from the corrections
        recorded in the Journal (src/journal_data.py vocab_usage), and the
        newest corrections. ``total`` is the sum of the words' counts."""
        try:
            from transcribe_whisper import load_vocab, load_sounds_like
            words = load_vocab()
            sounds = load_sounds_like(words)
            usage = _history_cache.vocab_usage()
            rows, total = [], 0
            for w in words:
                u = usage["by_entry"].get(w.casefold(), {})
                n = int(u.get("count") or 0)
                total += n
                rows.append({"word": w, "sounds_like": sounds.get(w, []),
                             "count": n, "last": u.get("last") or ""})
            return {"ok": True, "entries": rows, "total": total,
                    "recent": usage["recent"]}
        except Exception as e:
            _log_to_file(f"[vocab] get_vocab_book failed: {type(e).__name__}: {e}")
            return {"ok": False, "entries": [], "total": 0, "recent": []}

    def try_vocab(self, text: str) -> dict:
        """Vocabulary, Try a sentence: what the saved Vocabulary makes of
        ``text``, as a dictation would (the step before the clean-up). The
        text stays here: nothing is sent or kept. ``marks`` are the
        (start, end) of each word it changed."""
        from transcribe_whisper import load_vocab, load_sounds_like, apply_vocab_marked
        text = str(text or "")[:TRY_VOCAB_MAX_CHARS]
        try:
            words = load_vocab()
            out, changes, spans = apply_vocab_marked(text, words, load_sounds_like(words))
            return {"ok": True, "text": out, "changes": [[h, u] for h, u in changes],
                    "marks": [[a, b] for a, b in spans]}
        except Exception as e:
            _log_to_file(f"[vocab] try_vocab failed: {type(e).__name__}: {e}")
            return {"ok": False, "text": text, "changes": [], "marks": []}

    def demo_overlay_show(self) -> dict:
        """Show overlay with mic feedback for wizard demo (Step 4)."""
        global _pipeline
        try:
            if _pipeline and _pipeline.overlay:
                _pipeline.overlay.show()
                # Start showing mic levels without actually recording
                if hasattr(_pipeline, 'audio'):
                    _pipeline.audio.start_monitoring()
                return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "Pipeline not initialized"}

    def demo_overlay_hide(self) -> dict:
        """Hide overlay after wizard demo."""
        global _pipeline
        try:
            if _pipeline and _pipeline.overlay:
                _pipeline.overlay.hide()
                # Stop mic monitoring
                if hasattr(_pipeline, 'audio'):
                    _pipeline.audio.stop_monitoring()
                return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "Pipeline not initialized"}

    # ── Settings API ──────────────────────────────────────────────────────────

    def _settings_file(self):
        return DATA_DIR / "settings.json"

    def _load_settings_file(self) -> dict:
        """settings.json for reading. {} when missing or unreadable. Never
        use this before a save: use _editing_settings."""
        try:
            sf = self._settings_file()
            if sf.exists():
                data = json.loads(sf.read_text(encoding="utf-8-sig"))
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def _save_settings_file(self, data: dict):
        """Write settings.json atomically. The replace is retried while
        another handle briefly locks the file (it is read during every
        dictation): a bare os.replace failed with "Access is denied"."""
        sf = self._settings_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(sf, data)

    @contextlib.contextmanager
    def _editing_settings(self):
        """Change settings.json: ``with self._editing_settings() as stored:``.

        The read, the change and the save happen under one lock, so two
        bridge calls cannot drop each other's change. A settings.json that
        cannot be parsed is kept as settings.unreadable-<time>.json rather
        than replaced by one setting; one that cannot be opened (locked)
        raises before anything is written, where the old code read {} and
        saved just the new setting over the hotkey, provider order, private
        mode and spelling. Saved only when something changed, and not at
        all if the block raises."""
        with _settings_lock:
            sf = self._settings_file()
            sf.parent.mkdir(parents=True, exist_ok=True)
            stored, kept = read_json_for_update(sf, dict)
            if kept is not None:
                _log_to_file(f"[settings] settings.json could not be read; kept as {kept.name}")
            before = copy.deepcopy(stored)
            yield stored
            if stored != before or kept is not None:
                self._save_settings_file(stored)

    def get_theme(self) -> dict:
        """The saved theme, which the page applies when it is ready. The
        page's own localStorage is wiped at every restart (the window runs
        in private mode), so settings.json is the record. "" when none."""
        try:
            from theme import THEMES
            theme = str(self._load_settings_file().get("theme") or "").strip().lower()
            return {"theme": theme if theme in THEMES else ""}
        except Exception:
            return {"theme": ""}

    def set_theme(self, theme: str) -> dict:
        """Remember the UI theme ('cream', 'dark' or 'auto') in settings.json,
        so the next launch can paint the window in the right colour before the
        page loads (see src/theme.py). Called only when the user picks a
        theme, never at start-up (see get_theme)."""
        try:
            from theme import THEMES
            theme = str(theme or "").strip().lower()
            if theme not in THEMES:
                return {"ok": False, "error": "Unknown theme."}
            with self._editing_settings() as stored:
                stored["theme"] = theme
            return {"ok": True}
        except Exception as e:
            _log_to_file(f"[theme] could not save theme: {e}")
            return {"ok": False, "error": "Couldn't save the theme."}

    def _update_env_var(self, key: str, value: str):
        """Update or add a variable in the user's .env file."""
        env_path = DATA_DIR / ".env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        if env_path.exists():
            # utf-8-sig drops a BOM an editor may have added; the rewrite below
            # then saves plain UTF-8, which is what python-dotenv reads.
            lines = env_path.read_text(encoding="utf-8-sig").splitlines()
        new_lines = []
        found = False
        for line in lines:
            if line.strip().startswith(f"{key}=") or line.strip() == key:
                new_lines.append(f"{key}={value}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={value}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    def get_settings(self) -> dict:
        """Return current settings for the UI."""
        from style_layer import cleanup_mode, signature_settings, styles_by_category
        from transforms import window_view as window_view_transforms
        from command_mode import window_view as window_view_command
        stored = self._load_settings_file()
        key = os.getenv("OPENAI_API_KEY", "")
        groq_key = os.getenv("GROQ_API_KEY", "")
        cerebras_key = os.getenv("CEREBRAS_API_KEY", "")

        def _mask(k):
            if len(k) > 12:
                return k[:8] + "…" + k[-4:]
            elif k:
                return "*" * len(k)
            return ""

        local_whisper_active = _pipeline and hasattr(_pipeline.transcriber, "_backend") and \
                               _pipeline.transcriber._backend in ("mlx", "faster")
        from style_openai import _normalize_provider_order
        transcription_backend = "unknown"
        styling_backend = "unknown"
        if _pipeline:
            # Report the provider each stage tries first: the first one in the
            # user's order that has a key. This used to name Cerebras for
            # clean-up whenever a Cerebras key existed, even with Groq first,
            # and ignored the order for speech.
            transcriber = _pipeline.transcriber
            transcription_backend = getattr(transcriber, "_backend", "api")
            if transcription_backend not in ("mlx", "faster"):
                has_stt = {"groq": bool(getattr(transcriber, "_groq_client", None)),
                           "openai": bool(getattr(transcriber, "client", None))}
                first = next((p for p in (getattr(transcriber, "_cloud_order", None)
                                          or ["groq", "openai"]) if has_stt.get(p)), None)
                transcription_backend = {"groq": "groq", "openai": "api"}.get(first, "none")
            styler = _pipeline.styler
            has_cleanup = {"groq": bool(getattr(styler, "_use_groq", False)),
                           "cerebras": bool(getattr(styler, "_use_cerebras", False)),
                           "openai": bool(getattr(styler, "client", None))}
            order = _normalize_provider_order(getattr(styler, "_provider_order", None))
            styling_backend = next((p for p in order if has_cleanup.get(p)), "none")
        return {
            "api_key_set":           bool(key),
            "api_key_masked":        _mask(key),
            "groq_key_set":          bool(groq_key),
            "groq_key_masked":       _mask(groq_key),
            "cerebras_key_set":      bool(cerebras_key),
            "cerebras_key_masked":   _mask(cerebras_key),
            "local_whisper":         os.getenv("LOCAL_WHISPER", "0") == "1",
            "local_whisper_active":  local_whisper_active,
            "transcription_backend": transcription_backend,
            "styling_backend":       styling_backend,
            "language":              stored.get("language", "en"),
            "dialect":               stored.get("dialect", "auto"),
            "auto_paste":            stored.get("auto_paste", True),
            # "restore": put the user's own clipboard back after a paste
            # (the default); "keep": leave the dictation on it (what 3.15.1
            # did). macOS only; Windows always keeps it.
            "clipboard_after_paste": ("keep" if stored.get("clipboard_after_paste") == "keep"
                                      else "restore"),
            # Sound cues (src/sounds.py sounds_enabled): off until asked for.
            # Tips (src/notifier.py tips_enabled): on until turned off.
            # Anything but a real True / False in settings.json means the
            # default. Written out here because tests lift this method alone.
            "sounds":                stored.get("sounds") is True,
            "tips":                  stored.get("tips") is not False,
            # The engine's own default and clean-up, so Settings shows the
            # order Waffler really uses (it showed groq, cerebras, openai
            # while the engine ran groq, openai, cerebras).
            "provider_order":        _normalize_provider_order(stored.get("provider_order")),
            # Style (src/style_layer.py): clean-up "on" or "off", a style for
            # each kind of app ("auto" until chosen), and the email signature
            # (off, empty). Anything unreadable in settings.json is the default.
            "cleanup":               cleanup_mode(stored),
            "style_by_category":     styles_by_category(stored),
            "email_signature":       signature_settings(stored),
            # Transforms (src/transforms.py): off, no shortcut, Polish with
            # its default rules. Anything unreadable in settings.json is that.
            "transforms":            window_view_transforms(stored),
            # Command mode (src/command_mode.py): off, no shortcut, Perplexity.
            "command_mode":          window_view_command(stored),
        }

    def save_settings(self, settings: dict) -> dict:
        """Save settings — updates .env and/or settings.json, applies live where possible."""
        try:
            notes  = []
            # Checked before anything is changed, so a refused value saves
            # none of the payload. The picker only offers known codes; this
            # keeps a bad one out of settings.json, where it would reach
            # speech to text as a language no provider accepts.
            if "language" in settings and not (
                    isinstance(settings["language"], str)
                    and _languages.is_valid(settings["language"])):
                return {"ok": False, "error": "Waffler doesn't know that language. "
                                              "Pick one from the list."}
            # The same for the clipboard choice: only the two it has.
            if "clipboard_after_paste" in settings and \
                    settings["clipboard_after_paste"] not in ("restore", "keep"):
                return {"ok": False, "error": CLIPBOARD_CHOICE_UNKNOWN}
            # Sounds and tips are switches: a true or a false, nothing else
            # (a string "false" would otherwise count as on).
            if "sounds" in settings and not isinstance(settings["sounds"], bool):
                return {"ok": False, "error": SOUNDS_UNKNOWN}
            if "tips" in settings and not isinstance(settings["tips"], bool):
                return {"ok": False, "error": TIPS_UNKNOWN}
            # Style (Settings, Style): clean-up On or Off, a style for each
            # kind of app, the email signature. A refused value saves none of
            # the payload.
            import style_layer as _sl
            problem = _sl.settings_problem(settings)
            if problem:
                return {"ok": False, "error": problem}
            # Transforms (Settings, Transforms): switch, shortcut, Polish
            # rules, the outline, the person's own transforms. Checked as a
            # whole, including the shortcut against the dictation hotkey.
            import transforms as _tf
            if "transforms" in settings:
                problem = _tf.settings_problem(settings, self._load_settings_file())
                if problem:
                    return {"ok": False, "error": problem}
            # Command mode (Settings, Command mode): switch, hold-to-talk
            # shortcut, search engine. Checked as a whole too, including the
            # shortcut against the dictation hotkey and the Transform shortcut.
            import command_mode as _cm
            if "command_mode" in settings:
                problem = _cm.settings_problem(settings, self._load_settings_file())
                if problem:
                    return {"ok": False, "error": problem}
            with self._editing_settings() as stored:
                # ── OpenAI API key ────────────────────────────────────────────────
                new_key = (settings.get("api_key") or "").strip()
                if new_key and not new_key.startswith("sk-…"):
                    self._update_env_var("OPENAI_API_KEY", new_key)
                    os.environ["OPENAI_API_KEY"] = new_key
                    from openai import OpenAI as _OAI
                    if _pipeline:
                        _pipeline.transcriber.api_key = new_key
                        _pipeline.transcriber.client  = _OAI(api_key=new_key)
                        _pipeline.styler.api_key      = new_key
                        _pipeline.styler.client       = _OAI(api_key=new_key)
                    notes.append("OpenAI API key updated")

                # ── Groq API key ─────────────────────────────────────────────────
                new_groq = (settings.get("groq_key") or "").strip()
                if new_groq and not new_groq.startswith("gsk_…"):
                    self._update_env_var("GROQ_API_KEY", new_groq)
                    os.environ["GROQ_API_KEY"] = new_groq
                    notes.append("Groq API key updated — restart for speed boost")

                # ── Local Whisper toggle ─────────────────────────────────────────
                if "local_whisper" in settings:
                    val = "1" if settings["local_whisper"] else "0"
                    self._update_env_var("LOCAL_WHISPER", val)
                    os.environ["LOCAL_WHISPER"] = val
                    notes.append("Restart app for Whisper mode change")

                # ── Language ─────────────────────────────────────────────────────
                if "language" in settings:
                    stored["language"] = settings["language"]
                    notes.append(f"Language: {settings['language']}")

                # ── Dialect / Spelling ───────────────────────────────────────────
                if "dialect" in settings:
                    stored["dialect"] = settings["dialect"]
                    notes.append(f"Spelling: {settings['dialect']}")

                # ── Auto-paste ───────────────────────────────────────────────────
                if "auto_paste" in settings:
                    stored["auto_paste"] = bool(settings["auto_paste"])
                    notes.append(f"Auto-paste: {'on' if settings['auto_paste'] else 'off'}")

                # ── Clipboard after paste ("restore" | "keep") ───────────────────
                if "clipboard_after_paste" in settings:
                    stored["clipboard_after_paste"] = settings["clipboard_after_paste"]
                    notes.append(f"Clipboard after paste: {settings['clipboard_after_paste']}")

                # ── Sound cues, and tips (read per use: no restart) ───────────────
                if "sounds" in settings:
                    stored["sounds"] = settings["sounds"]
                    notes.append(f"Sounds: {'on' if settings['sounds'] else 'off'}")
                if "tips" in settings:
                    stored["tips"] = settings["tips"]
                    notes.append(f"Tips: {'on' if settings['tips'] else 'off'}")

                # ── Style: clean-up, a style per kind of app, the signature ──────
                # Read again for every dictation: no restart. The signature's
                # text is never put in the notes (they come back to the page).
                if "cleanup" in settings:
                    stored["cleanup"] = settings["cleanup"]
                    notes.append(f"Clean-up: {settings['cleanup']}")
                if "style_by_category" in settings:
                    stored["style_by_category"] = _sl.merged_styles(stored, settings)
                    notes.append("Styles: " + ", ".join(
                        f"{k} {v}" for k, v in stored["style_by_category"].items()))
                if "email_signature" in settings:
                    stored["email_signature"] = _sl.merged_signature(stored, settings)
                    notes.append(f"Email signature: {'on' if stored['email_signature']['enabled'] else 'off'}")

                # ── Transforms: read again for every run; the shortcut and the
                # menu item are applied live (below). The notes never carry
                # the person's own wording.
                if "transforms" in settings:
                    stored["transforms"] = _tf.merged(stored, settings)
                    notes.append(f"Transforms: {'on' if stored['transforms']['enabled'] else 'off'}")

                # ── Command mode: read again for every command; the shortcut is
                # applied live (below).
                if "command_mode" in settings:
                    stored["command_mode"] = _cm.merged(stored, settings)
                    notes.append(f"Command mode: {'on' if stored['command_mode']['enabled'] else 'off'}")

                # ── Provider fallback order ──────────────────────────────────────
                # A list like ["cerebras","groq","openai"]. Persisted AND applied
                # live to the running pipeline so reordering takes effect on the
                # very next dictation — no restart needed.
                if "provider_order" in settings and isinstance(settings["provider_order"], list):
                    from style_openai import _normalize_provider_order
                    order = _normalize_provider_order(settings["provider_order"])
                    stored["provider_order"] = order
                    if _pipeline:
                        try:
                            _pipeline.styler._provider_order = order
                            _pipeline.transcriber._cloud_order = [
                                p for p in order if p in ("groq", "openai")
                            ]
                        except Exception as _e:
                            _log_to_file(f"provider_order live-apply failed: {_e}")
                    notes.append(f"Provider order: {' → '.join(order)}")

            if "transforms" in settings and _pipeline is not None:
                try:
                    _pipeline.apply_transform_settings()
                except Exception as _e:
                    _log_to_file(f"[transform] settings not applied live: {type(_e).__name__}")
            if "command_mode" in settings and _pipeline is not None:
                try:
                    _pipeline.apply_command_settings()
                except Exception as _e:
                    _log_to_file(f"[command] settings not applied live: {type(_e).__name__}")

            return {"ok": True, "notes": notes}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Diagnostics / bug-report bundle ───────────────────────────────────────

    def download_logs(self) -> dict:
        """Bundle all diagnostic-relevant files into a single zip on the user's
        Desktop, then open Finder/Explorer to it. Designed for "my friend's
        Waffler is broken, send me your logs" workflows.

        Includes:
          - app.log, crash.log              (runtime + Python crash dumps)
          - settings.json, config.json,
            setup_complete.json, vocab.json,
            vocab_sounds.json               (config / state — no PII)
          - macOS DiagnosticReports/*.ips   (last 5 system crash dumps)
          - sysinfo.txt                     (synthesised: version, OS,
                                             hotkey, audio device, VPN)

        Deliberately EXCLUDES:
          - .env                            (API keys)
          - history.json                    (user transcripts — PII)
          - snippets.json                   (can hold an address or signature)
          - notifications.json              (which tips were shown: counters
                                             only, nothing a bug report needs)
          - usage.jsonl                     (what each call cost: not needed)
          - inflight.json                   (which takes were being turned into
                                             text: file names and times only)

        Returns:
          {"ok": True, "path": "/Users/.../Desktop/waffler-logs-...zip"}
          {"ok": False, "error": str}
        """
        import zipfile
        import platform as _plat

        try:
            from src import __version__ as _ver
        except ImportError:
            _ver = "unknown"

        try:
            home = Path.home()
            desktop = home / "Desktop"
            if not desktop.exists():
                # Headless / containerised envs may not have Desktop; fall back to home.
                desktop = home
            stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            zip_path = desktop / f"waffler-logs-{stamp}.zip"

            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                # 1) Runtime logs from DATA_DIR. tail of app.log only if huge
                # (cap at last 2 MB so the zip stays sharable over chat).
                for name in ("app.log", "crash.log"):
                    src_path = DATA_DIR / name
                    if not src_path.exists():
                        continue
                    try:
                        if src_path.stat().st_size > 2 * 1024 * 1024:
                            with open(src_path, "rb") as f:
                                f.seek(-2 * 1024 * 1024, 2)
                                tail = f.read()
                            zf.writestr(f"logs/{name}.tail", tail)
                        else:
                            zf.write(src_path, f"logs/{name}")
                    except Exception as e:
                        zf.writestr(f"logs/{name}.READ_ERROR", str(e))

                # 2) Config snapshots (no PII, no keys).
                for name in ("settings.json", "config.json",
                             "setup_complete.json", "vocab.json", "vocab_sounds.json"):
                    src_path = DATA_DIR / name
                    if src_path.exists():
                        try:
                            if name == "settings.json":
                                # The email signature can hold a name, a phone
                                # number or an address: its text is left out.
                                zf.writestr(f"config/{name}", _privacy.settings_for_logs(
                                    src_path.read_text(encoding="utf-8-sig")))
                            else:
                                zf.write(src_path, f"config/{name}")
                        except Exception as e:
                            zf.writestr(f"config/{name}.READ_ERROR", str(e))

                # 3) macOS system crash reports (last 5). Each .ips is ~70 KB.
                if _plat.system() == "Darwin":
                    reports_dir = home / "Library" / "Logs" / "DiagnosticReports"
                    if reports_dir.is_dir():
                        try:
                            ips_files = sorted(
                                reports_dir.glob("Waffler-*.ips"),
                                key=lambda p: p.stat().st_mtime,
                                reverse=True,
                            )[:5]
                            for ips in ips_files:
                                zf.write(ips, f"crashes-system/{ips.name}")
                        except Exception as e:
                            zf.writestr("crashes-system.READ_ERROR", str(e))

                # 4) Synthesised one-page system snapshot.
                try:
                    # Best-effort — every line wrapped so a single failure
                    # doesn't kill the whole snapshot.
                    def _safe(thunk, default="<error>"):
                        try:
                            return thunk()
                        except Exception:
                            return default

                    lines = [
                        f"Waffler version : {_ver}",
                        f"Generated       : {datetime.now().isoformat(timespec='seconds')}",
                        f"OS              : {_plat.system()} {_plat.release()} ({_plat.machine()})",
                        f"Python          : {sys.version.splitlines()[0]}",
                        f"DATA_DIR        : {DATA_DIR}",
                        f"PROJECT_ROOT    : {PROJECT_ROOT}",
                        "",
                        "── Pipeline ────────────────────────────────",
                        f"Pipeline init   : {_safe(lambda: _pipeline is not None)}",
                        f"Transcribe back : {_safe(lambda: getattr(_pipeline.transcriber, '_backend', '?'))}",
                        f"Style back      : {_safe(lambda: getattr(_pipeline.styler, '_backend', '?'))}",
                        f"Style model     : {_safe(lambda: getattr(_pipeline.styler, 'model', '?'))}",
                        "",
                        "── Hotkey ──────────────────────────────────",
                        f"Current config  : {_safe(lambda: self.get_hotkey_config())}",
                        "",
                        "── Audio ───────────────────────────────────",
                        f"Sample rate     : {_safe(lambda: getattr(_pipeline.recorder, 'sample_rate', '?'))}",
                        f"Devices         : {_safe(lambda: self.get_audio_devices())}",
                        "",
                        "── API keys (presence only) ───────────────",
                        f"GROQ_API_KEY    : {'set' if (os.environ.get('GROQ_API_KEY') or '').strip() else 'unset'}",
                        f"CEREBRAS_API_KEY: {'set' if (os.environ.get('CEREBRAS_API_KEY') or '').strip() else 'unset'}",
                        f"OPENAI_API_KEY  : {'set' if (os.environ.get('OPENAI_API_KEY') or '').strip() else 'unset'}",
                    ]
                    zf.writestr("sysinfo.txt", "\n".join(lines))
                except Exception as e:
                    zf.writestr("sysinfo.ERROR", str(e))

                # 5) A README for the recipient — what's in here, what's NOT,
                # and how to read it.
                zf.writestr("README.txt",
                    "Waffler diagnostic bundle\n"
                    "=========================\n\n"
                    f"Generated by Waffler v{_ver} on {datetime.now().isoformat(timespec='seconds')}.\n\n"
                    "Contents:\n"
                    "  logs/app.log              — runtime log (tail-clipped if >2 MB)\n"
                    "  logs/crash.log            — Python crash dumps\n"
                    "  config/                   — non-secret settings + vocab\n"
                    "  crashes-system/           — macOS system crash reports (.ips)\n"
                    "  sysinfo.txt               — one-page system snapshot\n\n"
                    "NOT included (intentional):\n"
                    "  - API keys (~/.waffler-hosted/.env)\n"
                    "  - Transcript history (~/.waffler-hosted/history.json)\n"
                    "  - Snippets (~/.waffler-hosted/snippets.json)\n"
                    "  - The text of your email signature (left out of config/settings.json)\n"
                    "  - Which tips were shown (~/.waffler-hosted/notifications.json)\n"
                    "  - What each call cost (~/.waffler-hosted/usage.jsonl)\n"
                    "  - Which takes were being processed (~/.waffler-hosted/inflight.json)\n\n"
                    "Share this zip when reporting a bug to https://github.com/jbf-tars/Waffler/issues\n"
                )

            _log_to_file(f"[download_logs] wrote {zip_path}")

            # Open Finder/Explorer to the saved file so the user can see + share it.
            # `subprocess` is imported here because this method never had it in
            # scope: it was only imported inside OTHER methods, so both Popen
            # calls below raised NameError, which the bare `except` swallowed.
            # Net effect: the zip was written but the folder never opened and
            # nothing said why. pyflakes had been reporting this as an
            # undefined name, but the CI lint step could never fail.
            import subprocess
            try:
                if _platform.system() == "Darwin":
                    subprocess.Popen(["open", "-R", str(zip_path)])
                elif _platform.system() == "Windows":
                    subprocess.Popen(["explorer", "/select,", str(zip_path)])
            except Exception:
                pass  # File still saved; just couldn't auto-reveal.

            return {"ok": True, "path": str(zip_path)}
        except Exception as e:
            _log_to_file(f"[download_logs] failed: {e}")
            return {"ok": False, "error": str(e)}

    # ── History utilities ─────────────────────────────────────────────────────

    def export_history(self, fmt=None) -> dict:
        """Export the Journal as a file on the Desktop, then show it in its
        folder (src/history_export.py), the way download_logs does with its
        zip. ``fmt`` is "json" (every kept field), "csv" (timestamp, app,
        language, words, text; a spreadsheet will not run a cell as a
        formula) or "txt" (dated paragraphs).

        Returns {"ok": True, "path", "format", "count"} or {"ok": False,
        "error": a plain sentence, "reason"}. With no ``fmt`` it answers as it
        it always did: {"ok", "content", "count"}, the text format, and
        nothing is written."""
        history = load_history()
        if not history:
            return {"ok": False, "reason": "empty", "error": "Your Journal is empty, so there is nothing to export."}
        if fmt is None:
            return {"ok": True, "count": len(history),
                    "content": _history_export.render_txt(history)}
        if fmt not in _history_export.FORMATS:
            return {"ok": False, "reason": "format", "error": "Pick JSON, CSV or Plain text."}
        try:
            path = _history_export.write_file(
                history, fmt, _history_export.desktop_folder())
        except Exception as e:
            _log_to_file(f"[export] failed ({type(e).__name__}: {e})")
            return {"ok": False, "reason": "error",
                    "error": "Couldn't save the file. Try again."}
        _log_to_file(f"[export] wrote {path.name} ({len(history)} entries)")
        _history_export.reveal_in_folder(path, _platform.system())
        return {"ok": True, "path": str(path), "format": fmt, "count": len(history)}

    # ── One entry: delete, undo, redo ─────────────────────────────────────

    def delete_history_item(self, item_id=None, timestamp="", text="") -> dict:
        """Delete one Journal entry, and the recording kept with it.

        The entry is found by its ``item_id`` (entries saved with an id), or,
        for an older one without an id, by its ``timestamp`` and ``text``.
        It goes from history.json at once, in one atomic write; what was
        removed is kept in memory for a few seconds so the window can offer
        Undo (undo_history_change). Not sent cards have their own delete
        (delete_unsent), which also removes their file.

        Returns {"ok": True, "undo": token} or {"ok": False, "reason"}."""
        try:
            with _history_lock:
                history = _load_history_for_update()
                idx = _history_ops.find(history, item_id, timestamp, text)
                if idx < 0:
                    return {"ok": False, "reason": "not_found"}
                entry = history[idx]
                if entry.get("failed"):
                    return {"ok": False, "reason": "not_sent"}
                del history[idx]
                save_history(history)
        except Exception as e:
            _log_to_file(f"[history] could not delete an entry ({type(e).__name__}: {e})")
            return {"ok": False, "reason": "error"}
        _history_cache.invalidate()
        taken = None
        name = entry.get("audio")
        if name:
            taken = _recent_audio.take_out(DATA_DIR, name)
        token = _history_ops.SHELF.put(_history_ops.DELETED, entry, audio=taken)
        if _pipeline:
            _pipeline.swap_last_text(_history_meta.words_of(entry))
        _log_to_file(f"[history] entry deleted by the user"
                     f"{' with its recording' if taken else ''}")
        return {"ok": True, "undo": token}

    def undo_history_change(self, token) -> dict:
        """Undo the last delete or redo the window offered Undo for: the
        entry goes back where its time says (or, for a redo, the old words
        replace the new ones), with its recording if it had one. Works for a
        few seconds after; {"ok": False, "reason": "expired"} after that.
        Returns {"ok": True, "kind", "item"}."""
        record = _history_ops.SHELF.take(token)
        if record is None:
            return {"ok": False, "reason": "expired"}
        entry = record["entry"]
        try:
            with _history_lock:
                history = _load_history_for_update()
                if record["kind"] == _history_ops.DELETED:
                    if (_history_ids.is_id(entry.get("id"))
                            and _history_ops.find(history, entry["id"]) >= 0):
                        return {"ok": False, "reason": "expired"}   # already back
                    _history_ops.insert_in_order(history, entry)
                else:
                    new = record.get("now") or {}
                    idx = _history_ops.find(history, new.get("id"), new.get("timestamp", ""),
                                            new.get("text", ""))
                    if idx < 0:
                        return {"ok": False, "reason": "not_found"}
                    history[idx] = entry
                save_history(history)
        except Exception as e:
            _log_to_file(f"[history] could not undo ({type(e).__name__}: {e})")
            return {"ok": False, "reason": "error"}
        _history_cache.invalidate()
        if record["audio"]:
            _recent_audio.put_back(DATA_DIR, record["audio"])
        if _pipeline and record["kind"] == _history_ops.REPLACED:
            _pipeline.swap_last_text(_history_meta.words_of(record.get("now") or {}),
                                     _history_meta.words_of(entry))
        item = dict(entry)
        if item.get("audio") and item["audio"] in _recent_audio.names(DATA_DIR):
            item["can_redo"] = True
        return {"ok": True, "kind": record["kind"], "item": item}

    def redo_history_item(self, item_id=None, timestamp="", text="") -> dict:
        """"Redo this take": send the entry's kept recording through speech to
        text and the clean-up again and replace the words with the result,
        in the same place (WafflerPipeline.redo_take). Nothing is pasted.
        Returns {"ok", "item", "undo"} or {"ok": False, "reason"}."""
        if not _pipeline:
            return {"ok": False, "reason": "not_ready"}
        try:
            return _pipeline.redo_take(item_id, timestamp, text)
        except Exception as e:
            _log_to_file(f"[redo] failed ({type(e).__name__}: {e})")
            return {"ok": False, "reason": "error"}

    # ── Recordings that were not sent (Journal "Not sent" cards) ─────────

    def retry_unsent(self, unsent_id: str) -> dict:
        """Try again: send a saved recording to speech to text, with the
        user's own keys. The words go into its Journal card; nothing is
        pasted. Returns {"ok", "item", "reason"} (see WafflerPipeline.resend_unsent)."""
        if not _pipeline:
            return {"ok": False, "reason": "not_ready"}
        try:
            return _pipeline.resend_unsent(str(unsent_id or ""), auto=False)
        except Exception as e:
            _log_to_file(f"[unsent] Try again failed ({type(e).__name__}: {e})")
            return {"ok": False, "reason": "error"}

    def retry_all_unsent(self) -> dict:
        """Settings' "Send now": try every waiting recording once."""
        if not _pipeline:
            return {"ok": False, "reason": "not_ready", "sent": 0, "total": 0}
        waiting = _unsent.pending(load_history(), DATA_DIR / _unsent.UNSENT_DIRNAME,
                                  include_cancelled=False)
        sent = 0
        for uid, _entry, _path in waiting:
            r = _pipeline.resend_unsent(uid, auto=False)
            if r.get("ok"):
                sent += 1
            elif r.get("reason") in (_unsent.REASON_OFFLINE, _unsent.REASON_BLOCKED,
                                     _unsent.REASON_TIMEOUT, _unsent.REASON_RATE_LIMITED):
                break   # still unreachable: no point trying the rest now
        return {"ok": True, "sent": sent, "total": len(waiting)}

    def delete_all_unsent(self) -> dict:
        """Settings' "Delete" next to Try again: every recording waiting to
        be sent goes, with its Journal card. The ones cancelled with Esc
        are not counted as waiting, so they stay (each card has Delete)."""
        if not _pipeline:
            return {"ok": False, "reason": "not_ready", "deleted": 0, "total": 0}
        waiting = _unsent.pending(load_history(), DATA_DIR / _unsent.UNSENT_DIRNAME,
                                  include_cancelled=False)
        deleted = 0
        for uid, _entry, _path in waiting:
            if _pipeline.delete_unsent(uid).get("ok"):
                deleted += 1
        if _pipeline._unsent_waiting == 0 and _tray_state_now == _tray_state.NOT_SENT:
            _set_tray_state(_tray_state.IDLE)
        return {"ok": deleted == len(waiting), "deleted": deleted, "total": len(waiting)}

    def delete_unsent(self, unsent_id: str, timestamp: str = "") -> dict:
        """Delete a Not sent recording and its Journal card.

        A card whose recording could not be saved, or whose file has gone,
        has no id: then only its Journal entry is removed, found by its
        time, and only if it is a Not sent entry."""
        if unsent_id:
            if _pipeline:
                return _pipeline.delete_unsent(str(unsent_id))
            return {"ok": False, "reason": "not_ready"}
        unsent_dir = DATA_DIR / _unsent.UNSENT_DIRNAME
        try:
            with _history_lock:
                history = _load_history_for_update()
                for i in range(len(history) - 1, -1, -1):
                    h = history[i]
                    if (isinstance(h, dict) and h.get("failed")
                            and str(h.get("timestamp", "")) == str(timestamp or "")
                            and _unsent.resolve_file(unsent_dir, _unsent.entry_id(h)) is None):
                        del history[i]
                        save_history(history)
                        return {"ok": True}
        except Exception as e:
            _log_to_file(f"[unsent] could not remove the entry: {e}")
            return {"ok": False, "reason": "error"}
        return {"ok": False, "reason": "not_found"}

    def reveal_unsent(self, unsent_id: str) -> dict:
        """Show the file: open its folder with the recording selected."""
        path = _unsent.resolve_file(DATA_DIR / _unsent.UNSENT_DIRNAME, str(unsent_id or ""))
        if path is None:
            return {"ok": False, "reason": "missing"}
        import subprocess
        try:
            if _platform.system() == "Windows":
                # One string: explorer parses /select,"<path>" itself, and a
                # Windows path cannot contain a double quote.
                subprocess.Popen(f'explorer /select,"{path}"')
            elif _platform.system() == "Darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
            return {"ok": True}
        except Exception as e:
            _log_to_file(f"[unsent] could not show the file: {e}")
            return {"ok": False, "reason": "error"}

    def get_unsent_summary(self) -> dict:
        """How many recordings are waiting to be sent (Settings, Data)."""
        try:
            waiting = _unsent.pending(load_history(), DATA_DIR / _unsent.UNSENT_DIRNAME,
                                      include_cancelled=False)
        except Exception:
            waiting = []
        return {
            "count": len(waiting),
            "automatic": sum(1 for _u, e, _p in waiting if _unsent.will_auto_retry(e)),
            "provider": _pipeline._speech_provider_name() if _pipeline else "",
        }

    # ── Privacy and data (3.15) ──────────────────────────────────────────

    def get_recent_audio(self) -> dict:
        """Recent recordings (src/recent_audio.py): whether Waffler keeps
        the last few, how many are kept now, and the limit."""
        try:
            return _recent_audio.summary(DATA_DIR, self._load_settings_file())
        except Exception as e:
            _log_to_file(f"[recent audio] summary failed: {e}")
            return {"enabled": True, "count": 0, "keep": _recent_audio.KEEP}

    def set_recent_audio(self, on) -> dict:
        """Switch keeping recent recordings on or off. Off stops new ones
        being kept; delete_recent_audio removes the ones already there."""
        try:
            with self._editing_settings() as stored:
                stored[_recent_audio.SETTING] = bool(on)
            return {"ok": True, **_recent_audio.summary(DATA_DIR, stored)}
        except Exception as e:
            _log_to_file(f"[recent audio] could not save the switch: {e}")
            return {"ok": False, "error": "Couldn't change that setting. Try again."}

    def delete_recent_audio(self) -> dict:
        """Delete now: every kept recording goes."""
        try:
            n = _recent_audio.delete_all(DATA_DIR)
            _log_to_file(f"[recent audio] deleted {n} recording(s) on request")
            return {"ok": True, "deleted": n, **_recent_audio.summary(DATA_DIR, self._load_settings_file())}
        except Exception as e:
            _log_to_file(f"[recent audio] delete failed: {e}")
            return {"ok": False, "error": "Couldn't delete them. Try again."}

    def get_history_retention(self) -> dict:
        """How long the Journal keeps dictations: 0 keeps everything."""
        return {"keep_days": _privacy.history_keep_days(self._load_settings_file()),
                "choices": list(_privacy.HISTORY_CHOICES)}

    def preview_history_retention(self, days) -> dict:
        """How many dictations a shorter retention would delete now, so the
        window can ask before it does."""
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = 0
        try:
            return {"would_delete": _privacy.count_older(load_history(), days)}
        except Exception:
            return {"would_delete": 0}

    def set_history_retention(self, days) -> dict:
        """Save the retention and apply it at once."""
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = -1
        if days not in _privacy.HISTORY_CHOICES:
            return {"ok": False, "error": "Couldn't change that setting. Try again."}
        try:
            with self._editing_settings() as stored:
                stored[_privacy.HISTORY_SETTING] = days
            with _history_lock:
                history = _load_history_for_update()
                kept = _retain_history(history, force=True)
                if len(kept) != len(history):
                    save_history(kept)
            return {"ok": True, "keep_days": days, "deleted": len(history) - len(kept)}
        except Exception as e:
            _log_to_file(f"[history] could not change retention: {e}")
            return {"ok": False, "error": "Couldn't change that setting. Try again."}

    def delete_my_data(self) -> dict:
        """Delete all my data: history, usage, recent recordings, recordings
        not sent and the logs (src/privacy_data.py). Keys, the words list
        and settings stay; deleting keys too is factory_reset, which the
        window asks about separately. Waffler keeps running."""
        lock = getattr(_pipeline, "_unsent_lock", None) if _pipeline else None
        if lock is not None and not lock.acquire(timeout=2.0):
            return {"ok": False, "error": "Waffler is sending a recording. Try again in a moment."}
        try:
            with _history_lock:
                result = _privacy.delete_my_data(DATA_DIR)
            if _pipeline:
                _pipeline._unsent_waiting = 0
                _pipeline.forget_last_result()    # nothing left to "paste last"
                _pipeline.forget_notifier()       # tips and counters start afresh
            if _tray_state_now == _tray_state.NOT_SENT:
                _set_tray_state(_tray_state.IDLE)
            if not result["ok"]:
                _log_to_file(f"[privacy] delete all my data: could not delete {result['failed']}")
                return {"ok": False, "error": "Some of it couldn't be deleted. Close anything "
                                              "using Waffler's files and try again."}
            return {"ok": True}
        except Exception as e:
            _log_to_file(f"[privacy] delete all my data failed: {type(e).__name__}: {e}")
            return {"ok": False, "error": "Couldn't delete your data. Try again."}
        finally:
            if lock is not None:
                lock.release()

    def get_cleanup_pause(self) -> dict:
        """The clean-up pause the Journal shows at the top, or None."""
        try:
            return _cleanup_pause.view(_cleanup_pause_now, datetime.now())
        except Exception:
            return None

    def clear_history(self) -> dict:
        """Wipe all saved transcriptions."""
        try:
            with _history_lock:
                save_history([])
            if _pipeline:
                _pipeline.forget_last_result()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_url(self, url: str):
        """Open a URL in the system browser (non-blocking, with scheme validation)."""
        from urllib.parse import urlparse
        import subprocess
        import webbrowser

        # Validate URL scheme (security: only allow http/https)
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            _log_to_file(f"open_url blocked: invalid scheme '{parsed.scheme}' in URL: {url}")
            return

        _log_to_file(f"open_url: {url[:120]}")
        try:
            if _platform.system() == "Darwin":
                subprocess.Popen(["/usr/bin/open", url])
            elif _platform.system() == "Windows":
                os.startfile(url)
            else:
                webbrowser.open(url)
        except Exception as e:
            _log_to_file(f"open_url error: {e}")
            webbrowser.open(url)

    def get_onboarding_status(self) -> dict:
        """Returns whether the app needs first-run setup."""
        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        has_any_key = bool(openai_key or groq_key)
        setup_done = _is_setup_complete()
        needs_setup = not setup_done or not has_any_key
        # Where setup was left, so a Mac "Quit & Reopen" after a permission
        # carries on from the same screen. Only while setup is still needed.
        resume = ""
        if needs_setup:
            try:
                resume = str(self._load_settings_file().get("setup_step") or "")
            except Exception:
                resume = ""
        return {
            "needs_setup": needs_setup,
            "has_key": has_any_key,
            "has_openai_key": bool(openai_key),
            "has_groq_key": bool(groq_key),
            "setup_complete": setup_done,
            "resume_step": resume,
        }

    # ── First-run setup (3.15) ──────────────────────────────────────────

    _SETUP_STEPS = ("connect", "permissions", "try", "anywhere")

    def save_setup_step(self, step: str) -> dict:
        """Remember the setup screen on show (see get_onboarding_status)."""
        if step not in self._SETUP_STEPS:
            return {"ok": False}
        try:
            with self._editing_settings() as stored:
                stored["setup_step"] = step
            return {"ok": True}
        except Exception as e:
            _log_to_file(f"[setup] step not saved: {e}")
            return {"ok": False}

    def get_start_at_login(self) -> dict:
        """{"supported", "enabled", "reason"}, read from the operating system
        (src/login_item.py), never from a remembered copy."""
        try:
            return LoginItem().status()
        except Exception as e:
            _log_to_file(f"[login item] status failed: {e}")
            return {"supported": False, "enabled": False,
                    "reason": "Couldn't check whether Waffler starts at sign-in."}

    def set_start_at_login(self, on) -> dict:
        """Switch starting at sign-in on or off. Returns {"ok", "enabled"} and,
        when it couldn't, an "error" sentence."""
        try:
            result = LoginItem().set(bool(on))
        except Exception as e:
            _log_to_file(f"[login item] set failed: {e}")
            result = {"ok": False, "enabled": False,
                      "error": "Couldn't change starting at sign-in. Try again."}
        _log_to_file(f"[login item] start at sign-in {'on' if on else 'off'}: "
                     f"ok={result.get('ok')} enabled={result.get('enabled')}")
        return result

    def start_dictation_for_setup(self) -> dict:
        """Start the real hotkey before setup's last screen sends the user to
        Notepad or TextEdit to try it. The practice listener stops first, so
        only one listener ever watches the keys."""
        try:
            self.wizard_stop_hotkey_test()
        except Exception:
            pass
        if _pipeline_running_or_starting():
            return {"ok": True}
        threading.Thread(target=_initialize_pipeline, daemon=True,
                         name="PipelineInitSetup").start()
        return {"ok": True}

    def open_practice_editor(self) -> dict:
        """Open Notepad (Windows) or TextEdit (Mac), an empty page to dictate
        into, with the real hotkey already listening."""
        self.start_dictation_for_setup()
        import subprocess
        try:
            if _platform.system() == "Darwin":
                subprocess.Popen(["/usr/bin/open", "-a", "TextEdit"])
            elif _platform.system() == "Windows":
                subprocess.Popen(["notepad.exe"])
            else:
                return {"ok": False, "error": "There's no practice editor on this computer."}
            return {"ok": True}
        except Exception as e:
            _log_to_file(f"[setup] practice editor did not open: {e}")
            name = "TextEdit" if _platform.system() == "Darwin" else "Notepad"
            return {"ok": False, "error": f"Couldn't open {name}. Open any app you type in instead."}

    def request_permission(self, name: str) -> dict:
        """Setup's Allow buttons (Mac): show macOS's own prompt for one
        permission (src/mac_permissions.py). A second press after a refusal
        opens that permission's pane, because macOS won't ask twice."""
        asked = getattr(self, "_perm_asked", None)
        if asked is None:
            asked = self._perm_asked = set()
        again = name in asked
        asked.add(name)
        if name == "microphone":
            result = _mac_perms.request_microphone()
        elif name == "input_monitoring":
            result = _mac_perms.request_input_monitoring(already_asked=again)
        elif name == "accessibility":
            result = _mac_perms.request_accessibility(already_asked=again)
        else:
            return {"ok": False}
        _log_to_file(f"[permissions] asked for {name}: {result}")
        return result

    def get_fn_key_conflict(self) -> dict:
        """Mac: whether holding Fn (the hotkey) also opens the emoji picker or
        another job. Read only; Waffler never changes the setting."""
        if _platform.system() != "Darwin":
            return {"conflict": False, "title": "", "detail": ""}
        try:
            keys = self.get_hotkey_config().get("keys") or ["fn"]
            return _mac_perms.fn_conflict(_mac_perms.read_fn_usage(), keys)
        except Exception as e:
            _log_to_file(f"[fn key] check failed: {e}")
            return {"conflict": False, "title": "", "detail": ""}

    def open_keyboard_settings(self) -> dict:
        return _mac_perms.open_pane("keyboard")

    # Key shapes we will lift from the clipboard. Deliberately strict: this
    # reads the user's clipboard, so it must be incapable of returning anything
    # that is not obviously an API key. Anything not matching these is ignored
    # and never leaves the function.
    _KEY_PATTERNS = (
        # Each is anchored so it cannot match inside another key: without
        # the lookbehind, a Cerebras "csk-..." key matches the OpenAI
        # "sk-..." pattern and is filed under the wrong provider.
        ("groq",     r"(?<![A-Za-z0-9])gsk_[A-Za-z0-9]{20,}"),
        ("cerebras", r"(?<![A-Za-z0-9])csk-[A-Za-z0-9_\-]{20,}"),
        ("openai",   r"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    )

    def peek_clipboard_key(self) -> dict:
        """Return an API key sitting on the clipboard, if there is one.

        Setup's most annoying moment is the hand-off: the user creates a key on
        the provider's site, copies it, alt-tabs back, finds the field and
        pastes. The copy has already happened, so the app can simply notice.

        Privacy: this only ever returns text matching a known key shape. Normal
        clipboard contents are never read back into the UI, never logged, and
        never stored. Returns {"found": False} for anything else.
        """
        try:
            from src.clipboard import ClipboardManager
            import re as _re
            text = (ClipboardManager.paste() or "").strip()
            if not text or len(text) > 300:
                return {"found": False}
            for provider, pattern in self._KEY_PATTERNS:
                m = _re.search(pattern, text)
                if m:
                    return {"found": True, "provider": provider, "key": m.group(0)}
            return {"found": False}
        except Exception:
            # A clipboard that cannot be read is not an error worth surfacing;
            # the user can always paste by hand.
            return {"found": False}

    def validate_api_key(self, api_key: str) -> dict:
        """Validate an OpenAI API key by making a lightweight API call."""
        api_key = (api_key or "").strip()
        if not api_key:
            return {"ok": False, "error": "No API key provided"}
        if not api_key.startswith("sk-"):
            return {"ok": False, "error": "Key should start with sk-"}
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            client.models.list()
            # Key is valid — persist it
            self._update_env_var("OPENAI_API_KEY", api_key)
            os.environ["OPENAI_API_KEY"] = api_key
            return {"ok": True, "message": "API key is valid"}
        except Exception as e:
            _log_to_file(f"[keys] OpenAI key check failed: {type(e).__name__}: {str(e)[:160]}")
            return {"ok": False, "error": key_check_error("OpenAI", e)}

    def validate_groq_key(self, api_key: str) -> dict:
        """Validate a Groq API key by listing models."""
        api_key = (api_key or "").strip()
        if not api_key:
            return {"ok": False, "error": "No API key provided"}
        if not api_key.startswith("gsk_"):
            return {"ok": False, "error": "Key should start with gsk_"}
        try:
            import groq
            client = groq.Groq(api_key=api_key)
            models = client.models.list()
            # Key is valid — persist it
            self._update_env_var("GROQ_API_KEY", api_key)
            os.environ["GROQ_API_KEY"] = api_key
            # The same answer lists the models this key can use, so setup
            # can say "Speech to text: working" and "Clean-up: working"
            # (src/first_run.py) rather than only "the key is valid".
            try:
                services = _first_run.groq_services(_first_run.model_ids(models))
            except Exception:
                services = []
            return {"ok": True, "message": "Groq key is valid", "services": services}
        except ImportError:
            return {"ok": False, "error": "Groq SDK not installed"}
        except Exception as e:
            _log_to_file(f"[keys] Groq key check failed: {type(e).__name__}: {str(e)[:160]}")
            # "kind" lets setup try again by itself after a busy moment.
            return {"ok": False, "error": key_check_error("Groq", e),
                    "kind": classify_request_error(e)}

    def validate_cerebras_key(self, api_key: str) -> dict:
        """Validate a Cerebras API key by doing a minimal chat-completions
        round-trip. We can't use the /models endpoint because some scoped
        keys lack the 'models:read' permission but still have
        text_to_speech / chat permissions."""
        api_key = (api_key or "").strip()
        if not api_key:
            return {"ok": False, "error": "No API key provided"}
        # v3.14.40 — match the prefix-check pattern used for Groq (gsk_) and
        # OpenAI (sk-). Cerebras keys always start with "csk-" (Cerebras
        # Secret Key); without this check, pasting a Groq key here previously
        # produced a confusing "Invalid Cerebras API key" from the round-trip
        # instead of the obvious "wrong provider" diagnostic.
        if not api_key.startswith("csk-"):
            return {"ok": False, "error": "Key should start with csk-"}
        try:
            from openai import OpenAI as _OpenAI
            client = _OpenAI(api_key=api_key, base_url="https://api.cerebras.ai/v1")
            # Tiny ping — 5 token budget on the smallest free-tier model.
            client.chat.completions.create(
                model="llama-3.1-8b",
                messages=[{"role": "user", "content": "Reply with just OK"}],
                max_tokens=5,
                temperature=0,
            )
            self._update_env_var("CEREBRAS_API_KEY", api_key)
            os.environ["CEREBRAS_API_KEY"] = api_key
            return {"ok": True, "message": "Cerebras key is valid"}
        except Exception as e:
            error_msg = str(e)
            lower = error_msg.lower()
            _log_to_file(f"[keys] Cerebras key check failed: {type(e).__name__}: {error_msg[:160]}")
            kind = classify_request_error(e)
            if kind in ("unauthorized", "forbidden"):
                return {"ok": False, "error": key_check_error("Cerebras", e)}
            elif "429" in error_msg or "high traffic" in lower:
                # The validation hit Cerebras's load-shedding. The key is
                # probably valid; we just can't confirm right now. Accept
                # provisionally so the user isn't blocked at setup time.
                self._update_env_var("CEREBRAS_API_KEY", api_key)
                os.environ["CEREBRAS_API_KEY"] = api_key
                return {"ok": True, "message": "Cerebras rate-limited the check; key saved (will retry on next dictation)"}
            elif "404" in error_msg and "model" in lower:
                # Specific model not on this tier — but the key is valid
                # if the auth path got us as far as a model check.
                self._update_env_var("CEREBRAS_API_KEY", api_key)
                os.environ["CEREBRAS_API_KEY"] = api_key
                return {"ok": True, "message": "Key saved (your tier may not include some models, that's fine)"}
            else:
                return {"ok": False, "error": key_check_error("Cerebras", e)}

    def test_hotkey(self) -> dict:
        """Return hotkey configuration info for the current platform."""
        import platform as plat

        # Get actual hotkey configuration for both platforms
        config = self.get_hotkey_config()
        display = config.get("display", "Win + Ctrl" if plat.system() == "Windows" else "Fn")

        return {
            "ok": True,
            "platform": plat.system(),
            "hotkey": display,
            "mode": "hold",
            "description": (
                f"Hold {display} to record. Release to stop. Press Space while holding to lock recording on (sticky mode). "
                f"Press {display} again to stop sticky mode."
            ),
        }

    def open_accessibility_settings(self) -> dict:
        """Open System Settings to the Accessibility permission panel."""
        import platform as plat
        if plat.system() != "Darwin":
            return {"ok": True, "message": "Not needed on this platform"}

        try:
            import subprocess
            # Open System Settings to Privacy & Security > Accessibility
            subprocess.run([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
            ])
            return {"ok": True, "message": "Opening Accessibility settings"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_input_monitoring_settings(self) -> dict:
        """Open System Settings to the Input Monitoring permission panel."""
        import platform as plat
        if plat.system() != "Darwin":
            return {"ok": True, "message": "Not needed on this platform"}

        try:
            import subprocess
            # Open System Settings to Privacy & Security > Input Monitoring
            subprocess.run([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
            ])
            return {"ok": True, "message": "Opening Input Monitoring settings"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def factory_reset(self) -> dict:
        """Clear all Waffler data and quit the app."""
        try:
            import shutil
            data_dir = DATA_DIR
            if _pipeline:
                _pipeline.forget_last_result()
            if data_dir.exists():
                shutil.rmtree(data_dir)
                _log_to_file("[factory reset] Data directory cleared via UI")
            # Back to a first launch: nothing starts Waffler at sign-in until
            # setup switches it on again.
            try:
                LoginItem().disable()
            except Exception:
                pass

            # Delay window destruction to avoid crash
            # (can't destroy window while inside API callback - JS bridge is still active)
            def delayed_quit():
                time.sleep(0.5)  # Wait for response to be sent to JS
                global _should_quit
                _should_quit = True
                if _window_ref:
                    try:
                        _window_ref.destroy()
                        _log_to_file("[factory reset] Window destroyed successfully")
                    except Exception as e:
                        _log_to_file(f"[factory reset] Window destroy error: {e}")

            threading.Thread(target=delayed_quit, daemon=True, name="FactoryResetQuit").start()
            _log_to_file("[factory reset] Scheduled delayed quit")

            return {"ok": True, "message": "Factory reset complete"}
        except Exception as e:
            _log_to_file(f"[factory reset] Error: {e}")
            return {"ok": False, "error": str(e)}

    # ── Hotkey Config APIs ───────────────────────────────────────────────

    def _get_mac_hotkey_display(self, keys) -> str:
        """Convert Mac hotkey keys to display string."""
        if not keys:
            return "Fn"

        key_map = {
            "fn": "Fn",
            "cmd": "Command",
            "command": "Command",
            "shift": "Shift",
            "option": "Option",
            "alt": "Option",
            "control": "Control",
            "ctrl": "Control",
            "space": "Space"
        }

        parts = []
        for key in keys:
            display_key = key_map.get(key.lower(), key.capitalize())
            parts.append(display_key)

        return " + ".join(parts) if len(parts) > 1 else parts[0] if parts else "Fn"

    def get_hotkey_config(self) -> dict:
        """Return current hotkey configuration."""
        try:
            stored = self._load_settings_file()
            keys = stored.get("hotkey_keys")
            if _platform.system() == "Windows":
                from windows_hotkey import KEY_TO_VK, DEFAULT_HOTKEY, MODIFIER_KEYS
            else:
                # Mac: Default to Fn (only reliable option until modifier detection is fixed)
                if not keys:
                    keys = ["fn"]
                display = self._get_mac_hotkey_display(keys)
                return {"ok": True, "keys": keys, "display": display}
            if not keys or not isinstance(keys, list):
                keys = DEFAULT_HOTKEY
            for k in keys:
                if k not in KEY_TO_VK:
                    _log_to_file(f"Invalid hotkey key '{k}', falling back to default")
                    keys = DEFAULT_HOTKEY
                    break
            from hotkey_rules import display as _hk_display, WINDOWS as _HK_WIN
            return {"ok": True, "keys": keys, "display": _hk_display(keys, _HK_WIN)}
        except Exception as e:
            return {"ok": True, "keys": ["win", "ctrl"], "display": "Win + Ctrl"}

    def save_hotkey_config(self, keys) -> dict:
        """Save hotkey config and restart the listener.

        Returns {"ok": True, "keys": [...], "display": "..."} with the keys
        actually saved, or {"ok": False, "error": "<one sentence>"}. The rules
        live in src/hotkey_rules.py; the key table comes from the platform's
        own listener, so only keys it can watch are accepted."""
        try:
            if isinstance(keys, str):
                try:
                    keys = json.loads(keys)
                except ValueError:
                    keys = None

            # Platform-specific key tables
            from hotkey_rules import check as _check_hotkey
            if _platform.system() == "Windows":
                from windows_hotkey import KEY_TO_VK, MODIFIER_KEYS
                KEY_MAP = KEY_TO_VK
            elif _platform.system() == "Darwin":
                from mac_hotkey_monitor import KEY_TO_KEYCODE, MODIFIER_FLAGS
                KEY_MAP = {**KEY_TO_KEYCODE, **{k: v for k, v in MODIFIER_FLAGS.items()}}
                MODIFIER_KEYS = set(MODIFIER_FLAGS.keys())
            else:
                return {"ok": False, "error": "Changing the hotkey isn't supported on this computer."}

            verdict = _check_hotkey(keys, _platform.system(), KEY_MAP, MODIFIER_KEYS)
            if not verdict["ok"]:
                _log_to_file(f"Hotkey not saved ({keys!r}): {verdict['error']}")
                return verdict
            keys = verdict["keys"]

            # A new dictation hotkey must not overlap the Transform shortcut or
            # the Command shortcut (src/action_hotkeys.py): holding it would
            # also press the other.
            import action_hotkeys as _ah
            clash = _ah.dictation_clash_message(
                keys, list(_ah.saved_shortcuts(self._load_settings_file()).values()), _platform.system())
            if clash:
                _log_to_file(f"Hotkey not saved ({keys!r}): it overlaps an action shortcut")
                return {"ok": False, "error": clash}

            # Save to settings.json
            with self._editing_settings() as stored:
                stored["hotkey_keys"] = keys
            _log_to_file(f"Hotkey config saved: {keys}")

            # Restart listener if pipeline is running
            if _pipeline and hasattr(_pipeline, 'hotkey_listener') and _pipeline.hotkey_listener:
                _log_to_file("Restarting hotkey listener with new keys...")
                _pipeline.hotkey_listener.stop()

                def _restart():
                    time.sleep(0.3)  # wait for old hook to uninstall
                    if _platform.system() == "Windows":
                        from windows_hotkey import WindowsHotkeyListener
                        _pipeline.hotkey_listener = WindowsHotkeyListener(
                            on_press=_pipeline.on_hotkey_press,
                            on_release=_pipeline.on_hotkey_release,
                            on_cancel=_pipeline._on_hotkey_cancel,   # Esc (see its docstring)
                            keys=keys,
                            on_lock=_pipeline._on_hands_free_lock,
                        )
                    elif _platform.system() == "Darwin":
                        from smart_hotkey import SmartHotkeyListener
                        _pipeline.hotkey_listener = SmartHotkeyListener(
                            on_press=_pipeline.on_hotkey_press,
                            on_release=_pipeline.on_hotkey_release,
                            on_cancel=_pipeline._on_hotkey_cancel,   # Esc (see its docstring)
                            keys=keys,
                            on_lock=_pipeline._on_hands_free_lock,
                        )
                    # The Transform shortcut listens through this listener's
                    # tap on a Mac, so it joins the new one (before start(),
                    # which on Windows does not return).
                    try:
                        _pipeline.apply_transform_settings()
                        _pipeline.apply_command_settings()
                    except Exception as _e:
                        _log_to_file(f"[shortcut] action shortcuts not re-attached: {type(_e).__name__}")
                    _log_to_file("New hotkey listener starting...")
                    _pipeline.hotkey_listener.start()

                threading.Thread(target=_restart, daemon=True, name="HotkeyRestart").start()

            return {"ok": True, "keys": keys, "display": verdict["display"]}
        except Exception as e:
            _log_to_file(f"save_hotkey_config error: {e}")
            return {"ok": False, "error": "Couldn't save the hotkey. Please try again."}

    def save_transform_hotkey(self, keys) -> dict:
        """Set the Transform shortcut (Settings, Transforms), or clear it with
        an empty list. The window's key recorder calls this with the keys it
        heard. Returns what save_hotkey_config does: {"ok": True, "keys",
        "display"} or {"ok": False, "error": "<one sentence>"}; the rules are
        src/action_hotkeys.py's."""
        try:
            if isinstance(keys, str):
                try:
                    keys = json.loads(keys)
                except ValueError:
                    return {"ok": False, "error": "Hold the keys you want, then save."}
            if keys is None:
                keys = []           # nothing given clears it, as an empty list does
            saved = self.save_settings({"transforms": {"hotkey": keys}})
            if not saved.get("ok"):
                return {"ok": False, "error": saved.get("error") or "Couldn't save the shortcut. Please try again."}
            view = _transforms.window_view(self._load_settings_file())
            return {"ok": True, "keys": view["hotkey"], "display": view["hotkey_display"]}
        except Exception as e:
            _log_to_file(f"save_transform_hotkey error: {type(e).__name__}")
            return {"ok": False, "error": "Couldn't save the shortcut. Please try again."}

    def save_command_hotkey(self, keys) -> dict:
        """Set the Command shortcut (Settings, Command mode), the keys held
        while speaking an instruction, or clear it with an empty list. The
        window's key recorder calls this with the keys it heard. Returns what
        save_transform_hotkey does: {"ok": True, "keys", "display"} or
        {"ok": False, "error": "<one sentence>"}; the rules are
        src/action_hotkeys.py's, and the shortcut may not overlap the dictation
        hotkey or the Transform shortcut."""
        try:
            if isinstance(keys, str):
                try:
                    keys = json.loads(keys)
                except ValueError:
                    return {"ok": False, "error": "Hold the keys you want, then save."}
            if keys is None:
                keys = []           # nothing given clears it, as an empty list does
            saved = self.save_settings({"command_mode": {"hotkey": keys}})
            if not saved.get("ok"):
                return {"ok": False, "error": saved.get("error") or "Couldn't save the shortcut. Please try again."}
            view = _command_mode.window_view(self._load_settings_file())
            return {"ok": True, "keys": view["hotkey"], "display": view["hotkey_display"]}
        except Exception as e:
            _log_to_file(f"save_command_hotkey error: {type(e).__name__}")
            return {"ok": False, "error": "Couldn't save the shortcut. Please try again."}

    # ── Permission APIs ─────────────────────────────────────────────────

    def check_permissions(self) -> dict:
        """Enhanced permission checking with detailed feedback."""
        # Use direct checks instead of PermissionsManager (more reliable)
        accessibility = self.check_accessibility_permission()
        input_monitoring = self.check_input_monitoring_permission()
        # The microphone is asked for in setup too (3.15). It used to be
        # reported as never granted, so the first practice recording was the
        # moment macOS asked, mid-hold, and it came back silent.
        mic_status = _mac_perms.microphone_status()
        mic = mic_status in ("granted", "not_applicable")

        result = {
            "ok": True,
            "platform": "Darwin" if sys.platform == "darwin" else sys.platform,
            "accessibility_granted": accessibility,
            "input_monitoring_granted": input_monitoring,
            "mic_granted": mic,
            "mic_status": mic_status,
            "all_granted": accessibility and input_monitoring and mic,
        }

        return result

    def get_permission_explanations(self) -> dict:
        """Get explanations for why each permission is needed."""
        permissions_mgr = PermissionsManager()
        return permissions_mgr.PERMISSION_EXPLANATIONS

    def request_accessibility_permission(self) -> dict:
        """Enhanced accessibility permission request with step-by-step guidance."""
        permissions_mgr = PermissionsManager()
        return permissions_mgr.request_accessibility_permission()

    def open_permission_settings(self, permission_type: str) -> dict:
        """Open the relevant system settings page for the given permission."""
        permissions_mgr = PermissionsManager()
        return permissions_mgr.open_permission_settings(permission_type)

    def request_input_monitoring_permission(self) -> dict:
        """Request input monitoring permission for Fn key detection."""
        permissions_mgr = PermissionsManager()
        result = permissions_mgr.check_input_monitoring_permission()
        
        if result.status.value == "granted":
            return {"ok": True, "message": "Input monitoring already granted"}
        elif result.status.value == "not_applicable":
            return {"ok": True, "message": "Not needed on this platform"}
        else:
            # Open settings for manual grant
            return permissions_mgr.open_permission_settings("input_monitoring")

    def request_mic_permission(self) -> dict:
        """Enhanced microphone permission request."""
        permissions_mgr = PermissionsManager()
        return permissions_mgr.request_microphone_permission()

    def trigger_permission_requests(self) -> dict:
        """
        Trigger macOS permission prompts by attempting to use the APIs.
        This causes macOS to show system dialogs and add Waffler to permission lists.
        Only runs on macOS. Fails silently on other platforms.
        """
        if sys.platform != "darwin":
            _log_to_file("[INFO] Permission triggers skipped (not macOS)")
            return {"ok": True, "platform": sys.platform, "triggered": False}

        try:
            _log_to_file("[INFO] Triggering macOS permission requests...")

            # Trigger Accessibility permission prompt
            from ApplicationServices import AXIsProcessTrusted
            AXIsProcessTrusted()
            _log_to_file("[INFO] Accessibility permission trigger called")

            # Note: Input Monitoring permission will be triggered automatically
            # when the user first tries to use the Fn key hotkey. No need to
            # create event taps here as it can interfere with the actual hotkey listener.

            return {"ok": True, "platform": "darwin", "triggered": True}

        except Exception as e:
            _log_to_file(f"[INFO] Permission trigger error: {e}")
            # Fail silently - users can still use "Open System Settings" buttons
            return {"ok": True, "platform": "darwin", "triggered": False, "error": str(e)}

    # ── Wizard Hotkey Test API ─────────────────────────────────────────────

    def wizard_init_step2(self) -> dict:
        """Start hotkey monitor for wizard Step 2 (hotkey detection feedback)."""
        global _wizard_step2_monitor
        try:
            # Clean up any existing monitor
            if _wizard_step2_monitor:
                try:
                    _wizard_step2_monitor.stop()
                except Exception:
                    pass
                _wizard_step2_monitor = None

            _log_to_file("Starting hotkey monitor for wizard Step 2...")
            stored = self._load_settings_file()
            keys = stored.get("hotkey_keys")

            if _platform.system() == "Windows":
                _wizard_step2_monitor = WindowsHotkeyListener(
                    on_press=lambda: None,  # No action needed - just monitoring state
                    on_release=lambda: None,
                    keys=keys,
                )
                threading.Thread(target=_wizard_step2_monitor.start, daemon=True, name="WizardStep2Monitor").start()
            else:
                _wizard_step2_monitor = SmartHotkeyListener(
                    on_press=lambda: None,  # No action needed - just monitoring state
                    on_release=lambda: None,
                )
                _wizard_step2_monitor.start()

            _log_to_file("Wizard Step 2 hotkey monitor started")
            return {"ok": True}
        except Exception as e:
            _log_to_file(f"Wizard Step 2 init error: {e}")
            return {"ok": False, "error": str(e)}

    def wizard_cleanup_step2(self) -> dict:
        """Stop hotkey monitor for wizard Step 2."""
        global _wizard_step2_monitor
        try:
            if _wizard_step2_monitor:
                _wizard_step2_monitor.stop()
                _wizard_step2_monitor = None
                _log_to_file("Wizard Step 2 monitor stopped")
            return {"ok": True}
        except Exception as e:
            _log_to_file(f"Wizard Step 2 cleanup error: {e}")
            return {"ok": False, "error": str(e)}

    def wizard_start_hotkey_test(self, device_index) -> dict:
        """Start setup's practice: its own hotkey listener, recorder,
        transcriber and styler, so holding the hotkey on the "Hold ... and
        talk" screen runs a full dictation (transcription and clean-up) into
        the screen instead of into another app."""
        global _wizard_recorder, _wizard_hotkey, _wizard_transcriber, _wizard_styler
        global _wizard_recording, _wizard_result, _wizard_overlay
        try:
            # The real hotkey is already listening (setup's last screen was
            # reached and then left with Back): two listeners would both
            # record, so the practice doesn't start.
            if _pipeline_running_or_starting():
                return {"ok": False, "error": "Waffler is already listening. Hold the hotkey in "
                                              "any app to try it there."}
            # Starting again (after a hotkey change) replaces the old
            # listener rather than adding a second one.
            if _wizard_hotkey is not None or _wizard_recorder is not None:
                self.wizard_stop_hotkey_test()

            # Keys come first: without one there is nothing to try.
            openai_key = os.getenv("OPENAI_API_KEY", "")
            groq_key = os.getenv("GROQ_API_KEY", "")
            if not openai_key and not groq_key:
                # Keys are the step before this one (step 2 of 3 on Windows,
                # 3 of 4 on a Mac); this used to say "Complete Step 1".
                return {"ok": False, "error": "No API key found. Go back a step and add your key."}

            # A Mac that refused the microphone opens a stream that only
            # ever delivers silence. Say so before the user tries.
            if _mac_perms.microphone_status() in ("denied", "restricted"):
                return {"ok": False, "mic": "denied",
                        "error": "Waffler isn't allowed to use the microphone. Allow it in "
                                 "System Settings, then come back."}

            try:
                device_index = int(device_index) if device_index is not None else None
            except (TypeError, ValueError):
                device_index = None
            if device_index is None:
                device_index = get_selected_device_index()
            _wizard_result = None
            _wizard_recording = False

            # The practice recorder uses the microphone picked on screen; it
            # used to ignore it and always record from the default one.
            _wizard_recorder = AudioRecorder(sample_rate=16000, channels=1,
                                             device_index=device_index)

            # Create overlay for wizard Step 4 visual feedback.
            # Previously skipped due to threading-crash concerns, but the
            # overlay launches as a SUBPROCESS so it's GIL-independent.
            # Wrap in try/except so any spawn failure doesn't take down the
            # wizard — recording still works, just without the waffle pill.
            _wizard_overlay = None
            try:
                from src.overlay import RecordingOverlay
                _wizard_overlay = RecordingOverlay()
                _wizard_overlay.prestart()
                _log_to_file("Wizard overlay started for Try-It step")
            except Exception as _e:
                _log_to_file(f"Wizard overlay init failed (recording still works): {_e}")
                _wizard_overlay = None

            _wizard_transcriber = WhisperTranscriber(
                api_key=openai_key, groq_api_key=groq_key,
            )
            # The same clean-up every dictation gets (setup used to stop at
            # the transcription, so it never showed what Waffler does).
            try:
                _wizard_styler = OpenAIStyler(
                    api_key=openai_key,
                    max_tokens=1024,
                    prompt_style=getattr(_config, "prompt_style", "normal") or "normal",
                    groq_api_key=groq_key,
                    cerebras_api_key=os.getenv("CEREBRAS_API_KEY", ""),
                )
            except Exception as _e:
                _log_to_file(f"Wizard styler init failed (words shown as said): {_e}")
                _wizard_styler = None

            # Create temporary hotkey listener
            stored = self._load_settings_file()
            keys = stored.get("hotkey_keys")
            if _platform.system() == "Windows":
                _wizard_hotkey = WindowsHotkeyListener(
                    on_press=_wizard_on_press,
                    on_release=_wizard_on_release,
                    keys=keys,
                )
                threading.Thread(
                    target=_wizard_hotkey.start, daemon=True, name="WizardHotkeyThread"
                ).start()
            else:
                # The saved keys, so a hotkey picked on this screen is the one
                # the practice listens for.
                _wizard_hotkey = SmartHotkeyListener(
                    on_press=_wizard_on_press,
                    on_release=_wizard_on_release,
                    keys=keys,
                )
                # Start directly - pynput creates its own thread internally
                # Running in background thread causes macOS dispatch queue crashes
                _wizard_hotkey.start()

            config = self.get_hotkey_config()
            display = config.get("display", "Win + Ctrl")
            _log_to_file("Wizard hotkey test started")
            return {"ok": True, "message": f"Press {display} to start recording"}
        except Exception as e:
            _log_to_file(f"Wizard hotkey test error: {e}")
            return {"ok": False, "error": "Couldn't start the test recording. Try again, or skip "
                                          "for now and try it from the Journal."}

    def wizard_stop_hotkey_test(self) -> dict:
        """Stop the temporary wizard hotkey listener and clean up.

        CRITICAL: the wizard's ``_wizard_recorder`` owns its own
        ``sd.InputStream``. Setting ``_wizard_recorder = None`` without
        first draining the stream is the wizard→pipeline handoff segfault
        (Bug B v3): CoreAudio's HAL thread can fire one last callback
        into the freed CFFI closure while ``WafflerPipeline`` is
        constructing its own InputStream a few ms later. We MUST call
        ``shutdown()`` (full stop → drain → close sequence under
        ``_STREAM_LOCK``) before dropping the reference. ``shutdown()``
        is bounded by an internal 2s watchdog so a wedged audio device
        can't block the wizard close.
        """
        global _wizard_hotkey, _wizard_recorder, _wizard_transcriber, _wizard_styler
        global _wizard_recording, _wizard_overlay
        try:
            if _wizard_hotkey:
                _wizard_hotkey.stop()
                _wizard_hotkey = None
            if _wizard_recorder:
                if _wizard_recording:
                    try:
                        _wizard_recorder.stop()
                    except Exception:
                        pass
                    _wizard_recording = False
                # Fully tear down the InputStream BEFORE dropping the
                # Python reference. Without this, the HAL thread can fire
                # into the freed CFFI closure when the main pipeline
                # creates its own stream a few ms later.
                try:
                    _wizard_recorder.shutdown()
                except Exception as _e:
                    _log_to_file(f"Wizard recorder shutdown failed: {_e}")
            if _wizard_overlay:
                _wizard_overlay.stop()
                _wizard_overlay = None
            _wizard_recorder = None
            _wizard_transcriber = None
            _wizard_styler = None
            _log_to_file("Wizard hotkey test stopped (recorder drained)")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def complete_setup(self) -> dict:
        """Called when the setup wizard finishes. Initializes the pipeline
        in a background thread so the IPC returns immediately and the
        webview doesn't block while pipeline init runs (which can take
        2-3 seconds for OpenAI/Cerebras client construction).

        Earlier versions ran this synchronously to dodge an SSL crash
        from PyInstaller-bundled httpx on Windows worker threads — but
        that long-blocking IPC then crashed EdgeChromium's GUI thread
        in C code (different crash, no Python frame in the dump). With
        the main-thread SSL context monkey-patch from main() in place,
        background-thread pipeline init is now safe again: every httpx
        client reuses the pre-built context regardless of which thread
        constructs it.
        """
        try:
            _mark_setup_complete()
            # Setup is over: nothing to resume next time.
            try:
                with self._editing_settings() as stored:
                    stored.pop("setup_step", None)
            except Exception:
                pass
            threading.Thread(
                target=_initialize_pipeline,
                daemon=True,
                name="PipelineInit",
            ).start()
            return {"ok": True, "message": "Setup complete! Waffler is ready."}
        except Exception as e:
            _log_to_file(f"complete_setup error: {e}")
            import traceback
            _log_to_file(traceback.format_exc())
            return {"ok": False, "error": str(e)}

    # ── Snippets API ──────────────────────────────────────────────────────────

    def _snippets_file(self):
        return DATA_DIR / "snippets.json"

    def get_snippets(self) -> list:
        """The saved snippets, [{trigger, expansion}], in the order they were
        saved (src/snippets.py)."""
        return _snippets.load(self._snippets_file())

    def set_snippets(self, snippets: list) -> dict:
        """Save the snippets (snippets.json, UTF-8, written atomically).

        The list is tidied first (src/snippets.py clean: outer space
        stripped, empty ones dropped, a trigger already there in other
        capitals dropped) and held to the limits; the saved list comes back
        as ``snippets``, so the page shows what was kept. A list that is over
        a limit is refused whole with one plain sentence, and nothing is
        saved. The next dictation reads the file, so no restart is needed."""
        cleaned, error = _snippets.clean(snippets)
        if error:
            return {"ok": False, "error": error}
        try:
            sf = self._snippets_file()
            sf.parent.mkdir(parents=True, exist_ok=True)
            with _snippets_lock:
                # A snippets.json that can't be read (a hand edit gone wrong)
                # is kept aside, not written over; one that is locked stops
                # the save.
                read_json_for_update(sf, list)
                write_json_atomic(sf, cleaned)
            return {"ok": True, "count": len(cleaned), "snippets": cleaned}
        except Exception as e:
            _log_to_file(f"[snippets] not saved: {type(e).__name__}: {e}")
            return {"ok": False, "error": "Couldn't save your snippets. Try again."}

    # ── Usage Tracking API ─────────────────────────────────────────────────
    def get_usage_stats(self) -> dict:
        """Return usage statistics for display in Settings.

        Now includes today / this-week / this-month buckets and a
        per-provider breakdown (Groq · Cerebras · OpenAI) so the
        Usage card in Settings can show where the money is going.
        """
        usage = load_usage()

        now = datetime.now()
        today_iso = now.strftime("%Y-%m-%d")
        current_month = now.strftime("%Y-%m")
        # ISO week start (Monday)
        from datetime import timedelta as _td
        week_start = (now - _td(days=now.weekday())).strftime("%Y-%m-%d")

        # Aggregates
        total_cost = 0.0
        month_cost = 0.0
        week_cost = 0.0
        today_cost = 0.0
        # Rewrites of selected text (record_usage purpose "transform") are
        # real spend and stay in every total, but they are not dictations:
        # "about X a dictation" must not count them.
        transform_cost = 0.0
        transform_count = 0
        # The same for spoken commands (purpose "command"): the speech to text
        # of the instruction and the model's answer. A command is counted once,
        # by its model call; its speech is not one of the "transcriptions".
        command_cost = 0.0
        command_count = 0
        command_speech = 0
        whisper_count = 0
        gpt_count = 0
        total_duration = 0.0
        total_input_tokens = 0
        total_output_tokens = 0

        # Per-provider buckets: { provider: { cost, count, type counts } }
        by_provider: dict = {}

        for entry in usage:
            cost = entry.get("cost_usd", 0)
            total_cost += cost
            purpose = entry.get("purpose")
            if purpose == "transform":
                transform_cost += cost
                transform_count += 1
            etype = entry.get("type")
            provider = (entry.get("provider") or "unknown").lower()
            if purpose == "command":
                command_cost += cost
                if etype == "gpt":
                    command_count += 1

            if etype == "whisper":
                if purpose == "command":
                    command_speech += 1
                else:
                    whisper_count += 1
                    total_duration += entry.get("duration_seconds", 0)
            elif etype == "gpt":
                gpt_count += 1
                total_input_tokens += entry.get("input_tokens", 0)
                total_output_tokens += entry.get("output_tokens", 0)

            # Bucket by date
            ts = entry.get("timestamp", "")
            if ts.startswith(current_month):
                month_cost += cost
            if ts[:10] >= week_start:
                week_cost += cost
            if ts.startswith(today_iso):
                today_cost += cost

            # Per-provider running totals
            bucket = by_provider.setdefault(provider, {
                "cost_usd": 0.0,
                "count": 0,
                "whisper_count": 0,
                "gpt_count": 0,
                # Calls priced at an unpublished rate (Cerebras). The panel
                # marks the provider's cost as an estimate when this is > 0.
                "estimated_count": 0,
            })
            bucket["cost_usd"] += cost
            bucket["count"] += 1
            if _entry_rate_is_estimate(entry):
                bucket["estimated_count"] += 1
            if etype == "whisper":
                bucket["whisper_count"] += 1
            elif etype == "gpt":
                bucket["gpt_count"] += 1

        # Round per-provider costs for clean display
        for p, b in by_provider.items():
            b["cost_usd"] = round(b["cost_usd"], 6)

        transcription_count = whisper_count
        avg_cost = ((total_cost - transform_cost - command_cost) / transcription_count
                    if transcription_count > 0 else 0)

        return {
            "total_cost_usd": round(total_cost, 4),
            "month_cost_usd": round(month_cost, 4),
            "week_cost_usd": round(week_cost, 4),
            "today_cost_usd": round(today_cost, 4),
            "transcription_count": transcription_count,
            "gpt_count": gpt_count,
            "total_duration_seconds": round(total_duration, 2),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "avg_cost_per_transcription": round(avg_cost, 4),
            "transform_count": transform_count,
            "transform_cost_usd": round(transform_cost, 6),
            "command_count": command_count,
            "command_cost_usd": round(command_cost, 6),
            "by_provider": by_provider,
        }

    def reset_usage(self) -> dict:
        """Reset/clear all usage statistics."""
        try:
            save_usage([])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def restart_app(self) -> dict:
        """Quit and relaunch Waffler.

        Used after settings changes that require a fresh process to
        pick up — most notably API key changes, since the styler's
        client objects (OpenAI / Groq / Cerebras) are constructed once
        at pipeline init and don't re-read the keys on the fly. Without
        a restart the user saves a new Cerebras key, expects it to take
        over fallback duties, and is confused when Groq is still being
        called with the old key.

        Strategy per platform:
          - **macOS (v3.14.30)** — spawn a *detached* shell that polls
            our PID, and once we're gone, calls ``/usr/bin/open -n`` on
            the .app bundle. This is the Sparkle-updater pattern. The
            previous implementation called ``open -n`` *while we were
            still running*, which Launch Services sometimes collapses
            into a "bring existing to front" no-op for signed/notarized
            bundles — even with ``-n``. The 600ms ``os._exit(0)`` then
            killed us with no new instance ever spawned. Waiting for the
            old PID to die first removes the race: by the time
            ``open -n`` runs, Launch Services has nothing to collapse.
          - **Windows** — re-spawn ``sys.executable`` directly. The
            Windows installer puts ``Waffler.exe`` at the same path
            we're running from, and there's no instance management
            overhead.
        """
        import subprocess
        import sys
        try:
            if _platform.system() == "Darwin":
                from pathlib import Path
                p = Path(sys.executable)
                # Walk up to the .app bundle: typically
                # /Applications/Waffler.app/Contents/MacOS/Waffler
                for _ in range(5):
                    if p.suffix == ".app":
                        break
                    p = p.parent

                if p.suffix == ".app":
                    # Detached "wait for parent to die, then relaunch" shell.
                    # Escape single quotes in the bundle path so paths
                    # containing apostrophes (rare but possible) survive.
                    bundle_path = str(p).replace("'", "'\\''")
                    parent_pid = os.getpid()
                    script = (
                        f"while kill -0 {parent_pid} 2>/dev/null; "
                        f"do sleep 0.1; done; "
                        f"/usr/bin/open -n '{bundle_path}'"
                    )
                    subprocess.Popen(
                        ["/bin/sh", "-c", script],
                        start_new_session=True,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    # Source-run fallback: just re-exec ourselves immediately.
                    subprocess.Popen([sys.executable] + sys.argv)
            else:  # Windows / Linux
                subprocess.Popen([sys.executable] + sys.argv[1:])

            # Quit promptly. On macOS the detached shell is already
            # watching for us to die, so the sooner we go the faster the
            # new instance launches. The 250ms delay just lets the IPC
            # response flush back to the JS side first.
            def _quit_after():
                import time
                time.sleep(0.25)
                os._exit(0)
            threading.Thread(target=_quit_after, daemon=True).start()
            return {"ok": True}
        except Exception as e:
            _log_to_file(f"restart_app error: {e}")
            return {"ok": False, "error": str(e)}

    # ── Permission Checking API ────────────────────────────────────────

    def check_accessibility_permission(self) -> bool:
        """Check if Accessibility permission is granted (live).

        The plain ``AXIsProcessTrusted()`` PyObjC binding has a long-standing
        issue under PyInstaller-bundled Python apps: once it returns ``False``
        early in the process lifetime it tends to keep returning ``False``
        even after the user toggles Accessibility ON in System Settings —
        the result appears to be cached inside PyObjC's interop layer for
        the running process.

        This made the wizard pill never tick green for Accessibility, while
        Input Monitoring worked fine (because it goes through ``IOHIDCheckAccess``
        via raw ctypes, bypassing the cache).

        Fix: query via ctypes against the ApplicationServices framework,
        which calls the C function fresh each time
        (``mac_permissions.accessibility_trusted``). We try, in order:

            1. ``AXIsProcessTrustedWithOptions(NULL)`` via ctypes — modern API,
               explicitly designed for repeated polling.
            2. ``AXIsProcessTrusted()`` via ctypes — older fallback.
            3. PyObjC ``AXIsProcessTrusted()`` — last-resort fallback for
               environments where ApplicationServices.framework can't be
               dlopened (very rare).
        """
        if sys.platform != "darwin":
            return True  # Not applicable

        # The ctypes paths first (src/mac_permissions.py): they bypass any
        # PyObjC caching. The hotkey pre-check and the paste use the same call.
        result = _mac_perms.accessibility_trusted()
        if result is not None:
            _log_to_file(f"[DEBUG] Accessibility trusted (ctypes) returned: {result}")
            return result
        _log_to_file("[DEBUG] ctypes Accessibility check unavailable; trying PyObjC")

        # Last resort: PyObjC binding (the original implementation).
        try:
            from ApplicationServices import AXIsProcessTrusted
            result = bool(AXIsProcessTrusted())
            _log_to_file(f"[DEBUG] AXIsProcessTrusted (PyObjC fallback) returned: {result}")
            return result
        except Exception as e:
            _log_to_file(f"[ERROR] All Accessibility checks failed: {e}")
            return False

    def check_input_monitoring_permission(self) -> bool:
        """Check Input Monitoring permission using Apple's canonical
        IOHIDCheckAccess API.

        The previous implementation used CGEventTapCreate, which returns
        non-null even when Input Monitoring is denied (as long as
        Accessibility is granted). That always reported "granted" once
        Accessibility was on, which is why detection was unreliable and
        was effectively unused.

        IOHIDCheckAccess(kIOHIDRequestTypeListenEvent) is the canonical
        API Apple uses internally. Returns:
            0 = kIOHIDAccessTypeGranted
            1 = kIOHIDAccessTypeDenied
            2 = kIOHIDAccessTypeUnknown (not yet requested)
        We treat only 0 as "granted".
        """
        if sys.platform != "darwin":
            return True  # Not applicable on non-macOS
        try:
            import ctypes
            iokit = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/IOKit.framework/IOKit"
            )
            iokit.IOHIDCheckAccess.restype = ctypes.c_uint32
            iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
            # kIOHIDRequestTypeListenEvent = 1
            result = iokit.IOHIDCheckAccess(ctypes.c_uint32(1))
            granted = (result == 0)
            _log_to_file(f"[DEBUG] IOHIDCheckAccess returned: {result} (granted={granted})")
            return granted
        except Exception as e:
            _log_to_file(f"[ERROR] IOHIDCheckAccess failed: {e}")
            print(f"Error checking input monitoring permission: {e}")
            return False


# ── Global refs ───────────────────────────────────────────────────────
_window   = None
_api      = None
_pipeline = None   # set after WafflerPipeline is created
# Setup's last screen and its Done button can both start the pipeline, and
# WafflerPipeline takes a second or more to build. The lock and flag make the
# second call a no-op while the first is still building, so only one hotkey
# listener ever starts (two would paste, bill and log every dictation twice).
_pipeline_init_lock = threading.Lock()
_pipeline_initialising = False
_config   = None   # set in main()
_device_monitor = None   # v3.14.47 — default-input-device watcher (audio_device_monitor.AudioDeviceMonitor)

# ── Wizard temporary state ────────────────────────────────────────────
_wizard_recorder      = None   # temporary AudioRecorder for wizard
_wizard_hotkey        = None   # temporary hotkey listener for wizard (Step 4)
_wizard_step2_monitor = None   # temporary hotkey monitor for Step 2 detection
_wizard_transcriber   = None   # temporary WhisperTranscriber for wizard
_wizard_styler        = None   # temporary OpenAIStyler for the practice clean-up
_wizard_overlay       = None   # temporary overlay for wizard
_wizard_recording     = False  # is wizard currently recording?
_wizard_result        = None   # transcription result

SETUP_FILE = DATA_DIR / "setup_complete.json"


def _is_setup_complete() -> bool:
    """Check if the setup wizard has been completed before."""
    try:
        if SETUP_FILE.exists():
            data = json.loads(SETUP_FILE.read_text(encoding="utf-8-sig"))
            return data.get("complete", False)
    except Exception:
        pass
    return False


def _mark_setup_complete():
    """Persist that setup wizard has been completed."""
    SETUP_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETUP_FILE.write_text(json.dumps({
        "complete": True,
        "completed_at": datetime.now().isoformat(timespec="seconds"),
    }, indent=2), encoding="utf-8")


def _log_to_file(msg: str):
    """Write a debug line to ~/.waffler-hosted/app.log (visible even with console=False)."""
    try:
        log_path = DATA_DIR / "app.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%H:%M:%S')
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts}  {msg}\n")
    except Exception:
        pass
    print(msg)


def _transcripts_loggable() -> bool:
    """True only when the user opted into transcript text in app.log.

    Fails closed while `_config` is still None, so anything logged during early
    startup cannot leak speech even if config later turns the flag on.
    """
    return bool(_config and _config.log_transcripts)


def _wizard_on_press():
    """Wizard hotkey press — start recording."""
    global _wizard_recording, _wizard_result
    if _wizard_recording:
        return
    _wizard_recording = True
    _wizard_result = None
    if _wizard_recorder:
        _wizard_recorder.start()
    _log_to_file("Wizard: recording started")
    # Show overlay pill
    if _wizard_overlay:
        try:
            _wizard_overlay.show()
        except Exception as e:
            _log_to_file(f"Wizard overlay show error: {e}")
    # Level feed for the overlay and setup's meter.
    threading.Thread(target=_wizard_level_loop, daemon=True, name="WizLevelLoop").start()
    if _window:
        try:
            _window.evaluate_js("window.wizOnRecordingStart && window.wizOnRecordingStart()")
        except Exception:
            pass


def _wizard_level_loop():
    """Feed live audio level to the wizard overlay at ~30fps while recording."""
    tick = 0
    while _wizard_recording and _wizard_recorder:
        lvl = _wizard_recorder.get_level()
        if _wizard_overlay:
            try:
                _wizard_overlay.update_level(lvl)
            except Exception:
                pass
        # Setup's own meter (the waffle by the microphone name) gets the
        # same level, a few times a second, so a dead mic shows before the
        # keys come up.
        if tick % 3 == 0:
            _push_wizard_js("wizOnLevel", round(float(lvl or 0), 3))
        tick += 1
        time.sleep(0.033)


def _wizard_on_release():
    """Wizard hotkey release — stop recording and transcribe."""
    global _wizard_recording, _wizard_result
    if not _wizard_recording:
        return
    _wizard_recording = False
    _log_to_file("Wizard: recording stopped, transcribing...")

    # Hide overlay pill
    if _wizard_overlay:
        try:
            _wizard_overlay.hide()
        except Exception:
            pass

    if _window:
        try:
            _window.evaluate_js("window.wizOnRecordingStop && window.wizOnRecordingStop()")
        except Exception:
            pass

    try:
        audio_bytes = _wizard_recorder.stop() if _wizard_recorder else b""
        if not audio_bytes:
            _wizard_result = None
            _push_wizard_silent()
            return

        # Silence detection — windowed check so pauses don't dilute speech
        is_silent = False
        if len(audio_bytes) < 8000:  # Reduced from 16044 to allow shorter words (0.25s instead of 0.5s)
            is_silent = True
            _log_to_file(f"Wizard: recording too short ({len(audio_bytes)} bytes)")
        else:
            try:
                import numpy as np
                audio_arr = np.frombuffer(audio_bytes[44:], dtype=np.int16).astype(np.float32)
                samples_per_window = 16000  # 1 second at 16kHz
                is_silent = True
                for i in range(0, len(audio_arr), samples_per_window):
                    window = audio_arr[i:i + samples_per_window]
                    if len(window) < 1600:
                        break
                    # Lowered threshold from 30 to 15 to catch quieter/quicker speech
                    if float(np.sqrt(np.mean(window ** 2))) >= 15:
                        is_silent = False
                        break
                if is_silent:
                    _log_to_file(f"Wizard: no speech window detected")
            except Exception:
                pass

        if is_silent:
            _wizard_result = None
            _push_wizard_silent()
            return

        try:
            transcript = _wizard_transcriber.transcribe_sync(audio_bytes) if _wizard_transcriber else ""
        except Exception as e:
            _log_to_file(f"Wizard transcription error: {type(e).__name__}: {str(e)[:160]}")
            provider = "Groq" if os.getenv("GROQ_API_KEY") else "OpenAI"
            _push_wizard_error(key_check_error(provider, e))
            return
        transcript = (transcript or "").strip()
        _wizard_result = transcript or "(Empty transcription)"
        # Length only unless logging.log_transcripts is on. app.log ships inside
        # the "Download Logs" bundle, so speech stays out of it by default.
        _log_to_file(
            f"Wizard transcription: "
            f"{transcript_for_log(_wizard_result, allowed=_transcripts_loggable())}"
        )
        if not transcript:
            _push_wizard_silent()
            return
        _wizard_finish_practice(transcript, len(audio_bytes) / 32000.0)
    except Exception as e:
        _wizard_result = None
        _log_to_file(f"Wizard practice error: {type(e).__name__}: {e}")
        _push_wizard_error("Something went wrong with that one. Hold the keys and try again.")


def _wizard_finish_practice(transcript: str, audio_seconds: float):
    """The rest of setup's practice dictation: the clean-up every dictation
    gets, "You said" next to "Waffler wrote" on screen, and the result saved
    as the first Journal entry (src/first_run.py). Nothing is pasted: the
    user is looking at Waffler's own window."""
    _push_wizard_js("wizOnCleaning", transcript)
    styler = _wizard_styler

    def _style(text):
        if styler is None:
            raise RuntimeError("no styling providers configured")
        return styler.style(text)

    def _plain(text):
        try:
            return styler._format_email_layout(styler._basic_clean(text)) if styler else text
        except Exception:
            return text

    result = _first_run.finish_practice(transcript, _style, _plain)
    usage = result["usage"]
    provider = "groq" if os.getenv("GROQ_API_KEY") else "openai"
    record_usage_safely("whisper", duration_seconds=audio_seconds, provider=provider)
    if usage.get("api_used"):
        record_usage_safely("gpt", input_tokens=usage.get("input_tokens", 0),
                            output_tokens=usage.get("output_tokens", 0),
                            provider=usage.get("provider", "openai"))
    saved = append_history_safely(_first_run.journal_entry(result["said"], result["wrote"]))
    _log_to_file(f"Wizard practice finished: cleaned={result['cleaned']} "
                 f"provider={usage.get('provider', 'none')} saved={saved}")
    _push_wizard_js("wizOnPracticeResult", {
        "said": result["said"], "wrote": result["wrote"],
        "cleaned": result["cleaned"], "note": result["note"], "saved": saved,
    })


def _push_wizard_js(fn: str, payload):
    """Call window.<fn>(payload) in the setup page, if it is there."""
    if _window:
        try:
            _window.evaluate_js(f"window.{fn} && window.{fn}({json.dumps(payload)})")
        except Exception:
            pass


def _push_wizard_error(message: str):
    _push_wizard_js("wizOnPracticeError", message)


def _push_wizard_silent():
    """Push 'no audio' notification to JS during wizard."""
    if _window:
        try:
            _window.evaluate_js("window.wizOnSilentRecording && window.wizOnSilentRecording()")
        except Exception:
            pass


def _pipeline_running_or_starting() -> bool:
    """True once the real pipeline exists or is being built, so nothing
    starts a second hotkey listener alongside it."""
    return _pipeline is not None or _pipeline_initialising


def _initialize_pipeline():
    """Create pipeline and start hotkey after setup is complete. Safe to call
    from several threads at once: only the first call builds the pipeline."""
    global _pipeline_initialising
    with _pipeline_init_lock:
        if _pipeline is not None or _pipeline_initialising:
            _log_to_file("Pipeline already initialized or starting, skipping")
            return
        _pipeline_initialising = True
    try:
        _build_pipeline()
    finally:
        with _pipeline_init_lock:
            _pipeline_initialising = False


def _build_pipeline():
    """The body of _initialize_pipeline; only ever runs under its guard."""
    global _pipeline

    _config.reload_env()

    if not _config.has_api_key:
        _log_to_file("Cannot initialize pipeline: no API key found")
        return

    try:
        _log_to_file("Creating WafflerPipeline...")
        pipeline = WafflerPipeline(_config)
        _pipeline = pipeline
        _refresh_tray_menu()    # "Paste last" can now see the last result
        _log_to_file("Pipeline created, starting hotkey thread...")

        hotkey_thread = threading.Thread(
            target=pipeline.start_hotkey,
            daemon=True,
            name="HotkeyThread"
        )
        hotkey_thread.start()
        _log_to_file(f"Hotkey thread started (config key: {_config.hotkey})")

        # v3.14.47 — default-input-device monitor. User report: "When
        # Waffler is open and I add my wireless mic, Settings sees it
        # but Waffler doesn't — I have to close and reopen the app."
        # Cause: ``sd.InputStream`` binds to whatever PortAudio considered
        # the default at the moment it was created, and the monitoring
        # stream we start above never gets re-created. Fix: poll the
        # default input device every 2 s; on change, tear down + recreate
        # the stream via the existing ``stop_monitoring`` /
        # ``start_monitoring`` path on ``AudioRecorder``.
        try:
            from src.audio_device_monitor import AudioDeviceMonitor as _ADM
        except ImportError:
            from audio_device_monitor import AudioDeviceMonitor as _ADM

        def _on_default_input_changed(old_name: str, new_name: str) -> None:
            """Recreate the monitoring stream so the new device is used
            immediately. Skipped if a recording is in flight — restarting
            mid-recording would lose the captured audio."""
            if _pipeline is None or not getattr(_pipeline, "audio", None):
                return
            if getattr(_pipeline.audio, "is_recording", False):
                _log_to_file(
                    f"[audio-monitor] device changed to {new_name!r} but "
                    f"recording is in flight — deferring stream restart"
                )
                return
            try:
                _pipeline.audio.stop_monitoring()
                _pipeline.audio.start_monitoring()
                _log_to_file(
                    f"[audio-monitor] monitoring stream restarted on "
                    f"{new_name!r}"
                )
            except Exception as e:
                _log_to_file(f"[audio-monitor] restart failed: {e}")

        global _device_monitor
        _device_monitor = _ADM(
            on_change=_on_default_input_changed, log_fn=_log_to_file
        )
        _device_monitor.start()
    except Exception as e:
        _log_to_file(f"Pipeline init error: {e}")
        import traceback
        traceback.print_exc()


def set_window(w):
    global _window
    _window = w


# ── Python to page notifications ─────────────────────────────────────
# evaluate_js waits for the page to answer. pywebview's Cocoa backend waits on
# a semaphore with no timeout, and on Windows each call took about 0.44 s on
# the hot path (and caused COM re-entrancy crashes from background threads).
# notify_js_status("processing") ran in the release handler BEFORE the
# processing thread was started, so a slow page delayed every dictation and a
# page that never answered stopped it altogether, with the pill frozen.
#
# Now every notification goes onto one queue that one thread drains. The
# dictation never waits for the page; if the page stops answering, updates
# queue up to a limit and are then dropped, and the dictation carries on.
_JS_QUEUE_MAX = 200
_js_queue = queue.Queue(maxsize=_JS_QUEUE_MAX)
_js_thread = None
_js_thread_lock = threading.Lock()
_js_dropped = [0]


def _js_drain():
    while True:
        script = _js_queue.get()
        w = _window
        if not w:
            continue
        try:
            w.evaluate_js(script)
        except Exception:
            pass


def _post_js(script: str) -> bool:
    """Queue a script for the page. Never blocks. False when dropped."""
    global _js_thread
    if not _window:
        return False
    try:
        _js_queue.put_nowait(script)
    except queue.Full:
        _js_dropped[0] += 1
        if _js_dropped[0] in (1, 100, 1000):
            _log_to_file(f"[js] the window is not answering; {_js_dropped[0]} "
                         f"update(s) dropped so dictation is not held up")
        return False
    with _js_thread_lock:
        if _js_thread is None or not _js_thread.is_alive():
            _js_thread = threading.Thread(target=_js_drain, daemon=True, name="JsNotify")
            _js_thread.start()
    return True


def notify_js_status(status: str):
    """Tell the JS frontend about recording status (safely escaped).
    Queued, never blocking (see _post_js)."""
    _post_js(f"window.waffler_status && window.waffler_status({json.dumps(status)})")


def notify_js_item_updated(unsent_id: str, item: dict):
    """A Not sent entry changed: a retry failed again, or it was sent and is
    now a normal entry. The page swaps the card in place."""
    _post_js("window.waffler_item_updated && window.waffler_item_updated("
             f"{json.dumps(unsent_id)}, {json.dumps(item)})")


def notify_js_window_visible(visible: bool):
    """Tell the page whether its window can be seen, so it pauses every
    animation while hidden in the tray or menu bar, or minimised
    (ui/app.js waffler_window_visible). Sent from its own thread:
    evaluate_js waits for the page, and the callers include pywebview's
    window event handlers, which must not block."""
    w = _window
    if not w:
        return
    js = ("window.waffler_window_visible && window.waffler_window_visible(%s)"
          % ("true" if visible else "false"))

    def _send():
        try:
            w.evaluate_js(js)
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True, name="JsWindowVisible").start()


def notify_js_new_item(item: dict):
    """Push a new transcript item to the JS frontend. Queued, never blocking."""
    _post_js(f"window.waffler_refresh && window.waffler_refresh({json.dumps(item)})")


# Clean-up paused by a provider's limit (src/cleanup_pause.py): the Journal
# says so at the top until it ends. Kept in memory only; a restart forgets it,
# as the styler forgets its own cooldown.
_cleanup_pause_now = None


def _set_cleanup_pause(pause):
    """Remember a clean-up pause and tell the window (never blocks)."""
    global _cleanup_pause_now
    _cleanup_pause_now = pause
    view = _cleanup_pause.view(pause, datetime.now()) if pause else None
    _post_js("window.waffler_cleanup_paused && window.waffler_cleanup_paused("
             f"{json.dumps(view)})")


# ── Tray / menu bar state ─────────────────────────────────────────────
_tray_state_now = _tray_state.IDLE
_tray_working_ico = None     # Path of the generated "working" icon, once made


def _app_icon_path():
    """icon.ico for the tray and the title bar (dev or installed), or one
    built from ui/logo-icon.png when the build left icon.ico out."""
    candidates = [PROJECT_ROOT / "icon.ico"]
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "icon.ico")
    candidates.append(Path(sys.executable).parent / "_internal" / "icon.ico")
    return _tray_state.find_app_icon(candidates, PROJECT_ROOT / "ui" / "logo-icon.png",
                                     DATA_DIR / "app-icon.ico")


def _tray_working_icon_path():
    """The Windows tray icon with an amber dot, made once from icon.ico."""
    global _tray_working_ico
    if _tray_working_ico is not None:
        return _tray_working_ico or None
    try:
        src = _app_icon_path()
        if src is None:
            raise FileNotFoundError("no app icon")
        _tray_working_ico = _tray_state.make_working_icon(src, DATA_DIR / "tray-working.ico")
    except Exception as e:
        _log_to_file(f"[tray] working icon not made ({type(e).__name__}: {e})")
        _tray_working_ico = ""
    return _tray_working_ico or None


def _set_tray_state(state: str):
    """Mirror the dictation in the tray (Windows) or menu bar (Mac) icon:
    the tooltip names the state; while working the Windows icon gets an
    amber dot and the Mac icon dims. Never raises, never blocks for long."""
    global _tray_state_now
    if state == _tray_state_now:
        return
    _tray_state_now = state
    icon = _tray_icon
    if icon is None:
        return
    tip = _tray_state.tip_for(state)
    try:
        if _platform.system() == "Windows":
            icon.title = tip
            want = _tray_working_icon_path() if _tray_state.shows_working_icon(state) else None
            current = getattr(icon, "_waffler_ico_override", None)
            if want != current:
                icon._waffler_ico_override = want
                icon.icon = icon.icon      # reloads through the patched loader
        elif _platform.system() == "Darwin":
            from PyObjCTools import AppHelper

            def _apply():
                try:
                    button = icon.button()
                    if button is not None:
                        button.setToolTip_(tip)
                        button.setAppearsDisabled_(_tray_state.shows_working_icon(state))
                except Exception as e:
                    _log_to_file(f"[tray] menu bar state not shown: {e}")
            AppHelper.callAfter(_apply)
    except Exception as e:
        _log_to_file(f"[tray] state not shown ({type(e).__name__}: {e})")


# ── Pipeline ──────────────────────────────────────────────────────────
# Minimum measured speech in a sub-500ms press for it to count as a real
# dictation rather than a brush of the hotkey. A deliberate "Yes" runs to
# roughly 0.3s of voiced audio; an accidental tap has essentially none.
_MIN_TAP_SPEECH_S = 0.15


class WafflerPipeline:
    def __init__(self, config: Config):
        self.config = config
        self.audio = AudioRecorder(
            sample_rate=config.sample_rate,
            channels=config.channels,
            # The saved microphone choice. Passed at construction because the
            # picker previously stored a selection that never reached stream
            # creation, so choosing a mic in Settings silently did nothing.
            device_index=get_selected_device_index(),
        )
        # Pre-warm the audio input stream at pipeline init so the FIRST
        # hotkey press is instant. The stream stays alive across recordings
        # and feeds a 500ms pre-roll buffer that gets spliced into every
        # new recording — eliminates the "first 1-2 syllables clipped"
        # symptom caused by Windows InputStream.start() taking 50-300ms
        # to actually begin producing samples.
        try:
            self.audio.start_monitoring()
            _log_to_file("Audio stream pre-warmed (continuous monitor + pre-roll)")
        except Exception as e:
            _log_to_file(f"Audio pre-warm failed (will create stream on first hotkey): {e}")

        groq_key = config.groq_api_key or ""
        openai_key = config.openai_api_key or ""

        if not groq_key and not openai_key:
            raise ValueError("At least one API key is required (Groq or OpenAI)")

        # User-configurable provider fallback order (Settings → Provider
        # order). Read once here and handed to both the transcriber and the
        # styler. None -> each uses its canonical default (Groq first).
        _provider_order = None
        try:
            _sf = DATA_DIR / "settings.json"
            if _sf.exists():
                _provider_order = json.loads(_sf.read_text(encoding="utf-8-sig")).get("provider_order")
        except Exception:
            _provider_order = None

        # Transcriber — Groq Whisper (fast) → OpenAI Whisper (fallback), in the
        # user's configured order (Cerebras auto-skipped — no speech-to-text).
        # OpenAI model defaults to gpt-4o-mini-transcribe (half the cost of
        # whisper-1 and noticeably better quality). Override via
        # OPENAI_WHISPER_MODEL env var.
        self.transcriber = WhisperTranscriber(
            api_key=openai_key,
            groq_api_key=groq_key,
            provider_order=_provider_order,
        )
        _log_to_file(f"Transcriber backend: {self.transcriber._backend}")

        # Styler — three-tier fallback chain:
        #   1. Cerebras Llama 3.3 70B   (fastest ever for this model, ~1M
        #      tokens/day free) — primary when CEREBRAS_API_KEY is set
        #   2. Groq Llama 3.3 70B       (very fast, lower daily free cap)
        #   3. OpenAI gpt-4.1-mini      (slower but always available;
        #      gpt-4.1 auto-routed for inputs ≥ 200 words)
        # Same prompt sent everywhere so behaviour is consistent.
        # DO NOT pass model= here — the OpenAIStyler ctor default is
        # gpt-4.1-mini and overriding it silently was a real bug.
        cerebras_key = getattr(config, "cerebras_api_key", "") or ""
        self.styler = OpenAIStyler(
            api_key=openai_key,
            max_tokens=1024,
            prompt_style=config.prompt_style,
            groq_api_key=groq_key,
            cerebras_api_key=cerebras_key,
            provider_order=_provider_order,
        )
        _log_to_file(
            f"Styler chain: cerebras={'yes' if self.styler._use_cerebras else 'no'}"
            f" groq={'yes' if self.styler._use_groq else 'no'}"
            f" openai={'yes' if self.styler.client else 'no'}"
            f" (default openai model: {self.styler.model})"
        )
        # Legacy log line kept for log-grep compatibility — superseded by the
        # 'Styler chain:' line above which shows all three tiers.
        # Order matches the actual style() routing: Groq → Cerebras → OpenAI.
        primary = "groq" if self.styler._use_groq else ("cerebras" if self.styler._use_cerebras else "openai")
        _log_to_file(f"Styler backend: {primary}")

        self.clipboard = ClipboardManager()
        _pasteboard.set_logger(_log_to_file)
        _text_context.set_logger(_log_to_file)
        # Safe paste (src/pasteboard.py): what "Paste last" pastes, the gate
        # that keeps a failed-paste message to one every 30 s, and the check
        # of the microphone and Accessibility each time the hotkey goes down
        # (src/mac_permissions.py).
        self._last_result = _pasteboard.LastResult(history_loader=_history_cache.items)
        # Every toast and tip asks the notifier first (src/notifier.py): one
        # slot, caps and gaps, what was dismissed. Its counters are kept in
        # notifications.json. The permission check and the failed-paste
        # message take their gaps from the same place.
        self._notifier = _notifier.Notifier(
            store=_notifier.FileStore(), context=self._notifier_context, log=_log_to_file)
        self._precheck = _mac_perms.PermissionPrecheck(notifier=self._notifier)
        # Optional sound cues (src/sounds.py), and the press that locked
        # hands-free (the "Don't tap, hold" hint is not for those).
        self._cues = _sound_cues.CuePlayer(settings=self._read_settings, log=_log_to_file)
        self._hands_free_press_id = 0
        # Transforms (Settings, Transforms): rewrite the selected text. The
        # shortcut that starts one (src/action_hotkeys.py) and the run itself
        # (src/transform_action.py). Off by default; both are idle until the
        # person turns it on.
        self._action_hotkeys = _action_hotkeys.ActionHotkeys(log=_log_to_file)
        self._transforms = _transform_action.TransformRunner(self._transform_hooks())
        # Command mode (Settings, Command mode): hold a shortcut, say what you
        # want, and the selection is edited, text is drafted, or a web search
        # opens (src/command_action.py). Off by default; idle until switched on.
        self._commands = _command_action.CommandRunner(self._command_hooks())
        threading.Thread(target=self._warm_paste_deps, daemon=True, name="PasteWarm").start()
        self.is_recording = False
        self._is_paused = False
        self._recording_session = 0  # incremented each press; guards _show_no_audio_toast
        self._recording_start_time = None  # Track when recording started

        # Floating recording overlay
        self.overlay = RecordingOverlay(
            on_cancel=self._on_overlay_cancel,
            on_stop=self._on_overlay_stop,
            on_cancel_request=self._on_overlay_cancel_request,
            on_toast_action=self._on_toast_action,
        )
        # Pre-start overlay subprocess so first recording has no delay
        threading.Thread(target=self.overlay.prestart, daemon=True).start()
        self._prev_window = None  # focused window before recording starts

        # Use persisted audio device (if set)
        saved_idx = get_selected_device_index()
        if saved_idx is not None:
            self._device_index = saved_idx
        else:
            self._device_index = None  # sounddevice default

        # Cancellation tracking for _process() thread
        self._processing_cancelled = threading.Event()
        self._processing_id = 0  # Bumps on each PRESS (a new generation)
        self._current_press_id = 0  # id of the in-flight recording, set on press
        self._processing_lock = threading.Lock()

        # One watchdog over every dictation's processing (src/pipeline_watchdog.py):
        # the working pill, the "still working" offer, deadlines, cancel.
        self._watchdog = _pw.PipelineWatchdog(
            on_begin=self._ui_begin,
            on_working=self._ui_working,
            on_offer=self._ui_offer,
            on_withdraw_offer=self._ui_withdraw_offer,
            on_finished=self._ui_finished,
            on_stuck=self._ui_stuck,
            is_current=self._run_is_current,
            log=_log_to_file,
        )

        # Recordings that were not sent (src/unsent.py). One resend at a
        # time; the drain thread sends them again when the provider answers.
        self._unsent_lock = threading.Lock()
        self._drain_lock = threading.Lock()
        # Speech requests Waffler stopped waiting for that are still running,
        # by unsent id (see _collect_late_words).
        self._unsent_in_flight = {}
        self._in_flight_lock = threading.Lock()
        # Where an email signature was added lately, for the apps that do not
        # show the text before the cursor (src/style_layer.py). Memory only.
        self._signature_memory = _style_layer.SignatureMemory()
        self._unsent_waiting = self._count_unsent()
        threading.Thread(target=self._unsent_drain_loop, daemon=True,
                         name="UnsentDrain").start()

    # ── Safe paste, "Paste last", permission check ────────────────────────

    def _warm_paste_deps(self):
        """Do the slow first-time work (the microphone status, loading
        Accessibility) off the hotkey's path. Best effort."""
        try:
            self._precheck.warm()
            _pasteboard.warm_up()
            self._cues.warm()
            _text_context.warm()     # the first call loads AppKit and Accessibility (about 70 ms)
        except Exception as e:
            _log_to_file(f"[paste] warm-up skipped: {type(e).__name__}")

    def _read_settings(self) -> dict:
        """settings.json as it is now, for the paste and the permission check
        ({} when missing or unreadable). Read per use: Settings changes apply
        to the next dictation."""
        try:
            sf = DATA_DIR / "settings.json"
            if sf.exists():
                data = json.loads(sf.read_text(encoding="utf-8-sig"))
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def _show_toast_safe(self, style, heading, body, buttons=None, timeout=None,
                         reposition=False):
        """overlay.show_toast that never raises. Call it off the thread that
        must not wait (the overlay may be restarting)."""
        try:
            self.overlay.show_toast(style=style, heading=heading, body=body, buttons=buttons,
                                    timeout=timeout, reposition=reposition)
        except Exception as e:
            _log_to_file(f"[overlay] toast failed: {type(e).__name__}")

    def _toast_allowed(self, kind_id: str) -> bool:
        """May this message be shown now? Every toast and tip asks here first;
        src/notifier.py decides (one slot, caps, gaps, tips waiting for a
        quiet moment) and counts what it lets through. An error always passes
        unless it is a duplicate inside its gap. If the notifier itself
        fails, the message is shown: it must never cost anyone an error."""
        try:
            decision = self._notifier.request(kind_id)
        except Exception as e:
            _log_to_file(f"[notifier] skipped ({type(e).__name__}); showing {kind_id}")
            return True
        if not decision.show:
            _log_to_file(f"[notifier] {kind_id} not shown ({decision.reason})")
            return False
        if decision.sound:
            self._play_cue(decision.sound)
        return True

    def _notifier_context(self):
        """Is this a quiet moment for a tip? Asked by the notifier, for tips
        only: not while recording or processing, not before setup is done, and
        not with "Show tips" off."""
        run = self._watchdog.current_run()
        return _notifier.Context(
            recording=bool(self.is_recording),
            processing=run is not None,
            setup_done=bool(_is_setup_complete()),
            tips_enabled=_notifier.tips_enabled(self._read_settings()),
        )

    def _play_cue(self, name: str):
        """A sound cue, when the person has sounds on. The recorder is told
        to ignore the moments the cue sounds in first, so the microphone never
        hears it (src/sounds.py, AudioRecorder.deafen_for). A recorder that
        cannot be told gets no cue. Never raises."""
        try:
            deafen = getattr(self.audio, "deafen_for", None)
            if deafen is None:
                return
            self._cues.play(name, deafen=deafen)
        except Exception as e:
            _log_to_file(f"[sounds] {name} cue skipped ({type(e).__name__})")

    def _on_hands_free_lock(self):
        """The hotkey listener saw Space while the key was held: recording is
        locked on. Remembers which press, and gives the lock tick."""
        self._hands_free_press_id = self._current_press_id
        self._play_cue(_sound_cues.LOCK)

    def _hotkey_name(self) -> str:
        """The person's hotkey as the Hotkey page shows it ("Fn", "Win + Ctrl")."""
        return _hold_hint.hotkey_name(self._read_settings().get("hotkey_keys"), _platform.system())

    def _maybe_hold_hint(self, processing_id: int, duration_s: float):
        """A quick tap was just dropped as an accidental press. Say what to do,
        quietly, once in a while. On a thread: it waits for a second press
        before saying anything. Never raises."""
        try:
            hands_free = self._hands_free_press_id == processing_id
            if not _hold_hint.wants_hint(duration_s, hands_free):
                return
            if not _notifier.tips_enabled(self._read_settings()):
                return          # no need to wait to find that out
            threading.Thread(target=self._hold_hint_later, args=(processing_id,),
                             daemon=True, name="HoldHint").start()
        except Exception as e:
            _log_to_file(f"[hint] skipped ({type(e).__name__})")

    def _hold_hint_later(self, processing_id: int):
        try:
            time.sleep(_hold_hint.SETTLE_S)
            with self._processing_lock:
                alone = processing_id == self._processing_id and not self.is_recording
            if not alone:
                _log_to_file("[hint] a second press followed the tap: no hint")
                return
            if not self._toast_allowed("hold_hint"):
                return
            heading, body = hold_hint_message(self._hotkey_name())
            self.overlay.show_toast(style="info", heading=heading, body=body,
                                    buttons=_notifier.tip_buttons("hold_hint"),
                                    timeout=_hold_hint.HINT_SECONDS)
        except Exception as e:
            _log_to_file(f"[hint] not shown ({type(e).__name__})")

    def _announce_permission_problem(self, problem):
        """One toast whose button opens the pane that fixes it."""
        threading.Thread(
            target=lambda: self._show_toast_safe(
                "error", problem.heading, problem.body,
                buttons=[{"label": problem.button_label, "action": problem.action,
                          "kind": "primary"},
                         {"label": "Dismiss", "action": "dismiss", "kind": "secondary"}],
                timeout=12,
                reposition=True),    # nothing was recorded: the cursor's screen
            daemon=True, name="PermissionToast",
        ).start()

    def _permissions_block_recording(self) -> bool:
        """The hotkey has just gone down. Is the microphone (or, with
        auto-paste on, Accessibility) known to be switched off?

        If so: one toast whose button opens the exact Privacy pane, and True,
        because recording would capture silence (or paste nowhere) and the
        recording pill would hide the toast anyway. A check that errors, a
        permission not asked for yet (the system prompt comes on first use)
        and anything within the 60 s gap per permission all return False:
        recording starts exactly as before. Never raises; microseconds when
        everything is granted."""
        if _platform.system() != "Darwin":
            return False
        try:
            stored = self._read_settings()
            problem = self._precheck.check(need_accessibility=bool(stored.get("auto_paste", True)))
        except Exception as e:
            _log_to_file(f"[precheck] skipped: {type(e).__name__}")
            return False
        if problem is None:
            return False
        _log_to_file(f"[precheck] {problem.permission} is {problem.status}: "
                     f"toast shown, not recording")
        self._announce_permission_problem(problem)
        # Nothing started, so keep the hotkey's state in step (the same
        # reset the cancel paths do).
        listener = getattr(self, "hotkey_listener", None)
        if listener is not None and hasattr(listener, "reset_state"):
            try:
                listener.reset_state()
            except Exception:
                pass
        return True

    def _watch_listener(self):
        """Mac: every few seconds, is the key listener still delivering keys,
        and is Accessibility still on?

        A permission taken away while Waffler runs (or never given) leaves a
        listener that gets no key press at all, so the check at key-down can
        never see it. This asks the listener itself, and says so once with a
        button to the pane that fixes it (a dead listener again every ten
        minutes: src/mac_permissions.py check_idle). It also keeps the
        microphone answer the key-down check uses fresh, so a switch turned
        off while Waffler sat idle is caught by the very next press."""
        time.sleep(10)          # the listener is up within a second
        while True:
            try:
                self._precheck.warm()
                listener = getattr(self, "hotkey_listener", None)
                alive = listener.is_alive() if hasattr(listener, "is_alive") else None
                problem = self._precheck.check_idle(
                    alive, need_accessibility=bool(self._read_settings().get("auto_paste", True)))
                if problem is not None:
                    _log_to_file(f"[precheck] {problem.permission} while idle "
                                 f"(key listener alive: {alive}): toast shown")
                    self._announce_permission_problem(problem)
            except Exception as e:
                _log_to_file(f"[precheck] listener check failed: {type(e).__name__}")
            time.sleep(10)

    def _announce_paste_failure(self, outcome, reposition=False):
        """One message (at most every 30 s) when a paste failed. The words are
        already on the clipboard and in the Journal. ``reposition`` puts it on
        the cursor's screen: for "Paste last", where no recording has just put
        the pill there."""
        platform = "darwin" if _platform.system() == "Darwin" else "win32"
        toast = _pasteboard.report_paste_failure(outcome, self._notifier, platform)
        if toast is None:
            if outcome is not None and outcome.status == _pasteboard.FAILED:
                _log_to_file("[paste] failure message suppressed (once every 30 s)")
            return
        self._show_toast_safe("warn", toast["heading"], toast["body"], buttons=toast["buttons"],
                              reposition=reposition)

    def _settle_paste(self, result, ticket):
        """What the paste step left behind: ``(on_clipboard, pasted)``, with
        the message when the words did not land. ``result`` is the watchdog's
        CallResult for ``clipboard.paste_text``.

        After a paste that went in, the clipboard is the user's own again
        (restored), so ``on_clipboard`` is False. A paste that outlived its
        deadline is told to stop: ``ticket.abandon()`` decides, under a lock,
        whether it will leave the words on the clipboard (the message says
        so, and it is true) or had already begun putting the old clipboard
        back, which means it went in."""
        if result.ok:
            outcome = result.value
            if not isinstance(outcome, _pasteboard.PasteOutcome):
                return False, True      # not told otherwise: it went in
            if outcome.status == _pasteboard.FAILED:
                # Shown now, before the pill ends, so the pill skips its
                # tick: this one needs the user to act.
                self._announce_paste_failure(outcome)
            return outcome.on_clipboard, outcome.status != _pasteboard.FAILED
        if not ticket.abandon():
            _log_to_file(f"[pipeline] paste ran past its deadline but had gone in "
                         f"({result.status})")
            return False, True
        _log_to_file(f"[pipeline] paste did not finish ({result.status}"
                     f"{': ' + repr(result.error) if result.error else ''}); "
                     f"the text is left on the clipboard and in History")
        platform = "darwin" if _platform.system() == "Darwin" else "win32"
        heading, body = paste_timed_out_message(platform)
        try:
            if self._toast_allowed("paste_timed_out"):
                self.overlay.show_toast(style="warn", heading=heading, body=body)
        except Exception:
            pass
        return True, False

    def _remember(self, text: str):
        """Keep the newest result for "Paste last", and refresh the tray menu."""
        self._last_result.set(text)
        _refresh_tray_menu()

    def forget_last_result(self):
        """Nothing to paste again: the user deleted their data."""
        self._last_result.clear()
        _refresh_tray_menu()

    def swap_last_text(self, old: str, new: str = ""):
        """An entry was deleted (``new`` empty) or redone: if "Paste last" was
        holding its old words, it must not paste them any more."""
        self._last_result.swap(old, new)
        _refresh_tray_menu()

    def forget_notifier(self):
        """The user deleted their data: what the notifier remembers (which
        tips were shown, hidden or closed) goes with it."""
        self._notifier.forget()

    def last_transcript_titles(self):
        """(paste line, copy line, has a result) for the menu: a word count,
        never the text, because menus show on screen shares."""
        words = self._last_result.word_count()
        return (last_transcript_title("Paste", words), last_transcript_title("Copy", words),
                words > 0)

    def paste_last(self) -> dict:
        """Paste the last transcript into the app in front (menu bar, bridge),
        the same safe paste as a dictation."""
        text = self._last_result.get()
        if not text:
            _log_to_file("[paste-last] nothing to paste yet")
            return {"ok": False, "error": "no_transcript"}
        stored = self._read_settings()
        # Windows: the tray menu leaves a hidden window of ours in front, so
        # aim at the app the user last dictated into. macOS pastes into
        # whatever has focus (opening a menu bar menu does not take it).
        target = self._prev_window if _platform.system() == "Windows" else None
        # The menu has just closed: give the focus a moment to settle.
        outcome = self.clipboard.paste_text(
            text, window_handle=target, restore=_pasteboard.restore_after_paste(stored),
            settle_delay=0.2)
        _log_to_file(f"[paste-last] outcome={outcome.status} words={len(text.split())}")
        self._announce_paste_failure(outcome, reposition=True)
        return {"ok": outcome.ok, "outcome": outcome.status}

    def copy_last(self) -> dict:
        """Put the last transcript on the clipboard."""
        text = self._last_result.get()
        if not text:
            return {"ok": False, "error": "no_transcript"}
        ok = self.clipboard.copy(text)
        _log_to_file(f"[copy-last] ok={ok} words={len(text.split())}")
        return {"ok": bool(ok)}

    # ── Transforms: rewrite the selected text (src/transform_action.py) ────

    def _transform_hooks(self):
        """What a transform run needs from this app, as the pipeline's own
        methods. Late-bound, so a replaced styler or clipboard is the one used."""
        def usage(reply):
            record_usage_safely("gpt", input_tokens=reply.input_tokens,
                                output_tokens=reply.output_tokens, provider=reply.provider,
                                purpose="transform")

        def refocus():
            if _platform.system() == "Windows":
                proc = getattr(self.overlay, "_process", None)
                _transform_action.refocus_last_app(
                    {getattr(proc, "pid", None)}, log=_log_to_file)

        return _transform_action.Hooks(
            settings=self._read_settings,
            dictation_busy=self._dictation_busy,
            read_selection=lambda: _text_context.read_selection(allow_copy_fallback=True),
            reread_selection=lambda method: _text_context.read_selection(
                allow_copy_fallback=(method == _text_context.METHOD_COPY), timeout=0.9),
            front_app=lambda: _text_context.frontmost_app(),
            window=lambda: self.clipboard.get_focused_window(),
            refocus=refocus,
            chat=lambda *a, **k: self.styler._chat(*a, **k),
            dashes=lambda text: self.styler._strip_em_dashes(text),
            profanity=lambda styled, raw: self.styler._restore_censored_profanity(styled, raw),
            paste=lambda text, window, restore, ticket: self.clipboard.paste_text(
                text, window, restore=restore, ticket=ticket),
            settle_paste=self._settle_paste,
            copy=lambda text: self.clipboard.copy(text),
            remember=self._remember,
            record_usage=usage,
            save_history=append_history_safely,
            announce_item=notify_js_new_item,
            say=self._transform_say,
            ui_begin=self._transform_ui_begin,
            ui_tick=self._transform_ui_tick,
            ui_end=self._transform_ui_end,
            log=_log_to_file,
            platform="darwin" if _platform.system() == "Darwin" else "win32",
        )

    def _dictation_busy(self) -> bool:
        """A dictation is being recorded or processed, or a command is being
        spoken or carried out (a transform waits for either)."""
        spoken = getattr(self, "_commands", None)
        return (bool(self.is_recording) or bool(self._watchdog.active())
                or (spoken is not None and spoken.busy()))

    def transforms_enabled(self) -> bool:
        return bool(_transforms.settings(self._read_settings())["enabled"])

    def run_transform(self, origin: str = "shortcut"):
        """Rewrite the text selected in the app in front with the chosen
        transform. Returns at once; the work is on its own thread. Never raises."""
        try:
            return self._transforms.start(origin)
        except Exception as e:
            _log_to_file(f"[transform] not started: {type(e).__name__}")
            return None

    def apply_transform_settings(self):
        """Make the shortcut and the menu item match settings.json: register
        the shortcut when Transforms is on and one is set, drop it otherwise,
        and join the dictation hotkey's listener (a Mac listens to keys
        through its one event tap). Called at start, after the dictation
        hotkey is rebuilt, and after a save. Never raises."""
        try:
            stored = self._read_settings()
            view = _transforms.settings(stored)
            platform = "Windows" if _platform.system() == "Windows" else "Darwin"
            if view["enabled"] and view["hotkey"]:
                verdict = self._action_hotkeys.register(
                    "transform", view["hotkey"], self._on_transform_shortcut,
                    _action_hotkeys.dictation_keys(stored, platform))
                if not verdict["ok"]:
                    self._action_hotkeys.unregister("transform")
                    _log_to_file("[transform] the saved shortcut is not usable: " + verdict["error"])
            else:
                self._action_hotkeys.unregister("transform")
            listener = getattr(self, "hotkey_listener", None)
            if listener is not None:
                self._action_hotkeys.attach_listener(listener)
            if self._action_hotkeys.registered():
                self._action_hotkeys.start()
        except Exception as e:
            _log_to_file(f"[transform] shortcut not applied: {type(e).__name__}")
        _refresh_tray_menu()

    def _on_transform_shortcut(self):
        self.run_transform("shortcut")

    def _transform_say(self, kind, style, heading, body, buttons=None, timeout=None):
        """A transform's message, through the governor (src/notifier.py)."""
        if self._toast_allowed(kind):
            self._show_toast_safe(style, heading, body, buttons=buttons, timeout=timeout,
                                  reposition=True)

    def _transform_ui_begin(self, run):
        """The pill turns to its working look (elapsed time and an X) and the
        window says it is working. Esc is armed: until the paste starts it
        stops the run (_on_hotkey_cancel)."""
        notify_js_status("processing")
        _set_tray_state(_tray_state.WORKING)
        self._set_listener_processing(True)
        try:
            self.overlay.show()
            self.overlay.show_working(0.0, reliable=True)
        except Exception as e:
            _log_to_file(f"[overlay] working pill not shown: {e}")

    def _transform_ui_tick(self, run, seconds):
        try:
            self.overlay.show_working(seconds)
        except Exception:
            pass

    def _transform_ui_end(self, run, outcome):
        self._set_listener_processing(False)
        try:
            self.overlay.end_working("done" if outcome == _pw.DONE else "quiet")
        except Exception as e:
            _log_to_file(f"[overlay] working pill not ended: {e}")
        notify_js_status("done" if outcome == _pw.DONE else "idle")
        _set_tray_state(_tray_state.IDLE)

    # ── Command mode: speak an instruction (src/command_action.py) ──────────

    def _command_hooks(self):
        """What a command run needs from this app, as the pipeline's own
        methods. Late-bound, so a replaced styler, recorder or clipboard is the
        one used."""
        def usage(reply):
            record_usage_safely("gpt", input_tokens=reply.input_tokens,
                                output_tokens=reply.output_tokens, provider=reply.provider,
                                purpose="command")

        def speech_usage(seconds, info):
            provider = (info or {}).get("backend") or getattr(self.transcriber, "_backend", "")
            if provider in ("mlx", "faster"):
                provider = "local"
            elif provider == "api":
                provider = "openai"
            if seconds > 0:
                record_usage_safely("whisper", duration_seconds=seconds, provider=provider or "groq",
                                    purpose="command")

        def apply_vocab(text):
            # The Vocabulary corrects the spoken instruction like any words (no
            # clean-up pass, no snippets, no style).
            from transcribe_whisper import load_vocab, load_sounds_like, apply_vocab_changes
            vocab = load_vocab()
            if not vocab or not text:
                return text
            sounds = load_sounds_like(vocab)
            fixed, _changes = (apply_vocab_changes(text, vocab, sounds) if sounds
                               else apply_vocab_changes(text, vocab))
            return fixed

        def open_url(url):
            import webbrowser
            return bool(webbrowser.open(url, new=2))

        def no_audio():
            threading.Thread(target=self._show_no_audio_toast, daemon=True, name="NoAudioToast").start()

        return _command_action.Hooks(
            settings=self._read_settings,
            busy_elsewhere=self._command_blocked,
            permissions_ok=lambda: not self._permissions_block_recording(),
            start_audio=lambda: self.audio.start(),
            stop_audio=lambda: self.audio.stop(),
            level=lambda: self.audio.get_level(),
            transcribe=lambda audio: self._transcribe_with_provenance(audio),
            apply_vocab=apply_vocab,
            classify_speech_error=lambda error: _unsent.classify_reason(str(error)),
            speech_provider=self._speech_provider_name,
            read_selection=lambda copy: _text_context.read_selection(allow_copy_fallback=copy),
            read_context=lambda chars: _text_context.get_text_around_cursor(chars),
            reread_selection=lambda method: _text_context.read_selection(
                allow_copy_fallback=(method == _text_context.METHOD_COPY), timeout=0.9),
            front_app=lambda: _text_context.frontmost_app(),
            window=lambda: self.clipboard.get_focused_window(),
            chat=lambda *a, **k: self.styler._chat(*a, **k),
            dashes=lambda text: self.styler._strip_em_dashes(text),
            profanity=lambda styled, raw: self.styler._restore_censored_profanity(styled, raw),
            paste=lambda text, window, restore, ticket: self.clipboard.paste_text(
                text, window, restore=restore, ticket=ticket),
            settle_paste=self._settle_paste,
            copy=lambda text: self.clipboard.copy(text),
            remember=self._remember,
            record_usage=usage,
            record_speech=speech_usage,
            save_history=append_history_safely,
            announce_item=notify_js_new_item,
            open_url=open_url,
            cue=lambda name: self._play_cue(name),
            no_audio=no_audio,
            say=self._command_say,
            ui_begin=self._command_ui_begin,
            ui_level=self._command_ui_level,
            ui_working=self._command_ui_working,
            ui_tick=self._command_ui_tick,
            ui_end=self._command_ui_end,
            log=_log_to_file,
            platform="darwin" if _platform.system() == "Darwin" else "win32",
        )

    def _command_blocked(self) -> bool:
        """Something else has the microphone or the model: a dictation being
        recorded or processed, or a transform running."""
        rewrite = getattr(self, "_transforms", None)
        return (bool(self.is_recording) or bool(self._watchdog.active())
                or (rewrite is not None and rewrite.busy()))

    def command_enabled(self) -> bool:
        return bool(_command_mode.settings(self._read_settings())["enabled"])

    def apply_command_settings(self):
        """Make the Command shortcut match settings.json: register it (a
        hold-to-talk combination) when Command mode is on and one is set, drop
        it otherwise, and join the dictation hotkey's listener (a Mac listens to
        keys through its one event tap). Called at start, after the dictation
        hotkey is rebuilt, and after a save. Never raises."""
        try:
            stored = self._read_settings()
            view = _command_mode.settings(stored)
            platform = "Windows" if _platform.system() == "Windows" else "Darwin"
            if view["enabled"] and view["hotkey"]:
                verdict = self._action_hotkeys.register_hold(
                    "command", view["hotkey"], self._commands.down, self._commands.up,
                    _action_hotkeys.dictation_keys(stored, platform))
                if not verdict["ok"]:
                    self._action_hotkeys.unregister("command")
                    _log_to_file("[command] the saved shortcut is not usable: " + verdict["error"])
            else:
                self._action_hotkeys.unregister("command")
            listener = getattr(self, "hotkey_listener", None)
            if listener is not None:
                self._action_hotkeys.attach_listener(listener)
            if self._action_hotkeys.registered():
                self._action_hotkeys.start()
        except Exception as e:
            _log_to_file(f"[command] shortcut not applied: {type(e).__name__}")

    def _command_say(self, kind, style, heading, body, buttons=None, timeout=None):
        """A command's message, through the governor (src/notifier.py)."""
        if self._toast_allowed(kind):
            self._show_toast_safe(style, heading, body, buttons=buttons, timeout=timeout,
                                  reposition=True)

    def _command_ui_begin(self, run):
        """The keys are down and the microphone is on: the recording pill, the
        status, and Esc armed (until the paste starts it stops the command,
        _on_hotkey_cancel)."""
        notify_js_status("listening")
        _set_tray_state(_tray_state.RECORDING)
        self._set_listener_processing(True)
        try:
            self.overlay.show()
        except Exception as e:
            _log_to_file(f"[overlay] recording pill not shown: {e}")

    def _command_ui_level(self, level):
        try:
            self.overlay.update_level(level)
        except Exception:
            pass

    def _command_ui_working(self, run):
        """The keys are up: the recording pill turns to the working look."""
        notify_js_status("processing")
        _set_tray_state(_tray_state.WORKING)
        try:
            self.overlay.show_working(0.0, reliable=True)
        except Exception as e:
            _log_to_file(f"[overlay] working pill not shown: {e}")

    def _command_ui_tick(self, run, seconds):
        try:
            self.overlay.show_working(seconds)
        except Exception:
            pass

    def _command_ui_end(self, run, outcome):
        self._set_listener_processing(False)
        try:
            shown = getattr(run, "shown", "")
            if shown == "working":
                self.overlay.end_working("done" if outcome == _pw.DONE else "quiet")
            elif shown == "recording":
                self.overlay.hide()
        except Exception as e:
            _log_to_file(f"[overlay] pill not put back: {e}")
        notify_js_status("done" if outcome == _pw.DONE else "idle")
        _set_tray_state(_tray_state.IDLE)

    def set_device(self, device_index: int):
        """Update the audio device used for future recordings.

        Forwards to the recorder. Storing it here alone was the bug: the
        picker reported success while capture carried on using the OS default.
        """
        self._device_index = device_index
        try:
            self.audio.set_device(device_index)
        except Exception as e:
            _log_to_file(f"Failed to apply device {device_index} to recorder: {e}")
        _log_to_file(f"Audio device changed to index {device_index}")

    def _on_overlay_cancel(self):
        """User confirmed cancel: discard the recording.

        The clipboard is left alone. It used to be cleared "to prevent paste of
        cancelled transcription", but the transcript is only copied after
        styling, and _process checks for a cancel before that, so clearing
        protected nothing and wiped whatever the user had copied themselves
        (23 times in one user's log)."""
        # The pill is shared: its X stops a transform or a command too. They
        # and a dictation never overlap, so this leaves the dictation alone.
        for action in (getattr(self, "_transforms", None), getattr(self, "_commands", None)):
            if action is not None and action.busy() and not self.is_recording:
                action.cancel()
                return
        # Flip is_recording and arm cancellation under one lock so a
        # concurrent release can't slip a _process() through between them.
        with self._processing_lock:
            was_recording = self.is_recording
            self.is_recording = False
            self._processing_cancelled.set()
        if was_recording:
            self.audio.stop()
            self.overlay.hide()
            notify_js_status("idle")
            _log_to_file("Recording cancelled by user")
            # Reset hotkey listener state to prevent sticky mode desync
            if hasattr(self, 'hotkey_listener') and self.hotkey_listener:
                if hasattr(self.hotkey_listener, 'reset_state'):
                    self.hotkey_listener.reset_state()
            return
        # Not recording: the working pill's X cancels the dictation that is
        # being processed. Its wait ends at once (the watchdog run wakes it)
        # and nothing is pasted or kept. Once the paste has started it is too
        # late, and the words are kept. (Esc goes to _on_hotkey_cancel, which
        # keeps the recording.)
        watchdog = getattr(self, "_watchdog", None)
        run = watchdog.current_run() if watchdog is not None else None
        if run is not None:
            if run.decide(_pw.CANCEL):
                _log_to_file(f"Dictation {run.generation} cancelled by user during {run.stage}")
            else:
                _log_to_file(f"Cancel during {run.stage} ignored: too late, keeping the words")

    def _on_hotkey_cancel(self):
        """Esc, from the hotkey listener.

        While recording it discards the recording, as it always has. While a
        dictation is being processed it acts only once the "still working"
        offer is on screen, and even then it keeps the recording (or the
        words) in the Journal and pastes nothing. Esc also reaches the app
        in front, so a reflex Esc straight after letting go (closing the
        emoji picker a Mac's Fn key opens, the Start menu, an autocomplete
        list) used to throw the dictation away without a trace. The pill's
        X and the offer's Cancel are clicks on Waffler itself: they still
        discard."""
        with self._processing_lock:
            recording = self.is_recording
        if recording:
            self._on_overlay_cancel()
            return
        # Esc stops a transform or a command too (armed while one runs):
        # nothing is replaced, and the key is not a dictation's.
        for action in (getattr(self, "_transforms", None), getattr(self, "_commands", None)):
            if action is not None and action.cancel():
                return
        watchdog = getattr(self, "_watchdog", None)
        run = watchdog.current_run() if watchdog is not None else None
        if run is None:
            return
        if not run.offer_on_screen():
            _log_to_file(f"Esc during {run.stage} left to the app in front "
                         f"(no offer on screen)")
            return
        if run.decide(_pw.CANCEL, keep=True):
            _log_to_file(f"Dictation {run.generation} stopped with Esc during {run.stage}; "
                         f"nothing pasted, the recording is kept")
        else:
            _log_to_file(f"Esc during {run.stage} ignored: too late, keeping the words")

    def _on_overlay_stop(self):
        """User clicked ■ on overlay — stop & process."""
        if self.is_recording:
            self.on_hotkey_release()
            # Reset hotkey listener state to prevent sticky mode desync
            if hasattr(self, 'hotkey_listener') and self.hotkey_listener:
                if hasattr(self.hotkey_listener, 'reset_state'):
                    self.hotkey_listener.reset_state()

    def _on_overlay_cancel_request(self):
        """User clicked X on overlay: directly cancel without confirmation.
        While recording it discards the recording; on the working pill it
        cancels the dictation being processed."""
        # Skip toast confirmation - directly cancel
        self._on_overlay_cancel()

    def _on_toast_action(self, action: str):
        """Handle toast button clicks from overlay."""
        _log_to_file(f"Toast action: {action}")
        # Whatever was clicked, that message is answered and gone.
        self._notifier.release()
        tip = _notifier.parse_tip_action(action)
        if tip is not None:
            # A tip's own buttons: "Got it" counts as a dismissal (two and it
            # stays away), "Don't show again" is for good.
            verb, kind_id = tip
            if verb == _notifier.TIP_MUTE:
                self._notifier.mute(kind_id)
            else:
                self._notifier.dismiss(kind_id)
            self.overlay.hide_toast()
        elif action == "confirm":
            # User confirmed cancel
            self._on_overlay_cancel()
        elif action == "dismiss":
            # User wants to keep recording — just hide toast
            self.overlay.hide_toast()
        elif action in (_pw.KEEP_WAITING, _pw.PASTE_RAW, _pw.SEND_LATER, "cancel_processing"):
            # An answer to the "still working" offer (_ui_offer). The toast
            # has already closed itself and the working pill is back, so Esc
            # goes back to the app in front.
            self._set_listener_processing(False)
            run = self._watchdog.current_run()
            if run is None:
                return
            choice = _pw.CANCEL if action == "cancel_processing" else action
            if not run.decide(choice):
                _log_to_file(f"Offer answer '{action}' came too late ({run.stage})")
            run.close_offer()
        elif action == "open_journal":
            self._show_journal()
        elif action.startswith("show_transform"):
            # A transform's "Show in Journal": the Journal opens on its entry,
            # with the before and after (ui/app.js waffler_show_transform).
            self._show_journal()
            entry_id = _history_ids.parse(action.partition(":")[2])
            if entry_id is not None:
                _post_js("window.waffler_show_transform && "
                         f"window.waffler_show_transform({int(entry_id)})")
        elif action.startswith("show_command"):
            # A command's "Show in Journal": the Journal opens on its entry
            # (ui/app.js waffler_show_command).
            self._show_journal()
            entry_id = _history_ids.parse(action.partition(":")[2])
            if entry_id is not None:
                _post_js("window.waffler_show_command && "
                         f"window.waffler_show_command({int(entry_id)})")
        elif action in _mac_perms.PANE_ACTIONS:
            # A permission message's button: open the exact Privacy pane. Off
            # the overlay's reader thread (it waits for `open` to answer).
            threading.Thread(target=_mac_perms.open_pane,
                             args=(_mac_perms.PANE_ACTIONS[action],),
                             daemon=True, name="OpenPrivacyPane").start()
        elif action == "copy_last":
            # The failed-paste message's "Copy again", off the overlay's
            # reader thread: it may have to wait behind a paste.
            threading.Thread(target=self.copy_last, daemon=True, name="CopyAgain").start()
        elif action == "select_mic":
            # v3.14.35 — bring Waffler to front and open Settings, where
            # the in-app mic picker lives. Previously this called
            # ``start ms-settings:privacy-microphone`` which is
            # *Windows-only* syntax — on macOS ``start`` isn't a command,
            # ``Popen`` failed, the broad ``except`` swallowed the error,
            # the toast hid, and the user saw nothing happen at all
            # (then had to quit + relaunch). The new behaviour is the
            # same on both platforms and strictly more useful: the user
            # sees Waffler's own device dropdown so they can immediately
            # switch to a different mic without leaving the app.
            try:
                if _window is not None:
                    try:
                        _window.show()
                        # restore() only exists on some pywebview versions
                        if hasattr(_window, "restore"):
                            _window.restore()
                    except Exception as e:
                        _log_to_file(f"[select_mic] window restore failed: {e}")
                    try:
                        _window.evaluate_js(
                            "if (typeof showPage === 'function') showPage('settings');"
                        )
                    except Exception as e:
                        _log_to_file(f"[select_mic] navigate-to-settings failed: {e}")
                else:
                    _log_to_file("[select_mic] no window reference — toast button click lost")
            except Exception as e:
                _log_to_file(f"select_mic action failed: {e}")
            self.overlay.hide_toast()

    def _press_target(self, press_id):
        """The app that was in front when press ``press_id`` began, as an
        ``AppContext`` (``UNKNOWN`` when it was not captured, or belongs to
        another press). Read once at the start of ``_process`` so a newer
        press cannot change it."""
        pc = getattr(self, "_press_context", None)
        if pc and pc[0] == press_id:
            return pc[1]
        pa = getattr(self, "_press_app", None)      # the older shape: (press id, name)
        if pa and pa[0] == press_id and pa[1]:
            return _text_context.make_context("", pa[1])
        return _text_context.UNKNOWN

    def on_hotkey_press(self):
        """Start recording."""
        # Never beside a transform (src/transform_action.py): the hotkey is
        # told why, and the listener's state is put back, as for a blocked
        # permission below.
        for action in (getattr(self, "_transforms", None), getattr(self, "_commands", None)):
            if action is not None and action.busy():
                action.refuse_dictation()
                listener = getattr(self, "hotkey_listener", None)
                if listener is not None and hasattr(listener, "reset_state"):
                    try:
                        listener.reset_state()
                    except Exception:
                        pass
                return
        # Permissions first, and cheap: a microphone or Accessibility switched
        # off gets one message with a button to the right pane, instead of a
        # silent recording or a paste that goes nowhere.
        if not self.is_recording and self._permissions_block_recording():
            return
        # Claim the recording slot + open a new generation atomically. Doing
        # the is_recording check+flip and the id bump under one lock closes
        # two races: (a) two near-simultaneous presses both starting a
        # recording, and (b) a stale _process() pasting cancelled text — by
        # bumping _processing_id HERE (it used to bump on release), any
        # in-flight _process from a previous generation is immediately
        # superseded (see _is_cancelled). The old code bumped on release and
        # cleared the cancel flag on press, which let a quick re-press clear
        # the flag out from under the old thread → it pasted the cancelled
        # transcript into the user's app.
        with self._processing_lock:
            if self.is_recording:
                return
            self.is_recording = True
            self._processing_id += 1
            self._current_press_id = self._processing_id
            self._processing_cancelled.clear()
        # Capture focused window BEFORE overlay takes focus
        self._prev_window = self.clipboard.get_focused_window()
        # And the app in front, for the Journal and for the clean-up
        # (src/text_context.py: its name, identifier and kind, plus the
        # window's title for a browser). Kept with this press's id: the next
        # press must not rename this dictation's app, and neither must
        # whatever is in front when processing ends.
        try:
            self._press_context = (self._current_press_id, _text_context.frontmost_app())
        except Exception:
            self._press_context = (self._current_press_id, _text_context.UNKNOWN)
        _log_to_file("Recording started")
        self._recording_session += 1
        # Track recording start time for duration checks
        import time
        self._recording_start_time = time.time()
        self.audio.start()
        # The start cue, now that the microphone is live (src/sounds.py).
        self._play_cue(_sound_cues.START)
        notify_js_status("listening")
        _set_tray_state(_tray_state.RECORDING)
        try:
            self.overlay.show()
        except Exception as e:
            print(f"[overlay] show failed: {e}")
        # Start VU level feed thread
        threading.Thread(target=self._level_loop, daemon=True, name="LevelLoop").start()

    def on_hotkey_release(self):
        """In toggle mode this fires on hotkey-up but is also called on second press."""
        # Atomic check+flip so only the FIRST of two racing release events
        # (overlay ■ click + hotkey-up, or the 12-min auto-stop racing a
        # manual release) wins and spawns _process — otherwise both fire and
        # produce a double transcription + double paste. The generation id was
        # assigned on press, so we reuse it here.
        with self._processing_lock:
            if not self.is_recording:
                return
            self.is_recording = False
            current_id = self._current_press_id
        _log_to_file("Recording stopped, processing")
        self._is_paused = False
        # Start processing FIRST. Overlay writes go to the overlay child's
        # stdin, and if that child is alive but has stopped reading, the pipe
        # fills and the write blocks. Touching the overlay before this line
        # meant a wedged overlay stopped the audio ever being snapshotted,
        # losing a recording the user had already finished speaking. Nothing
        # about keeping the user's words should depend on the UI being
        # responsive.
        #
        # The pill is no longer hidden here: _process's watchdog run turns it
        # into the working pill (elapsed time and an X) straight away, and
        # ends it with a tick or a message. Hiding it here, then drawing the
        # progress on the hidden pill, is why nothing showed that Waffler was
        # working. It is only hidden if processing cannot start at all.
        try:
            threading.Thread(target=lambda: self._process(current_id), daemon=True,
                             name=f"Process-{current_id}").start()
        except RuntimeError as e:
            # "can't start new thread": the process is out of threads. Keep
            # the recording as Not sent rather than lose it.
            _log_to_file(f"Could not start processing the recording: {e}")
            try:
                self.overlay.hide()
            except Exception as e2:
                print(f"[overlay] hide failed: {e2}")
            try:
                _audio = self.audio.stop()
                if _audio:
                    self._handle_failed_transcription(_audio, "error")
            except Exception as e3:
                _log_to_file(f"Could not keep the recording either: {e3}")
            notify_js_status("error")

    def toggle_pause(self):
        """Toggle pause state during recording."""
        if not self.is_recording:
            return
        self._is_paused = not self._is_paused
        self.audio.toggle_pause()
        
        # Update overlay state
        if self._is_paused:
            self.overlay.update_state("paused")
            notify_js_status("paused")
            _log_to_file("Recording paused")
        else:
            self.overlay.update_state("recording")
            notify_js_status("listening")
            _log_to_file("Recording resumed")

    def _level_loop(self):
        """Feed live audio level to the overlay at ~30fps while recording."""
        import time as _time
        warning_shown = False
        while self.is_recording:
            lvl = self.audio.get_level()
            try:
                self.overlay.update_level(lvl)
            except Exception:
                pass

            # Check recording duration and warn/stop if too long
            if hasattr(self, '_recording_start_time'):
                elapsed = _time.time() - self._recording_start_time

                # Warning at 10 minutes
                if elapsed >= 600 and not warning_shown:
                    warning_shown = True
                    _log_to_file("⚠️  Recording duration: 10 minutes - approaching API limit")
                    try:
                        if self._toast_allowed("long_recording"):
                            self.overlay.show_toast(
                                style="warn",
                                heading="Long recording",
                                body="Recording will auto-stop at 12 minutes (API limit).",
                            )
                            # Hide toast after 3 seconds
                            threading.Thread(target=lambda: (_time.sleep(3), self.overlay.hide_toast()), daemon=True).start()
                    except Exception as e:
                        _log_to_file(f"Warning toast failed: {e}")

                # Auto-stop at 12 minutes (before 25MB API limit)
                elif elapsed >= 720:
                    _log_to_file("⚠️  Recording auto-stopped at 12 minutes (API limit)")
                    try:
                        if self._toast_allowed("recording_limit"):
                            self.overlay.show_toast(
                                style="warn",
                                heading="Recording stopped",
                                body="12-minute limit reached. Processing your audio now...",
                            )
                    except Exception:
                        pass
                    # Trigger stop via hotkey release
                    self.on_hotkey_release()
                    break

            _time.sleep(0.033)  # ~30 fps

    def _show_no_audio_toast(self):
        """Show 'We couldn't hear you' toast on the overlay.

        v3.14.69 — previously this called self.overlay.show() before show_toast,
        which set _visible=True in the overlay subprocess. The toast's own
        _show_toast then orderOut'd the pill (to make room), but when the user
        dismissed the toast, _hide_toast saw _visible=True and orderFront'd the
        pill BACK — so the waffle visibly reappeared with no Fn press. Removed
        the spurious show()/hide() pair; show_toast handles subprocess-alive on
        its own, and the toast positions itself from the last-recording's
        _waffle_x/_waffle_y, which is exactly the screen the user just spoke
        on. The pill semantically does not belong in this code path: the
        recording is already over.
        """
        try:
            if not self._toast_allowed("no_audio"):
                return
            self.overlay.show_toast(
                style="error",
                heading="We couldn't hear you",
                body="Check your mic is connected and not muted.",
            )
            # Auto-hide after 4 seconds. show_toast also has a per-style
            # auto-dismiss timer in the subprocess; this is a belt-and-braces
            # cleanup if the user never dismisses and that timer fails.
            import time as _t
            _t.sleep(4)
            # Only this style, so a newer message or offer is left alone.
            self.overlay.hide_toast(style="error")
            self._notifier.release("no_audio")
        except Exception as e:
            _log_to_file(f"[overlay] no-audio toast failed: {e}")

    def _process(self, processing_id: int):
        """Process audio: transcribe, style, copy to clipboard, paste.

        Every blocking step runs through this dictation's watchdog run
        (src/pipeline_watchdog.py): a hung provider, a hung paste or an
        exception on a worker thread ends in a tick or a plain message, never
        in a pill that sits on "Processing". The working pill's X can stop it
        at any point before the paste; Esc can while the "still working"
        offer is on screen, and then the recording or words are kept."""

        # One watchdog run per dictation. It shows the working pill at once
        # and is ended in the finally below with how the dictation ended.
        run = self._watchdog.begin(processing_id)
        _outcome = _pw.NOTHING

        # These two were previously one predicate, which is what discarded
        # finished work: pressing the hotkey again while the previous dictation
        # was still processing looked identical to the user cancelling it, so
        # the completed transcript was thrown away without ever being saved.
        # They mean different things and now have different consequences (see
        # src/pipeline_policy.py).
        def _is_superseded():
            """A NEWER recording has started. This generation must not paste,
            because the newer one owns the focus and the clipboard, but what it
            produced is still the user's words."""
            with self._processing_lock:
                return processing_id != self._processing_id

        def _is_cancelled_explicitly():
            """The user cancelled THIS generation (the overlay's X or Cancel)."""
            with self._processing_lock:
                if processing_id != self._processing_id:
                    return False
                return self._processing_cancelled.is_set()

        def _is_cancelled():
            """Abort-early predicate for the stages BEFORE a result exists.
            Up to that point there is nothing worth preserving, so either
            condition should stop the work. A run the watchdog gave up on is
            finished too: its recording has been kept as Not sent."""
            return _is_superseded() or _is_cancelled_explicitly() or run.abandoned

        # Snapshot the paste target NOW. self._prev_window is overwritten by
        # the next press, so reading it later could send this dictation's paste
        # into the window the user opened for the following one.
        _target_window = self._prev_window
        # The app that was in front when this press began (for the Journal
        # and the clean-up), and the name of this take's kept recording once
        # there is one.
        _target_ctx = self._press_target(processing_id)
        _target_app = _target_ctx.name
        run.app_context = _target_ctx
        _ring_name = ""
        # Set once the styled text is on the clipboard (not after a paste
        # that put the user's own clipboard back). After that point the error
        # handler must not "salvage" the raw transcript over it.
        _clipboard_written = False
        _pasted = False    # set when the words went into the user's app
        transcript = None  # for the error handler, whatever fails first
        styled = None
        _history_written = False   # this dictation is in the Journal
        try:
            # Calculate recording duration for error suppression
            import time
            recording_duration = time.time() - self._recording_start_time if self._recording_start_time else 0
            _log_to_file(f"Recording duration: {recording_duration:.2f}s")

            # Early abort if already cancelled. (Every exit from here on hands
            # its outcome to the watchdog in the finally below, which sets the
            # window's status and ends the pill, but only while this dictation
            # still owns them. The old per-exit notify_js_status("idle") calls
            # also fired for a superseded dictation and reset the NEXT
            # recording's "Recording" label.)
            if _is_cancelled():
                _log_to_file(f"Processing {processing_id} aborted: cancelled before start")
                _outcome = _pw.CANCELLED if _is_cancelled_explicitly() else _pw.NOTHING
                return

            # v3.14.36 — silently discard very short taps (< 0.5 s).
            # Two failure modes this prevents:
            #   1. Spam-Fn → 20 "We couldn't hear you" toasts stacking up
            #   2. Spam-Fn → 20 transcription API calls → rate limit hit
            #      almost immediately on Groq free tier (20 req/min)
            # 0.5 s is comfortably below any deliberate dictation but well
            # above the typical Fn-mistap duration. We still drain the
            # audio buffer (otherwise the next press would start with
            # stale samples) and ping the JS status back to idle, but
            # everything downstream — stop_audio's RMS check, toast,
            # transcription, styling, history — is skipped entirely.
            #
            # Note: the previous byte-count check (< 0.3 s) at this site
            # was *dead code*: the 500 ms pre-roll buffer guarantees every
            # recording has > 0.3 s of bytes regardless of physical
            # press duration. The duration field is what actually tracks
            # press-to-release time, so the check moves up here.
            transcript = None  # init for error handler
            _log_to_file("[pipeline] stopping audio capture...")
            # Bounded: stop() waits on the recorder's stream lock, which a
            # stream rebuild on a dead device could hold indefinitely.
            _stopped = run.call(self.audio.stop, stage=_pw.PREPARING,
                                deadline=_pw.PREPARE_DEADLINE_S)
            if _stopped.status == _pw.CANCEL:
                _log_to_file(f"Processing {processing_id} cancelled while stopping the recording")
                _outcome = _pw.CANCELLED
                return
            if _stopped.status == _pw.DEADLINE and _stopped.late is not None:
                # The recording is finished; only stop() is still waiting for
                # the recorder (a device being rebuilt can hold its lock).
                # Before the deadline existed the dictation waited for it and
                # went through, so its bytes are not thrown away now: when
                # stop() returns they are kept as Not sent and sent from there.
                _log_to_file(f"[pipeline] stopping the recording took over "
                             f"{_pw.PREPARE_DEADLINE_S:.0f}s; it will be kept if it finishes")
                _stopped.late.when_done(self._keep_late_recording)
                try:
                    if self._toast_allowed("mic_slow_to_stop"):
                        self.overlay.show_toast(
                            style="warn", heading="Your mic was slow to stop",
                            body="Nothing was pasted. If the recording comes through, "
                                 "Waffler keeps it in the Journal and sends it from there.")
                except Exception:
                    pass
                _outcome = _pw.ERROR
                return
            if not _stopped.ok:
                raise RuntimeError(
                    f"stopping the recording did not finish ({_stopped.status}"
                    f"{': ' + repr(_stopped.error) if _stopped.error else ''})")
            audio_bytes = _stopped.value
            run.audio_bytes = audio_bytes or None
            run.set_audio_seconds(len(audio_bytes or b"") / 32000.0)
            # The stop cue, now the recording is safely captured: sounding
            # earlier would put it in the recording. Not if a newer press has
            # started recording (its audio is not ours to silence).
            if not _is_superseded() and not self.is_recording:
                self._play_cue(_sound_cues.STOP)

            # Accidental-tap guard. This used to discard ANY press under 500 ms
            # on press duration alone, before looking at the audio at all, so a
            # deliberate short answer ("Yes", "No", a date, a number) was
            # deleted outright: no transcription, no history, no toast, not
            # even debug audio. A brush of the hotkey and a real one-word
            # dictation are only distinguishable by what was actually captured,
            # so the decision now rests on measured speech.
            if recording_duration < 0.5:
                try:
                    _tap_speech = _speech_seconds(audio_bytes) if audio_bytes else 0.0
                except Exception:
                    _tap_speech = 0.0
                if _tap_speech < _MIN_TAP_SPEECH_S:
                    _log_to_file(
                        f"Short press ({recording_duration:.2f}s) with "
                        f"{_tap_speech:.2f}s of speech — discarding as accidental tap"
                    )
                    # Say what to do, once in a while (src/hold_hint.py).
                    self._maybe_hold_hint(processing_id, recording_duration)
                    return
                _log_to_file(
                    f"Short press ({recording_duration:.2f}s) but {_tap_speech:.2f}s "
                    f"of real speech — transcribing it rather than discarding"
                )
            _log_to_file(f"[pipeline] audio captured: {len(audio_bytes) if audio_bytes else 0} bytes")
            # Keep the last few recordings on disk (LOCAL ONLY — same privacy
            # class as history.json; never leaves the machine, excluded from
            # the Download Logs bundle). Exists because "the transcript is
            # missing half my speech" reports are undiagnosable without the
            # audio: v3.14.78 proved this once, then the capability was
            # removed and the next incident (2026-07-29: 57s of speech -> 18
            # words) was guesswork again. Date-stamped names + mtime pruning
            # fix v3.14.78's rotation bug (HHMMSS-only names sorted wrongly
            # across days and deleted the newest files).
            # 3.15: said in Settings, Privacy and data, with a switch to stop
            # keeping them (keep_recent_audio) and "Delete now" (src/recent_audio.py).
            try:
                if audio_bytes:
                    _stored = {}
                    _sf = DATA_DIR / "settings.json"
                    try:
                        if _sf.exists():
                            _stored = json.loads(_sf.read_text(encoding="utf-8-sig"))
                    except Exception:
                        pass
                    _kept = _recent_audio.keep(DATA_DIR, audio_bytes, _stored)
                    if _kept is not None:
                        _ring_name = _kept.name
                        run.audio_name = _ring_name
                        # Marked as being turned into text: if Waffler stops
                        # before the dictation ends, the next start brings
                        # it back as a card (src/take_marker.py).
                        _take_marker.note_safely(DATA_DIR, _ring_name, _log_to_file)
            except Exception as _e:
                _log_to_file(f"[pipeline] debug-audio save failed: {_e}")
            if not audio_bytes:
                _log_to_file("No audio bytes captured")
                # Only show error toast if recording was held for > 1 second
                if recording_duration >= 1.0:
                    threading.Thread(target=self._show_no_audio_toast, daemon=True).start()
                return

            # Check if audio is effectively silent.
            # Windowed peak-RMS: if ANY short window passes, proceed.
            # 0.25 s windows (was 1.0 s) stop a 0.5 s "hello" from being
            # diluted by surrounding silence to below threshold.
            # RMS threshold 12 (was 30) matches quieter mics / soft speech
            # without letting genuine room tone through — room tone
            # typically sits around 3-8 on a well-gained mic.
            try:
                import numpy as np
                audio_arr = np.frombuffer(audio_bytes[44:], dtype=np.int16).astype(np.float32)
                samples_per_window = 4000  # 0.25 s at 16 kHz
                min_rms = 12.0
                is_silent = True
                for i in range(0, len(audio_arr), samples_per_window):
                    window = audio_arr[i:i + samples_per_window]
                    if len(window) < 400:  # skip a tiny trailing chunk (< 25 ms)
                        break
                    wrms = float(np.sqrt(np.mean(window ** 2)))
                    if wrms >= min_rms:
                        is_silent = False
                        break
                if is_silent:
                    overall_rms = float(np.sqrt(np.mean(audio_arr ** 2)))
                    _log_to_file(f"Audio too quiet (overall RMS={overall_rms:.0f}, no 250ms window >= {min_rms})")
                    # DEAD-STREAM DETECTION: a real mic in a silent room always
                    # has a noise floor of ~3-10. overall RMS ≈ 0 means the
                    # stream is delivering zero-filled buffers — the device went
                    # stale (Mac sleep/wake, mic hot-swap). The stream stays
                    # ".active" so start() keeps reusing it and EVERY following
                    # recording is silent too, until the user force-quits. Break
                    # the loop: hard-rebuild the stream so the next press
                    # re-acquires the device, and tell the user to retry.
                    rms_is_dead = overall_rms < 1.0 and len(audio_arr) > 4000
                    if rms_is_dead:
                        _log_to_file("Dead audio stream (RMS≈0) — rebuilding so next press re-acquires the mic")
                        try:
                            self.audio.force_rebuild()
                        except Exception as _e:
                            _log_to_file(f"force_rebuild failed: {_e}")
                    if recording_duration >= 1.0:
                        if rms_is_dead:
                            if self._toast_allowed("mic_reset"):
                                threading.Thread(
                                    target=lambda: self.overlay.show_toast(
                                        style="warn",
                                        heading="Mic reset",
                                        body="Your mic stopped responding after sleep or a device "
                                             "change. It has been reset: press and speak again.",
                                    ),
                                    daemon=True,
                                ).start()
                        else:
                            _log_to_file("Showing 'couldn't hear you' toast")
                            threading.Thread(target=self._show_no_audio_toast, daemon=True).start()
                    else:
                        _log_to_file("Suppressing error toast (quick tap)")
                    return
            except Exception:
                pass  # If numpy check fails, continue with transcription

            # PARTIAL-DEAD-STREAM DETECTION + per-recording audio diagnostics.
            # The fully-silent check above only fires when the WHOLE recording
            # is dead. But the mic stream can also go dead PART-WAY through a
            # long hands-free recording — CoreAudio keeps the stream ".active"
            # but starts handing back zero-filled buffers. The first half
            # transcribes fine; the back half is digital silence, so Whisper
            # returns only ~half the words.
            #
            # The original test — ">=30% of windows are digital silence" —
            # assumed a real mic always has a noise floor, so exact zeros could
            # only mean a dead stream. Modern capture breaks that assumption:
            # noise suppression (Windows Voice Focus, headset DSP, Krisp-style
            # filters) emits EXACT zeros whenever you are not speaking, so
            # pausing to think looked identical to the mic dying. Measured over
            # 861 real recordings it fired 4 times and was wrong all 4 times —
            # every one transcribed completely, at 1.56-3.05 words per second
            # of live audio, while telling the user to re-record.
            #
            # What separates the two is SHAPE, not amount: gated pauses are many
            # short dead runs with speech after each, a dead stream is one long
            # run that never recovers. mic_dropout_signal() measures that, and
            # even then we do not alarm the user on audio alone — a speaker who
            # stops talking before releasing the hotkey also ends on silence.
            # The warning is deferred until the transcript can confirm it.
            self._mic_dropout = None
            try:
                import numpy as _np
                from src.quality import mic_dropout_signal as _mic_signal
                _arr = _np.frombuffer(audio_bytes[44:], dtype=_np.int16).astype(_np.float32)
                _win = 4000  # 0.25 s
                _rms_windows = []
                for _i in range(0, len(_arr), _win):
                    _w = _arr[_i:_i + _win]
                    if len(_w) < 400:
                        break
                    _rms_windows.append(float(_np.sqrt(_np.mean(_w ** 2))))
                _sig = _mic_signal(_rms_windows, window_s=_win / 16000.0)
                _speech = sum(1 for _r in _rms_windows if _r >= 12.0)
                _log_to_file(
                    f"[pipeline] audio diag: {recording_duration:.1f}s, "
                    f"{len(_rms_windows)} windows, "
                    f"digital-silence={_sig['dead_fraction']*100:.0f}%, "
                    f"speech-windows={_speech}, "
                    f"longest-dead-run={_sig['longest_dead_run_s']:.1f}s, "
                    f"terminal={_sig['terminal']}"
                )
                if _sig["suspected"]:
                    _log_to_file(
                        f"⚠️  Possible dead stream: {_sig['longest_dead_run_s']:.1f}s "
                        f"unbroken digital silence to end of take. Rebuilding for "
                        f"next press; warning deferred until the transcript is in."
                    )
                    # Rebuilding is cheap and harmless, so it happens on
                    # suspicion. Only the user-facing alarm waits for evidence.
                    try:
                        self.audio.force_rebuild()
                    except Exception as _e:
                        _log_to_file(f"force_rebuild (partial) failed: {_e}")
                    self._mic_dropout = _sig
            except Exception:
                pass

            # Check cancellation before expensive transcription
            if _is_cancelled():
                _log_to_file(f"Processing {processing_id} aborted: cancelled before transcription")
                _outcome = _pw.CANCELLED if _is_cancelled_explicitly() else _pw.NOTHING
                return

            # Transcribe. Bounded by the watchdog run: the pill shows the
            # elapsed time, the "still working" offer comes after
            # max(8 s, 0.4 x the recording), and speech to text as a whole
            # has one deadline (pipeline_watchdog.transcribe_deadline_s). A
            # single request used to be able to run for 240 s, a fallback and
            # a retry could chain three, and nothing could stop it.
            _t0 = time.time()
            _asr = run.call(self._transcribe_with_provenance, audio_bytes,
                            stage=_pw.TRANSCRIBING,
                            deadline=_pw.transcribe_deadline_s(run.audio_seconds))
            if _asr.status == _pw.CANCEL and not run.cancel_keeps:
                _log_to_file(f"Processing {processing_id} cancelled during transcription")
                _outcome = _pw.CANCELLED
                return
            if not _asr.ok:
                # No transcription engine could turn the audio into text: a
                # failure (often a VPN exit IP that Groq blocks), the
                # deadline, the user chose "Send later", or Esc while the
                # offer was up. The recording is kept as a Not sent card in
                # the Journal, and sent again when the provider answers
                # (src/unsent.py); one cancelled with Esc waits for Try again.
                if _asr.status == _pw.CANCEL:
                    _why = _unsent.REASON_CANCELLED
                elif _asr.status == _pw.SEND_LATER:
                    _why = _unsent.REASON_LATER
                elif _asr.status == _pw.DEADLINE:
                    _why = (f"deadline: no answer in "
                            f"{_pw.transcribe_deadline_s(run.audio_seconds):.0f}s")
                else:
                    _why = str(_asr.error)
                _log_to_file(f"[pipeline] transcription not finished ({_asr.status}), "
                             f"keeping the recording: {_why[:160]}")
                run.enter(_pw.SAVING)
                if not run.abandoned:
                    _uid = self._handle_failed_transcription(audio_bytes, _why,
                                                             audio_name=_ring_name,
                                                             app_context=_target_ctx)
                    run.saved_as_unsent = True
                    # The request itself is still running and will probably
                    # be billed: its answer goes into the card, and nothing
                    # sends this recording again while it runs.
                    self._collect_late_words(_uid, _asr.late, run.audio_seconds)
                _outcome = _pw.CANCELLED if _asr.status == _pw.CANCEL else _pw.NOT_SENT
                return
            transcript, _asr_info = _asr.value
            _t_transcribe = (time.time() - _t0) * 1000
            _log_to_file(f"[pipeline] transcription: {_t_transcribe:.0f}ms")
            if not transcript:
                _log_to_file("Empty transcription result")
                # Only show error toast if recording was held for > 1 second
                if recording_duration >= 1.0:
                    _log_to_file("Showing 'couldn't hear you' toast")
                    threading.Thread(target=self._show_no_audio_toast, daemon=True).start()
                else:
                    _log_to_file("Suppressing error toast (quick tap)")
                return
            run.transcript = transcript

            # Apply vocabulary fuzzy matching corrections. What changed is
            # kept with the Journal entry ("vocab_changes") so the card can
            # show it; before 3.15 it was only in app.log.
            # Never fatal: a Vocabulary that cannot be applied must not cost
            # the words (as Try again already did).
            _vocab_changes = []
            try:
                from transcribe_whisper import load_vocab, load_sounds_like, apply_vocab_changes
                vocab = load_vocab()
                if vocab:
                    # With no "sounds like" saved, the same call as before 3.15.
                    _sounds = load_sounds_like(vocab)
                    _fixed, _vocab_changes = (
                        apply_vocab_changes(transcript, vocab, _sounds) if _sounds
                        else apply_vocab_changes(transcript, vocab))
                    transcript = _fixed
                    run.transcript = transcript
                    if _vocab_changes:
                        _log_to_file("Vocabulary corrections applied: " + ", ".join(
                            f"'{h}' → '{u}'" for h, u in _vocab_changes))
            except Exception as e:
                _vocab_changes = []
                _log_to_file(f"Vocabulary step skipped ({type(e).__name__}: {e})")

            # Record Whisper usage - calculate from audio bytes (works for all backends)
            # Audio is 16kHz, 16-bit mono = 32000 bytes/second
            whisper_duration = len(audio_bytes) / 32000.0
            whisper_provider = _asr_info.get("backend") or self.transcriber._backend
            if whisper_provider in ("mlx", "faster"):
                whisper_provider = "local"
            elif whisper_provider == "api":
                whisper_provider = "openai"
            if whisper_duration > 0:
                record_usage_safely("whisper", duration_seconds=whisper_duration,
                                    provider=whisper_provider)

            # Style. The styler stops itself at 30 s; the watchdog run is the
            # backstop, and lets the user choose "Paste as is" once the wait
            # passes the offer threshold. Any failure here pastes the words
            # as they were said rather than failing the dictation.
            _t1 = time.time()
            # The Style section (Settings, Style), read once for this
            # dictation: clean-up On or Off, a style for the kind of app, the
            # email signature (src/style_layer.py).
            _settings = self._read_settings()
            # A dictation that is only a snippet's trigger is that snippet:
            # no model call, and nothing to clean up (src/snippets.py).
            _whole_snippet = self._whole_snippet(transcript)
            if _whole_snippet is not None:
                _sty = _pw.CallResult("ok", value=(_whole_snippet, {
                    "input_tokens": 0, "output_tokens": 0, "api_used": False,
                    "provider": "snippet"}))
            elif _style_layer.cleanup_is_off(_settings):
                # Clean-up is off: no model is asked. The words are what the
                # speech service heard (after the Vocabulary above and the
                # Snippets below), with nothing taken out: not even the
                # filler words the plain tidy-up removes. Its own provider
                # tag, so nothing reads it as a clean-up that failed.
                _sty = _pw.CallResult("ok", value=(transcript.strip(), _style_layer.off_usage()))
            else:
                _sty = run.call(self.styler.style, transcript, stage=_pw.STYLING,
                                deadline=_pw.STYLE_DEADLINE_S, app_context=_target_ctx)
            if _sty.status == _pw.CANCEL and not run.cancel_keeps:
                _log_to_file(f"Processing {processing_id} cancelled during styling")
                _outcome = _pw.CANCELLED
                return
            if _sty.ok:
                styled, gpt_usage = _sty.value
            else:
                # Includes Esc while the offer was up: the words exist, so
                # they are kept (as said) and saved below, but not pasted.
                styled = self._unstyled(transcript)
                gpt_usage = {"input_tokens": 0, "output_tokens": 0,
                             "api_used": False, "provider": "basic_clean"}
                if _sty.status == _pw.DEADLINE:
                    gpt_usage["fallback_reason"] = (
                        f"TIMEOUT|clean-up took longer than {_pw.STYLE_DEADLINE_S:.0f}s - pasted raw")
                elif _sty.status == "error":
                    gpt_usage["fallback_reason"] = f"{type(_sty.error).__name__}: {_sty.error}"
                _log_to_file(f"[pipeline] styling not used ({_sty.status}); "
                             f"pasting the words as said")
            _t_style = (time.time() - _t1) * 1000
            _log_to_file(f"[pipeline] styling ({gpt_usage.get('provider', 'local')}): {_t_style:.0f}ms")
            if not styled:
                styled = transcript

            # Say so when the words were pasted without the clean-up, in
            # plain words (src/user_messages.py cleanup_skipped_message):
            # "Clean-up paused for about 17 minutes" after a limit, and a
            # sentence for a block, no connection, running out of time or no
            # key. The styler's raw reason stays in the log. Not after Esc:
            # that dictation gets its own message below.
            _as_said = ""
            if gpt_usage.get("fallback_reason") and not run.cancel_keeps:
                reason = gpt_usage["fallback_reason"]
                heading, body = cleanup_skipped_message(reason)
                try:
                    _pause = _cleanup_pause.from_reason(reason, datetime.now())
                    if _pause:
                        _as_said = _cleanup_pause.LIMIT_TAG
                        _set_cleanup_pause(_pause)
                except Exception as _e:
                    _log_to_file(f"[pipeline] clean-up pause note failed: {_e}")
                _log_to_file(f"[pipeline] styling fell back to basic_clean: {reason}")
                try:
                    if self._toast_allowed("cleanup_skipped"):
                        self.overlay.show_toast(style="warn", heading=heading, body=body)
                except Exception as _e:
                    _log_to_file(f"[pipeline] fallback toast failed: {_e}")

            # Record GPT usage (if API was used)
            if gpt_usage.get("api_used"):
                record_usage_safely(
                    "gpt",
                    input_tokens=gpt_usage.get("input_tokens", 0),
                    output_tokens=gpt_usage.get("output_tokens", 0),
                    provider=gpt_usage.get("provider", "openai"),
                )

            # The style for this kind of app, as code (src/style_post.py), then
            # the snippets (text expansion). Neither touches a whole-snippet
            # dictation: its text is already the expansion, a trigger inside
            # it must not set another off, and it is pasted exactly. The
            # style comes first so that what a snippet pastes is never
            # restyled, and it is not applied when the clean-up is off
            # ("exactly what was heard").
            _style_used = ""
            if _whole_snippet is None:
                if gpt_usage.get("provider") != _style_layer.PROVIDER_OFF:
                    styled, _style_used = self._style_words(styled, _target_ctx, _settings)
                styled = self._apply_snippets(styled)
            # The words the speaker said, finished: what the quality checks
            # and the word count look at. The signature is added below.
            _body = styled
            # The email signature (opt-in): under a new email in an email
            # app. Not for a snippet, and not for a dictation that will not be
            # pasted because a newer one has started or this one was cancelled.
            if not (_is_superseded() or _is_cancelled_explicitly() or run.abandoned or run.cancelled):
                styled, _ = self._add_signature(run, styled, _target_ctx, _settings,
                                                snippet=_whole_snippet is not None)

            # One decision, taken once, for both remaining side effects.
            # Pasting is about the present (whose window and clipboard is
            # this?); keeping is about the past (did the user get words out of
            # it?). Deciding them together under the lock also closes the gap
            # where a cancel landing between two separate checks let the paste
            # through anyway.
            #
            # This used to come after an early "if _is_cancelled(): return"
            # that also fired for a SUPERSEDED dictation, so pressing the
            # hotkey again while one was still being cleaned up threw its
            # finished words away, the very loss src/pipeline_policy.py was
            # written to stop. Only an explicit cancel discards now. It also
            # comes before the copy: a superseded dictation must not write
            # the clipboard either, because the newer one owns it. A run the
            # watchdog gave up on is treated like a superseded one.
            from src.pipeline_policy import decide as _decide
            with self._processing_lock:
                _superseded = processing_id != self._processing_id or run.abandoned
                _cancelled = ((not _superseded) and self._processing_cancelled.is_set()) \
                    or run.cancelled
            # Esc while the offer was up is treated like a superseded one too:
            # the words are kept in the Journal and nothing is pasted, because
            # Esc also reaches the app in front and may not have been meant
            # for Waffler. A click on the X or Cancel still discards.
            _esc_kept = run.cancelled and run.cancel_keeps
            if _esc_kept:
                _superseded, _cancelled = True, False
            _policy = _decide(superseded=_superseded, cancelled=_cancelled)

            if not _policy["save_history"]:
                _log_to_file(f"Processing {processing_id} discarded: cancelled by the user")
                _outcome = _pw.CANCELLED
                return

            # Paste. One step, bounded by the watchdog run: a clipboard that
            # stays locked or a paste keystroke that never returns used to
            # hold the dictation (and the History save after it) for good.
            # On a Mac it keeps the user's own clipboard, puts the words on it
            # for a moment, pastes, and puts it back (src/pasteboard.py);
            # either way it says whether the paste went in. A failed clipboard
            # write never goes on to "paste", which once replaced the user's
            # selection with whatever unrelated text was on the clipboard
            # already. The transcript is saved to History below either way, so
            # the words are never lost.
            _t2 = time.time()
            _t_paste = 0.0
            _step_ran = False
            # It is kept either way (only a cancel returned above): "Paste
            # last" can reach it, whatever goes wrong below.
            self._remember(styled)
            _paste_label = _history_meta.PASTE_NOT_PASTED     # for the Journal entry
            if _policy["paste"]:
                stored = self._read_settings()
                if stored.get("auto_paste", True):
                    # _target_window, not self._prev_window: the latter now
                    # belongs to whatever the user pressed most recently.
                    _ticket = _pasteboard.PasteTicket()
                    _paste = run.call(self.clipboard.paste_text, styled, _target_window,
                                      restore=_pasteboard.restore_after_paste(stored),
                                      ticket=_ticket, stage=_pw.PASTING,
                                      deadline=_pw.PASTE_DEADLINE_S)
                    # Where the words are now: after a restore they are not on
                    # the clipboard any more, and the messages must not say so.
                    _clipboard_written, _pasted = self._settle_paste(_paste, _ticket)
                    _paste_label = _history_meta.paste_label(_paste, _pasted)
                    _step_ran = True
                else:
                    # Auto-paste is off: the words just go on the clipboard.
                    _copy = run.call(self.clipboard.copy, styled, stage=_pw.PASTING,
                                     deadline=_pw.PASTE_DEADLINE_S)
                    _clipboard_written = bool(_copy.ok and _copy.value)
                    _paste_label = (_history_meta.PASTE_COPIED if _clipboard_written
                                    else _history_meta.PASTE_FAILED)
                    _step_ran = _clipboard_written
                    if not _clipboard_written:
                        _log_to_file(f"Clipboard write FAILED ({_copy.status})")
                        try:
                            if self._toast_allowed("clipboard_copy_failed"):
                                threading.Thread(target=lambda: self.overlay.show_toast(
                                    style="warn", heading="Couldn't copy to the clipboard",
                                    body="Your text is saved in the Journal. Copy it from there.",
                                ), daemon=True).start()
                        except Exception:
                            pass
            elif _policy["reason"] != "ok":
                _log_to_file(
                    f"Processing {processing_id} not pasted ({_policy['reason']})"
                    + ("; transcript still saved to History"
                       if _policy["save_history"] else "")
                )

            if _step_ran:
                _t_paste = (time.time() - _t2) * 1000
                _log_to_file(f"[pipeline] clipboard+paste: {_t_paste:.0f}ms")
                _log_to_file(f"[pipeline] TOTAL: {_t_transcribe + _t_style + _t_paste:.0f}ms")

            # Save to history.
            #
            # PROVENANCE — "text" is the FILTERED transcript, not the raw
            # speech-recognition response. transcribe_sync() applies the
            # hallucination filter and the vocab-echo / boilerplate discards
            # before returning, so what the UI labels "original" has always
            # been a processed artifact. When filtering actually changed
            # something we now also store the untouched provider words under
            # "asr_text" so a filtering mistake stays recoverable, and stamp
            # "text_is" so the meaning of this field is explicit rather than
            # assumed. Entries WITHOUT "text_is" pre-date this change: their
            # "text" is also filtered, but by an older filter whose exact
            # behaviour is not recorded — they must not be presented as
            # recovered originals.
            #
            # The provenance comes from _asr_info, a snapshot taken on the
            # transcription's own thread the moment it returned. Reading the
            # transcriber's attributes here instead could pick up a later
            # dictation's values, now that a request given up on can finish in
            # the background.
            run.enter(_pw.SAVING)
            item = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "text": transcript,
                "styled": styled,
                # The words dictated: a signature added under them is not.
                "word_count": len(_body.split()),
                "text_is": "asr_filtered",
            }
            # What else is known about this take (src/history_meta.py), every
            # field optional: its length, the speech in it, the language, who
            # heard and tidied it, what became of the paste, the app, and its
            # kept recording ("Redo this take").
            try:
                item.update(_history_meta.take_fields(
                    audio_seconds=whisper_duration,
                    speech_seconds=_asr_info.get("last_speech_seconds", 0.0),
                    language=self._read_settings().get("language", "en"),
                    stt=whisper_provider, cleanup=(gpt_usage or {}).get("provider", ""),
                    paste=_paste_label, app=_target_app, audio=_ring_name,
                    app_bundle=_target_ctx.bundle_id, category=_target_ctx.category,
                    style=_style_used))
            except Exception as _e:
                _log_to_file(f"[pipeline] take details not kept: {type(_e).__name__}")
            if _as_said:
                # The Journal tags it "As said: limit reached".
                item["as_said"] = _as_said
            if _vocab_changes:
                # The Journal shows "Vocabulary: Malek → Malak" under the card.
                item["vocab_changes"] = [[h, u] for h, u in _vocab_changes]
            try:
                _asr_raw = _asr_info.get("last_asr_response", "") or ""
                if _asr_info.get("last_asr_filtered", False) and _asr_raw != transcript:
                    item["asr_text"] = _asr_raw
                    _log_to_file(
                        f"[pipeline] ASR filter changed the transcript "
                        f"({len(_asr_raw.split())} -> {len(transcript.split())} words); "
                        f"untouched response preserved in history"
                    )
            except Exception as _e:
                _log_to_file(f"[pipeline] provenance capture failed: {_e}")

            # A suspected mic dropout is only reported once the transcript
            # agrees something is missing. If the words came back at a normal
            # rate for the audio that DID have signal, nothing was lost and a
            # "please re-record" toast would be a false alarm — which is what
            # every previous firing of this warning turned out to be.
            try:
                if getattr(self, "_mic_dropout", None):
                    _live_s = max(
                        0.1,
                        recording_duration * (1.0 - self._mic_dropout["dead_fraction"]),
                    )
                    _wps = len((transcript or "").split()) / _live_s
                    if _wps < 1.0:
                        if self._toast_allowed("mic_dropped_out"):
                            threading.Thread(
                                target=lambda: self.overlay.show_toast(
                                    style="warn",
                                    heading="Mic dropped out",
                                    body="Your mic cut out partway through - some of this "
                                         "may be missing. Mic reset; please re-record.",
                                ),
                                daemon=True,
                            ).start()
                        _log_to_file(
                            f"[pipeline] mic dropout CONFIRMED by transcript "
                            f"({_wps:.2f} words/live-second)"
                        )
                    else:
                        _log_to_file(
                            f"[pipeline] mic dropout suspected but transcript is "
                            f"healthy ({_wps:.2f} words/live-second) - no warning shown"
                        )
            except Exception as _e:
                _log_to_file(f"[pipeline] dropout confirmation failed: {_e}")

            # ── Quality signals ────────────────────────────────────────────
            # Computed locally from MEASURED audio and from what the pipeline
            # actually did - never from a judgement about the prose, because
            # reading a transcript cannot reveal what is missing from it.
            # Flag, never block: the paste already happened above.
            try:
                from src.quality import assess as _assess
                _q = _assess(
                    speech_seconds=_asr_info.get("last_speech_seconds", 0.0),
                    transcript_words=len((transcript or "").split()),
                    styled_words=len((_body or "").split()),
                    asr_filtered=_asr_info.get("last_asr_filtered", False),
                    styling_provider=(gpt_usage or {}).get("provider", ""),
                    styled_text=_body or "",
                    retry_fired=_asr_info.get("last_retry_fired", False),
                    deadline_fired="TIMEOUT" in str((gpt_usage or {}).get("fallback_reason", "")),
                    retry_rejected=_asr_info.get("last_retry_rejected", False),
                    # Casual and very casual drop the last full stop on
                    # purpose; a signature is not the end of what was said.
                    ending_dropped_on_purpose=_style_used in ("casual", "very_casual"),
                )
                if _q["level"] != "ok":
                    item["quality"] = _q
                    _log_to_file(
                        f"[quality] {_q['level']}: {','.join(_q['flags'])} "
                        f"({_q['words_per_speech_second']} w/s)"
                    )
                # Metadata-only quality log. Deliberately contains NO transcript
                # text: it sits beside history.json but must stay safe to read,
                # share and aggregate. Bounded so it cannot grow without limit.
                try:
                    _qlog = DATA_DIR / "quality.jsonl"
                    if _qlog.exists() and _qlog.stat().st_size > 2_000_000:
                        _keep = _qlog.read_text(encoding="utf-8").splitlines()[-5000:]
                        _qlog.write_text("\n".join(_keep) + "\n", encoding="utf-8")
                    with open(_qlog, "a", encoding="utf-8") as _f:
                        _f.write(json.dumps({
                            "timestamp": item["timestamp"],
                            "level": _q["level"],
                            "flags": _q["flags"],
                            "speech_s": round(float(
                                _asr_info.get("last_speech_seconds", 0.0) or 0.0), 1),
                            "transcript_words": len((transcript or "").split()),
                            "styled_words": len((_body or "").split()),
                            "words_per_speech_second": _q["words_per_speech_second"],
                            "styling_provider": (gpt_usage or {}).get("provider", ""),
                            "asr_provider": _asr_info.get("_last_cloud_provider", "") or "",
                        }) + "\n")
                except Exception as _e:
                    _log_to_file(f"[quality] log write failed: {_e}")
            except Exception as _e:
                _log_to_file(f"[quality] assessment failed: {_e}")
            _saved = append_history_safely(item)
            _history_written = _saved
            if _esc_kept:
                # Said only while this dictation still owns the pill.
                if self._run_is_current(run) and self._toast_allowed("cancelled_kept"):
                    try:
                        self.overlay.show_toast(
                            style="warn", heading="Cancelled",
                            body=("Nothing was pasted. Your words are in the Journal if "
                                  "you want them after all." if _saved else
                                  "Nothing was pasted, and Waffler couldn't save your "
                                  "words to the Journal."),
                            buttons=([{"label": "Show in Journal", "action": "open_journal",
                                       "kind": "primary"},
                                      {"label": "Dismiss", "action": "dismiss",
                                       "kind": "secondary"}] if _saved else None))
                    except Exception:
                        pass
            elif not _saved:
                # The words were pasted (or are on the clipboard); only the
                # History copy is missing. Say so, and leave the clipboard.
                try:
                    if self._toast_allowed("history_not_saved"):
                        threading.Thread(target=lambda: self.overlay.show_toast(
                            style="warn", heading="Not saved to History",
                            body="Your text was pasted, but Waffler couldn't save it to History.",
                        ), daemon=True).start()
                except Exception:
                    pass

            # Notify JS. The "done" status itself (and the pill's tick) comes
            # from the watchdog in the finally below, only while this
            # dictation still owns the pill.
            if _saved:
                notify_js_new_item(item)

            # Metadata only. app.log is what the "Download Logs" diagnostic
            # bundle ships, so logging content here would leak the user's
            # dictations to anyone they send logs to. Setting
            # logging.log_transcripts: true adds the styled text on the line
            # below - opt-in, for chasing truncation bugs like v3.14.78's.
            _log_to_file(f"Done: {len(_body.split())} words, {len(_body)} chars")
            if _transcripts_loggable():
                _log_to_file(f"Styled text: {styled}")
            _outcome = _pw.CANCELLED if _esc_kept else _pw.DONE

            # A dictation just went through, so the speech service answers:
            # send any recordings that are waiting (src/unsent.py).
            self._drain_unsent_soon("a dictation just went through")

        except Exception as e:
            error_msg = str(e)
            _log_to_file(f"Pipeline error: {error_msg}")
            import traceback
            traceback.print_exc()
            _outcome = _pw.ERROR

            # Nothing was turned into text yet, but the recording exists:
            # keep it as Not sent rather than lose it.
            if not transcript and run.audio_bytes and not run.saved_as_unsent:
                try:
                    self._handle_failed_transcription(run.audio_bytes, error_msg,
                                                      audio_name=_ring_name,
                                                      app_context=_target_ctx)
                    run.saved_as_unsent = True
                    _outcome = _pw.NOT_SENT
                except Exception as _e:
                    _log_to_file(f"[pipeline] could not keep the recording: {_e}")

            # The words exist but something after speech to text failed:
            # put them in the Journal, so they are not lost with the error.
            _journal_saved = _history_written
            if transcript and not _history_written and not run.saved_as_unsent:
                _words = styled or transcript
                try:
                    self._remember(_words)    # so "Paste last" is these words
                except Exception:
                    pass
                _entry = {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "text": transcript,
                    "styled": _words,
                    "word_count": len(_words.split()),
                }
                try:
                    _entry.update(_history_meta.take_fields(
                        audio_seconds=run.audio_seconds, app=_target_app, audio=_ring_name,
                        app_bundle=_target_ctx.bundle_id, category=_target_ctx.category))
                except Exception:
                    pass
                _journal_saved = append_history_safely(_entry)
                if _journal_saved:
                    _log_to_file("[pipeline] the words were kept in the Journal after the error")
                    try:
                        notify_js_new_item(_entry)
                    except Exception:
                        pass

            # Show user-visible error toast with specific message
            try:
                # Only genuine mic-level errors get the `error` style with
                # the Select-mic button. Everything else uses `warn` (single
                # Dismiss) so the action matches the problem.
                if _outcome == _pw.NOT_SENT:
                    pass    # _handle_failed_transcription has said so
                elif "RATE_LIMIT" in error_msg or "429" in error_msg:
                    # The provider named in "RATE_LIMIT|<limit>|<wait>|<details>"
                    # and its wait, in plain words: "Groq says you've reached
                    # your limit for now. Try again in about 17 minutes."
                    heading, body = limit_reached_message(error_msg)
                    if self._toast_allowed("limit_reached"):
                        self.overlay.show_toast(style="warn", heading=heading, body=body)
                elif "CONNECTION" in error_msg or "Connection error" in error_msg or "timeout" in error_msg.lower():
                    if self._toast_allowed("connection_failed"):
                        self.overlay.show_toast(
                            style="warn",
                            heading="Connection failed",
                            body="Couldn't reach the server. Check your internet or VPN.",
                        )
                elif "403" in error_msg or "Access denied" in error_msg:
                    # Most common cause on a working install is a VPN exit IP
                    # that Groq blocks at the network layer. Without an
                    # OpenAI key set, transcription has no fallback and the
                    # whole pipeline 403s. Tell the user the actual fix.
                    _has_openai = bool(
                        os.environ.get("OPENAI_API_KEY") or os.environ.get("openai_api_key")
                    )
                    if _has_openai:
                        body = "API key may be invalid, or your VPN server's IP is blocked. Try a different VPN server."
                    else:
                        body = "Your VPN server's IP is blocked by Groq. Switch to a different VPN server/location and try again."
                    if self._toast_allowed("access_denied"):
                        self.overlay.show_toast(
                            style="warn",
                            heading="Access denied",
                            body=body,
                        )
                    # Salvage: if a transcript already existed when the error
                    # hit, don't throw the user's words away — copy the raw
                    # text to the clipboard so it's at least recoverable.
                    # Never over the styled text already put there.
                    if transcript and not _clipboard_written and not _pasted:
                        try:
                            self.clipboard.copy(transcript)
                        except Exception:
                            pass
                else:
                    # Still try to salvage: paste the raw transcript, unless
                    # the styled text is already on the clipboard. The toast
                    # used to say the text was copied even when there was no
                    # text at all.
                    _salvaged = False
                    if transcript and not _clipboard_written and not _pasted:
                        try:
                            _salvaged = bool(self.clipboard.copy(transcript))
                        except Exception:
                            pass
                    if self._toast_allowed("pipeline_error"):
                        self.overlay.show_toast(
                            style="warn",
                            heading="Something went wrong",
                            body=_words_kept_message(_salvaged or _clipboard_written,
                                                     _journal_saved,
                                                     pasted=_pasted and not _clipboard_written),
                        )
            except Exception:
                pass
        finally:
            # Every exit, early return or exception, ends this dictation's
            # watchdog run exactly once. That sets the window's status, ends
            # the working pill (with a tick when it worked) and the tray
            # icon, so nothing can be left showing "processing". If the
            # watchdog already gave up on this run, this does nothing.
            try:
                self._watchdog.end(run, _outcome)
            except Exception as _e:
                _log_to_file(f"[pipeline] could not end the dictation cleanly: {_e}")
            # However it ended, this take is no longer waiting for its words
            # (a hard stop never gets here: that is what the mark is for).
            if _ring_name:
                _take_marker.clear_safely(DATA_DIR, _ring_name, _log_to_file)

    # ── Steps the watchdog runs on worker threads ────────────────────────

    def _transcribe_with_provenance(self, audio_bytes: bytes):
        """transcribe_sync, plus a snapshot of what it recorded about itself,
        taken on the same thread the moment it returns. A request the
        watchdog gave up on can finish later in the background and overwrite
        the transcriber's attributes; the snapshot keeps each dictation's
        provenance and quality signals its own."""
        t = self.transcriber
        text = t.transcribe_sync(audio_bytes)
        info = {name: getattr(t, name, default) for name, default in (
            ("last_asr_response", ""), ("last_asr_filtered", False),
            ("last_speech_seconds", 0.0), ("last_retry_fired", False),
            ("last_retry_rejected", False), ("_last_cloud_provider", ""),
            ("_backend", ""),
        )}
        info["backend"] = info.pop("_backend")
        return text, info

    def _unstyled(self, transcript: str) -> str:
        """The words as said, with only the regex tidy-up the styler falls
        back to itself. Used for "Paste as is" and when the clean-up fails."""
        try:
            return self.styler._format_email_layout(self.styler._basic_clean(transcript))
        except Exception:
            return transcript

    def _speech_provider_name(self) -> str:
        """The speech-to-text provider a dictation goes to first, by name."""
        t = getattr(self, "transcriber", None)
        if t is None:
            return ""
        for prov in (getattr(t, "_cloud_order", None) or ["groq", "openai"]):
            if prov == "groq" and getattr(t, "_groq_client", None) is not None:
                return "Groq"
            if prov == "openai" and getattr(t, "client", None) is not None:
                return "OpenAI"
        return ""

    def _show_journal(self):
        """Bring the window forward on the Journal (a toast's "Show in
        Journal" button)."""
        global _window_hidden
        if _window is None:
            return
        try:
            _window_hidden = False
            _window.show()
            if hasattr(_window, "restore"):
                _window.restore()
        except Exception as e:
            _log_to_file(f"[open_journal] window restore failed: {e}")
        notify_js_window_visible(True)
        _post_js("if (typeof showPage === 'function') showPage('home');")

    # ── What the user sees while a dictation is processed ─────────────────
    # Callbacks for the PipelineWatchdog made in __init__. They run on the
    # dictation's thread (begin, finished) or the watchdog's (the rest).

    def _run_is_current(self, run) -> bool:
        """The pill and the window's status belong to ``run`` until a newer
        recording starts."""
        with self._processing_lock:
            return run.generation == self._processing_id and not self.is_recording

    def _set_listener_processing(self, active: bool):
        """Arm Esc for the dictation being processed (the listener passes it
        to _on_hotkey_cancel). Armed only while the "still working" offer is
        on screen; the rest of the time Esc belongs to the app in front."""
        listener = getattr(self, "hotkey_listener", None)
        if listener is not None and hasattr(listener, "set_processing"):
            try:
                listener.set_processing(active)
            except Exception as e:
                _log_to_file(f"[hotkey] set_processing failed: {e}")

    def _ui_begin(self, run):
        if not self._run_is_current(run):
            return
        # Esc is not armed here: straight after letting go it is far more
        # likely meant for the app in front. _ui_offer arms it.
        notify_js_status("processing")
        _set_tray_state(_tray_state.WORKING)
        try:
            # The recording pill turns into the working pill: it stays on
            # screen with the elapsed time and one X, instead of vanishing.
            self.overlay.show_working(0.0, reliable=True)
        except Exception as e:
            _log_to_file(f"[overlay] working pill not shown: {e}")

    def _ui_working(self, run, seconds):
        self.overlay.show_working(seconds)

    def _ui_offer(self, run, stage):
        """The wait passed max(8 s, 0.4 x audio): say so, and offer choices."""
        provider = self._speech_provider_name() or "The speech service"
        if stage == _pw.TRANSCRIBING:
            heading = "Still working on it"
            body = (f"{provider} is slow to answer. Your recording is safe: "
                    f"keep waiting, or let Waffler send it later.")
            buttons = [
                {"label": "Keep waiting", "action": _pw.KEEP_WAITING, "kind": "primary"},
                {"label": "Send later", "action": _pw.SEND_LATER, "kind": "secondary"},
                {"label": "Cancel", "action": "cancel_processing", "kind": "danger"},
            ]
        elif stage == _pw.STYLING:
            heading = "Still cleaning up"
            body = ("The clean-up is slow. You can paste your words as you "
                    "said them instead.")
            buttons = [
                {"label": "Paste as is", "action": _pw.PASTE_RAW, "kind": "primary"},
                {"label": "Keep waiting", "action": _pw.KEEP_WAITING, "kind": "secondary"},
                {"label": "Cancel", "action": "cancel_processing", "kind": "danger"},
            ]
        else:
            return
        if self._toast_allowed("still_working"):
            self.overlay.show_toast(style="info", heading=heading, body=body, buttons=buttons)
        # While the offer is up, Esc stops the wait (keeping the recording).
        run.offer_shown()
        self._set_listener_processing(True)

    def _ui_withdraw_offer(self, run):
        if self._run_is_current(run):
            self._set_listener_processing(False)
            # Only an "info" toast: a message that replaced the offer stays.
            self.overlay.hide_toast(style="info")
            self._notifier.release("still_working")

    def _ui_finished(self, run, outcome):
        """End of a dictation, exactly once: a tick, or back to Ready."""
        if not self._run_is_current(run):
            return   # a newer recording owns the pill and the status now
        self._set_listener_processing(False)
        try:
            self.overlay.end_working("done" if outcome == _pw.DONE else "quiet")
        except Exception as e:
            _log_to_file(f"[overlay] working pill not ended: {e}")
        if outcome == _pw.DONE:
            notify_js_status("done")
        elif outcome == _pw.CANCELLED:
            notify_js_status("cancelled")
        elif outcome == _pw.NOT_SENT:
            notify_js_status("not_sent")
        elif outcome in (_pw.ERROR, _pw.STUCK):
            notify_js_status("error")
        else:
            notify_js_status("idle")
        _set_tray_state(_tray_state.NOT_SENT if outcome == _pw.NOT_SENT
                        else _tray_state.IDLE)

    def _ui_stuck(self, run):
        """The watchdog gave up on a dictation that stopped making progress
        outside any bounded step. Keep what exists of the user's words."""
        saved = False
        if run.audio_bytes and not run.transcript and not run.saved_as_unsent:
            run.saved_as_unsent = True
            saved = bool(self._handle_failed_transcription(
                run.audio_bytes, _unsent.REASON_STUCK, toast=False,
                audio_name=getattr(run, "audio_name", ""),
                app_context=getattr(run, "app_context", None)))
        if not self._run_is_current(run):
            return
        heading, body = _unsent.toast_text(_unsent.REASON_STUCK, saved=True)
        if not saved:
            body = ("Waffler stopped waiting. If your words come through, "
                    "they'll be in the Journal.")
        try:
            if self._toast_allowed("pipeline_stuck"):
                self.overlay.show_toast(style="warn", heading=heading, body=body)
        except Exception:
            pass

    # ── Recordings that were not sent (src/unsent.py) ─────────────────────

    def _save_unsent_recording(self, audio_bytes: bytes):
        """Persist the raw WAV of a recording we couldn't transcribe to
        ``unsent/`` in the data folder so it is never lost. ``audio_bytes`` is
        already a complete WAV (44-byte header + PCM), so it is written
        verbatim. Returns the Path, or None on failure. Names are unique:
        two failures in the same second used to share one file name, and the
        second overwrote the first."""
        try:
            unsent_dir = DATA_DIR / _unsent.UNSENT_DIRNAME
            unsent_dir.mkdir(parents=True, exist_ok=True)
            wav_path = _unsent.new_unsent_path(unsent_dir)
            with open(wav_path, "xb") as f:
                f.write(audio_bytes)
            _log_to_file(
                f"[pipeline] saved unsent recording: {wav_path.name} "
                f"({len(audio_bytes)} bytes)"
            )
            return wav_path
        except Exception as e:
            _log_to_file(f"[pipeline] failed to save unsent recording: {e}")
            return None

    def _handle_failed_transcription(self, audio_bytes: bytes, reason: str,
                                     toast: bool = True, audio_name: str = "",
                                     app_context=None) -> str:
        """Speech to text produced no text (no connection, a VPN block, a
        limit, the watchdog's deadline, or the user chose "Send later").

        The recording is saved and a "Not sent" card goes into the Journal
        with Try again, Show the file and Delete (ui/app.js). Waffler also
        sends it again by itself once the provider answers (_drain_unsent).
        Returns the recording's unsent id, or "" when it could not be saved.
        """
        wav_path = self._save_unsent_recording(audio_bytes)
        kind = _unsent.classify_reason(reason)
        provider = self._speech_provider_name()
        uid = wav_path.name if wav_path else ""

        # Journal entry. The raw error stays in "error" for the logs; the
        # card shows a plain sentence built from not_sent_reason.
        try:
            item = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "text": "",
                "styled": _unsent.STYLED_NOTE if uid else _unsent.STYLED_NOTE_NO_FILE,
                "word_count": 0,
                "failed": True,
                "error": str(reason)[:200],
                "audio_path": str(wav_path) if wav_path else "",
                "unsent_id": uid,
                "not_sent_reason": kind,
                "provider_name": provider,
            }
            item["will_retry"] = bool(uid) and _unsent.will_auto_retry(item)
            # How long it was, and which kept recording it is, so a card that
            # a crash left behind is not made again at the next start
            # (src/take_marker.py).
            try:
                ctx = app_context or _text_context.UNKNOWN
                item.update(_history_meta.take_fields(
                    audio_seconds=max(0, len(audio_bytes) - 44) / 32000.0,
                    audio=audio_name, app=ctx.name, app_bundle=ctx.bundle_id,
                    category=ctx.category))
            except Exception:
                pass            # the card matters more than its details
            append_history(item)
            if uid and _unsent.is_waiting(item):
                self._unsent_waiting += 1
            try:
                notify_js_new_item(item)
            except Exception:
                pass
        except Exception as e:
            _log_to_file(f"[pipeline] failed to journal failed transcription: {e}")

        if toast:
            heading, body = _unsent.toast_text(kind, provider, saved=bool(uid))
            buttons = None
            if uid:
                buttons = [
                    {"label": "Show in Journal", "action": "open_journal", "kind": "primary"},
                    {"label": "Dismiss", "action": "dismiss", "kind": "secondary"},
                ]
            try:
                if self._toast_allowed("not_sent"):
                    self.overlay.show_toast(style="warn", heading=heading, body=body,
                                            buttons=buttons)
            except Exception:
                pass
        return uid

    def _count_unsent(self) -> int:
        try:
            return len(_unsent.pending(load_history(), DATA_DIR / _unsent.UNSENT_DIRNAME,
                                       include_cancelled=False))
        except Exception:
            return 0

    def _replace_unsent_entry(self, unsent_id: str, new_entry) -> bool:
        """Swap the Not sent entry for ``unsent_id`` with ``new_entry`` (or
        remove it when ``new_entry`` is None). True when saved."""
        try:
            with _history_lock:
                history = _load_history_for_update()
                idx, _entry = _unsent.find_entry(history, unsent_id)
                if idx < 0:
                    return False
                if new_entry is None:
                    del history[idx]
                else:
                    history[idx] = new_entry
                save_history(history)
            return True
        except Exception as e:
            _log_to_file(f"[unsent] Journal not updated ({type(e).__name__}: {e})")
            return False

    def resend_unsent(self, unsent_id: str, auto: bool = False) -> dict:
        """Send a saved recording again: speech to text, clean-up, and the
        words into its Journal card. Nothing is pasted.

        Returns {"ok": True, "item": ...} once the card holds the words, or
        {"ok": False, "reason": ..., "item": ...}. Bounded like a dictation,
        so a provider that still does not answer cannot hang it.

        While a request Waffler stopped waiting for is still running for this
        recording, Try again waits for that answer instead of sending the
        recording (and paying for it) a second time, and the automatic
        sending leaves it alone.
        """
        with self._unsent_lock:
            try:
                history = load_history()
            except Exception:
                history = []
            _idx, entry = _unsent.find_entry(history, unsent_id)
            if entry is None:
                return {"ok": False, "reason": "not_found"}
            path = _unsent.resolve_file(DATA_DIR / _unsent.UNSENT_DIRNAME, unsent_id)
            if path is None:
                return {"ok": False, "reason": "missing", "item": entry}
            late = self._in_flight(unsent_id)
            if late is not None and auto:
                return {"ok": False, "reason": "in_flight", "item": entry}
            try:
                audio_bytes = path.read_bytes()
            except Exception as e:
                _log_to_file(f"[unsent] could not read {unsent_id}: {e}")
                return {"ok": False, "reason": "missing", "item": entry}

            _log_to_file(f"[unsent] sending {unsent_id} again "
                         f"({'automatic' if auto else 'Try again'})")
            run = _pw.DictationRun(f"resend:{unsent_id}")
            run.set_audio_seconds(len(audio_bytes) / 32000.0)
            deadline = _pw.transcribe_deadline_s(run.audio_seconds)
            asr = None
            if late is not None:
                _log_to_file(f"[unsent] {unsent_id}: an earlier request is still running; "
                             f"waiting for its answer instead of sending it again")
                if not late.wait(deadline):
                    asr = _pw.CallResult(_pw.DEADLINE)
                elif late.error is None:
                    asr = _pw.CallResult("ok", value=late.value)
                # That request failed: send the recording again below.
            if asr is None:
                asr = run.call(self._transcribe_with_provenance, audio_bytes,
                               stage=_pw.TRANSCRIBING, deadline=deadline)
                if not asr.ok:
                    self._collect_late_words(unsent_id, asr.late, run.audio_seconds)
            transcript = asr.value[0] if asr.ok and asr.value else ""
            if not asr.ok or not transcript:
                if asr.ok:
                    kind = _unsent.REASON_EMPTY   # it went through, but nothing was heard
                elif asr.status == _pw.DEADLINE:
                    kind = _unsent.REASON_TIMEOUT
                else:
                    kind = _unsent.classify_reason(str(asr.error))
                _log_to_file(f"[unsent] {unsent_id} still not sent ({asr.status}, {kind})")
                updated = _unsent.with_attempt(entry, auto=auto, reason=kind)
                if kind == _unsent.REASON_EMPTY:
                    updated["will_retry"] = False
                if self._replace_unsent_entry(unsent_id, updated):
                    notify_js_item_updated(unsent_id, updated)
                return {"ok": False, "reason": kind, "item": updated}
            return self._fill_unsent_card(unsent_id, entry, path, asr.value,
                                          run.audio_seconds)

    def _tidy_transcript(self, transcript: str, info: dict, audio_seconds: float,
                         label: str, style: bool = True, app_context=None):
        """The words a recording sent again is turned into: the Vocabulary,
        what the speech cost, the clean-up (or none, when it is off), the
        style for the kind of app it was dictated in and the snippets, as a
        dictation gets (but never pasted, and never with the email signature,
        which is for a paste). Used by Try again on a Not sent card and by
        "Redo this take". ``label`` names the step for the watchdog;
        ``app_context`` is the app the take was dictated in, if its entry
        recorded one (the clean-up is told, as for a dictation).

        Returns (transcript, styled, vocab_changes, stt, cleanup, style): the
        words as heard (after the Vocabulary), as cleaned up, what the
        Vocabulary changed, who heard and who tidied them, and the style the
        words were finished in (``""`` for none)."""
        vocab_changes = []
        try:
            from transcribe_whisper import load_vocab, load_sounds_like, apply_vocab_changes
            vocab = load_vocab()
            if vocab:
                sounds = load_sounds_like(vocab)
                transcript, vocab_changes = (
                    apply_vocab_changes(transcript, vocab, sounds) if sounds
                    else apply_vocab_changes(transcript, vocab))
        except Exception as e:
            _log_to_file(f"[unsent] vocabulary step skipped: {e}")
        provider = (info or {}).get("backend") or ""
        provider = {"api": "openai", "mlx": "local", "faster": "local"}.get(provider, provider)
        record_usage_safely("whisper", duration_seconds=audio_seconds,
                            provider=provider or "groq")

        styled = ""
        cleanup = ""
        settings = self._read_settings()
        # A recording that is only a snippet's trigger is that snippet, as in
        # _process: no clean-up, and its text is not searched for triggers.
        whole_snippet = self._whole_snippet(transcript)
        if whole_snippet is not None:
            styled = whole_snippet
            cleanup = "snippet"
        elif _style_layer.cleanup_is_off(settings):
            # Off, as in _process: the words as heard, no model.
            styled = transcript.strip()
            cleanup = _style_layer.PROVIDER_OFF
        elif style:
            run = _pw.DictationRun(label)
            sty = run.call(self.styler.style, transcript, stage=_pw.STYLING,
                           deadline=_pw.STYLE_DEADLINE_S, app_context=app_context)
            if sty.ok and sty.value and sty.value[0]:
                styled, usage = sty.value
                cleanup = usage.get("provider", "")
                if usage.get("api_used"):
                    record_usage_safely("gpt", input_tokens=usage.get("input_tokens", 0),
                                        output_tokens=usage.get("output_tokens", 0),
                                        provider=usage.get("provider", "openai"))
        if not styled:
            styled = self._unstyled(transcript)
            cleanup = cleanup or "basic_clean"
        style_used = ""
        if whole_snippet is None:
            if cleanup != _style_layer.PROVIDER_OFF:
                styled, style_used = self._style_words(styled, app_context, settings)
            styled = self._apply_snippets(styled)
        return transcript, styled, vocab_changes, (provider or "groq"), cleanup, style_used

    def _fill_unsent_card(self, unsent_id: str, entry: dict, path, asr_value,
                          audio_seconds: float, style: bool = True) -> dict:
        """Speech to text answered for a Not sent recording: tidy the words,
        put them into its card (a normal Journal entry from then on) and
        remove the file. Call with _unsent_lock held."""
        transcript, info = asr_value
        transcript, styled, vocab_changes, stt, cleanup, style_used = self._tidy_transcript(
            transcript, info, audio_seconds, f"fill:{unsent_id}", style,
            app_context=_history_meta.context_of(entry))

        new_item = _unsent.resolved(entry, transcript=transcript, styled=styled)
        if vocab_changes:
            new_item["vocab_changes"] = [[h, u] for h, u in vocab_changes]
        try:
            new_item.update(_history_meta.take_fields(
                speech_seconds=(info or {}).get("last_speech_seconds", 0.0),
                language=self._read_settings().get("language", "en"),
                stt=stt, cleanup=cleanup, style=style_used))
        except Exception as e:
            _log_to_file(f"[unsent] take details not kept: {type(e).__name__}")
        if not self._replace_unsent_entry(unsent_id, new_item):
            return {"ok": False, "reason": "not_saved", "item": entry}
        if path is not None:
            try:
                path.unlink()
            except Exception as e:
                _log_to_file(f"[unsent] sent, but {unsent_id} could not be removed: {e}")
        self._unsent_waiting = self._count_unsent()
        _log_to_file(f"[unsent] {unsent_id} sent: {len(styled.split())} words")
        notify_js_item_updated(unsent_id, new_item)
        if self._unsent_waiting == 0 and _tray_state_now == _tray_state.NOT_SENT:
            _set_tray_state(_tray_state.IDLE)
        return {"ok": True, "item": new_item}

    def _replace_history_entry(self, old: dict, new: dict) -> bool:
        """Swap a Journal entry (found by id, else by time and words) for
        ``new``, in one atomic write. True when saved."""
        try:
            with _history_lock:
                history = _load_history_for_update()
                idx = _history_ops.find(history, old.get("id"), old.get("timestamp", ""),
                                        old.get("text", ""))
                if idx < 0:
                    return False
                history[idx] = new
                save_history(history)
            _history_cache.invalidate()
            return True
        except Exception as e:
            _log_to_file(f"[history] entry not replaced ({type(e).__name__}: {e})")
            return False

    def redo_take(self, item_id=None, timestamp="", text="") -> dict:
        """"Redo this take": send an entry's kept recording (src/recent_audio.py)
        through speech to text and the clean-up again, and put the new words
        in its place. Like Try again on a Not sent card, nothing is pasted,
        and it fails the same way (no connection, a block, a limit, the
        watchdog's deadline). If it does, or nothing is heard, the entry
        is left as it was. The old words are kept for Undo for a few seconds.

        Returns {"ok": True, "item", "undo"} or {"ok": False, "reason"}.
        """
        # Never alongside a dictation: they share the clean-up's one client
        # (the drain of Not sent cards leaves them alone the same way).
        if self.is_recording or self._watchdog.active():
            return {"ok": False, "reason": "busy"}
        # One resend at a time, whichever kind.
        if not self._unsent_lock.acquire(timeout=2.0):
            return {"ok": False, "reason": "busy"}
        try:
            try:
                history = load_history()
            except Exception:
                history = []
            idx = _history_ops.find(history, item_id, timestamp, text)
            if idx < 0 or history[idx].get("failed") or not _history_meta.is_dictation(history[idx]):
                return {"ok": False, "reason": "not_found"}     # a rewrite or a command has no take to redo
            entry = history[idx]
            path = _recent_audio.resolve(DATA_DIR, entry.get("audio"))
            if path is None:
                return {"ok": False, "reason": "missing"}
            try:
                audio_bytes = path.read_bytes()
            except Exception as e:
                _log_to_file(f"[redo] could not read the recording: {e}")
                return {"ok": False, "reason": "missing"}

            _log_to_file("[redo] sending a kept recording again")
            run = _pw.DictationRun(f"redo:{path.name}")
            run.set_audio_seconds(len(audio_bytes) / 32000.0)
            asr = run.call(self._transcribe_with_provenance, audio_bytes,
                           stage=_pw.TRANSCRIBING,
                           deadline=_pw.transcribe_deadline_s(run.audio_seconds))
            transcript = asr.value[0] if asr.ok and asr.value else ""
            if not asr.ok or not transcript:
                if asr.ok:
                    kind = _unsent.REASON_EMPTY      # it went through, nothing was heard
                elif asr.status == _pw.DEADLINE:
                    kind = _unsent.REASON_TIMEOUT
                else:
                    kind = _unsent.classify_reason(str(asr.error))
                _log_to_file(f"[redo] not done ({asr.status}, {kind}); the entry is as it was")
                return {"ok": False, "reason": kind}
            info = asr.value[1]
            transcript, styled, changes, stt, cleanup, style_used = self._tidy_transcript(
                transcript, info, run.audio_seconds, f"redo:{path.name}",
                app_context=_history_meta.context_of(entry))
            new_item = _history_meta.redone_entry(
                entry, transcript=transcript, styled=styled, vocab_changes=changes,
                redone_at=datetime.now().isoformat(timespec="seconds"),
                speech_seconds=(info or {}).get("last_speech_seconds", 0.0),
                language=self._read_settings().get("language", "en"),
                stt=stt, cleanup=cleanup, style=style_used)
            if not self._replace_history_entry(entry, new_item):
                return {"ok": False, "reason": "not_saved"}
            token = _history_ops.SHELF.put(_history_ops.REPLACED, entry, replaced_by=new_item)
            self.swap_last_text(_history_meta.words_of(entry), _history_meta.words_of(new_item))
            _log_to_file(f"[redo] done: {len(styled.split())} words")
            return {"ok": True, "item": dict(new_item, can_redo=True), "undo": token}
        finally:
            self._unsent_lock.release()

    # ── Answers that arrive after Waffler stopped waiting ─────────────────
    # DictationRun.call stops waiting at a deadline or a choice, but the step
    # runs on until its own timeout (at least 60 s for a speech request, past
    # the 45 s deadline). A speech request answered then has been billed, and
    # its words used to be thrown away while the automatic resend sent (and
    # billed) the same audio again.

    def _in_flight(self, unsent_id: str):
        """The request still running for this recording, or None."""
        with self._in_flight_lock:
            late = self._unsent_in_flight.get(unsent_id)
        return late if late is not None and not late.done else None

    def _collect_late_words(self, unsent_id: str, late, audio_seconds: float):
        """Keep a speech request Waffler stopped waiting for. While it runs
        nothing sends this recording again, and the words it brings back go
        into the recording's Not sent card."""
        if not unsent_id or late is None:
            return
        with self._in_flight_lock:
            self._unsent_in_flight[unsent_id] = late
        late.when_done(lambda value, error: self._late_words_arrived(
            unsent_id, late, value, error, audio_seconds))

    def _late_words_arrived(self, unsent_id: str, late, value, error,
                            audio_seconds: float):
        """The request finished after Waffler had stopped waiting for it."""
        try:
            if error is not None:
                _log_to_file(f"[unsent] the late request for {unsent_id} failed too "
                             f"({type(error).__name__}); the card stays")
                return
            with self._unsent_lock:
                try:
                    history = load_history()
                except Exception:
                    history = []
                _idx, entry = _unsent.find_entry(history, unsent_id)
                if entry is None:
                    _log_to_file(f"[unsent] words for {unsent_id} arrived after its card "
                                 f"was sent or deleted")
                    return
                transcript = value[0] if value else ""
                if not transcript:
                    # It was heard, and nothing was said: don't pay to send it again.
                    updated = _unsent.with_attempt(entry, auto=False,
                                                   reason=_unsent.REASON_EMPTY)
                    updated["will_retry"] = False
                    if self._replace_unsent_entry(unsent_id, updated):
                        notify_js_item_updated(unsent_id, updated)
                    self._unsent_waiting = self._count_unsent()
                    _log_to_file(f"[unsent] the late request for {unsent_id} heard no words")
                    return
                path = _unsent.resolve_file(DATA_DIR / _unsent.UNSENT_DIRNAME, unsent_id)
                # The clean-up never competes with a dictation for the styler:
                # while one is running, the words go in as said.
                busy = self.is_recording or bool(self._watchdog.active())
                result = self._fill_unsent_card(unsent_id, entry, path, value,
                                                audio_seconds, style=not busy)
                if result.get("ok"):
                    _log_to_file(f"[unsent] {unsent_id}: the late answer filled its card")
        except Exception as e:
            _log_to_file(f"[unsent] late answer for {unsent_id} not used "
                         f"({type(e).__name__}: {e})")
        finally:
            with self._in_flight_lock:
                if self._unsent_in_flight.get(unsent_id) is late:
                    del self._unsent_in_flight[unsent_id]

    def _keep_late_recording(self, value, error):
        """stop() returned after the dictation stopped waiting for it (see
        _process): keep the finished recording as Not sent, and send it."""
        if error is not None or not value:
            why = f" ({type(error).__name__})" if error is not None else ""
            _log_to_file(f"[pipeline] the slow stop ended without a recording{why}")
            return
        try:
            speech = _speech_seconds(value)
        except Exception:
            speech = _MIN_TAP_SPEECH_S     # can't tell: keep it rather than lose it
        if speech < _MIN_TAP_SPEECH_S:
            _log_to_file("[pipeline] the slow stop's recording has no speech in it; not kept")
            return
        uid = self._handle_failed_transcription(value, _unsent.REASON_STUCK, toast=False)
        if not uid:
            return
        _log_to_file(f"[pipeline] the slow stop's recording is kept as {uid}")
        if not self.is_recording and not self._watchdog.active():
            _set_tray_state(_tray_state.NOT_SENT)
        self._drain_unsent_soon("a recording that was slow to stop was kept")

    def delete_unsent(self, unsent_id: str) -> dict:
        """Delete a Not sent recording and its Journal card."""
        # A resend holds this lock for as long as the provider takes; the
        # card should not freeze behind it, so say it is busy instead.
        if not self._unsent_lock.acquire(timeout=2.0):
            return {"ok": False, "reason": "busy"}
        try:
            if not _unsent.is_valid_id(unsent_id):
                return {"ok": False, "reason": "not_found"}
            path = _unsent.resolve_file(DATA_DIR / _unsent.UNSENT_DIRNAME, unsent_id)
            removed = self._replace_unsent_entry(unsent_id, None)
            if path is not None:
                try:
                    path.unlink()
                except Exception as e:
                    _log_to_file(f"[unsent] could not delete {unsent_id}: {e}")
                    return {"ok": False, "reason": "locked"}
            if removed or path is not None:
                self._unsent_waiting = self._count_unsent()
                _log_to_file(f"[unsent] {unsent_id} deleted by the user")
                return {"ok": True}
            return {"ok": False, "reason": "not_found"}
        finally:
            self._unsent_lock.release()

    def _drain_unsent(self, why: str, ignore_backoff: bool = False):
        """Send waiting recordings again, oldest first, within the limits in
        src/unsent.py. Stops at the first one the provider still refuses, so
        a service that is down is not hammered. Never runs while a dictation
        is recording or being processed."""
        if not self._drain_lock.acquire(blocking=False):
            return
        try:
            try:
                history = load_history()
            except Exception:
                return
            waiting = _unsent.pending(history, DATA_DIR / _unsent.UNSENT_DIRNAME,
                                      include_cancelled=False)
            self._unsent_waiting = len(waiting)
            # One still on its way (a request Waffler stopped waiting for) is
            # left to that request: sending it again would pay for it twice.
            due = [uid for uid, entry, _p in waiting
                   if _unsent.auto_retry_due(entry, ignore_backoff=ignore_backoff)
                   and self._in_flight(uid) is None]
            if not due:
                return
            _log_to_file(f"[unsent] {len(due)} recording(s) to send again ({why})")
            for uid in due:
                if self.is_recording or self._watchdog.active():
                    _log_to_file("[unsent] a dictation started; sending the rest later")
                    return
                result = self.resend_unsent(uid, auto=True)
                if not result.get("ok") and result.get("reason") in (
                        _unsent.REASON_OFFLINE, _unsent.REASON_BLOCKED,
                        _unsent.REASON_TIMEOUT, _unsent.REASON_RATE_LIMITED):
                    return
        except Exception as e:
            _log_to_file(f"[unsent] drain failed ({type(e).__name__}: {e})")
        finally:
            self._drain_lock.release()

    def _drain_unsent_soon(self, why: str):
        """Start a drain in the background, if anything is waiting. The
        provider just answered, so the usual wait between tries is skipped
        (the limit on tries still applies)."""
        if self._unsent_waiting <= 0:
            return
        threading.Thread(target=self._drain_unsent, args=(why,),
                         kwargs={"ignore_backoff": True}, daemon=True,
                         name="UnsentDrainNow").start()

    def _unsent_drain_loop(self):
        """Every 30 s, while recordings are waiting, try the ones that are
        due. History is only read when the count says something waits."""
        time.sleep(20)
        while True:
            try:
                if self._unsent_waiting > 0 and not self.is_recording \
                        and not self._watchdog.active():
                    self._drain_unsent("the regular check")
            except Exception as e:
                _log_to_file(f"[unsent] check failed: {e}")
            time.sleep(30)

    def _apply_snippets(self, text: str) -> str:
        """Replace snippet triggers in ``text`` with their expansions
        (src/snippets.py). Never fatal: a snippets file that can't be read
        leaves the text as it is."""
        try:
            return _snippets.apply(text, _snippets.load(DATA_DIR / "snippets.json"))
        except Exception as e:
            _log_to_file(f"[snippets] not applied ({type(e).__name__}: {e})")
            return text

    def _whole_snippet(self, text: str):
        """The expansion when the whole dictation is one snippet's trigger
        (any case, any punctuation), else None. That dictation is pasted as
        the snippet and is not sent to the clean-up: there is nothing to
        clean up, and a model must not rephrase a signature."""
        try:
            return _snippets.whole_utterance(text, _snippets.load(DATA_DIR / "snippets.json"))
        except Exception as e:
            _log_to_file(f"[snippets] not checked ({type(e).__name__}: {e})")
            return None

    # ── The Style section (src/style_layer.py, src/style_post.py) ─────────

    def _style_words(self, text: str, ctx, settings=None):
        """``(text, style)``: the style Settings, Style has for the kind of app
        the words are for, applied as code after the clean-up, and the style
        that was applied (``""`` for Auto, and for text or a language the rules
        do not style). Not for a snippet (the caller never asks) and not when
        the clean-up is off. Never fatal: a style that cannot be applied leaves
        the words as they are."""
        try:
            s = settings if isinstance(settings, dict) else self._read_settings()
            category = getattr(ctx, "category", "") or "other"
            if _style_layer.style_for(s, category) == _style_layer.AUTO:
                return text, ""      # today's behaviour: nothing is read, nothing runs
            language = s.get("language", "en")
            language = language if isinstance(language, str) and language else "en"
            from transcribe_whisper import load_vocab
            return _style_layer.finish_style(text, settings=s, category=category, language=language,
                                             protected_words=load_vocab())
        except Exception as e:
            _log_to_file(f"[style] not applied ({type(e).__name__})")
            return text, ""

    def _signature_reader(self, run, ctx):
        """The function the signature decision calls to see the text before the
        cursor: ``read(chars)`` gives that text, ``""`` for an empty field,
        None when it could not be read, or FIELD_MOVED when another app is in
        front now. The one place Waffler reads the field around the cursor, and
        only when the email signature is on and everything else says yes. The
        read goes through the watchdog run; what it finds is compared with the
        signature and dropped, never stored or logged."""
        def _same_app(front):
            if ctx.bundle_id and front.bundle_id:
                return ctx.bundle_id.lower() == front.bundle_id.lower()
            return bool(ctx.name and front.name) and ctx.name.lower() == front.name.lower()

        def read(chars):
            def _read():
                front = _text_context.frontmost_app(read_title=False)
                if front.known and not _same_app(front):
                    return _style_layer.FIELD_MOVED
                before, _after = _text_context.get_text_around_cursor(
                    chars, _style_layer.FIELD_READ_TIMEOUT_S)
                return before
            got = run.call(_read, stage=_pw.STYLING, deadline=_style_layer.FIELD_READ_DEADLINE_S)
            return got.value if got.ok else None
        return read

    def _add_signature(self, run, text: str, ctx, settings=None, snippet: bool = False):
        """``(text, added)``: ``text`` with the email signature under it when
        Settings, Style has one, the app is an email app, the dictation is the
        start of a message and the signature is not there yet (see
        src/style_layer.py decide_signature). Never for a snippet. Never
        fatal."""
        try:
            s = settings if isinstance(settings, dict) else self._read_settings()
            sig = _style_layer.signature_settings(s)
            if not sig["enabled"] or not sig["text"]:
                return text, False
            key = (ctx.bundle_id or ctx.name or "").strip().lower()
            choice = _style_layer.decide_signature(
                settings=s, category=ctx.category, text=text, snippet=snippet,
                read_before=self._signature_reader(run, ctx), app_key=key,
                memory=self._signature_memory)
            if choice.reason not in (_style_layer.REASON_NOT_EMAIL, _style_layer.REASON_DISABLED):
                _log_to_file(f"[signature] {choice.reason}")
            if not choice.add:
                return text, False
            self._signature_memory.note(key)
            return _style_layer.with_signature(text, sig["text"]), True
        except Exception as e:
            _log_to_file(f"[signature] not added ({type(e).__name__})")
            return text, False

    def start_hotkey(self):
        """Start the hotkey listener — platform-specific."""
        try:
            # Load configured keys from settings
            keys = None
            try:
                sf = DATA_DIR / "settings.json"
                if sf.exists():
                    stored = json.loads(sf.read_text(encoding="utf-8-sig"))
                    keys = stored.get("hotkey_keys")
            except Exception:
                pass

            if _platform.system() == "Windows":
                _log_to_file("Creating WindowsHotkeyListener...")
                self.hotkey_listener = WindowsHotkeyListener(
                    on_press=self.on_hotkey_press,
                    on_release=self.on_hotkey_release,
                    on_cancel=self._on_hotkey_cancel,   # Esc (see its docstring)
                    keys=keys,
                    on_lock=self._on_hands_free_lock,
                )
            else:
                _log_to_file(f"Creating SmartHotkeyListener with keys: {keys}")
                self.hotkey_listener = SmartHotkeyListener(
                    on_press=self.on_hotkey_press,
                    on_release=self.on_hotkey_release,
                    on_cancel=self._on_hotkey_cancel,   # Esc (see its docstring)
                    keys=keys,
                    on_lock=self._on_hands_free_lock,
                )
            # The Transform shortcut, if it is on. Before start(): on a Mac it
            # listens through this listener's tap, and on Windows start()
            # does not return until the hook ends.
            self.apply_transform_settings()
            self.apply_command_settings()
            _log_to_file("Calling hotkey.start()...")
            self.hotkey_listener.start()
            if _platform.system() == "Darwin":
                # A permission taken away leaves a listener that gets no key,
                # so the check at key-down never sees it: watch it from here.
                threading.Thread(target=self._watch_listener, daemon=True,
                                 name="ListenerWatch").start()
            self.hotkey_listener.join()
        except Exception as e:
            _log_to_file(f"start_hotkey CRASHED: {e}")
            import traceback
            traceback.print_exc()


# ── System Tray ──────────────────────────────────────────────────────
_tray_icon = None
_window_ref = None
_should_quit = False

# macOS NSStatusItem strong refs — set by _create_mac_menubar_icon().
# Held at module scope so PyObjC's garbage collector doesn't reclaim them
# once the constructor function returns (without these, the menu bar icon
# silently disappears after a GC cycle).
_mac_menubar_status_item = None
_mac_menubar_target = None
_mac_menubar_menu = None

# Dock-reopen support. When the window is closed it hides to the menu bar
# (v3.14.52) and the app keeps running. _window_hidden tracks that state so
# the activation observer below knows to bring the window back when the user
# clicks the Dock icon. _mac_reopen_observer holds the observer (strong ref
# so PyObjC doesn't GC it, same pattern as the menu-bar refs above).
_window_hidden = False
_mac_reopen_observer = None
# A start at sign-in (--hidden) keeps the window hidden. macOS can still
# activate the app while it launches, which the Dock-reopen handler would
# read as a Dock click; activations before this monotonic time are ignored.
_HIDDEN_START_GRACE_S = 5.0
_hidden_start_until = 0.0


def _reopen_should_show(window_hidden: bool, now: float, hidden_start_until: float) -> bool:
    """Whether an app activation should bring the hidden window back."""
    return bool(window_hidden) and now >= hidden_start_until


# "Paste last / Copy last transcript" menu bar items (macOS): (paste, copy).
_mac_last_items = None
# "Transform selected text" (macOS): shown only while Transforms is on.
_mac_transform_item = None


def _transform_async():
    """Menu bar or tray: rewrite the selected text. Off the menu's thread; the
    run itself is on another (the menu thread must never wait for a model)."""
    if _pipeline is not None:
        threading.Thread(target=_pipeline.run_transform, args=("menu",), daemon=True,
                         name="TransformMenu").start()


def _transforms_enabled() -> bool:
    """Is Transforms on? For the menu, read just as it opens. False if the
    app is not ready or settings.json cannot be read."""
    try:
        return bool(_pipeline is not None and _pipeline.transforms_enabled())
    except Exception:
        return False


def _refresh_transform_item():
    """Show or hide "Transform selected text". On the main thread, just
    before the menu opens (its delegate)."""
    if _mac_transform_item is not None:
        _mac_transform_item.setHidden_(not _transforms_enabled())


def _paste_last_async():
    """Menu bar or tray: paste the last transcript, off the menu's thread (the
    safe paste waits a second or so for the app in front)."""
    if _pipeline is not None:
        threading.Thread(target=_pipeline.paste_last, daemon=True, name="PasteLast").start()


def _copy_last_async():
    if _pipeline is not None:
        threading.Thread(target=_pipeline.copy_last, daemon=True, name="CopyLast").start()


def _last_transcript_menu():
    """(paste line, copy line, enabled) for the menu: the word count of the
    last result, never its text (menus show on screen shares). Both lines
    plain and disabled while there is none, or the app is not ready."""
    try:
        if _pipeline is not None:
            return _pipeline.last_transcript_titles()
    except Exception as e:
        _log_to_file(f"[menu] last transcript not read ({type(e).__name__})")
    return last_transcript_title("Paste", 0), last_transcript_title("Copy", 0), False


def _refresh_last_transcript_items():
    """Set the two menu bar lines from the last result. On the main thread:
    just before the menu opens (its delegate), and when the result changes."""
    if not _mac_last_items:
        return
    paste_item, copy_item = _mac_last_items
    paste_title, copy_title, enabled = _last_transcript_menu()
    paste_item.setTitle_(paste_title)
    copy_item.setTitle_(copy_title)
    paste_item.setEnabled_(enabled)
    copy_item.setEnabled_(enabled)


def _refresh_tray_menu():
    """The last result changed: update the menu's lines. The Mac menu also
    refreshes just before it opens; pystray builds the Windows menu once, so
    it is rebuilt here. Never raises."""
    try:
        if _platform.system() == "Windows":
            if _tray_icon is not None:
                _tray_icon.update_menu()
        elif _platform.system() == "Darwin" and _mac_last_items:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_refresh_last_transcript_items)
    except Exception:
        pass


def _create_tray_icon():
    """Create a status-area icon so the app can run in background.
    Windows: pystray system tray icon.
    Mac: rumps menu-bar icon (top-right, next to Wi-Fi/battery).
    """
    if _platform.system() == "Darwin":
        return bool(_create_mac_menubar_icon())
    if _platform.system() == "Windows":
        return bool(_create_windows_tray_icon())
    return False


def _create_mac_menubar_icon():
    """Create a macOS menu bar icon — must be called on the MAIN THREAD
    BEFORE webview.start() blocks the NSRunLoop.

    Uses NSStatusBar / NSStatusItem directly via PyObjC instead of rumps,
    because rumps wants to own the NSApplication (its `app.run()` calls
    NSApplication.shared().run()), which collides head-on with pywebview's
    own NSApp event loop and produces NSInternalInconsistencyException.

    NSStatusItem attaches to whatever NSApp is already running. Once
    registered, the menu's action callbacks are dispatched by the existing
    NSRunLoop — the same one pywebview uses — so menu clicks work
    cooperatively while the app's window is open, hidden, or even fully
    closed. That makes "close the window → app keeps running, click menu
    bar to bring it back" finally work on Mac.
    """
    global _tray_icon, _mac_menubar_status_item, _mac_menubar_target, _mac_menubar_menu
    global _mac_last_items, _mac_transform_item

    try:
        from AppKit import (
            NSStatusBar, NSImage, NSMenu, NSMenuItem, NSVariableStatusItemLength,
        )
        from Foundation import NSObject
        import objc

        # 1) Resolve the menu bar icon. We prefer a *template* image
        # (monochrome PNG with template=true) because that's the macOS
        # convention — it auto-renders correctly in both light and dark
        # menu-bar modes. Fall back to the regular icon if the template
        # asset isn't found.
        icon_path = PROJECT_ROOT / "menubar_icon_template.png"
        is_template = True
        if not icon_path.exists() and hasattr(sys, '_MEIPASS'):
            icon_path = Path(sys._MEIPASS) / "menubar_icon_template.png"
        if not icon_path.exists():
            icon_path = Path(sys.executable).parent / "_internal" / "menubar_icon_template.png"
        if not icon_path.exists():
            # Fall back to full-color icon
            is_template = False
            icon_path = PROJECT_ROOT / "icon.icns"
            if not icon_path.exists() and hasattr(sys, '_MEIPASS'):
                icon_path = Path(sys._MEIPASS) / "icon.icns"
            if not icon_path.exists():
                icon_path = Path(sys.executable).parent / "_internal" / "icon.icns"

        # 2) Create the status item with variable length (so the icon
        # determines its width, not a hardcoded square).
        status_bar = NSStatusBar.systemStatusBar()
        status_item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)

        button = status_item.button()
        if icon_path.exists() and button is not None:
            ns_image = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
            if ns_image is not None:
                # Resize to fit the menu bar (NSStatusBar height ≈ 22pt;
                # 18×18 leaves a touch of padding and matches Slack /
                # Discord menu-bar icons).
                ns_image.setSize_((18, 18))
                ns_image.setTemplate_(is_template)
                button.setImage_(ns_image)
            else:
                # Image load failed — fall back to a textual indicator
                # so the menu is at least findable.
                button.setTitle_("🧇")
        elif button is not None:
            button.setTitle_("🧇")

        # 3) Action-handler NSObject. The selectors look weird (snake-case
        # turned into camelCase with trailing colon and underscore) — that's
        # how PyObjC maps Python identifiers to Objective-C selectors. The
        # underscore at the end of e.g. `show_` becomes the `:` in the
        # Objective-C selector `show:`, marking it as taking one argument
        # (the sender).
        class WafflerMenuTarget(NSObject):
            def show_(self, _sender):  # noqa: N802 — Cocoa selector form
                _tray_show_window()

            def pasteLast_(self, _sender):  # noqa: N802
                _paste_last_async()

            def copyLast_(self, _sender):  # noqa: N802
                _copy_last_async()

            def transformSelected_(self, _sender):  # noqa: N802
                _transform_async()

            def menuNeedsUpdate_(self, _menu):  # noqa: N802 — NSMenuDelegate
                # Just before the menu opens: the word count of the last result.
                try:
                    _refresh_last_transcript_items()
                    _refresh_transform_item()
                except Exception as e:
                    _log_to_file(f"menu refresh failed: {type(e).__name__}")

            def factoryReset_(self, _sender):  # noqa: N802
                _perform_factory_reset()

            def quit_(self, _sender):  # noqa: N802
                _tray_quit()

        target = WafflerMenuTarget.alloc().init()

        # 4) Build the menu. Each item references the target + a selector
        # by name. The empty string key-equivalent means "no keyboard
        # shortcut" — menu-bar shortcuts in a non-frontmost app are
        # finicky on macOS so we leave them off rather than ship a
        # half-broken shortcut.
        menu = NSMenu.alloc().init()
        # The paste last / copy last lines are enabled and titled by hand
        # (menuNeedsUpdate_), not by the system's validation.
        menu.setAutoenablesItems_(False)
        menu.setDelegate_(target)

        item_show = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Show Waffler", b"show:", ""
        )
        item_show.setTarget_(target)
        menu.addItem_(item_show)

        menu.addItem_(NSMenuItem.separatorItem())

        # Paste last / copy last transcript. The title carries a word count,
        # never the text: menus are visible on screen shares.
        item_paste_last = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            last_transcript_title("Paste", 0), b"pasteLast:", ""
        )
        item_paste_last.setTarget_(target)
        item_paste_last.setEnabled_(False)
        menu.addItem_(item_paste_last)

        item_copy_last = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            last_transcript_title("Copy", 0), b"copyLast:", ""
        )
        item_copy_last.setTarget_(target)
        item_copy_last.setEnabled_(False)
        menu.addItem_(item_copy_last)

        # Transform selected text (beta): rewrite the selection in the app in
        # front. Hidden until Transforms is switched on in Settings; the menu
        # delegate shows it as the menu opens.
        item_transform = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Transform selected text", b"transformSelected:", ""
        )
        item_transform.setTarget_(target)
        item_transform.setHidden_(True)
        menu.addItem_(item_transform)

        menu.addItem_(NSMenuItem.separatorItem())

        item_reset = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Factory Reset...", b"factoryReset:", ""
        )
        item_reset.setTarget_(target)
        menu.addItem_(item_reset)

        menu.addItem_(NSMenuItem.separatorItem())

        item_quit = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Waffler", b"quit:", ""
        )
        item_quit.setTarget_(target)
        menu.addItem_(item_quit)

        status_item.setMenu_(menu)

        # 5) Hold strong references on module-level globals so PyObjC's
        # garbage collector doesn't reclaim them once this function
        # returns. Without these, the menu-bar icon vanishes after a
        # garbage-collection cycle.
        _mac_last_items = (item_paste_last, item_copy_last)
        _mac_transform_item = item_transform
        _mac_menubar_status_item = status_item
        _mac_menubar_target = target
        _mac_menubar_menu = menu
        _tray_icon = status_item  # legacy global for _tray_quit() etc.

        _log_to_file("Mac menu bar (NSStatusItem) installed")
        return True
    except Exception as e:
        _log_to_file(f"Mac menu bar error: {e}")
        return False


def _create_windows_tray_icon():
    """Create a Windows system tray icon — bypasses pystray's image pipeline.

    pystray normally converts PIL Image → temp ICO file → LoadImage.
    We monkeypatch _assert_icon_handle to load the HICON directly from
    icon.ico via Win32 LoadImageW, which is the same proven approach
    that works for the window title bar icon.

    Returns True when the icon is up. main() only lets the close button
    hide the window, and a sign-in start begin hidden, when it is: without
    a tray icon a hidden window could only be ended in Task Manager.
    """
    global _tray_icon
    try:
        import pystray
        from pystray._util import win32 as pw32
        from PIL import Image
        import types

        _ico_path = _app_icon_path()
        if _ico_path is None:
            _log_to_file("icon.ico not found for tray icon")
            return False

        _log_to_file(f"Tray icon: using {_ico_path}")
        ico_str = str(_ico_path)
        probe = pw32.LoadImage(None, ico_str, pw32.IMAGE_ICON, 0, 0,
                               pw32.LR_DEFAULTSIZE | pw32.LR_LOADFROMFILE)
        if not probe:
            _log_to_file(f"Tray icon: Windows could not load {_ico_path.name}")
            return False
        try:
            import ctypes
            ctypes.windll.user32.DestroyIcon(probe)
        except Exception:
            pass

        # We still need a PIL Image for pystray's constructor (it stores it),
        # but we'll bypass its ICO serialization when creating the HICON.
        img = Image.open(ico_str).convert('RGBA')

        # Paste last / copy last transcript, with a word count and never the
        # text. pystray calls the text and enabled functions when it (re)builds
        # the menu: _refresh_tray_menu does that when the last result changes.
        menu = pystray.Menu(
            pystray.MenuItem("Show Waffler", _tray_show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item=None: _last_transcript_menu()[0],
                             lambda icon=None, item=None: _paste_last_async(),
                             enabled=lambda item=None: _last_transcript_menu()[2]),
            pystray.MenuItem(lambda item=None: _last_transcript_menu()[1],
                             lambda icon=None, item=None: _copy_last_async(),
                             enabled=lambda item=None: _last_transcript_menu()[2]),
            # Transform selected text (beta), only while Transforms is on.
            pystray.MenuItem("Transform selected text",
                             lambda icon=None, item=None: _transform_async(),
                             visible=lambda item=None: _transforms_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", _tray_quit),
        )
        _tray_icon = pystray.Icon("Waffler", img, "Waffler", menu)

        # Monkeypatch: replace pystray's _assert_icon_handle so it loads
        # the HICON directly from icon.ico instead of PIL→tempICO→LoadImage.
        def _patched_assert_icon_handle(self):
            if self._icon_handle:
                return
            # _set_tray_state swaps in the "working" icon (an amber dot) while
            # a dictation is processed, and back again afterwards.
            path = getattr(self, "_waffler_ico_override", None) or ico_str
            self._icon_handle = pw32.LoadImage(
                None, str(path), pw32.IMAGE_ICON, 0, 0,
                pw32.LR_DEFAULTSIZE | pw32.LR_LOADFROMFILE)
            if not getattr(self, "_waffler_icon_logged", False):
                self._waffler_icon_logged = True
                _log_to_file(f"Tray HICON loaded direct from .ico: handle={self._icon_handle}")

        _tray_icon._assert_icon_handle = types.MethodType(
            _patched_assert_icon_handle, _tray_icon)

        _tray_icon.run_detached()
        _log_to_file("System tray icon created (patched pipeline)")
        # Make the "working" icon now, off the dictation's path.
        threading.Thread(target=_tray_working_icon_path, daemon=True,
                         name="TrayWorkingIcon").start()
        return True

    except Exception as e:
        _log_to_file(f"Tray icon error: {e}")
        _tray_icon = None
        return False


def _tray_show_window(icon=None, item=None):
    """Show the main window — from the menu bar, tray, Dock reopen, or the
    duplicate-launch focus signal. Clears the hidden flag so the activation
    observer treats the window as visible again."""
    global _window_hidden
    _window_hidden = False
    if _window_ref:
        try:
            _window_ref.show()
            _window_ref.restore()
        except Exception as e:
            _log_to_file(f"Tray show error: {e}")
    notify_js_window_visible(True)


def _tray_quit(icon=None, item=None):
    """Actually quit the app from tray."""
    global _should_quit
    _should_quit = True
    if _tray_icon:
        try:
            _tray_icon.stop()
        except Exception:
            pass
    if _window_ref:
        try:
            _window_ref.destroy()
        except Exception:
            pass


def _perform_factory_reset():
    """Clear all Waffler data and restart from setup."""
    try:
        import rumps

        # Show confirmation dialog
        response = rumps.alert(
            title="Factory Reset",
            message="This will delete all Waffler data including:\n\n• Recording history\n• Configuration settings\n• Usage statistics\n• Logs\n\nThe app will quit and restart from setup on next launch.\n\nThis cannot be undone.",
            ok="Reset Everything",
            cancel="Cancel"
        )

        if response == 1:  # User clicked "Reset Everything"
            if _pipeline:
                _pipeline.forget_last_result()
            # Clear data directory
            data_dir = DATA_DIR
            if data_dir.exists():
                import shutil
                shutil.rmtree(data_dir)
                _log_to_file("[factory reset] Data directory cleared")
            try:
                LoginItem().disable()
            except Exception:
                pass

            # Show success message
            rumps.alert(
                title="Reset Complete",
                message="All data has been cleared. Waffler will now quit.\n\nOn next launch, you'll go through setup again.",
                ok="Quit Now"
            )

            # Quit the app
            global _should_quit
            _should_quit = True
            rumps.quit_application()

    except Exception as e:
        _log_to_file(f"[factory reset] Error: {e}")
        try:
            import rumps
            rumps.alert(
                title="Reset Failed",
                message=f"Factory reset failed: {e}",
                ok="OK"
            )
        except:
            pass


def _on_window_closing():
    """Intercept window close: hide window, keep running in background.
    Both Mac and Windows have a status icon (menu bar / tray) to restore or quit.
    """
    global _window_hidden, _should_quit
    if _should_quit:
        return True  # Allow close

    # Distinguish a genuine quit (Cmd-Q / app-menu Quit) from the red-X close.
    # pywebview routes BOTH through this single `closing` event, and since
    # v3.14.52 we return False on a red-X to hide-to-menu-bar — but that also
    # swallowed Cmd-Q, so the app could never be quit from the keyboard and
    # the user had to Force Quit. The triggering NSEvent is still current
    # while this fires: if it's a Cmd-Q key-down, treat it as a real quit.
    # Anything else (red-X mouse click, Cmd-W, etc.) falls through to hide,
    # so this can't misfire into an accidental quit.
    if _platform.system() == "Darwin":
        try:
            from AppKit import (
                NSApplication, NSEventTypeKeyDown, NSEventModifierFlagCommand,
            )
            ev = NSApplication.sharedApplication().currentEvent()
            if ev is not None and ev.type() == NSEventTypeKeyDown:
                chars = ev.charactersIgnoringModifiers()
                if (chars and chars.lower() == "q"
                        and (ev.modifierFlags() & NSEventModifierFlagCommand)):
                    _log_to_file("[quit] Cmd-Q detected — allowing real quit")
                    _should_quit = True
                    if _window_ref:
                        try:
                            _window_ref.destroy()
                        except Exception:
                            pass
                    return True  # allow the app to actually terminate
        except Exception as e:
            _log_to_file(f"[quit] Cmd-Q detection failed (ignored): {e}")

    # Otherwise (red-X) → hide window, keep running in background.
    if _window_ref:
        try:
            _window_ref.hide()
        except Exception:
            pass
    _window_hidden = True
    notify_js_window_visible(False)
    return False  # Prevent close


def _install_mac_reopen_handler():
    """Bring the window back when the user clicks the Dock icon after the
    window was closed to the menu bar.

    Since v3.14.52 the red close-button hides the window instead of quitting;
    the app keeps running with a Dock icon + menu-bar item. But pywebview owns
    the NSApplication delegate and does NOT re-show an orderOut'd window on
    reopen, so clicking the Dock icon did nothing — the ONLY way back was the
    menu-bar 'Show Waffler'. We can't cleanly replace pywebview's delegate, so
    instead we observe NSApplicationDidBecomeActive (posted on a Dock-icon
    click / Cmd-Tab back) and, if the window is currently hidden, bring it
    forward — restoring the standard macOS "click the Dock icon to get the
    window" behaviour. The _window_hidden guard makes a normal activation
    (window already visible) a no-op, so we never fight the user mid-use.
    """
    global _mac_reopen_observer
    try:
        from Foundation import NSObject, NSNotificationCenter
        import objc  # noqa: F401 — ensures the PyObjC bridge is initialised

        class _WafflerReopenObserver(NSObject):
            def appBecameActive_(self, _notification):  # noqa: N802 — Cocoa selector
                if _reopen_should_show(_window_hidden, time.monotonic(), _hidden_start_until):
                    _log_to_file("[reopen] Dock activation with hidden window — showing")
                    _tray_show_window()
                elif _window_hidden:
                    _log_to_file("[reopen] activation while starting hidden at sign-in, ignored")

        obs = _WafflerReopenObserver.alloc().init()
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            obs,
            b"appBecameActive:",
            "NSApplicationDidBecomeActiveNotification",
            None,
        )
        _mac_reopen_observer = obs  # strong ref — PyObjC would GC it otherwise
        _log_to_file("Mac Dock-reopen handler installed")
        return True
    except Exception as e:
        _log_to_file(f"Mac reopen handler error: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────
def _request_input_monitoring_permission():
    """Request Input Monitoring permission on macOS (required for Fn key detection)"""
    try:
        from AppKit import NSEvent
        # Attempt to create a global monitor - this triggers permission prompt
        mask = 4096  # NSEventMaskFlagsChanged
        test_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            mask,
            lambda event: None
        )
        if test_monitor:
            NSEvent.removeMonitor_(test_monitor)
            _log_to_file("✅ Input Monitoring permission granted")
        else:
            _log_to_file("⚠️  Input Monitoring permission required")
            _log_to_file("   Enable in: System Preferences > Security & Privacy > Input Monitoring")
    except Exception as e:
        _log_to_file(f"⚠️  Could not request Input Monitoring permission: {e}")


def _disable_input_source_shortcut():
    """Disable the macOS input source keyboard shortcut to prevent ABC popup"""
    try:
        import subprocess
        # Disable "Select the previous input source" shortcut
        # This is the Fn+Space or Ctrl+Space shortcut that shows the ABC popup
        subprocess.run([
            "defaults", "write", "com.apple.symbolichotkeys",
            "AppleSymbolicHotKeys", "-dict-add", "60",
            "<dict><key>enabled</key><false/></dict>"
        ], check=False, capture_output=True)

        # Also disable "Select next source in Input menu"
        subprocess.run([
            "defaults", "write", "com.apple.symbolichotkeys",
            "AppleSymbolicHotKeys", "-dict-add", "61",
            "<dict><key>enabled</key><false/></dict>"
        ], check=False, capture_output=True)

        _log_to_file("✅ Disabled input source keyboard shortcuts (prevents ABC popup)")
    except Exception as e:
        _log_to_file(f"⚠️  Could not disable input source shortcuts: {e}")


def main():
    global _config, _window_ref, _window_hidden, _hidden_start_until

    # Load config (reads .env from project root via dotenv)
    os.chdir(PROJECT_ROOT)  # so config.yaml and .env are found

    # v3.14.45 — single-instance lock. The 08:31:54 reproduction in the
    # user's app.log showed THREE simultaneous main-mode Waffler.exe
    # processes after an in-app update, each installing its own keyboard
    # hook → Win+Ctrl press fired three on_release callbacks → three
    # _process threads → three pastes per dictation. Root cause was Inno
    # Setup's /RESTARTAPPLICATIONS flag relaunching more processes than
    # Restart Manager had killed. Defence-in-depth at the app layer:
    # acquire a named-mutex lock on Windows / fcntl.flock on POSIX. If
    # any other Waffler main-mode process is already running, exit
    # immediately before touching the pipeline / hotkey listener / audio
    # stream. Crash-safe: the kernel releases the lock on process exit
    # even on hard kill, so the lock can never get stuck.
    #
    # Taken FIRST, before the banner, the update reconciliation and VPN
    # detection. It used to come after them, so a duplicate launch wrote a
    # start-up banner, probed the network adapters and could read (and
    # consume) the pending-update marker meant for the running instance,
    # all before exiting.
    try:
        from src.single_instance import acquire as _acquire_lock, signal_focus_to_existing as _signal_focus
    except ImportError:
        from single_instance import acquire as _acquire_lock, signal_focus_to_existing as _signal_focus
    if not _acquire_lock():
        # v3.14.46 — Slack-style focus-existing-window UX. The second
        # instance signals the first to bring its window to the front
        # (via ~/.waffler-hosted/focus.signal — a polled file the first
        # instance's watcher thread is waiting on) then exits. So a
        # double-click of the Waffler icon while it's already running
        # surfaces the existing window rather than silently doing
        # nothing.
        _log_to_file(
            "[single-instance] another Waffler main-mode process is already "
            "running — signalling it to bring its window to front, then exiting."
        )
        _signal_focus()
        sys.exit(0)

    # 3.15.1: once, keep the vocabulary fixes this person relied on that
    # 3.15's stricter matcher stopped making (e.g. Whisper's "waffle" for
    # Waffler). Reads app.log before the tidy below may rotate it.
    _vr_flag = DATA_DIR / "vocab_corrections_restored"
    if not _vr_flag.exists():
        try:
            from transcribe_whisper import restore_lost_corrections
            _added = restore_lost_corrections([DATA_DIR / "app.log", DATA_DIR / "app.log.1"])
            if _added:
                _log_to_file("[vocab] kept earlier fixes as 'sounds like' spellings: " + ", ".join(
                    f"{e} ({', '.join(v)})" for e, v in _added.items()))
            _vr_flag.write_text("3.15.1\n", encoding="utf-8")
        except Exception as _e:
            _log_to_file(f"[vocab] restoring earlier fixes failed: {type(_e).__name__}")

    # 3.15 (plan SR9): once, remove the transcript lines that versions before
    # the redaction wrote to app.log, then start a new log when it is over
    # 5 MB; and apply the chosen history retention. Before anything else
    # writes to the log; the result is logged as counts only.
    try:
        _tidy = _privacy.tidy_logs_at_start(DATA_DIR, DATA_DIR / "settings.json")
        if _tidy.get("scrubbed"):
            _log_to_file(f"[privacy] removed {_tidy['scrubbed']} old transcript line(s) from app.log")
        if _tidy.get("rotated"):
            _log_to_file("[privacy] app.log was over 5 MB: the old one is app.log.1")
    except Exception as _e:
        _log_to_file(f"[privacy] log tidy failed: {type(_e).__name__}")
    try:
        with _history_lock:
            _h = _load_history_for_update()
            _kept = _retain_history(_h, force=True)
            if len(_kept) != len(_h):
                save_history(_kept)
    except Exception as _e:
        _log_to_file(f"[history] retention at start-up failed: {type(_e).__name__}")

    # The usage record is appended to (usage.jsonl), not rewritten.
    threading.Thread(target=_move_old_usage, daemon=True, name="UsageMove").start()
    # A take that Waffler was stopped in the middle of comes back as a
    # Not sent card, before the window first asks for the Journal.
    _recover_interrupted_takes()

    # v3.14.30 — stamp the running version into the banner so every
    # "is this the right build?" question becomes a 1-second grep
    # against app.log instead of a separate `grep __version__` against
    # the installed bundle.
    try:
        from src import __version__ as _waffler_version
    except Exception:
        _waffler_version = "unknown"
    _log_to_file(
        f"=== Waffler starting === (v{_waffler_version}, "
        f"PROJECT_ROOT={PROJECT_ROOT})"
    )

    # Reconcile the last update attempt against what is actually running.
    # Without this an update that silently did nothing looked exactly like one
    # that worked: on 2026-07-29 a v3.14.85 install passed digest and
    # Authenticode verification, restarted, and came back on v3.14.84 with no
    # error anywhere. The result is logged unconditionally and surfaced to the
    # UI on failure, so "I updated and it is the same version" is now provable
    # from app.log instead of being a matter of the user's word against ours.
    try:
        from src import updater as _upd
        _pending = _upd.check_pending_update(_waffler_version)
        if _pending:
            _log_to_file(
                f"[update] {'OK' if _pending['ok'] else 'FAILED'}: {_pending['message']}"
            )
            if not _pending["ok"]:
                globals()["_UPDATE_FAILURE_NOTICE"] = _pending
    except Exception as _e:
        _log_to_file(f"[update] pending-update check failed: {_e}")

    # v3.14.48 — diagnostic VPN detection. User reported "Waffler doesn't
    # work or is very slow with a VPN. Having this issue with NordVPN."
    # The underlying cause (VPN exit IPs blocked by Groq / Cerebras, or
    # added latency on every request) isn't always fixable from inside
    # Waffler — but a one-line ``[vpn] on`` / ``[vpn] off`` in the
    # startup banner makes future "why is this slow?" investigations
    # one-grep instead of guessing. The detector is best-effort and
    # never blocks startup; any error logs nothing extra.
    try:
        from src.vpn_detect import is_vpn_active as _is_vpn_active
    except ImportError:
        from vpn_detect import is_vpn_active as _is_vpn_active
    try:
        _vpn_on = _is_vpn_active()
        _log_to_file(
            f"[vpn] {'on (VPN tunnel detected — providers may be slower or block requests)' if _vpn_on else 'off'}"
        )
    except Exception as _e:
        _log_to_file(f"[vpn] detection failed: {_e}")

    # v3.14.31 — log the actual macOS mic TCC status at startup. The
    # existing PermissionsManager.check_microphone_permission() opens an
    # sd.InputStream and returns GRANTED if no exception is raised — but
    # that's wrong: a TCC-denied app on macOS gets a stream that opens
    # silently and delivers zero-valued samples (no exception). That's
    # the "bytes captured: 26668, RMS=0" signature in the 17:45 chaos
    # log. The only reliable way to detect mic denial is to ask AVFoundation
    # via PyObjC. Status codes:
    #   0 = NotDetermined (will prompt on first capture)
    #   1 = Restricted (parental controls / MDM, can't be changed)
    #   2 = Denied (user actively denied)
    #   3 = Authorized
    if sys.platform == "darwin":
        try:
            from AVFoundation import (
                AVCaptureDevice,
                AVMediaTypeAudio,
            )
            _av_status = AVCaptureDevice.authorizationStatusForMediaType_(
                AVMediaTypeAudio
            )
            _av_status_name = {
                0: "NotDetermined",
                1: "Restricted",
                2: "Denied",
                3: "Authorized",
            }.get(_av_status, f"Unknown({_av_status})")
            _log_to_file(f"[mic-tcc] AVCaptureDevice mic status: {_av_status_name}")
            if _av_status == 2:
                _log_to_file(
                    "[mic-tcc] WARNING: mic permission DENIED. "
                    "Streams will open but deliver zero samples. "
                    "Fix: System Settings → Privacy & Security → Microphone → enable Waffler."
                )
        except Exception as _e:
            _log_to_file(f"[mic-tcc] AVFoundation check failed: {_e}")

    # Pre-warm Python's SSL stack on the MAIN thread to prevent a
    # PyInstaller-related crash on Windows. The OpenAI / Groq / Cerebras
    # clients all go through httpx, which calls ssl.create_default_context()
    # in its HTTPTransport.__init__ — i.e. once per client instance. When
    # that call happens on a background thread in a PyInstaller-bundled
    # process on Windows, the underlying Windows cert-store load can
    # segfault the whole process.
    #
    # The naive fix (just calling ssl.create_default_context() once on
    # the main thread, like v3.14.5 did) does NOT work — httpx creates
    # a fresh context per client, so the warm-up was discarded.
    #
    # The real fix: build ONE SSL context on the main thread using
    # certifi's bundled cert PEM (avoiding the Windows cert-store call
    # that's actually crashing), then monkey-patch httpx._config.create_ssl_context
    # so every client reuses that same context regardless of which
    # thread the client is constructed on.
    try:
        import ssl
        import certifi
        _MAIN_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
        _log_to_file(f"SSL context pre-built on main thread (cafile={certifi.where()})")
        try:
            import httpx._config as _httpx_config
            def _waffler_ssl_factory(*_args, **_kwargs):
                return _MAIN_SSL_CTX
            _httpx_config.create_ssl_context = _waffler_ssl_factory
            _log_to_file("Patched httpx.create_ssl_context → main-thread context")
        except Exception as _e:
            _log_to_file(f"httpx SSL patch skipped ({_e}); fallback warm-up only")
    except Exception as _e:
        _log_to_file(f"SSL pre-warm failed (continuing): {_e}")

    try:
        config = Config()
    except Exception as e:
        _log_to_file(f"Config error: {e}")
        sys.exit(1)

    _config = config
    _log_to_file(f"Config loaded: has_api_key={config.has_api_key}, setup_complete={_is_setup_complete()}")

    # Request Input Monitoring permission for Fn key on Mac
    if _platform.system() == "Darwin":
        _request_input_monitoring_permission()
        _disable_input_source_shortcut()

    # Only auto-initialize pipeline if setup was already completed
    if config.has_api_key and _is_setup_complete():
        _initialize_pipeline()
    else:
        _log_to_file("Skipping pipeline init (no key or setup incomplete)")

    # Create pywebview window (always — wizard runs inside it)
    api = Api()
    _api_ref = api  # keep reference

    ui_dir = PROJECT_ROOT / "ui"
    html_path = ui_dir / "index.html"

    # Paint the native window in the theme's own background, so opening the
    # app no longer flashes dark under the default Cream theme.
    try:
        from theme import window_background, os_prefers_dark
        _theme = api._load_settings_file().get("theme", "cream")
        _window_bg = window_background(
            _theme, os_prefers_dark() if _theme == "auto" else None)
    except Exception as _e:
        _log_to_file(f"[theme] window background fell back to cream: {_e}")
        _window_bg = "#FDFCFC"

    # Started at sign-in (src/login_item.py passes --hidden): wait in the
    # tray or menu bar with the hotkey ready instead of opening the window
    # on every login. Only once setup is done; before that the window is
    # where setup happens.
    # The Windows tray icon is made first (it does not need the window), so
    # a start at sign-in only stays hidden, and closing the window only hides
    # it, when the icon to bring it back is really there. Before 3.15 the
    # installer left icon.ico out, the tray never came up, and a hidden
    # Waffler could only be ended in Task Manager.
    _windows_tray_up = False
    if _platform.system() == "Windows":
        _windows_tray_up = _create_windows_tray_icon()
        if not _windows_tray_up:
            _log_to_file("No tray icon: the close button quits and the window opens at sign-in")
    can_hide = _windows_tray_up or _platform.system() == "Darwin"
    start_hidden = (_HIDDEN_FLAG in sys.argv and config.has_api_key
                    and _is_setup_complete() and can_hide)
    if start_hidden:
        _window_hidden = True
        _hidden_start_until = time.monotonic() + _HIDDEN_START_GRACE_S
        _log_to_file("Started at sign-in: window stays hidden until opened")
    # An update can install to a new folder; keep an existing start-at-sign-in
    # entry pointing at this copy. Never switches it on by itself.
    try:
        if LoginItem().refresh():
            _log_to_file("[login item] start at sign-in now points at this copy")
    except Exception as _e:
        _log_to_file(f"[login item] refresh skipped: {_e}")

    window = webview.create_window(
        title="Waffler",
        url=str(html_path),
        width=1100,
        height=780,
        min_size=(900, 640),
        resizable=True,
        background_color=_window_bg,
        js_api=api,
        frameless=False,
        easy_drag=False,
        hidden=start_hidden,
    )

    set_window(window)
    _window_ref = window

    # v3.14.46 — start the focus-existing-window watcher. When a second
    # main-mode Waffler attempts to launch, its single_instance.acquire()
    # call fails and it touches ~/.waffler-hosted/focus.signal before
    # exiting; the daemon thread we start here polls that file every
    # 200 ms and calls window.show() (+ window.restore() if available)
    # so the existing window comes to front. Slack/Discord/VS Code all
    # do the same thing on duplicate-launch.
    try:
        from src.single_instance import start_focus_watcher
    except ImportError:
        from single_instance import start_focus_watcher
    start_focus_watcher(window, log_fn=_log_to_file)

    # Intercept close → hide to menu bar / tray.
    # v3.14.52 — Mac now installs an NSStatusItem directly into pywebview's
    # existing NSApp (rumps wanted to own a separate NSApplication, which
    # collided with pywebview's and corrupted the run loop, hence the long-
    # standing comment about skipping the Mac path). The status item must
    # be created on the MAIN THREAD before webview.start() takes it, which
    # is exactly where we are right now.
    if _platform.system() == "Darwin":
        if _create_mac_menubar_icon():
            # Only intercept close → hide if the menu bar actually came up;
            # otherwise the user has no way to reopen the window and the
            # app becomes invisible/unrecoverable.
            window.events.closing += _on_window_closing
            # Let a Dock-icon click reopen the window too, not just the
            # menu-bar 'Show Waffler' item.
            _install_mac_reopen_handler()
    elif _platform.system() == "Windows" and _windows_tray_up:
        window.events.closing += _on_window_closing

    def _on_shown():
        """Set the window icon after pywebview has created the native window."""
        if _platform.system() != "Windows":
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32

            ico_path = _app_icon_path()
            if ico_path is None:
                _log_to_file("icon.ico not found for window icon")
                return

            ico_str = str(ico_path)
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x0010
            LR_DEFAULTSIZE = 0x0040

            # Load large (32x32) and small (16x16) icons
            big = user32.LoadImageW(0, ico_str, IMAGE_ICON, 32, 32,
                                    LR_LOADFROMFILE)
            small = user32.LoadImageW(0, ico_str, IMAGE_ICON, 16, 16,
                                      LR_LOADFROMFILE)

            if not big and not small:
                _log_to_file(f"LoadImageW failed for {ico_str}")
                return

            # Find the pywebview window by title
            hwnd = user32.FindWindowW(None, "Waffler")
            if not hwnd:
                _log_to_file("FindWindowW('Waffler') returned 0")
                return

            WM_SETICON = 0x0080
            ICON_BIG = 1
            ICON_SMALL = 0
            if big:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
            if small:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
            _log_to_file("Window icon set successfully")
        except Exception as e:
            _log_to_file(f"Window icon error: {e}")

    window.events.shown += _on_shown

    # Minimised windows can still count as visible to the page, so say so:
    # the page pauses its animations until the window is restored. The
    # handlers take no arguments, so pywebview calls them as they are.
    def _on_minimized():
        notify_js_window_visible(False)

    def _on_restored():
        notify_js_window_visible(True)

    if getattr(window.events, "minimized", None) is not None:
        window.events.minimized += _on_minimized
    if getattr(window.events, "restored", None) is not None:
        window.events.restored += _on_restored

    print("Waffler window launching...")
    # Start webview — this blocks until window is closed
    # debug=True enables right-click Inspect Element and JS console
    webview.start(debug=False)

    # Clean up tray
    if _tray_icon:
        try:
            _tray_icon.stop()
        except Exception:
            pass

    print("Window closed.")


if __name__ == "__main__":
    main()
