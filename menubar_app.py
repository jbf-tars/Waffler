#!/usr/bin/env python3
"""
VoiceFlow Menu Bar App
macOS menu bar interface for voice dictation (ADHD-optimised)
Uses rumps for the menu bar UI.
"""

import sys
import os
import threading
import time
from pathlib import Path

# ── Nuke conflicting env vars BEFORE loading dotenv ──────────────────────────
for _var in ['AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_ENDPOINT',
             'MINIMAX_API_KEY', 'DEEPGRAM_API_KEY']:
    os.environ.pop(_var, None)

# ── Load .env from project root ───────────────────────────────────────────────
from dotenv import load_dotenv
_env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=_env_path, override=True)

# ── Validate API key early ────────────────────────────────────────────────────
_api_key = os.getenv('OPENAI_API_KEY')
if not _api_key:
    print("❌ OPENAI_API_KEY not found in .env")
    sys.exit(1)

# ── Add src to path ───────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent / 'src'))

import rumps
from audio import AudioRecorder
from transcribe_whisper import WhisperTranscriber
from style_openai import OpenAIStyler
from clipboard import ClipboardManager
from notify import NotificationManager

# ── Icons ─────────────────────────────────────────────────────────────────────
ICON_IDLE       = "VF"
ICON_RECORDING  = "VF ●"
ICON_PROCESSING = "VF ..."


class VoiceFlowApp(rumps.App):
    """VoiceFlow menu bar application"""

    def __init__(self):
        # Build menu items first
        self.recording_item = rumps.MenuItem("Start Recording", callback=self.toggle_recording)
        self.last_item      = rumps.MenuItem("Last transcript: —")
        self.last_item.set_callback(None)   # greyed-out by no callback

        super().__init__(
            name="VoiceFlow",
            title=ICON_IDLE,
            menu=[
                rumps.MenuItem("VoiceFlow", callback=None),   # title row, no callback
                None,                                          # separator
                self.recording_item,
                self.last_item,
                None,                                          # separator
            ],
            quit_button="Quit",
        )

        # ── Pipeline components ───────────────────────────────────────────────
        openai_key = os.getenv('OPENAI_API_KEY')
        self.audio      = AudioRecorder(sample_rate=16000, channels=1)
        self.transcriber = WhisperTranscriber(api_key=openai_key, model="whisper-1")
        self.styler      = OpenAIStyler(api_key=openai_key, model="gpt-4o-mini",
                                         prompt_style="adhd_ramble")
        self.clipboard   = ClipboardManager()
        self.notifier    = NotificationManager(enabled=True)

        # ── State ─────────────────────────────────────────────────────────────
        self.is_recording   = False
        self._record_thread = None
        self._process_thread = None

    # ─────────────────────────────────────────────────────────────────────────
    # Menu callbacks
    # ─────────────────────────────────────────────────────────────────────────

    def toggle_recording(self, sender):
        """Start or stop recording when user clicks menu item"""
        if self.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    # ─────────────────────────────────────────────────────────────────────────
    # Recording lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def _start_recording(self):
        """Begin capturing microphone audio"""
        if self.is_recording:
            return

        self.is_recording = True
        self.title = ICON_RECORDING
        self.recording_item.title = "Recording…  (click to stop)"
        self.notifier.listening()

        # Record in background thread
        self._record_thread = threading.Thread(target=self._record_loop, daemon=True)
        self._record_thread.start()

    def _stop_recording(self):
        """Stop capture and kick off processing"""
        if not self.is_recording:
            return

        self.is_recording = False
        self.title = ICON_PROCESSING
        self.recording_item.title = "Processing…"

        # Let the record loop finish, then process
        def wait_and_process():
            if self._record_thread:
                self._record_thread.join(timeout=2)
            self._process_audio()

        self._process_thread = threading.Thread(target=wait_and_process, daemon=True)
        self._process_thread.start()

    def _record_loop(self):
        """Continuously capture audio chunks while is_recording is True"""
        self.audio.start()
        while self.is_recording:
            self.audio.record_chunk(duration=0.1)

    # ─────────────────────────────────────────────────────────────────────────
    # Pipeline: transcribe → style → clipboard → notify
    # ─────────────────────────────────────────────────────────────────────────

    def _process_audio(self):
        start = time.time()
        try:
            # Get audio bytes
            audio_bytes = self.audio.stop()
            if not audio_bytes:
                self._reset_ui("No audio captured")
                return

            self.notifier.processing()

            # 1. Transcribe
            print("📡 Transcribing via Whisper…")
            transcript = self.transcriber.transcribe_sync(audio_bytes)
            if not transcript:
                self._reset_ui("No transcript")
                return

            print(f"📝 Transcript: {transcript[:80]}")

            # 2. Style / clean up
            print("🤖 Styling via GPT-4o-mini…")
            styled = self.styler.style(transcript)
            if not styled:
                styled = transcript  # fallback: use raw transcript

            print(f"✅ Styled: {styled[:80]}")

            # 3. Copy to clipboard
            self.clipboard.copy(styled)

            # 4. Update UI
            preview = styled[:40] + "…" if len(styled) > 40 else styled
            self.last_item.title = f"Last: {preview}"

            # 5. Notify user
            self.notifier.ready(preview=styled)

            total_ms = (time.time() - start) * 1000
            print(f"✅ Done in {total_ms:.0f}ms — text in clipboard, press Cmd+V to paste")

        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.notifier.error(str(exc))
            self._reset_ui(f"Error: {exc}")
        finally:
            self._reset_ui_idle()

    def _reset_ui(self, status: str = ""):
        """Show a transient status then reset to idle"""
        if status:
            print(f"⚠️  {status}")

    def _reset_ui_idle(self):
        """Return icon and menu item to idle state"""
        self.title = ICON_IDLE
        self.recording_item.title = "Start Recording"


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("🎙️  VoiceFlow Menu Bar — starting…")
    print(f"   OPENAI_API_KEY: {'✅ set' if os.getenv('OPENAI_API_KEY') else '❌ missing'}")
    try:
        print("   Initialising app...")
        app = VoiceFlowApp()
        print("   App created — launching menu bar...")
        app.run()
    except Exception as e:
        import traceback
        print(f"\n❌ CRASH: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
