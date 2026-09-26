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
import tempfile
import atexit
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
from atomic_json import write_json_atomic
import pipeline_watchdog as _pw
import unsent as _unsent
import tray_state as _tray_state
import first_run as _first_run
import journal_data as _journal
import recent_audio as _recent_audio
import privacy_data as _privacy
import cleanup_pause as _cleanup_pause
import mac_permissions as _mac_perms
from login_item import LoginItem, HIDDEN_FLAG as _HIDDEN_FLAG
from user_messages import (
    DOWNLOAD_PAGE,
    UPDATE_DOWNLOAD_FAILED,
    UPDATE_INSTALL_FAILED,
    UPDATE_NO_INSTALLER,
    classify_request_error,
    cleanup_skipped_message,
    key_check_error,
    limit_reached_message,
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
USAGE_FILE = DATA_DIR / "usage.json"

# Serialises the load→append→save read-modify-write on history.json. The file
# write itself is atomic (os.replace), but the read-modify-write around it is
# not — two threads (a processing thread + clear_history from the JS bridge,
# or two overlapping recordings) could otherwise interleave and lose entries.
_history_lock = threading.Lock()

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
    ensure_data_dir()
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(history: list):
    """Save transcription history with an atomic write. The replace is
    retried while another handle briefly locks the file (Windows)."""
    ensure_data_dir()
    write_json_atomic(HISTORY_FILE, history)


# The parsed history and its counts, kept until history.json changes
# (src/journal_data.py). The window asks for pages and counts often; it
# used to read and rescan the whole file every time.
_history_cache = _journal.HistoryCache(HISTORY_FILE, load_history)


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
        history = _retain_history(load_history())
        history.append(item)
        save_history(history)


# ── Usage Tracking ──────────────────────────────────────────────────────
def load_usage() -> list:
    """Load usage records from usage.json."""
    ensure_data_dir()
    if not USAGE_FILE.exists():
        return []
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_usage(usage: list):
    """Save usage records to usage.json with an atomic write. The replace is
    retried while another handle briefly locks the file (Windows)."""
    ensure_data_dir()
    write_json_atomic(USAGE_FILE, usage)


def record_usage(entry_type: str, duration_seconds: float = None,
                 input_tokens: int = 0, output_tokens: int = 0,
                 provider: str = "openai"):
    """Record an API usage entry with cost calculation."""
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

    usage = load_usage()
    usage.append(entry)
    save_usage(usage)
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
            updater.install_and_restart(installer_path)
            return {"ok": True}  # usually unreachable — process exits
        except Exception as e:
            _log_to_file(f"[update] install failed: {e}")
            return {"ok": False, "error": UPDATE_INSTALL_FAILED, "download_page": DOWNLOAD_PAGE}

    def get_history(self, limit=None, offset=0, query="") -> list:
        """Journal entries, newest first: all of them, or one page.

        ``limit`` and ``offset`` page through them (the window draws about
        50 at a time and asks for more as you scroll); ``query`` keeps only
        entries whose clean text or transcript contains it.

        Not sent entries get their live state: the recording's id, whether
        its file is still there, and whether Waffler will still send it by
        itself (entries older than a day, or out of tries, will not)."""
        items = _journal.page(_history_cache.items(), limit, offset, query)
        unsent_dir = DATA_DIR / _unsent.UNSENT_DIRNAME
        out = []
        for item in items:
            if isinstance(item, dict) and item.get("failed"):
                item = dict(item)
                uid = _unsent.entry_id(item)
                if uid and _unsent.resolve_file(unsent_dir, uid) is None:
                    uid = ""        # the file has gone; the card can only be deleted
                item["unsent_id"] = uid
                item["will_retry"] = bool(uid) and _unsent.will_auto_retry(item)
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
                stored = self._load_settings_file()
                stored["prompt_style"] = mode_id
                self._save_settings_file(stored)
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

    def get_vocab(self) -> list:
        """Return the user's custom vocabulary list."""
        from transcribe_whisper import load_vocab
        return load_vocab()

    def set_vocab(self, words: list) -> dict:
        """Save the user's custom vocabulary list."""
        import json
        from transcribe_whisper import VOCAB_FILE
        try:
            VOCAB_FILE.parent.mkdir(parents=True, exist_ok=True)
            VOCAB_FILE.write_text(json.dumps(words, indent=2), encoding="utf-8")
            return {"ok": True, "count": len(words)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

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
        try:
            sf = self._settings_file()
            if sf.exists():
                return json.loads(sf.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
        return {}

    def _save_settings_file(self, data: dict):
        """Save settings file with atomic write"""
        sf = self._settings_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=sf.parent,
            suffix='.tmp',
            text=True
        )
        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, sf)  # Atomic on POSIX
        except Exception as e:
            try:
                os.unlink(tmp_path)
            except:
                pass
            raise e

    def set_theme(self, theme: str) -> dict:
        """Remember the UI theme ('cream', 'dark' or 'auto') in settings.json,
        so the next launch can paint the window in the right colour before the
        page loads (see src/theme.py). The UI's own copy stays in
        localStorage."""
        try:
            from theme import THEMES
            theme = str(theme or "").strip().lower()
            if theme not in THEMES:
                return {"ok": False, "error": "Unknown theme."}
            stored = self._load_settings_file()
            if stored.get("theme") != theme:
                stored["theme"] = theme
                self._save_settings_file(stored)
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
            # The engine's own default and clean-up, so Settings shows the
            # order Waffler really uses (it showed groq, cerebras, openai
            # while the engine ran groq, openai, cerebras).
            "provider_order":        _normalize_provider_order(stored.get("provider_order")),
        }

    def save_settings(self, settings: dict) -> dict:
        """Save settings — updates .env and/or settings.json, applies live where possible."""
        try:
            stored = self._load_settings_file()
            notes  = []

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

            self._save_settings_file(stored)
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
            setup_complete.json, vocab.json (config / state — no PII)
          - macOS DiagnosticReports/*.ips   (last 5 system crash dumps)
          - sysinfo.txt                     (synthesised: version, OS,
                                             hotkey, audio device, VPN)

        Deliberately EXCLUDES:
          - .env                            (API keys)
          - history.json                    (user transcripts — PII)

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
                             "setup_complete.json", "vocab.json"):
                    src_path = DATA_DIR / name
                    if src_path.exists():
                        try:
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
                    "  - Transcript history (~/.waffler-hosted/history.json)\n\n"
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

    def export_history(self) -> dict:
        """Return all transcript history as formatted text for download."""
        history = load_history()
        if not history:
            return {"ok": False, "error": "No history to export"}
        lines = [
            "# Waffler — Transcript History",
            f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"Total entries: {len(history)}",
            "",
        ]
        for item in history:
            ts   = item.get("timestamp", "")
            text = item.get("styled") or item.get("text") or ""
            lines.append(f"── {ts} ──────────────────────")
            lines.append(text)
            lines.append("")
        return {"ok": True, "content": "\n".join(lines), "count": len(history)}

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
                history = load_history()
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
            stored = self._load_settings_file()
            stored[_recent_audio.SETTING] = bool(on)
            self._save_settings_file(stored)
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
            stored = self._load_settings_file()
            stored[_privacy.HISTORY_SETTING] = days
            self._save_settings_file(stored)
            with _history_lock:
                history = load_history()
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
            stored = self._load_settings_file()
            if stored.get("setup_step") != step:
                stored["setup_step"] = step
                self._save_settings_file(stored)
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
        if _pipeline is not None:
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

            # Save to settings.json
            stored = self._load_settings_file()
            stored["hotkey_keys"] = keys
            self._save_settings_file(stored)
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
                        )
                    elif _platform.system() == "Darwin":
                        from smart_hotkey import SmartHotkeyListener
                        _pipeline.hotkey_listener = SmartHotkeyListener(
                            on_press=_pipeline.on_hotkey_press,
                            on_release=_pipeline.on_hotkey_release,
                            on_cancel=_pipeline._on_hotkey_cancel,   # Esc (see its docstring)
                            keys=keys,
                        )
                    _log_to_file("New hotkey listener starting...")
                    _pipeline.hotkey_listener.start()

                threading.Thread(target=_restart, daemon=True, name="HotkeyRestart").start()

            return {"ok": True, "keys": keys, "display": verdict["display"]}
        except Exception as e:
            _log_to_file(f"save_hotkey_config error: {e}")
            return {"ok": False, "error": "Couldn't save the hotkey. Please try again."}

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
            if _pipeline is not None:
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
                stored = self._load_settings_file()
                if stored.pop("setup_step", None) is not None:
                    self._save_settings_file(stored)
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
        """Return list of {trigger, expansion} snippet dicts."""
        try:
            sf = self._snippets_file()
            if sf.exists():
                return json.loads(sf.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
        return []

    def set_snippets(self, snippets: list) -> dict:
        """Save snippets list with atomic write"""
        try:
            sf = self._snippets_file()
            sf.parent.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=sf.parent,
                suffix='.tmp',
                text=True
            )
            try:
                with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                    json.dump(snippets, f, indent=2)
                os.replace(tmp_path, sf)  # Atomic on POSIX
            except Exception as e:
                try:
                    os.unlink(tmp_path)
                except:
                    pass
                raise e
            return {"ok": True, "count": len(snippets)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

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
            etype = entry.get("type")
            provider = (entry.get("provider") or "unknown").lower()

            if etype == "whisper":
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
        avg_cost = total_cost / transcription_count if transcription_count > 0 else 0

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
        which calls the C function fresh each time. We try, in order:

            1. ``AXIsProcessTrustedWithOptions(NULL)`` via ctypes — modern API,
               explicitly designed for repeated polling.
            2. ``AXIsProcessTrusted()`` via ctypes — older fallback.
            3. PyObjC ``AXIsProcessTrusted()`` — last-resort fallback for
               environments where ApplicationServices.framework can't be
               dlopened (very rare).
        """
        if sys.platform != "darwin":
            return True  # Not applicable

        # Try ctypes paths first — they bypass any PyObjC caching.
        try:
            import ctypes
            appservices = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )

            # Preferred: AXIsProcessTrustedWithOptions(NULL).
            # Signature: Boolean AXIsProcessTrustedWithOptions(CFDictionaryRef options)
            # Passing NULL means "check without prompting" and always re-queries TCC.
            try:
                appservices.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
                appservices.AXIsProcessTrustedWithOptions.argtypes = [ctypes.c_void_p]
                result = bool(appservices.AXIsProcessTrustedWithOptions(None))
                _log_to_file(f"[DEBUG] AXIsProcessTrustedWithOptions(NULL) returned: {result}")
                return result
            except (AttributeError, OSError) as e:
                _log_to_file(f"[DEBUG] AXIsProcessTrustedWithOptions unavailable: {e}; falling back")

            # Fallback: AXIsProcessTrusted() via ctypes.
            appservices.AXIsProcessTrusted.restype = ctypes.c_bool
            appservices.AXIsProcessTrusted.argtypes = []
            result = bool(appservices.AXIsProcessTrusted())
            _log_to_file(f"[DEBUG] AXIsProcessTrusted (ctypes) returned: {result}")
            return result
        except Exception as e:
            _log_to_file(f"[DEBUG] ctypes Accessibility check failed: {e}; trying PyObjC")

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


def _initialize_pipeline():
    """Create pipeline and start hotkey after setup is complete."""
    global _pipeline
    if _pipeline:
        _log_to_file("Pipeline already initialized, skipping")
        return

    _config.reload_env()

    if not _config.has_api_key:
        _log_to_file("Cannot initialize pipeline: no API key found")
        return

    try:
        _log_to_file("Creating WafflerPipeline...")
        pipeline = WafflerPipeline(_config)
        _pipeline = pipeline
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


def _tray_working_icon_path():
    """The Windows tray icon with an amber dot, made once from icon.ico."""
    global _tray_working_ico
    if _tray_working_ico is not None:
        return _tray_working_ico or None
    try:
        src = PROJECT_ROOT / "icon.ico"
        if not src.exists() and hasattr(sys, "_MEIPASS"):
            src = Path(sys._MEIPASS) / "icon.ico"
        if not src.exists():
            src = Path(sys.executable).parent / "_internal" / "icon.ico"
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
        self._unsent_waiting = self._count_unsent()
        threading.Thread(target=self._unsent_drain_loop, daemon=True,
                         name="UnsentDrain").start()

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
        if action == "confirm":
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

    def on_hotkey_press(self):
        """Start recording."""
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
        _log_to_file("Recording started")
        self._recording_session += 1
        # Track recording start time for duration checks
        import time
        self._recording_start_time = time.time()
        self.audio.start()
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
        # Set once the styled text is on the clipboard. After that point the
        # error handler must not "salvage" the raw transcript over it.
        _clipboard_written = False
        transcript = None  # for the error handler, whatever fails first
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
                    _recent_audio.keep(DATA_DIR, audio_bytes, _stored)
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
                    _uid = self._handle_failed_transcription(audio_bytes, _why)
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

            # Apply vocabulary fuzzy matching corrections
            from transcribe_whisper import load_vocab, apply_vocab_corrections
            vocab = load_vocab()
            if vocab:
                transcript, corrections = apply_vocab_corrections(transcript, vocab)
                if corrections:
                    _log_to_file(f"Vocabulary corrections applied: {', '.join(corrections)}")

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
            _sty = run.call(self.styler.style, transcript, stage=_pw.STYLING,
                            deadline=_pw.STYLE_DEADLINE_S)
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

            # Apply snippets (text expansion)
            styled = self._apply_snippets(styled)

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

            # Copy to clipboard, then paste. Both are bounded by the watchdog
            # run: a clipboard that stays locked or a paste keystroke that
            # never returns used to hold the dictation (and the History save
            # after it) for good. The result of the copy was also once
            # discarded, so a failed write still went on to "paste",
            # replacing the user's selection with whatever unrelated text
            # was on the clipboard already. The transcript is saved to
            # History below either way, so the words are never lost.
            _t2 = time.time()
            _t_paste = 0.0
            _copied = False
            if _policy["paste"]:
                _copy = run.call(self.clipboard.copy, styled, stage=_pw.PASTING,
                                 deadline=_pw.PASTE_DEADLINE_S)
                _copied = bool(_copy.ok and _copy.value)
                _clipboard_written = _copied
                if not _copied:
                    _log_to_file(f"Clipboard write FAILED ({_copy.status}): skipping paste "
                                 f"so stale clipboard contents cannot overwrite the selection")
                    try:
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

            if _policy["paste"] and _copied:
                stored = {}
                _sf = DATA_DIR / "settings.json"
                try:
                    if _sf.exists():
                        stored = json.loads(_sf.read_text(encoding="utf-8-sig"))
                except Exception:
                    pass
                if stored.get("auto_paste", True):
                    # _target_window, not self._prev_window: the latter now
                    # belongs to whatever the user pressed most recently.
                    _paste = run.call(self.clipboard.auto_paste, _target_window,
                                      stage=_pw.PASTING, deadline=_pw.PASTE_DEADLINE_S)
                    if not _paste.ok:
                        _log_to_file(f"[pipeline] paste did not finish ({_paste.status}"
                                     f"{': ' + repr(_paste.error) if _paste.error else ''}); "
                                     f"the text is on the clipboard and in History")
                        _paste_key = "Cmd+V" if _platform.system() == "Darwin" else "Ctrl+V"
                        # Shown before the pill ends, so the pill skips its
                        # tick: this one needs the user to act.
                        try:
                            self.overlay.show_toast(
                                style="warn", heading="Not pasted",
                                body=f"Your text is on the clipboard and in the Journal. "
                                     f"Press {_paste_key} to paste it.",
                            )
                        except Exception:
                            pass
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
                "word_count": len(styled.split()),
                "text_is": "asr_filtered",
            }
            if _as_said:
                # The Journal tags it "As said: limit reached".
                item["as_said"] = _as_said
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
                    styled_words=len((styled or "").split()),
                    asr_filtered=_asr_info.get("last_asr_filtered", False),
                    styling_provider=(gpt_usage or {}).get("provider", ""),
                    styled_text=styled or "",
                    retry_fired=_asr_info.get("last_retry_fired", False),
                    deadline_fired="TIMEOUT" in str((gpt_usage or {}).get("fallback_reason", "")),
                    retry_rejected=_asr_info.get("last_retry_rejected", False),
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
                            "styled_words": len((styled or "").split()),
                            "words_per_speech_second": _q["words_per_speech_second"],
                            "styling_provider": (gpt_usage or {}).get("provider", ""),
                            "asr_provider": _asr_info.get("_last_cloud_provider", "") or "",
                        }) + "\n")
                except Exception as _e:
                    _log_to_file(f"[quality] log write failed: {_e}")
            except Exception as _e:
                _log_to_file(f"[quality] assessment failed: {_e}")
            _saved = append_history_safely(item)
            if _esc_kept:
                # Said only while this dictation still owns the pill.
                if self._run_is_current(run):
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
            _log_to_file(f"Done: {len(styled.split())} words, {len(styled)} chars")
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
                    self._handle_failed_transcription(run.audio_bytes, error_msg)
                    run.saved_as_unsent = True
                    _outcome = _pw.NOT_SENT
                except Exception as _e:
                    _log_to_file(f"[pipeline] could not keep the recording: {_e}")

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
                    self.overlay.show_toast(style="warn", heading=heading, body=body)
                elif "CONNECTION" in error_msg or "Connection error" in error_msg or "timeout" in error_msg.lower():
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
                    self.overlay.show_toast(
                        style="warn",
                        heading="Access denied",
                        body=body,
                    )
                    # Salvage: if a transcript already existed when the error
                    # hit, don't throw the user's words away — copy the raw
                    # text to the clipboard so it's at least recoverable.
                    # Never over the styled text already put there.
                    if transcript and not _clipboard_written:
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
                    if transcript and not _clipboard_written:
                        try:
                            _salvaged = bool(self.clipboard.copy(transcript))
                        except Exception:
                            pass
                    self.overlay.show_toast(
                        style="warn",
                        heading="Something went wrong",
                        body=("Your words are on the clipboard and in the Journal."
                              if (_salvaged or _clipboard_written) else
                              "That dictation didn't go through. Please try again."),
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
        self.overlay.show_toast(style="info", heading=heading, body=body, buttons=buttons)
        # While the offer is up, Esc stops the wait (keeping the recording).
        run.offer_shown()
        self._set_listener_processing(True)

    def _ui_withdraw_offer(self, run):
        if self._run_is_current(run):
            self._set_listener_processing(False)
            # Only an "info" toast: a message that replaced the offer stays.
            self.overlay.hide_toast(style="info")

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
                run.audio_bytes, _unsent.REASON_STUCK, toast=False))
        if not self._run_is_current(run):
            return
        heading, body = _unsent.toast_text(_unsent.REASON_STUCK, saved=True)
        if not saved:
            body = ("Waffler stopped waiting. If your words come through, "
                    "they'll be in the Journal.")
        try:
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
                                     toast: bool = True) -> str:
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
                history = load_history()
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

    def _fill_unsent_card(self, unsent_id: str, entry: dict, path, asr_value,
                          audio_seconds: float, style: bool = True) -> dict:
        """Speech to text answered for a Not sent recording: tidy the words,
        put them into its card (a normal Journal entry from then on) and
        remove the file. Call with _unsent_lock held."""
        transcript, info = asr_value
        try:
            from transcribe_whisper import load_vocab, apply_vocab_corrections
            vocab = load_vocab()
            if vocab:
                transcript, _c = apply_vocab_corrections(transcript, vocab)
        except Exception as e:
            _log_to_file(f"[unsent] vocabulary step skipped: {e}")
        provider = (info or {}).get("backend") or ""
        provider = {"api": "openai", "mlx": "local", "faster": "local"}.get(provider, provider)
        record_usage_safely("whisper", duration_seconds=audio_seconds,
                            provider=provider or "groq")

        styled = ""
        if style:
            run = _pw.DictationRun(f"fill:{unsent_id}")
            sty = run.call(self.styler.style, transcript, stage=_pw.STYLING,
                           deadline=_pw.STYLE_DEADLINE_S)
            if sty.ok and sty.value and sty.value[0]:
                styled, usage = sty.value
                if usage.get("api_used"):
                    record_usage_safely("gpt", input_tokens=usage.get("input_tokens", 0),
                                        output_tokens=usage.get("output_tokens", 0),
                                        provider=usage.get("provider", "openai"))
        if not styled:
            styled = self._unstyled(transcript)
        styled = self._apply_snippets(styled)

        new_item = _unsent.resolved(entry, transcript=transcript, styled=styled)
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
        """Replace snippet trigger phrases with their expansions."""
        import re
        snip_file = DATA_DIR / "snippets.json"
        try:
            if snip_file.exists():
                snippets = json.loads(snip_file.read_text(encoding="utf-8-sig"))
                for s in snippets:
                    trigger   = s.get("trigger", "").strip()
                    expansion = s.get("expansion", "")
                    if trigger:
                        pattern = rf'(?i)\b{re.escape(trigger)}\b'
                        text = re.sub(pattern, lambda m: expansion, text)
        except Exception as e:
            print(f"[snippets] error: {e}")
        return text

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
                )
            else:
                _log_to_file(f"Creating SmartHotkeyListener with keys: {keys}")
                self.hotkey_listener = SmartHotkeyListener(
                    on_press=self.on_hotkey_press,
                    on_release=self.on_hotkey_release,
                    on_cancel=self._on_hotkey_cancel,   # Esc (see its docstring)
                    keys=keys,
                )
            _log_to_file("Calling hotkey.start()...")
            self.hotkey_listener.start()
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


def _create_tray_icon():
    """Create a status-area icon so the app can run in background.
    Windows: pystray system tray icon.
    Mac: rumps menu-bar icon (top-right, next to Wi-Fi/battery).
    """
    if _platform.system() == "Darwin":
        _create_mac_menubar_icon()
    elif _platform.system() == "Windows":
        _create_windows_tray_icon()


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

        item_show = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Show Waffler", b"show:", ""
        )
        item_show.setTarget_(target)
        menu.addItem_(item_show)

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
    """
    global _tray_icon
    try:
        import pystray
        from pystray._util import win32 as pw32
        from PIL import Image
        import types

        # Resolve icon.ico path (dev or frozen)
        _ico_path = PROJECT_ROOT / "icon.ico"
        if not _ico_path.exists() and hasattr(sys, '_MEIPASS'):
            _ico_path = Path(sys._MEIPASS) / "icon.ico"
        if not _ico_path.exists():
            _ico_path = Path(sys.executable).parent / "_internal" / "icon.ico"

        if not _ico_path.exists():
            _log_to_file(f"icon.ico not found for tray icon")
            return

        _log_to_file(f"Tray icon: using {_ico_path}")
        ico_str = str(_ico_path)

        # We still need a PIL Image for pystray's constructor (it stores it),
        # but we'll bypass its ICO serialization when creating the HICON.
        img = Image.open(ico_str).convert('RGBA')

        menu = pystray.Menu(
            pystray.MenuItem("Show Waffler", _tray_show_window, default=True),
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

    except Exception as e:
        _log_to_file(f"Tray icon error: {e}")


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
                if _window_hidden:
                    _log_to_file("[reopen] Dock activation with hidden window — showing")
                    _tray_show_window()

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
    global _config, _window_ref, _window_hidden

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
            _h = load_history()
            _kept = _retain_history(_h, force=True)
            if len(_kept) != len(_h):
                save_history(_kept)
    except Exception as _e:
        _log_to_file(f"[history] retention at start-up failed: {type(_e).__name__}")

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
    start_hidden = (_HIDDEN_FLAG in sys.argv and config.has_api_key
                    and _is_setup_complete())
    if start_hidden:
        _window_hidden = True
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
    elif _platform.system() == "Windows":
        window.events.closing += _on_window_closing
        threading.Thread(target=_create_tray_icon, daemon=True).start()

    def _on_shown():
        """Set the window icon after pywebview has created the native window."""
        if _platform.system() != "Windows":
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32

            # Resolve icon.ico path (dev or frozen)
            ico_path = PROJECT_ROOT / "icon.ico"
            if not ico_path.exists():
                ico_path = Path(sys.executable).parent / "_internal" / "icon.ico"
            if not ico_path.exists() and hasattr(sys, '_MEIPASS'):
                ico_path = Path(sys._MEIPASS) / "icon.ico"
            if not ico_path.exists():
                _log_to_file(f"icon.ico not found for window icon")
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
