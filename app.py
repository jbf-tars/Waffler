#!/opt/homebrew/bin/python3.12
"""
VoiceFlow — macOS Desktop UI
Entry point: pywebview window + background hotkey/pipeline thread
"""

import sys
import os
import io
import json
import time
import threading
import pyperclip
from pathlib import Path
from datetime import datetime, date

# ── Safe stdout/stderr for frozen exe (Windows cp1252 can't handle emoji) ──
if getattr(sys, 'frozen', False):
    try:
        if sys.stdout and hasattr(sys.stdout, 'buffer'):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        if sys.stderr and hasattr(sys.stderr, 'buffer'):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

# ── Path setup ─────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import webview

from config import Config
from audio import AudioRecorder
import platform as _platform
from hotkey import HotkeyListener
from smart_hotkey import SmartHotkeyListener
if _platform.system() == "Windows":
    from windows_hotkey import WindowsHotkeyListener
from transcribe_whisper import WhisperTranscriber
from style_openai import OpenAIStyler
from license import validate_license, is_validated, get_license_key, LICENSE_FILE
from clipboard import ClipboardManager
from overlay import RecordingOverlay
from audio_devices import (
    list_input_devices,
    get_selected_device_index,
    set_selected_device_index,
    get_selected_device_name,
)
from app_detection import get_active_app


# ── History File ──────────────────────────────────────────────────────
HISTORY_FILE = Path.home() / ".voiceflow" / "history.json"
USAGE_FILE = Path.home() / ".voiceflow" / "usage.json"

# Pricing constants
WHISPER_COST_PER_SECOND = 0.0001  # $0.006/minute = $0.0001/second
GPT4O_MINI_INPUT_COST_PER_1M = 0.15  # $0.15 per 1M input tokens
GPT4O_MINI_OUTPUT_COST_PER_1M = 0.60  # $0.60 per 1M output tokens


def ensure_history_dir():
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)


def ensure_usage_dir():
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_history() -> list:
    ensure_history_dir()
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(history: list):
    ensure_history_dir()
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ── Usage Tracking ──────────────────────────────────────────────────────
def load_usage() -> list:
    """Load usage records from usage.json."""
    ensure_usage_dir()
    if not USAGE_FILE.exists():
        return []
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_usage(usage: list):
    """Save usage records to usage.json."""
    ensure_usage_dir()
    with open(USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(usage, f, ensure_ascii=False, indent=2)


def record_usage(entry_type: str, duration_seconds: float = None, input_tokens: int = 0, output_tokens: int = 0):
    """Record an API usage entry with cost calculation."""
    cost_usd = 0.0
    
    if entry_type == "whisper" and duration_seconds is not None:
        # Whisper: $0.006/minute = $0.0001/second
        cost_usd = duration_seconds * WHISPER_COST_PER_SECOND
    elif entry_type == "gpt":
        # GPT-4o-mini: $0.15/1M input, $0.60/1M output
        cost_usd = (input_tokens / 1_000_000) * GPT4O_MINI_INPUT_COST_PER_1M + \
                   (output_tokens / 1_000_000) * GPT4O_MINI_OUTPUT_COST_PER_1M
    
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "type": entry_type,
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

    def get_history(self) -> list:
        """Return transcript history (newest first)."""
        items = load_history()
        # Return newest first
        return list(reversed(items))

    def copy_item(self, text: str):
        """Copy text to clipboard."""
        try:
            pyperclip.copy(text)
        except Exception as e:
            print(f"[clipboard] Error: {e}")

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
            {"id": "smart",               "name": "📝 Normal",           "desc": "Auto-detects list / prose / task"},
            {"id": "adhd_ramble",         "name": "🌀 Ramble",           "desc": "Organises long brain dumps"},
            {"id": "agentic_engineering", "name": "⚡ Agentic Engineer", "desc": "Structured prompts for Claude / Cursor"},
        ]

    def get_current_mode(self) -> str:
        """Return the currently active prompt mode id."""
        if _pipeline:
            return _pipeline.styler.prompt_style
        return _config.prompt_style if _config else "smart"

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
            return {"name": "Unknown", "suggested_style": "smart", "error": str(e)}

    # ── Audio Device API ──────────────────────────────────────────────

    def get_audio_devices(self) -> list:
        """Return available audio input devices for the UI selector."""
        return list_input_devices()

    def get_selected_device(self) -> dict:
        """Return {index, name} of the currently selected device."""
        idx  = get_selected_device_index()
        name = get_selected_device_name()
        return {"index": idx, "name": name}

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

    # ── License API ──────────────────────────────────────────────────────────

    def check_license(self) -> dict:
        """Check if license is validated."""
        return {
            "is_validated": is_validated(),
            "license_key": get_license_key(),
        }

    def activate_license(self, license_key: str) -> dict:
        """Activate a license key."""
        import platform
        instance_name = f"{platform.system()} Device"
        result = validate_license(license_key, instance_name)
        return result

    # ── Settings API ──────────────────────────────────────────────────────────

    def _settings_file(self):
        return Path.home() / ".voiceflow" / "settings.json"

    def _load_settings_file(self) -> dict:
        try:
            sf = self._settings_file()
            if sf.exists():
                return json.loads(sf.read_text())
        except Exception:
            pass
        return {}

    def _save_settings_file(self, data: dict):
        sf = self._settings_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps(data, indent=2))

    def _update_env_var(self, key: str, value: str):
        """Update or add a variable in the user's .env file."""
        env_path = Path.home() / ".voiceflow" / ".env"
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
        if len(key) > 12:
            masked = key[:8] + "…" + key[-4:]
        elif key:
            masked = "*" * len(key)
        else:
            masked = ""
        local_whisper_active = _pipeline and hasattr(_pipeline.transcriber, "_backend") and \
                               _pipeline.transcriber._backend in ("mlx", "faster")
        return {
            "api_key_set":           bool(key),
            "api_key_masked":        masked,
            "local_whisper":         os.getenv("LOCAL_WHISPER", "0") == "1",
            "local_whisper_active":  local_whisper_active,
            "language":              stored.get("language", "auto"),
            "auto_paste":            stored.get("auto_paste", True),
        }

    def save_settings(self, settings: dict) -> dict:
        """Save settings — updates .env and/or settings.json, applies live where possible."""
        try:
            stored = self._load_settings_file()
            notes  = []

            # ── API key ──────────────────────────────────────────────────────
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
                notes.append("API key updated")

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
            "# VoiceFlow — Transcript History",
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
        """Open a URL in the system browser."""
        import webbrowser
        webbrowser.open(url)

    def get_onboarding_status(self) -> dict:
        """Returns whether the app needs first-run setup."""
        key = os.getenv("OPENAI_API_KEY", "").strip()
        setup_done = _is_setup_complete()
        return {
            "needs_setup": not setup_done or not bool(key),
            "has_key": bool(key),
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

    def test_hotkey(self) -> dict:
        """Return hotkey configuration info for the current platform."""
        import platform as plat
        is_win = plat.system() == "Windows"
        return {
            "ok": True,
            "platform": plat.system(),
            "hotkey": "Right Ctrl + Right Alt" if is_win else "Right Option (hold)",
            "mode": "toggle" if is_win else "hold",
            "description": (
                "Press Right Ctrl + Right Alt together to start recording. Press again to stop."
            ) if is_win else (
                "Hold the Right Option key to record. Release to stop."
            ),
        }

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
            return {"ok": True, "message": "Setup complete! VoiceFlow is ready."}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Snippets API ──────────────────────────────────────────────────────────

    def _snippets_file(self):
        return Path.home() / ".voiceflow" / "snippets.json"

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
        """Save snippets list."""
        try:
            sf = self._snippets_file()
            sf.parent.mkdir(parents=True, exist_ok=True)
            sf.write_text(json.dumps(snippets, indent=2))
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


# ── Global refs ───────────────────────────────────────────────────────
_window   = None
_api      = None
_pipeline = None   # set after VoiceFlowPipeline is created
_config   = None   # set in main()

SETUP_FILE = Path.home() / ".voiceflow" / "setup_complete.json"


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


def _initialize_pipeline():
    """Create pipeline and start hotkey after setup is complete."""
    global _pipeline
    if _pipeline:
        return  # already initialized

    _config.reload_env()

    if not _config.has_api_key:
        print("Cannot initialize pipeline: no API key")
        return

    try:
        pipeline = VoiceFlowPipeline(_config)
        _pipeline = pipeline

        hotkey_thread = threading.Thread(
            target=pipeline.start_hotkey,
            daemon=True,
            name="HotkeyThread"
        )
        hotkey_thread.start()
        print(f"Hotkey active: {_config.hotkey}")
    except Exception as e:
        print(f"Pipeline init error: {e}")
        import traceback
        traceback.print_exc()


def set_window(w):
    global _window
    _window = w


def notify_js_status(status: str):
    """Tell the JS frontend about recording status."""
    if _window:
        try:
            _window.evaluate_js(f"window.voiceflow_status && window.voiceflow_status('{status}')")
        except Exception:
            pass


def notify_js_new_item(item: dict):
    """Push a new transcript item to the JS frontend."""
    if _window:
        try:
            item_json = json.dumps(item)
            _window.evaluate_js(
                f"window.voiceflow_refresh && window.voiceflow_refresh({item_json})"
            )
        except Exception as e:
            print(f"[js] notify error: {e}")


# ── Pipeline ──────────────────────────────────────────────────────────
class VoiceFlowPipeline:
    def __init__(self, config: Config):
        self.config = config
        self.audio = AudioRecorder(
            sample_rate=config.sample_rate,
            channels=config.channels
        )

        # Transcriber (OpenAI Whisper)
        if config.openai_api_key:
            self.transcriber = WhisperTranscriber(
                api_key=config.openai_api_key,
                model="whisper-1"
            )
            print("🎤 Using OpenAI Whisper")
        else:
            raise ValueError("OPENAI_API_KEY is required")

        # Styler (GPT-4o-mini)
        self.styler = OpenAIStyler(
            api_key=config.openai_api_key,
            model="gpt-4o-mini",
            max_tokens=config.minimax_max_tokens,
            prompt_style=config.prompt_style
        )
        print("🤖 Using GPT-4o-mini")

        self.clipboard = ClipboardManager()
        self.is_recording = False
        self._is_paused = False

        # Floating recording overlay
        self.overlay = RecordingOverlay(
            on_cancel=self._on_overlay_cancel,
            on_stop=self._on_overlay_stop,
        )
        self._prev_window = None  # focused window before recording starts

        # Use persisted audio device (if set)
        saved_idx = get_selected_device_index()
        if saved_idx is not None:
            self._device_index = saved_idx
        else:
            self._device_index = None  # sounddevice default

    def set_device(self, device_index: int):
        """Update the audio device used for future recordings."""
        self._device_index = device_index
        print(f"🎤 Audio device changed to index {device_index}")

    def _on_overlay_cancel(self):
        """User clicked X on overlay — cancel recording."""
        if self.is_recording:
            self.is_recording = False
            self.audio.stop()        # discard audio
            self.overlay.hide()
            notify_js_status("idle")
            print("🚫 Recording cancelled by user")

    def _on_overlay_stop(self):
        """User clicked ■ on overlay — stop & process."""
        if self.is_recording:
            self.on_hotkey_release()

    def on_hotkey_press(self):
        """Start recording."""
        if self.is_recording:
            return
        # Capture focused window BEFORE overlay takes focus
        self._prev_window = self.clipboard.get_focused_window()
        print("🎤 Recording started…")
        self.is_recording = True
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
        print("🛑 Recording stopped, processing…")
        self.is_recording = False
        self._is_paused = False
        notify_js_status("processing")
        try:
            self.overlay.hide()
        except Exception as e:
            print(f"[overlay] hide failed: {e}")
        threading.Thread(target=self._process, daemon=True).start()

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
            print("⏸️ Recording paused")
        else:
            self.overlay.update_state("recording")
            notify_js_status("listening")
            print("▶️ Recording resumed")

    def _level_loop(self):
        """Feed live audio level to the overlay at ~30fps while recording."""
        import time as _time
        while self.is_recording:
            lvl = self.audio.get_level()
            try:
                self.overlay.update_level(lvl)
            except Exception:
                pass
            _time.sleep(0.033)  # ~30 fps

    def _process(self):
        try:
            audio_bytes = self.audio.stop()
            if not audio_bytes:
                notify_js_status("idle")
                return

            # Transcribe
            transcript = self.transcriber.transcribe_sync(audio_bytes)
            if not transcript:
                notify_js_status("idle")
                return

            # Apply vocabulary fuzzy matching corrections
            from transcribe_whisper import load_vocab, apply_vocab_corrections
            vocab = load_vocab()
            if vocab:
                transcript, corrections = apply_vocab_corrections(transcript, vocab)
                if corrections:
                    print(f"📖 Vocabulary corrections applied: {', '.join(corrections)}")

            # Record Whisper usage - calculate from audio bytes (works for all backends)
            # Audio is 16kHz, 16-bit mono = 32000 bytes/second
            whisper_duration = len(audio_bytes) / 32000.0
            if whisper_duration > 0:
                record_usage("whisper", duration_seconds=whisper_duration)

            # Style
            styled, gpt_usage = self.styler.style(transcript)
            if not styled:
                styled = transcript

            # Record GPT usage (if API was used)
            if gpt_usage.get("api_used"):
                record_usage(
                    "gpt",
                    input_tokens=gpt_usage.get("input_tokens", 0),
                    output_tokens=gpt_usage.get("output_tokens", 0)
                )

            # Apply snippets (text expansion)
            styled = self._apply_snippets(styled)

            # Copy to clipboard
            self.clipboard.copy(styled)

            # Auto-paste (respects settings)
            stored = {}
            _sf = Path.home() / ".voiceflow" / "settings.json"
            try:
                if _sf.exists():
                    stored = json.loads(_sf.read_text())
            except Exception:
                pass
            if stored.get("auto_paste", True):
                self.clipboard.auto_paste(self._prev_window)

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

            print(f"✅ Done: {styled[:80]}…")

        except Exception as e:
            print(f"❌ Pipeline error: {e}")
            import traceback
            traceback.print_exc()
            notify_js_status("idle")

    def _apply_snippets(self, text: str) -> str:
        """Replace snippet trigger phrases with their expansions."""
        import re
        snip_file = Path.home() / ".voiceflow" / "snippets.json"
        try:
            if snip_file.exists():
                snippets = json.loads(snip_file.read_text())
                for s in snippets:
                    trigger   = s.get("trigger", "").strip()
                    expansion = s.get("expansion", "")
                    if trigger:
                        pattern = rf'(?i)\b{re.escape(trigger)}\b'
                        text = re.sub(pattern, expansion, text)
        except Exception as e:
            print(f"[snippets] error: {e}")
        return text

    def start_hotkey(self):
        """Start the hotkey listener — platform-specific."""
        if _platform.system() == "Windows":
            hotkey = WindowsHotkeyListener(
                on_press=self.on_hotkey_press,
                on_release=self.on_hotkey_release,
            )
        else:
            hotkey = SmartHotkeyListener(
                on_press=self.on_hotkey_press,
                on_release=self.on_hotkey_release,
            )
        hotkey.start()
        hotkey.join()


# ── Main ──────────────────────────────────────────────────────────────
def main():
    global _config

    # Load config (reads .env from project root via dotenv)
    os.chdir(PROJECT_ROOT)  # so config.yaml and .env are found

    try:
        config = Config()
    except Exception as e:
        print(f"Config error: {e}")
        sys.exit(1)

    _config = config

    # Only auto-initialize pipeline if setup was already completed
    if config.has_api_key and _is_setup_complete():
        _initialize_pipeline()

    # Create pywebview window (always — wizard runs inside it)
    api = Api()
    _api_ref = api  # keep reference

    ui_dir = PROJECT_ROOT / "ui"
    html_path = ui_dir / "index.html"

    window = webview.create_window(
        title="VoiceFlow",
        url=str(html_path),
        width=900,
        height=640,
        min_size=(700, 480),
        resizable=True,
        background_color="#0d0d0f",
        js_api=api,
        frameless=False,
        easy_drag=False,
    )

    set_window(window)

    print("VoiceFlow window launching...")
    # Start webview — this blocks until window is closed
    webview.start(debug=False)

    print("Window closed.")


if __name__ == "__main__":
    main()
