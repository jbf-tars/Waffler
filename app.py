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

# Pricing constants
WHISPER_COST_PER_SECOND = 0.0001       # OpenAI: $0.006/minute
GROQ_WHISPER_COST_PER_SECOND = 0.0000467  # Groq: $0.0028/minute
GPT4O_MINI_INPUT_COST_PER_1M = 0.15   # GPT-4o-mini input
GPT4O_MINI_OUTPUT_COST_PER_1M = 0.60  # GPT-4o-mini output
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
            try:
                return tuple(int(x) for x in v.lstrip("v").split("."))
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
                download_url = latest_release.get("html_url", "")
                for asset in latest_release.get("assets", []):
                    if asset.get("name", "").endswith(suffix):
                        download_url = asset.get("browser_download_url", download_url)
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
        JS polls get_update_progress() to render a progress bar."""
        try:
            from src import updater
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
        """Launch the installer detached and exit the app so the upgrade can replace files."""
        try:
            from src import updater
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
        """Return word-count stats."""
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
        return {
            "today_words": today_words,
            "today_count": len(today_items),
            "total_words": total_words,
        }

    # ── Mode / Prompt API ─────────────────────────────────────────────

    def get_modes(self) -> list:
        """Return available prompt modes with display names."""
        return [
            {"id": "normal", "name": "Normal", "desc": "Keeps everything, cleans grammar"},
        ]

    def get_current_mode(self) -> str:
        """Return the currently active prompt mode id."""
        if _pipeline:
            return _pipeline.styler.prompt_style
        return _config.prompt_style if _config else "normal"

    def set_mode(self, mode_id: str) -> dict:
        """Switch to a different prompt mode."""
        valid = {m["id"] for m in self.get_modes()}
        if mode_id not in valid:
            return {"ok": False, "error": f"Unknown mode: {mode_id}"}
        try:
            if _pipeline:
                _pipeline.styler.prompt_style = mode_id
                _pipeline.styler.prompt_template = _pipeline.styler._load_prompt_template()
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
                monitor = getattr(_wizard_step2_monitor, '_monitor', None)
                if monitor:
                    # FnKeyMonitor uses _fn_pressed, MacHotkeyMonitor uses _hotkey_active
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
            if getattr(_pipeline.styler, "_use_groq", False):
                styling_backend = "groq"
            else:
                styling_backend = "openai"
        return {
            "api_key_set":           bool(key),
            "api_key_masked":        _mask(key),
            "groq_key_set":          bool(groq_key),
            "groq_key_masked":       _mask(groq_key),
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
                            keys=keys,
                        )
                    elif _platform.system() == "Darwin":
                        from smart_hotkey import SmartHotkeyListener
                        _pipeline.hotkey_listener = SmartHotkeyListener(
                            on_press=_pipeline.on_hotkey_press,
                            on_release=_pipeline.on_hotkey_release,
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

            # Create overlay for wizard Step 4 visual feedback
            # Skip overlay creation - causes crashes on first run when called from non-main thread
            # Overlay isn't critical for wizard functionality
            _wizard_overlay = None
            _log_to_file("Wizard overlay skipped (prevents threading crashes)")

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
        """Stop the temporary wizard hotkey listener and clean up."""
        global _wizard_hotkey, _wizard_recorder, _wizard_transcriber
        global _wizard_recording, _wizard_overlay
        try:
            if _wizard_hotkey:
                _wizard_hotkey.stop()
                _wizard_hotkey = None
            if _wizard_recording and _wizard_recorder:
                try:
                    _wizard_recorder.stop()
                except Exception:
                    pass
                _wizard_recording = False
            if _wizard_overlay:
                _wizard_overlay.stop()
                _wizard_overlay = None
            _wizard_recorder = None
            _wizard_transcriber = None
            _log_to_file("Wizard hotkey test stopped")
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
        """Called when the setup wizard finishes. Initializes the pipeline."""
        try:
            _mark_setup_complete()
            # Initialize pipeline in a background thread so this API call
            # returns immediately (pipeline init includes tkinter overlay
            # which can block on non-main threads).
            threading.Thread(
                target=_initialize_pipeline,
                daemon=True,
                name="PipelineInit"
            ).start()
            return {"ok": True, "message": "Setup complete! Waffler is ready."}
        except Exception as e:
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
        """Return usage statistics for display in Settings."""
        usage = load_usage()
        
        # Get current month for "this month" calculation
        now = datetime.now()
        current_month = now.strftime("%Y-%m")
        
        # Calculate totals
        total_cost = 0.0
        month_cost = 0.0
        whisper_count = 0
        gpt_count = 0
        total_duration = 0.0
        total_input_tokens = 0
        total_output_tokens = 0
        
        for entry in usage:
            total_cost += entry.get("cost_usd", 0)
            if entry.get("type") == "whisper":
                whisper_count += 1
                total_duration += entry.get("duration_seconds", 0)
            elif entry.get("type") == "gpt":
                gpt_count += 1
                total_input_tokens += entry.get("input_tokens", 0)
                total_output_tokens += entry.get("output_tokens", 0)
            
            # Check if this month
            ts = entry.get("timestamp", "")
            if ts.startswith(current_month):
                month_cost += entry.get("cost_usd", 0)
        
        transcription_count = whisper_count
        avg_cost = total_cost / transcription_count if transcription_count > 0 else 0
        
        return {
            "total_cost_usd": round(total_cost, 4),
            "month_cost_usd": round(month_cost, 4),
            "transcription_count": transcription_count,
            "gpt_count": gpt_count,
            "total_duration_seconds": round(total_duration, 2),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "avg_cost_per_transcription": round(avg_cost, 4),
        }

    def reset_usage(self) -> dict:
        """Reset/clear all usage statistics."""
        try:
            save_usage([])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Permission Checking API ────────────────────────────────────────

    def check_accessibility_permission(self) -> bool:
        """Check if Accessibility permission is granted"""
        try:
            from ApplicationServices import AXIsProcessTrusted
            result = AXIsProcessTrusted()
            _log_to_file(f"[DEBUG] AXIsProcessTrusted() returned: {result}")
            _log_to_file(f"[DEBUG] App bundle path: {os.path.abspath(__file__)}")
            return result
        except Exception as e:
            _log_to_file(f"[ERROR] Accessibility check failed: {e}")
            print(f"Error checking accessibility permission: {e}")
            return False

    def check_input_monitoring_permission(self) -> bool:
        """Check if Input Monitoring permission is granted"""
        try:
            # Input Monitoring is harder to check directly
            # We'll use a heuristic: try to create an event tap
            from Quartz import CGEventTapCreate, kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault, kCGEventFlagsChanged, CFRelease

            def dummy_callback(proxy, event_type, event, refcon):
                return event

            event_mask = (1 << kCGEventFlagsChanged)
            tap = CGEventTapCreate(
                kCGSessionEventTap,
                kCGHeadInsertEventTap,
                kCGEventTapOptionDefault,
                event_mask,
                dummy_callback,
                None
            )

            if tap is None:
                return False
            else:
                # Clean up - we don't actually need the tap
                CFRelease(tap)
                return True
        except Exception as e:
            print(f"Error checking input monitoring permission: {e}")
            return False


# ── Global refs ───────────────────────────────────────────────────────
_window   = None
_api      = None
_pipeline = None   # set after WafflerPipeline is created
_config   = None   # set in main()

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
        _log_to_file(f"Wizard transcription: {_wizard_result[:80]}")
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

        # Styler — Groq LLaMA (fast) → GPT-4o-mini (fallback)
        self.styler = OpenAIStyler(
            api_key=openai_key,
            model="gpt-4o-mini",
            max_tokens=1024,
            prompt_style=config.prompt_style,
            groq_api_key=groq_key,
        )
        _log_to_file(f"Styler backend: {'groq' if self.styler._use_groq else 'openai'}")

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
        self._processing_id = 0  # Increments with each recording
        self._processing_lock = threading.Lock()

    def set_device(self, device_index: int):
        """Update the audio device used for future recordings."""
        self._device_index = device_index
        _log_to_file(f"Audio device changed to index {device_index}")

    def _on_overlay_cancel(self):
        """User confirmed cancel — discard recording and clear clipboard."""
        if self.is_recording:
            self.is_recording = False
            self.audio.stop()
            self.overlay.hide()
            notify_js_status("idle")
            _log_to_file("Recording cancelled by user")
            # Reset hotkey listener state to prevent sticky mode desync
            if hasattr(self, 'hotkey_listener') and self.hotkey_listener:
                if hasattr(self.hotkey_listener, 'reset_state'):
                    self.hotkey_listener.reset_state()

        # Signal any running _process() thread to abort
        with self._processing_lock:
            self._processing_cancelled.set()

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
            # Open mic settings
            import subprocess as sp
            try:
                sp.Popen(["start", "ms-settings:privacy-microphone"], shell=True)
            except Exception:
                pass
            self.overlay.hide_toast()

    def on_hotkey_press(self):
        """Start recording."""
        if self.is_recording:
            return
        # Capture focused window BEFORE overlay takes focus
        self._prev_window = self.clipboard.get_focused_window()
        _log_to_file("Recording started")
        self._recording_session += 1
        self.is_recording = True
        # Track recording start time for duration checks
        import time
        self._recording_start_time = time.time()
        # Clear any previous cancellation state
        with self._processing_lock:
            self._processing_cancelled.clear()
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
        if not self.is_recording:
            return
        _log_to_file("Recording stopped, processing")
        self.is_recording = False
        self._is_paused = False
        notify_js_status("processing")
        try:
            self.overlay.hide()
        except Exception as e:
            print(f"[overlay] hide failed: {e}")
        # Assign unique ID and spawn process thread
        with self._processing_lock:
            self._processing_id += 1
            current_id = self._processing_id
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
        """Show 'We couldn't hear you' toast on the overlay."""
        session = self._recording_session  # snapshot — guard against new recordings
        try:
            self.overlay.show()
            self.overlay.show_toast(
                style="error",
                heading="We couldn't hear you",
                body="Check your mic is connected and not muted.",
            )
            # Auto-hide after 4 seconds, but only if no new recording has started
            import time as _t
            _t.sleep(4)
            self.overlay.hide_toast()
            if self._recording_session == session:
                self.overlay.hide()
        except Exception as e:
            _log_to_file(f"[overlay] no-audio toast failed: {e}")

    def _process(self, processing_id: int):
        """Process audio: transcribe, style, copy to clipboard, paste."""

        def _is_cancelled():
            """Check if this processing session has been cancelled."""
            if self._processing_cancelled.is_set():
                with self._processing_lock:
                    # New recording started - old cancellation doesn't apply
                    if processing_id != self._processing_id:
                        return False
                    return True
            return False

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

            # Check minimum duration — < 0.3s is likely accidental (quick tap)
            # Audio is 16kHz 16-bit mono = 32000 bytes/sec + 44 byte WAV header
            if len(audio_bytes) < 44 + 9600:  # 0.3 seconds + header
                _log_to_file(f"Recording too short ({len(audio_bytes)} bytes), treating as accidental tap")
                # No error toast for quick taps - silently discard
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
                    # Only show error toast if recording was held for > 1 second
                    if recording_duration >= 1.0:
                        _log_to_file("Showing 'couldn't hear you' toast")
                        threading.Thread(target=self._show_no_audio_toast, daemon=True).start()
                    else:
                        _log_to_file("Suppressing error toast (quick tap)")
                    notify_js_status("idle")
                    return
            except Exception:
                pass  # If numpy check fails, continue with transcription

            # Check cancellation before expensive transcription
            if _is_cancelled():
                _log_to_file(f"Processing {processing_id} aborted: cancelled before transcription")
                notify_js_status("idle")
                return

            # Transcribe
            _t0 = time.time()
            transcript = self.transcriber.transcribe_sync(audio_bytes)
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
            # Style
            _t1 = time.time()
            styled, gpt_usage = self.styler.style(transcript)
            _t_style = (time.time() - _t1) * 1000
            _log_to_file(f"[pipeline] styling ({gpt_usage.get('provider', 'local')}): {_t_style:.0f}ms")
            if not styled:
                styled = transcript

            # Warn the user when every styling provider failed and we had to
            # fall back to the regex-only cleaner. Without this, quality drops
            # silently (e.g. when Groq's free-tier quota is exhausted).
            if gpt_usage.get("fallback_reason"):
                reason = gpt_usage["fallback_reason"]
                heading = "Cleanup skipped"
                if reason.startswith("RATE_LIMIT|"):
                    # Format: RATE_LIMIT|<limit>|<retry_in>|<raw error snippet>
                    parts = reason.split("|", 3)
                    limit_kind = parts[1] if len(parts) > 1 else ""
                    retry_in = parts[2] if len(parts) > 2 else ""

                    # Map Groq's technical limit label to something readable.
                    lk = limit_kind.lower()
                    is_daily = "per day" in lk or "TPD" in limit_kind or "RPD" in limit_kind or "ASD" in limit_kind
                    if "tokens per day" in lk:
                        friendly = "daily token limit"
                    elif "tokens per minute" in lk:
                        friendly = "per-minute token limit"
                    elif "requests per day" in lk:
                        friendly = "daily request limit"
                    elif "requests per minute" in lk:
                        friendly = "per-minute request limit"
                    elif "audio seconds" in lk:
                        friendly = "daily audio limit" if is_daily else "hourly audio limit"
                    else:
                        friendly = "rate limit"

                    # Format Groq's retry duration (e.g. "15m43.488s") as a
                    # human-friendly "about N minutes" — strip milliseconds and
                    # round up so we never tell the user to try again "in 0 minutes".
                    import re as _re_fmt
                    _m = _re_fmt.match(r"^\s*(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?\s*$", retry_in)
                    if _m and _m.group(0).strip():
                        _h = int(_m.group(1) or 0)
                        _mn = int(_m.group(2) or 0)
                        _sec = float(_m.group(3) or 0)
                        if _h >= 1:
                            wait_hint = f"Try again in about {_h}h {_mn}m" if _mn else f"Try again in about {_h}h"
                        else:
                            _total_min = _mn + (1 if _sec > 0 else 0)  # round seconds up
                            if _total_min >= 1:
                                wait_hint = f"Try again in about {_total_min} minute{'s' if _total_min != 1 else ''}"
                            else:
                                wait_hint = "Try again in under a minute"
                    elif is_daily:
                        wait_hint = "Try again tomorrow"
                    else:
                        wait_hint = "Try again shortly"

                    body = f"Groq {friendly} hit. {wait_hint}, or add an OpenAI key in Settings as a fallback."
                elif "CONNECTION" in reason or "timeout" in reason.lower():
                    body = "No connection — pasted raw. Check your internet or VPN."
                else:
                    body = "Pasted raw. See the log for details."
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
            history = load_history()
            history.append(item)
            save_history(history)

            # Notify JS
            notify_js_status("done")
            notify_js_new_item(item)

            _log_to_file(f"Done: {styled[:80]}")

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
                    self.overlay.show_toast(
                        style="warn",
                        heading="Rate limit reached",
                        body="Groq API limit hit. Wait a moment and try again.",
                    )
                elif "CONNECTION" in error_msg or "Connection error" in error_msg or "timeout" in error_msg.lower():
                    self.overlay.show_toast(
                        style="warn",
                        heading="Connection failed",
                        body="Couldn't reach the server. Check your internet or VPN.",
                    )
                elif "403" in error_msg or "Access denied" in error_msg:
                    self.overlay.show_toast(
                        style="warn",
                        heading="Access denied",
                        body="API key may be invalid or VPN is blocking the request.",
                    )
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
                    keys=keys,
                )
            else:
                _log_to_file(f"Creating SmartHotkeyListener with keys: {keys}")
                self.hotkey_listener = SmartHotkeyListener(
                    on_press=self.on_hotkey_press,
                    on_release=self.on_hotkey_release,
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
    """Create a macOS menu bar icon using rumps."""
    global _tray_icon

    try:
        import rumps

        class WafflerMenuBar(rumps.App):
            def __init__(self):
                # Resolve icon path (dev or frozen)
                _icon_path = PROJECT_ROOT / "menubar_icon_template.png"
                if not _icon_path.exists() and hasattr(sys, '_MEIPASS'):
                    _icon_path = Path(sys._MEIPASS) / "menubar_icon_template.png"
                if not _icon_path.exists():
                    _icon_path = Path(sys.executable).parent / "_internal" / "menubar_icon_template.png"

                # Fallback to icon.icns if template not found
                if not _icon_path.exists():
                    _icon_path = PROJECT_ROOT / "icon.icns"
                    if not _icon_path.exists() and hasattr(sys, '_MEIPASS'):
                        _icon_path = Path(sys._MEIPASS) / "icon.icns"
                    if not _icon_path.exists():
                        _icon_path = Path(sys.executable).parent / "_internal" / "icon.icns"

                if _icon_path.exists():
                    super().__init__(None, icon=str(_icon_path), template=False)
                else:
                    super().__init__(None, title="🧇")

            @rumps.clicked("Show Waffler")
            def show_window(self, _):
                _tray_show_window()

            @rumps.clicked("Factory Reset...")
            def factory_reset(self, _):
                _perform_factory_reset()

            @rumps.clicked("Quit")
            def quit_app(self, _):
                _tray_quit()
                rumps.quit_application()

        app = WafflerMenuBar()
        _tray_icon = app
        app.run()
    except Exception as e:
        _log_to_file(f"Mac menu bar error: {e}")


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
    """Show the main window from tray."""
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
    if _should_quit:
        return True  # Allow close
    # Hide window, keep running in background
    if _window_ref:
        try:
            _window_ref.hide()
        except Exception:
            pass
    return False  # Prevent close


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
    _log_to_file(f"=== Waffler starting === (PROJECT_ROOT={PROJECT_ROOT})")

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

    # Intercept close → hide to tray (only if tray icon works)
    # Note: rumps tray icon on Mac must run on main thread (which pywebview owns),
    # so we skip it to avoid NSInternalInconsistencyException that corrupts the app.
    if _platform.system() == "Windows":
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
