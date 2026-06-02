#!/usr/bin/env python3
"""
Waffler — macOS Desktop UI
Entry point: pywebview window + background hotkey/pipeline thread
"""

import sys
import os
import io
import json
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
    _crash_log = open(Path.home() / ".waffler-hosted" / "crash.log", "a")
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

import webview

from config import Config
from audio import AudioRecorder
import platform as _platform
if _platform.system() == "Windows":
    from windows_hotkey import WindowsHotkeyListener
else:
    from smart_hotkey import SmartHotkeyListener
from transcribe_whisper import WhisperTranscriber
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


# ── Overlay Mode Handler ──────────────────────────────────────────────
# When launched with --overlay flag, run the overlay subprocess instead
# of the main app. This allows PyInstaller to freeze both entry points.
if '--overlay' in sys.argv:
    import platform as _plat
    if _plat.system() == "Windows":
        import overlay_process_windows
        overlay_process_windows.main()
    else:
        import overlay_process
        overlay_process.main()
    sys.exit(0)


# ── Data Directory ────────────────────────────────────────────────────
def get_data_directory():
    """Get the data directory for Waffler (~/.waffler-hosted/)."""
    data_dir = Path.home() / ".waffler-hosted"
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

# Pricing constants
WHISPER_COST_PER_SECOND = 0.0001       # OpenAI: $0.006/minute
GROQ_WHISPER_COST_PER_SECOND = 0.0000467  # Groq: $0.0028/minute
GPT4O_MINI_INPUT_COST_PER_1M = 0.15   # GPT-4o-mini input  # doc-drift-ok (per-model cost)
GPT4O_MINI_OUTPUT_COST_PER_1M = 0.60  # GPT-4o-mini output  # doc-drift-ok (per-model cost)
GROQ_LLM_INPUT_COST_PER_1M = 0.59     # Groq LLaMA 3.3 70B input
GROQ_LLM_OUTPUT_COST_PER_1M = 0.79    # Groq LLaMA 3.3 70B output


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
    """Save transcription history with atomic write"""
    ensure_data_dir()
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=HISTORY_FILE.parent,
        suffix='.tmp',
        text=True
    )
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, HISTORY_FILE)  # Atomic on POSIX
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except:
            pass
        raise e


def append_history(item: dict):
    """Atomically append one entry to history.json. Use this instead of a bare
    load→append→save so concurrent writers don't clobber each other."""
    with _history_lock:
        history = load_history()
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
    """Save usage records to usage.json with atomic write"""
    ensure_data_dir()
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=USAGE_FILE.parent,
        suffix='.tmp',
        text=True
    )
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(usage, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, USAGE_FILE)  # Atomic on POSIX
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except:
            pass
        raise e


def record_usage(entry_type: str, duration_seconds: float = None,
                 input_tokens: int = 0, output_tokens: int = 0,
                 provider: str = "openai"):
    """Record an API usage entry with cost calculation."""
    cost_usd = 0.0

    if entry_type == "whisper" and duration_seconds is not None:
        if provider == "groq":
            cost_usd = duration_seconds * GROQ_WHISPER_COST_PER_SECOND
        else:
            cost_usd = duration_seconds * WHISPER_COST_PER_SECOND
    elif entry_type == "gpt":
        if provider == "groq":
            cost_usd = (input_tokens / 1_000_000) * GROQ_LLM_INPUT_COST_PER_1M + \
                       (output_tokens / 1_000_000) * GROQ_LLM_OUTPUT_COST_PER_1M
        else:
            cost_usd = (input_tokens / 1_000_000) * GPT4O_MINI_INPUT_COST_PER_1M + \
                       (output_tokens / 1_000_000) * GPT4O_MINI_OUTPUT_COST_PER_1M

    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "type": entry_type,
        "provider": provider,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 6),
    }
    if duration_seconds is not None:
        entry["duration_seconds"] = round(duration_seconds, 3)

    usage = load_usage()
    usage.append(entry)
    save_usage(usage)
    return entry


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
                return {
                    "update_available": False,
                    "current_version": current_version,
                    "error": f"GitHub API returned HTTP {r.status_code}",
                }
            releases = r.json()
            if not isinstance(releases, list) or not releases:
                return {
                    "update_available": False,
                    "current_version": current_version,
                    "error": "No releases found",
                }

            # Filter out drafts and prereleases, pick highest semver
            candidates = [
                rel for rel in releases
                if not rel.get("draft") and not rel.get("prerelease") and rel.get("tag_name")
            ]
            if not candidates:
                return {
                    "update_available": False,
                    "current_version": current_version,
                    "error": "No published releases found",
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
                }
            return {
                "update_available": False,
                "current_version": current_version,
                "latest_version": latest_version,
            }
        except Exception as e:
            _log_to_file(f"[update] check failed: {e}")
            return {
                "update_available": False,
                "current_version": current_version,
                "error": str(e),
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
        p = urlparse(url or "")
        host = (p.hostname or "").lower()
        host_ok = host == "github.com" or host.endswith(".githubusercontent.com")
        path_ok = host != "github.com" or "/releases/download/" in p.path
        if p.scheme != "https" or not host_ok or not path_ok:
            _log_to_file(f"[update] refused untrusted download URL: {url[:120]}")
            return {"ok": False, "error": "Refusing to download from an untrusted URL."}
        try:
            from src import updater
            _log_to_file(f"[update] start_download requested: {url[:120]}")
            updater.start_download(url)
            return {"ok": True}
        except Exception as e:
            _log_to_file(f"[update] start_download failed: {e}")
            return {"ok": False, "error": str(e)}

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
                return {"ok": False, "error": "Refusing to install an unrecognised file."}
            updater.install_and_restart(installer_path)
            return {"ok": True}  # usually unreachable — process exits
        except Exception as e:
            _log_to_file(f"[update] install failed: {e}")
            return {"ok": False, "error": str(e)}

    def get_history(self) -> list:
        """Return transcript history (newest first)."""
        items = load_history()
        # Return newest first
        return list(reversed(items))

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
        """Return word-count stats plus the user's daily "stack streak".

        Streak rules (v3.14.16+):
          * Counts consecutive calendar days, ending today, that have at
            least one history entry.
          * If today has no entries yet, the streak is preserved as long
            as yesterday has one — so a 12-day streak doesn't snap to 0
            at midnight before the user has a chance to record. The
            streak only breaks once a full day passes without any entry.
        """
        history = load_history()
        today_str = date.today().isoformat()
        today_items = [
            h for h in history
            if str(h.get("timestamp", "")).startswith(today_str)
        ]
        today_words = sum(
            len((h.get("styled") or h.get("text") or "").split())
            for h in today_items
        )
        total_words = sum(
            len((h.get("styled") or h.get("text") or "").split())
            for h in history
        )

        # ── Stack streak ────────────────────────────────────────────
        from datetime import timedelta as _td
        days_with_entries = set()
        for h in history:
            ts = str(h.get("timestamp", ""))
            if len(ts) >= 10:
                try:
                    # Parse the YYYY-MM-DD prefix directly; cheaper than
                    # full ISO parsing and tolerant of trailing chars.
                    y, m, d = ts[:10].split("-")
                    days_with_entries.add(date(int(y), int(m), int(d)))
                except Exception:
                    pass

        today = date.today()
        # Anchor: today if there's an entry today, else yesterday. This
        # gives the user a one-day grace period to keep the streak alive
        # until they dictate something new.
        cursor = today if today in days_with_entries else (today - _td(days=1))
        streak = 0
        while cursor in days_with_entries:
            streak += 1
            cursor -= _td(days=1)

        return {
            "today_words": today_words,
            "today_count": len(today_items),
            "total_words": total_words,
            "streak_days": streak,
        }

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
            VOCAB_FILE.write_text(json.dumps(words, indent=2))
            return {"ok": True, "count": len(words)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def focus_window(self) -> dict:
        """Bring the Waffler window to the foreground."""
        try:
            import platform
            import webview

            if platform.system() == "Darwin":
                # macOS - activate the application using NSApp
                try:
                    from AppKit import NSApp, NSApplicationActivateIgnoringOtherApps
                    NSApp.activateIgnoringOtherApps_(NSApplicationActivateIgnoringOtherApps)
                except ImportError:
                    # Fallback if AppKit not available
                    pass

            # Also try webview's method
            windows = webview.windows
            if windows:
                windows[0].on_top = True
                windows[0].on_top = False

            return {"ok": True}
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
                return json.loads(sf.read_text())
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

    def _update_env_var(self, key: str, value: str):
        """Update or add a variable in the user's .env file."""
        env_path = DATA_DIR / ".env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        if env_path.exists():
            lines = env_path.read_text().splitlines()
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
        env_path.write_text("\n".join(new_lines) + "\n")

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
        transcription_backend = "unknown"
        styling_backend = "unknown"
        if _pipeline:
            transcription_backend = getattr(_pipeline.transcriber, "_backend", "api")
            if getattr(_pipeline.styler, "_use_cerebras", False):
                styling_backend = "cerebras"
            elif getattr(_pipeline.styler, "_use_groq", False):
                styling_backend = "groq"
            else:
                styling_backend = "openai"
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
        return {
            "needs_setup": not setup_done or not has_any_key,
            "has_key": has_any_key,
            "has_openai_key": bool(openai_key),
            "has_groq_key": bool(groq_key),
            "setup_complete": setup_done,
        }

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
            error_msg = str(e)
            if "401" in error_msg or "invalid" in error_msg.lower():
                return {"ok": False, "error": "Invalid API key"}
            elif "429" in error_msg:
                return {"ok": False, "error": "Rate limited — key may be valid but has no quota"}
            else:
                return {"ok": False, "error": f"Connection error: {error_msg[:100]}"}

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
            client.models.list()
            # Key is valid — persist it
            self._update_env_var("GROQ_API_KEY", api_key)
            os.environ["GROQ_API_KEY"] = api_key
            return {"ok": True, "message": "Groq key is valid"}
        except ImportError:
            return {"ok": False, "error": "Groq SDK not installed"}
        except Exception as e:
            error_msg = str(e)
            if "401" in error_msg or "invalid" in error_msg.lower():
                return {"ok": False, "error": "Invalid Groq API key"}
            elif "403" in error_msg:
                return {"ok": False, "error": "Access denied — this key may be expired or revoked. Generate a new one at console.groq.com/keys"}
            elif "429" in error_msg:
                return {"ok": False, "error": "Rate limited — try again shortly"}
            else:
                return {"ok": False, "error": f"Connection error: {error_msg[:100]}"}

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
            if "401" in error_msg or "unauthorized" in lower or "invalid" in lower:
                return {"ok": False, "error": "Invalid Cerebras API key"}
            elif "403" in error_msg:
                return {"ok": False, "error": "Access denied — key may be expired or scope-restricted"}
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
                return {"ok": False, "error": f"Connection error: {error_msg[:100]}"}

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

    def request_permissions(self) -> dict:
        """Request macOS permissions by attempting to create event tap. This triggers system prompts."""
        import platform as plat
        if plat.system() != "Darwin":
            return {"ok": True, "message": "Permissions not needed on this platform"}

        try:
            # Import the Fn key monitor which will attempt to create CGEventTap
            # This triggers the Input Monitoring permission prompt
            from fn_key_cgevent import FnKeyMonitor

            def dummy_callback():
                pass

            # Try to create the monitor - this will request permissions
            monitor = FnKeyMonitor(on_fn_press=dummy_callback, on_fn_release=dummy_callback)
            monitor.start()

            # Give it a moment to start
            time.sleep(0.5)

            # Stop it
            monitor.stop()

            return {
                "ok": True,
                "message": "Permission request triggered. Please grant Input Monitoring and Accessibility permissions in System Settings."
            }
        except Exception as e:
            return {
                "ok": False,
                "error": f"Failed to request permissions: {str(e)}"
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
            data_dir = Path.home() / ".waffler-hosted"
            if data_dir.exists():
                shutil.rmtree(data_dir)
                _log_to_file("[factory reset] Data directory cleared via UI")

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
                from windows_hotkey import KEY_TO_VK, DEFAULT_HOTKEY, MODIFIER_KEYS, hotkey_display
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
            return {"ok": True, "keys": keys, "display": hotkey_display(keys)}
        except Exception as e:
            return {"ok": True, "keys": ["win", "ctrl"], "display": "Win + Ctrl"}

    def save_hotkey_config(self, keys) -> dict:
        """Save hotkey config and restart the listener."""
        try:
            if isinstance(keys, str):
                keys = json.loads(keys)
            if not isinstance(keys, list) or len(keys) == 0:
                return {"ok": False, "error": "Invalid keys format"}

            # Platform-specific imports
            if _platform.system() == "Windows":
                from windows_hotkey import KEY_TO_VK, MODIFIER_KEYS, hotkey_display
                KEY_MAP = KEY_TO_VK
            elif _platform.system() == "Darwin":
                from mac_hotkey_monitor import KEY_TO_KEYCODE, MODIFIER_FLAGS
                KEY_MAP = {**KEY_TO_KEYCODE, **{k: v for k, v in MODIFIER_FLAGS.items()}}
                MODIFIER_KEYS = set(MODIFIER_FLAGS.keys())
                def hotkey_display(keys):
                    display_map = {"cmd": "⌘", "shift": "⇧", "option": "⌥", "control": "⌃", "fn": "Fn"}
                    return " + ".join(display_map.get(k, k.title()) for k in keys)
            else:
                return {"ok": False, "error": "Hotkey customization not supported on this platform"}

            # Validate: all keys recognized
            for k in keys:
                if k not in KEY_MAP:
                    return {"ok": False, "error": f"Unknown key: {k}"}

            # Validate: at least one modifier (or Fn on Mac)
            if not any(k in MODIFIER_KEYS for k in keys):
                modifier_names = "Cmd, Ctrl, Alt, Shift, or Fn" if _platform.system() == "Darwin" else "Ctrl, Alt, Shift, or Win"
                return {"ok": False, "error": f"At least one modifier key required ({modifier_names})"}

            # Validate: max 3 keys
            if len(keys) > 3:
                return {"ok": False, "error": "Maximum 3 keys allowed"}

            # Validate: reject reserved combos
            key_set = set(keys)
            if key_set == {"alt"} or key_set == {"win"}:
                return {"ok": False, "error": "Single modifier not allowed"}
            reserved = [{"ctrl", "alt"}, {"alt", "f4"}, {"alt", "tab"}]
            if key_set in reserved:
                return {"ok": False, "error": "This key combination is reserved by the system"}

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
                            on_cancel=_pipeline._on_overlay_cancel,  # v3.14.37 — Esc cancels
                            keys=keys,
                        )
                    elif _platform.system() == "Darwin":
                        from smart_hotkey import SmartHotkeyListener
                        _pipeline.hotkey_listener = SmartHotkeyListener(
                            on_press=_pipeline.on_hotkey_press,
                            on_release=_pipeline.on_hotkey_release,
                            on_cancel=_pipeline._on_overlay_cancel,  # v3.14.37 — Esc cancels
                            keys=keys,
                        )
                    _log_to_file("New hotkey listener starting...")
                    _pipeline.hotkey_listener.start()

                threading.Thread(target=_restart, daemon=True, name="HotkeyRestart").start()

            return {"ok": True, "display": hotkey_display(keys)}
        except Exception as e:
            _log_to_file(f"save_hotkey_config error: {e}")
            return {"ok": False, "error": str(e)}

    # ── Permission APIs ─────────────────────────────────────────────────

    def check_permissions(self) -> dict:
        """Enhanced permission checking with detailed feedback."""
        # Use direct checks instead of PermissionsManager (more reliable)
        accessibility = self.check_accessibility_permission()
        input_monitoring = self.check_input_monitoring_permission()

        result = {
            "ok": True,
            "platform": "Darwin" if sys.platform == "darwin" else sys.platform,
            "accessibility_granted": accessibility,
            "input_monitoring_granted": input_monitoring,
            "mic_granted": False,  # Not checked in wizard
            "all_granted": accessibility and input_monitoring,
        }

        return result

    def get_permission_status(self) -> dict:
        """Get detailed permission status for enhanced UI."""
        permissions_mgr = PermissionsManager()
        return permissions_mgr.get_permission_status_summary()

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

    def test_microphone(self, device_index, duration=2.0) -> dict:
        """Record a short clip and return the audio level."""
        try:
            import sounddevice as sd
            import numpy as np
            device_index = int(device_index)
            duration = min(float(duration), 5.0)
            recording = sd.rec(
                int(16000 * duration),
                samplerate=16000,
                channels=1,
                dtype='int16',
                device=device_index,
            )
            sd.wait()
            rms = float(np.sqrt(np.mean(recording.astype(np.float32) ** 2)))
            peak = float(np.max(np.abs(recording)))
            has_audio = rms > 100
            return {
                "ok": True,
                "rms": round(rms, 1),
                "peak": round(peak, 1),
                "has_audio": has_audio,
                "message": "Audio detected" if has_audio else "No audio detected — check your microphone",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

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

    def wizard_start_fn_detection(self) -> dict:
        """Start Fn key detection for wizard Step 3 (just detection, no recording)."""
        try:
            # Initialize hotkey listener if not already running
            if not hasattr(self, 'hotkey_listener') or not self.hotkey_listener:
                _log_to_file("Starting Fn key detection for wizard Step 3...")
                stored = self._load_settings_file()
                keys = stored.get("hotkey_keys")
                if _platform.system() == "Windows":
                    self.hotkey_listener = WindowsHotkeyListener(
                        on_press=lambda: None,  # Dummy handlers - just need listener for get_fn_key_state()
                        on_release=lambda: None,
                        keys=keys,
                    )
                else:
                    self.hotkey_listener = SmartHotkeyListener(
                        on_press=lambda: None,
                        on_release=lambda: None,
                    )
                threading.Thread(target=self.hotkey_listener.start, daemon=True, name="WizardFnDetection").start()
            return {"ok": True}
        except Exception as e:
            _log_to_file(f"Wizard Fn detection error: {e}")
            return {"ok": False, "error": str(e)}

    def wizard_start_hotkey_test(self, device_index) -> dict:
        """Start temporary hotkey listener for wizard Step 4."""
        global _wizard_recorder, _wizard_hotkey, _wizard_transcriber
        global _wizard_recording, _wizard_result, _wizard_overlay
        try:
            device_index = int(device_index)
            _wizard_result = None
            _wizard_recording = False

            # Create temporary audio recorder
            _wizard_recorder = AudioRecorder(sample_rate=16000, channels=1)

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

            # Create temporary transcriber using already-validated keys
            openai_key = os.getenv("OPENAI_API_KEY", "")
            groq_key = os.getenv("GROQ_API_KEY", "")
            if not openai_key and not groq_key:
                return {"ok": False, "error": "No API key found. Complete Step 1 first."}

            _wizard_transcriber = WhisperTranscriber(
                api_key=openai_key, groq_api_key=groq_key,
            )

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
                _wizard_hotkey = SmartHotkeyListener(
                    on_press=_wizard_on_press,
                    on_release=_wizard_on_release,
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
            return {"ok": False, "error": str(e)}

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
        global _wizard_hotkey, _wizard_recorder, _wizard_transcriber
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
            _log_to_file("Wizard hotkey test stopped (recorder drained)")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def wizard_get_recording_state(self) -> dict:
        """Poll the wizard recording state and result."""
        return {
            "recording": _wizard_recording,
            "result": _wizard_result,
        }

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
                return json.loads(sf.read_text())
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
            })
            bucket["cost_usd"] += cost
            bucket["count"] += 1
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
_wizard_overlay       = None   # temporary overlay for wizard
_wizard_recording     = False  # is wizard currently recording?
_wizard_result        = None   # transcription result

SETUP_FILE = DATA_DIR / "setup_complete.json"


def _is_setup_complete() -> bool:
    """Check if the setup wizard has been completed before."""
    try:
        if SETUP_FILE.exists():
            data = json.loads(SETUP_FILE.read_text())
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
    }, indent=2))


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
        # Start VU level feed in background
        threading.Thread(target=_wizard_level_loop, daemon=True, name="WizLevelLoop").start()
    if _window:
        try:
            _window.evaluate_js("window.wizOnRecordingStart && window.wizOnRecordingStart()")
        except Exception:
            pass


def _wizard_level_loop():
    """Feed live audio level to the wizard overlay at ~30fps while recording."""
    while _wizard_recording and _wizard_recorder and _wizard_overlay:
        lvl = _wizard_recorder.get_level()
        try:
            _wizard_overlay.update_level(lvl)
        except Exception:
            pass
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

        transcript = _wizard_transcriber.transcribe_sync(audio_bytes) if _wizard_transcriber else ""
        _wizard_result = transcript or "(Empty transcription)"
        # Metadata only — don't log the transcript text (PII; ships in the bundle).
        _log_to_file(f"Wizard transcription: {len(_wizard_result)} chars")
        _push_wizard_result(_wizard_result)
    except Exception as e:
        _wizard_result = f"(Error: {e})"
        _log_to_file(f"Wizard transcription error: {e}")
        _push_wizard_result(_wizard_result)


def _push_wizard_silent():
    """Push 'no audio' notification to JS during wizard."""
    if _window:
        try:
            _window.evaluate_js("window.wizOnSilentRecording && window.wizOnSilentRecording()")
        except Exception:
            pass


def _push_wizard_result(text: str):
    """Push wizard transcription result to JS."""
    if _window:
        try:
            result_json = json.dumps(text)
            _window.evaluate_js(f"window.wizOnTranscriptionResult && window.wizOnTranscriptionResult({result_json})")
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


def notify_js_status(status: str):
    """Tell the JS frontend about recording status (safely escaped)."""
    if _window:
        try:
            status_json = json.dumps(status)
            _window.evaluate_js(f"window.waffler_status && window.waffler_status({status_json})")
        except Exception:
            pass


def notify_js_new_item(item: dict):
    """Push a new transcript item to the JS frontend."""
    if _window:
        try:
            item_json = json.dumps(item)
            _window.evaluate_js(
                f"window.waffler_refresh && window.waffler_refresh({item_json})"
            )
        except Exception as e:
            print(f"[js] notify error: {e}")


# ── Pipeline ──────────────────────────────────────────────────────────
class WafflerPipeline:
    def __init__(self, config: Config):
        self.config = config
        self.audio = AudioRecorder(
            sample_rate=config.sample_rate,
            channels=config.channels
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

        # Transcriber — Groq Whisper (fast) → OpenAI Whisper (fallback).
        # OpenAI model defaults to gpt-4o-mini-transcribe (half the cost of
        # whisper-1 and noticeably better quality). Override via
        # OPENAI_WHISPER_MODEL env var.
        self.transcriber = WhisperTranscriber(
            api_key=openai_key,
            groq_api_key=groq_key,
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

    def set_device(self, device_index: int):
        """Update the audio device used for future recordings."""
        self._device_index = device_index
        _log_to_file(f"Audio device changed to index {device_index}")

    def _on_overlay_cancel(self):
        """User confirmed cancel — discard recording and clear clipboard."""
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

        # Clear clipboard to prevent paste of cancelled transcription
        try:
            import pyperclip
            pyperclip.copy("")
            _log_to_file("Clipboard cleared after cancel")
        except Exception as e:
            _log_to_file(f"Clipboard clear failed: {e}")

    def _on_overlay_stop(self):
        """User clicked ■ on overlay — stop & process."""
        if self.is_recording:
            self.on_hotkey_release()
            # Reset hotkey listener state to prevent sticky mode desync
            if hasattr(self, 'hotkey_listener') and self.hotkey_listener:
                if hasattr(self.hotkey_listener, 'reset_state'):
                    self.hotkey_listener.reset_state()

    def _on_overlay_cancel_request(self):
        """User clicked X on overlay — directly cancel without confirmation."""
        if not self.is_recording:
            return
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
        notify_js_status("processing")
        try:
            self.overlay.hide()
        except Exception as e:
            print(f"[overlay] hide failed: {e}")
        threading.Thread(target=lambda: self._process(current_id), daemon=True).start()

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
            self.overlay.hide_toast()
        except Exception as e:
            _log_to_file(f"[overlay] no-audio toast failed: {e}")

    def _process(self, processing_id: int):
        """Process audio: transcribe, style, copy to clipboard, paste."""

        def _is_cancelled():
            """True if this processing generation should abort.

            Two ways to be cancelled:
              1. A NEWER recording started (processing_id no longer current) —
                 this generation is stale, so whatever it produced must not be
                 pasted. Checked regardless of the cancel flag; this is what
                 prevents the "ghost paste" of an old transcript after a quick
                 re-press (the old code returned False here — the bug).
              2. The user explicitly cancelled THIS generation (flag set while
                 it is still the current one).
            """
            with self._processing_lock:
                if processing_id != self._processing_id:
                    return True
                return self._processing_cancelled.is_set()

        # Tracks whether _process reached its success "done" status; the
        # finally below resets the UI to idle on every OTHER exit so it can't
        # get stuck showing "recording"/"processing".
        _finalized = False
        try:
            # Calculate recording duration for error suppression
            import time
            recording_duration = time.time() - self._recording_start_time if self._recording_start_time else 0
            _log_to_file(f"Recording duration: {recording_duration:.2f}s")

            # Early abort if already cancelled
            if _is_cancelled():
                _log_to_file(f"Processing {processing_id} aborted: cancelled before start")
                notify_js_status("idle")
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
            if recording_duration < 0.5:
                _log_to_file(
                    f"Recording too short ({recording_duration:.2f}s < 0.5s) — "
                    f"discarding as accidental tap; no transcription, no toast"
                )
                try:
                    self.audio.stop()  # drain the recording buffer
                except Exception as _e:
                    _log_to_file(f"audio.stop() during short-tap discard failed: {_e}")
                notify_js_status("idle")
                return

            transcript = None  # init for error handler
            _log_to_file("[pipeline] stopping audio capture...")
            audio_bytes = self.audio.stop()
            _log_to_file(f"[pipeline] audio captured: {len(audio_bytes) if audio_bytes else 0} bytes")
            if not audio_bytes:
                _log_to_file("No audio bytes captured")
                # Only show error toast if recording was held for > 1 second
                if recording_duration >= 1.0:
                    threading.Thread(target=self._show_no_audio_toast, daemon=True).start()
                notify_js_status("idle")
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
                                    body="Your mic stopped responding (sleep or device change). Reset done — press and speak again.",
                                ),
                                daemon=True,
                            ).start()
                        else:
                            _log_to_file("Showing 'couldn't hear you' toast")
                            threading.Thread(target=self._show_no_audio_toast, daemon=True).start()
                    else:
                        _log_to_file("Suppressing error toast (quick tap)")
                    notify_js_status("idle")
                    return
            except Exception:
                pass  # If numpy check fails, continue with transcription

            # PARTIAL-DEAD-STREAM DETECTION + per-recording audio diagnostics.
            # The fully-silent check above only fires when the WHOLE recording
            # is dead. But the mic stream can also go dead PART-WAY through a
            # long hands-free recording — CoreAudio keeps the stream ".active"
            # but starts handing back zero-filled buffers. The first half
            # transcribes fine; the back half is digital silence, so Whisper
            # returns only ~half the words and the user sees "it dropped half
            # of what I said / it's not even picking it up". A real mic always
            # has a noise floor (~3-10 RMS) even in a silent room, so a window
            # of *exact* zeros (RMS < 1) means the stream delivered nothing —
            # never a natural pause. We measure the digital-silence fraction
            # and, if a meaningful chunk of an otherwise-speaking recording is
            # dead, rebuild the stream for next time + warn the user that this
            # take was likely truncated.
            try:
                import numpy as _np
                _arr = _np.frombuffer(audio_bytes[44:], dtype=_np.int16).astype(_np.float32)
                _win = 4000  # 0.25 s
                _total = 0
                _dead = 0
                _speech = 0
                for _i in range(0, len(_arr), _win):
                    _w = _arr[_i:_i + _win]
                    if len(_w) < 400:
                        break
                    _r = float(_np.sqrt(_np.mean(_w ** 2)))
                    _total += 1
                    if _r < 1.0:
                        _dead += 1
                    elif _r >= 12.0:
                        _speech += 1
                _dead_frac = (_dead / _total) if _total else 0.0
                _log_to_file(
                    f"[pipeline] audio diag: {recording_duration:.1f}s, "
                    f"{_total} windows, digital-silence={_dead_frac*100:.0f}%, "
                    f"speech-windows={_speech}"
                )
                # Degraded mid-recording: lots of dead windows but the take
                # also clearly contained real speech (so it's not just a
                # quiet pause-heavy dictation). 30% dead is far beyond any
                # natural pause pattern — natural pauses keep room-tone, they
                # don't go to exact zero.
                if _dead_frac >= 0.30 and _speech >= 2 and recording_duration >= 3.0:
                    _log_to_file(
                        f"⚠️  Partial dead stream: {_dead_frac*100:.0f}% of this recording was "
                        f"digital silence — mic stream went dead mid-take. Rebuilding for next press."
                    )
                    try:
                        self.audio.force_rebuild()
                    except Exception as _e:
                        _log_to_file(f"force_rebuild (partial) failed: {_e}")
                    threading.Thread(
                        target=lambda: self.overlay.show_toast(
                            style="warn",
                            heading="Mic dropped out",
                            body="Your mic cut out partway through — some of this may be missing. Mic reset; please re-record.",
                        ),
                        daemon=True,
                    ).start()
            except Exception:
                pass

            # Check cancellation before expensive transcription
            if _is_cancelled():
                _log_to_file(f"Processing {processing_id} aborted: cancelled before transcription")
                notify_js_status("idle")
                return

            # Show "Transcribing…" progress on the overlay so the user sees
            # the app is working. Run a ticker thread that bumps the elapsed
            # seconds counter every 500ms while transcription runs.
            #
            # v3.14.28 — also fires a one-shot "taking longer than usual"
            # toast once transcription crosses 10 seconds. Most dictations
            # complete in well under 2s; 10s means something is wrong
            # upstream (provider throttling, slow network, free tier
            # congestion). The toast nudges the user toward adding a
            # fallback key without blaming Waffler. Fires exactly once.
            _stage_start = time.time()
            _stage_stop = threading.Event()
            _slow_toast_fired = [False]  # list-wrapped so closure can mutate

            def _ticker_transcribe():
                while not _stage_stop.is_set():
                    elapsed = time.time() - _stage_start
                    try:
                        self.overlay.set_progress("Transcribing", elapsed)
                    except Exception:
                        pass
                    if elapsed >= 10 and not _slow_toast_fired[0]:
                        _slow_toast_fired[0] = True
                        try:
                            self.overlay.show_toast(
                                style="warn",
                                heading="Taking longer than usual",
                                body="Provider may be slow. Add a fallback key in Settings for reliability.",
                            )
                        except Exception:
                            pass
                    _stage_stop.wait(0.5)
            threading.Thread(target=_ticker_transcribe, daemon=True, name="ProgressTranscribe").start()

            # Transcribe
            _t0 = time.time()
            try:
                transcript = self.transcriber.transcribe_sync(audio_bytes)
            except Exception as _te:
                # No transcription engine could turn the audio into text. The
                # usual cause is a VPN exit IP that Groq blocks at the network
                # layer (HTTP 403) with no OpenAI/local fallback wired up — so
                # speech-to-text itself fails and there is no "raw text" to
                # paste. Rather than silently dropping the recording, preserve
                # the audio, journal it, and tell the user. Automatic
                # transcription fallback over a VPN is tracked in ROADMAP.md.
                _stage_stop.set()
                _log_to_file(f"[pipeline] transcription FAILED, preserving audio: {_te}")
                self._handle_failed_transcription(audio_bytes, str(_te))
                notify_js_status("idle")
                return
            _t_transcribe = (time.time() - _t0) * 1000
            _stage_stop.set()
            _log_to_file(f"[pipeline] transcription: {_t_transcribe:.0f}ms")
            if not transcript:
                _log_to_file("Empty transcription result")
                # Only show error toast if recording was held for > 1 second
                if recording_duration >= 1.0:
                    _log_to_file("Showing 'couldn't hear you' toast")
                    threading.Thread(target=self._show_no_audio_toast, daemon=True).start()
                else:
                    _log_to_file("Suppressing error toast (quick tap)")
                notify_js_status("idle")
                return

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
            whisper_provider = self.transcriber._backend
            if whisper_provider in ("mlx", "faster"):
                whisper_provider = "local"
            elif whisper_provider == "api":
                whisper_provider = "openai"
            if whisper_duration > 0:
                record_usage("whisper", duration_seconds=whisper_duration,
                             provider=whisper_provider)
            # Show "Styling…" progress — this is the slow stage on long
            # dictations (15-25s on full gpt-4.1 for 400+ word inputs).
            _style_start = time.time()
            _style_stop = threading.Event()
            _slow_style_toast_fired = [False]
            def _ticker_style():
                while not _style_stop.is_set():
                    elapsed = time.time() - _style_start
                    try:
                        self.overlay.set_progress("Styling", elapsed)
                    except Exception:
                        pass
                    # v3.14.28 — same slow-operation guard for styling.
                    # Threshold is 15s here (vs 10 for transcription) because
                    # styling legitimately takes 15-25s on full gpt-4.1 for
                    # 400+ word inputs. 15s is a "this is slow even for
                    # styling" threshold.
                    if elapsed >= 15 and not _slow_style_toast_fired[0]:
                        _slow_style_toast_fired[0] = True
                        try:
                            self.overlay.show_toast(
                                style="warn",
                                heading="Taking longer than usual",
                                body="Provider may be slow. Add a fallback key in Settings for reliability.",
                            )
                        except Exception:
                            pass
                    _style_stop.wait(0.5)
            threading.Thread(target=_ticker_style, daemon=True, name="ProgressStyle").start()

            # Style
            _t1 = time.time()
            styled, gpt_usage = self.styler.style(transcript)
            _t_style = (time.time() - _t1) * 1000
            _style_stop.set()
            _log_to_file(f"[pipeline] styling ({gpt_usage.get('provider', 'local')}): {_t_style:.0f}ms")
            if not styled:
                styled = transcript

            # Warn the user when every styling provider failed and we had to
            # fall back to the regex-only cleaner. Without this, quality drops
            # silently (e.g. when Groq's free-tier quota is exhausted).
            #
            # v3.14.19: rewrote the wording to be actionable. The previous
            # `else` branch ("Pasted raw. See the log for details.") gave the
            # user no idea what was wrong or what to do. Every branch now
            # tells the user (a) what happened, (b) what to do RIGHT NOW
            # (add a fallback key in Settings), and (c) the alternative
            # (wait until the limit resets).
            if gpt_usage.get("fallback_reason"):
                reason = gpt_usage["fallback_reason"]
                # Extract provider name if the styler prefixed it (v3.14.19+).
                # Format inside RATE_LIMIT messages: parts[3] now starts
                # with "<Provider>: " when the styler enriched it.
                provider_name = "Your styling provider"
                if reason.startswith("RATE_LIMIT|"):
                    _parts = reason.split("|", 3)
                    snippet = _parts[3] if len(_parts) > 3 else ""
                    if ":" in snippet:
                        cand = snippet.split(":", 1)[0].strip()
                        if cand in ("Groq", "Cerebras", "OpenAI"):
                            provider_name = cand

                heading = "Rate limit hit"

                if reason.startswith("RATE_LIMIT|"):
                    # Format: RATE_LIMIT|<limit>|<retry_in>|<raw error snippet>
                    parts = reason.split("|", 3)
                    limit_kind = parts[1] if len(parts) > 1 else ""
                    retry_in = parts[2] if len(parts) > 2 else ""

                    # Map the technical limit label to something readable.
                    lk = limit_kind.lower()
                    is_daily = (
                        "per day" in lk
                        or "TPD" in limit_kind
                        or "RPD" in limit_kind
                        or "ASD" in limit_kind
                    )
                    if "tokens per day" in lk:
                        friendly = "daily token limit"
                        is_daily = True
                    elif "tokens per minute" in lk:
                        friendly = "per-minute token limit"
                    elif "requests per day" in lk:
                        friendly = "daily request limit"
                        is_daily = True
                    elif "requests per minute" in lk:
                        friendly = "per-minute request limit"
                    elif "audio seconds" in lk:
                        friendly = "daily audio limit" if is_daily else "hourly audio limit"
                    elif lk == "cooldown":
                        friendly = "rate limit (cooldown active)"
                    else:
                        friendly = "rate limit"

                    # Format the retry duration (e.g. "15m43.488s" or
                    # "12s") as a human-friendly "about N minutes" —
                    # strip milliseconds and round up so we never tell
                    # the user to wait "0 minutes".
                    import re as _re_fmt
                    _m = _re_fmt.match(
                        r"^\s*(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?\s*$",
                        retry_in,
                    )
                    if _m and _m.group(0).strip():
                        _h = int(_m.group(1) or 0)
                        _mn = int(_m.group(2) or 0)
                        _sec = float(_m.group(3) or 0)
                        if _h >= 1:
                            wait_hint = (
                                f"resets in about {_h}h {_mn}m" if _mn
                                else f"resets in about {_h}h"
                            )
                        else:
                            _total_min = _mn + (1 if _sec > 0 else 0)
                            if _total_min >= 1:
                                wait_hint = (
                                    f"resets in about {_total_min} "
                                    f"minute{'s' if _total_min != 1 else ''}"
                                )
                            else:
                                wait_hint = "resets in under a minute"
                    elif is_daily:
                        wait_hint = "resets tomorrow"
                    else:
                        wait_hint = "resets shortly"

                    # v3.14.28 — concise version. Previously the body was
                    # 3+ sentences (~140 chars) which overflowed the toast
                    # and was hard to read in a flash. New version: one
                    # sentence, two pieces of info — what happened + what
                    # to do. Heading still names the provider + reset time.
                    other_providers = [
                        p for p in ("Cerebras", "OpenAI")
                        if p != provider_name
                    ]
                    fallback_hint = (
                        f"Add a {other_providers[0]} key for fallback."
                        if other_providers else
                        "Add a fallback key in Settings."
                    )

                    heading = f"{provider_name} limit hit · {wait_hint}"
                    body = (
                        f"Pasted raw — {fallback_hint}"
                    )

                elif "CONNECTION" in reason or "timeout" in reason.lower():
                    heading = "Connection failed"
                    body = (
                        "Pasted raw text — couldn't reach the styling provider. "
                        "Check your internet or VPN, then try again."
                    )

                elif reason.startswith("AUTH:") or "auth" in reason.lower() and "401" in reason:
                    heading = "Auth blocked"
                    body = (
                        "Pasted raw text — provider blocked the request "
                        "(likely a VPN, firewall, or expired key). "
                        "Try another provider key in Settings → API Keys, or toggle VPN off."
                    )

                elif "No styling providers configured" in reason:
                    heading = "No styling provider"
                    body = (
                        "Pasted raw text — no API key is set up yet. "
                        "Add a Groq key (free, 100k tokens/day) in Settings → API Keys to enable styling."
                    )

                else:
                    # Truly unknown error. Still give the user something
                    # actionable rather than telling them to read the log.
                    heading = "Styling skipped"
                    body = (
                        "Pasted raw text — your styling provider returned an unexpected error. "
                        "Try adding a fallback key in Settings → API Keys "
                        "(Groq, Cerebras, or OpenAI) so we can route around it next time."
                    )

                _log_to_file(f"[pipeline] styling fell back to basic_clean: {reason}")
                try:
                    self.overlay.show_toast(style="warn", heading=heading, body=body)
                except Exception as _e:
                    _log_to_file(f"[pipeline] fallback toast failed: {_e}")

            # Record GPT usage (if API was used)
            if gpt_usage.get("api_used"):
                record_usage(
                    "gpt",
                    input_tokens=gpt_usage.get("input_tokens", 0),
                    output_tokens=gpt_usage.get("output_tokens", 0),
                    provider=gpt_usage.get("provider", "openai"),
                )

            # Apply snippets (text expansion)
            styled = self._apply_snippets(styled)

            # CRITICAL: Check cancellation before copying to clipboard
            if _is_cancelled():
                _log_to_file(f"Processing {processing_id} aborted: cancelled before clipboard")
                notify_js_status("idle")
                return

            # Copy to clipboard
            _t2 = time.time()
            self.clipboard.copy(styled)

            # Check cancellation before auto-paste
            if _is_cancelled():
                _log_to_file(f"Processing {processing_id} aborted: cancelled before paste")
                notify_js_status("idle")
                return

            # Auto-paste (respects settings)
            stored = {}
            _sf = DATA_DIR / "settings.json"
            try:
                if _sf.exists():
                    stored = json.loads(_sf.read_text())
            except Exception:
                pass
            if stored.get("auto_paste", True):
                self.clipboard.auto_paste(self._prev_window)
            _t_paste = (time.time() - _t2) * 1000
            _log_to_file(f"[pipeline] clipboard+paste: {_t_paste:.0f}ms")
            _log_to_file(f"[pipeline] TOTAL: {_t_transcribe + _t_style + _t_paste:.0f}ms")

            # Check cancellation before saving to history
            if _is_cancelled():
                _log_to_file(f"Processing {processing_id} aborted: cancelled before history")
                notify_js_status("idle")
                return

            # Save to history
            item = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "text": transcript,
                "styled": styled,
                "word_count": len(styled.split()),
            }
            append_history(item)

            # Notify JS
            notify_js_status("done")
            notify_js_new_item(item)

            # Log metadata only — never the transcript text. app.log is what
            # the "Download Logs" diagnostic bundle ships, so logging content
            # here would leak the user's dictations to anyone they send logs to.
            _log_to_file(f"Done: {len(styled.split())} words, {len(styled)} chars")
            _finalized = True

        except Exception as e:
            error_msg = str(e)
            _log_to_file(f"Pipeline error: {error_msg}")
            import traceback
            traceback.print_exc()

            # Show user-visible error toast with specific message
            try:
                # Only genuine mic-level errors get the `error` style with
                # the Select-mic button. Everything else uses `warn` (single
                # Dismiss) so the action matches the problem.
                if "RATE_LIMIT" in error_msg or "429" in error_msg:
                    # v3.14.42 — extract concrete wait time and provider from the
                    # error format the styler raises: "RATE_LIMIT|<limit>|<wait>|<details>".
                    # Old message hardcoded "Groq API limit hit" even when the actual
                    # culprit was Cerebras or OpenAI — misleading the user about which
                    # provider to wait on or top up.
                    wait_label = ""
                    if "RATE_LIMIT|" in error_msg:
                        try:
                            parts = error_msg.split("RATE_LIMIT|", 1)[1].split("|")
                            # Field 1 is the limit type (e.g. "tokens per day (TPD)",
                            # "cooldown", "Cerebras"); field 2 is the wait time
                            # ("16m12s", "45s", "3s"). Show the wait if it parses.
                            if len(parts) >= 2 and parts[1].strip():
                                wait_label = parts[1].strip().rstrip(".")
                        except Exception:
                            pass
                    body = (
                        f"Try again in {wait_label}. (Add another provider key in "
                        f"Settings → API Keys for instant fallback.)"
                        if wait_label
                        else "Wait a moment and try again. (Add another provider key in "
                        "Settings → API Keys for instant fallback.)"
                    )
                    self.overlay.show_toast(
                        style="warn",
                        heading="Rate limit reached",
                        body=body,
                    )
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
                    if transcript:
                        try:
                            self.clipboard.copy(transcript)
                        except Exception:
                            pass
                else:
                    self.overlay.show_toast(
                        style="warn",
                        heading="Something went wrong",
                        body="Your text was copied to clipboard. Check logs for details.",
                    )
                    # Still try to salvage — paste the raw transcript
                    if transcript:
                        try:
                            self.clipboard.copy(transcript)
                        except Exception:
                            pass
            except Exception:
                pass

            notify_js_status("idle")
        finally:
            # Belt-and-braces: if _process exits any way other than the success
            # "done" path, return the UI to idle — so no future early-return can
            # leave it stuck showing "recording"/"processing".
            if not _finalized:
                try:
                    notify_js_status("idle")
                except Exception:
                    pass

    def _save_unsent_recording(self, audio_bytes: bytes):
        """Persist the raw WAV of a recording we couldn't transcribe to
        ``~/.waffler-hosted/unsent/`` so it is never lost. ``audio_bytes`` is
        already a complete WAV (44-byte header + PCM), so it is written
        verbatim. Returns the Path, or None on failure."""
        try:
            unsent_dir = DATA_DIR / "unsent"
            unsent_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            wav_path = unsent_dir / f"recording-{stamp}.wav"
            with open(wav_path, "wb") as f:
                f.write(audio_bytes)
            _log_to_file(
                f"[pipeline] saved unsent recording: {wav_path} "
                f"({len(audio_bytes)} bytes)"
            )
            return wav_path
        except Exception as e:
            _log_to_file(f"[pipeline] failed to save unsent recording: {e}")
            return None

    def _handle_failed_transcription(self, audio_bytes: bytes, reason: str):
        """Transcription produced no text at all (typically a VPN exit-IP
        block on Groq with no fallback engine). We can't conjure words from
        nothing — but we can make sure the user never loses the recording:
        save the audio, drop a journal entry pointing at it, and show an
        honest toast. Proper automatic fallback is tracked in ROADMAP.md."""
        wav_path = self._save_unsent_recording(audio_bytes)

        lower = reason.lower()
        is_block = (
            "403" in reason or "401" in reason
            or "access denied" in lower or "unauthorized" in lower
            or "permission" in lower
        )

        if is_block:
            note = (
                "Transcription blocked — your VPN server's exit IP is on Groq's "
                "block list. Switch to a different VPN server/location (or turn "
                "the VPN off), then re-record. Your audio was saved below."
            )
            toast_body = (
                "Your VPN server's IP is blocked by Groq. Try a different VPN "
                "server (or turn it off) and re-record — your audio's saved to History."
            )
        else:
            note = (
                "Transcription failed, so no text could be produced. Your "
                "audio was saved so you can retry."
            )
            toast_body = (
                "Couldn't transcribe that one. The recording was saved to your "
                "journal so nothing is lost — please try again."
            )

        # Journal entry — shows in History so the recording is visible, and the
        # saved WAV path rides along on the item for future recovery tooling.
        try:
            item = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "text": "",
                "styled": f"⚠️ {note}",
                "word_count": 0,
                "failed": True,
                "error": reason[:200],
                "audio_path": str(wav_path) if wav_path else "",
            }
            append_history(item)
            try:
                notify_js_new_item(item)
            except Exception:
                pass
        except Exception as e:
            _log_to_file(f"[pipeline] failed to journal failed transcription: {e}")

        try:
            self.overlay.show_toast(
                style="warn",
                heading="Recording saved — not transcribed",
                body=toast_body,
            )
        except Exception:
            pass

    def _apply_snippets(self, text: str) -> str:
        """Replace snippet trigger phrases with their expansions."""
        import re
        snip_file = DATA_DIR / "snippets.json"
        try:
            if snip_file.exists():
                snippets = json.loads(snip_file.read_text())
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
                    stored = json.loads(sf.read_text())
                    keys = stored.get("hotkey_keys")
            except Exception:
                pass

            if _platform.system() == "Windows":
                _log_to_file("Creating WindowsHotkeyListener...")
                self.hotkey_listener = WindowsHotkeyListener(
                    on_press=self.on_hotkey_press,
                    on_release=self.on_hotkey_release,
                    on_cancel=self._on_overlay_cancel,  # v3.14.37 — Esc cancels
                    keys=keys,
                )
            else:
                _log_to_file(f"Creating SmartHotkeyListener with keys: {keys}")
                self.hotkey_listener = SmartHotkeyListener(
                    on_press=self.on_hotkey_press,
                    on_release=self.on_hotkey_release,
                    on_cancel=self._on_overlay_cancel,  # v3.14.37 — Esc cancels
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
            self._icon_handle = pw32.LoadImage(
                None, ico_str, pw32.IMAGE_ICON, 0, 0,
                pw32.LR_DEFAULTSIZE | pw32.LR_LOADFROMFILE)
            _log_to_file(f"Tray HICON loaded direct from .ico: handle={self._icon_handle}")

        _tray_icon._assert_icon_handle = types.MethodType(
            _patched_assert_icon_handle, _tray_icon)

        _tray_icon.run_detached()
        _log_to_file("System tray icon created (patched pipeline)")

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
            data_dir = Path.home() / ".waffler-hosted"
            if data_dir.exists():
                import shutil
                shutil.rmtree(data_dir)
                _log_to_file("[factory reset] Data directory cleared")

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
    global _config, _window_ref

    # Load config (reads .env from project root via dotenv)
    os.chdir(PROJECT_ROOT)  # so config.yaml and .env are found
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

    window = webview.create_window(
        title="Waffler",
        url=str(html_path),
        width=1100,
        height=780,
        min_size=(900, 640),
        resizable=True,
        background_color="#0d0d0f",
        js_api=api,
        frameless=False,
        easy_drag=False,
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
