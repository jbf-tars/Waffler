#!/usr/bin/env python3
"""
VoiceFlow - Voice-to-Text Command Assistant
Main orchestrator
"""

import sys
import time
import threading
import platform as _platform
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import Config
from audio import AudioRecorder
from hotkey import HotkeyListener
from transcribe import DeepgramTranscriber
from transcribe_whisper import WhisperTranscriber
from transcribe_azure_whisper import AzureWhisperTranscriber
from style import MinimaxStyler
from style_openai import OpenAIStyler
from clipboard import ClipboardManager
from notify import NotificationManager


class VoiceFlow:
    """Main pipeline orchestrator"""
    
    def __init__(self, config: Config):
        self.config = config
        
        # Initialize components
        self.audio = AudioRecorder(
            sample_rate=config.sample_rate,
            channels=config.channels
        )
        
        # Use Whisper (like Whispr Flow) for better accuracy
        if hasattr(config, 'azure_openai_api_key') and config.azure_openai_api_key:
            self.transcriber = AzureWhisperTranscriber(
                api_key=config.azure_openai_api_key,
                endpoint=config.azure_openai_endpoint,
                deployment="whisper"
            )
            print("🎤 Using Azure OpenAI Whisper (like Whispr Flow)")
        elif hasattr(config, 'openai_api_key') and config.openai_api_key:
            self.transcriber = WhisperTranscriber(
                api_key=config.openai_api_key,
                model="whisper-1"
            )
            print("🎤 Using OpenAI Whisper (like Whispr Flow)")
        else:
            self.transcriber = DeepgramTranscriber(
                api_key=config.deepgram_api_key,
                model=config.deepgram_model,
                language=config.deepgram_language
            )
            print("🎤 Using Deepgram")
        
        # Use OpenAI GPT if no MiniMax key (preferred - same key as Whisper)
        if config.minimax_api_key:
            self.styler = MinimaxStyler(
                api_key=config.minimax_api_key,
                model=config.minimax_model,
                max_tokens=config.minimax_max_tokens,
                prompt_style=config.prompt_style
            )
            print("🤖 Using MiniMax for LLM cleanup")
        elif config.openai_api_key:
            self.styler = OpenAIStyler(
                api_key=config.openai_api_key,
                model="gpt-4o-mini",
                max_tokens=config.minimax_max_tokens,
                prompt_style=config.prompt_style
            )
            print("🤖 Using OpenAI GPT-4o-mini for LLM cleanup")
        else:
            # No LLM - just pass through the transcript
            self.styler = None
            print("⚠️  No LLM configured - transcript will be used as-is")
        
        self.clipboard = ClipboardManager()
        self.notify = NotificationManager(enabled=config.notifications_enabled)
        
        # State
        self.is_recording = False
        self.recording_thread = None
        
    def on_hotkey_press(self):
        """Called when hotkey is pressed - start recording"""
        if self.is_recording:
            return
            
        print("\n" + "="*60)
        print("🎤 RECORDING - Hold hotkey and speak...")
        print("="*60)
        
        self.is_recording = True
        self.audio.start()
        self.notify.listening()
        
        # Start recording thread
        self.recording_thread = threading.Thread(target=self._record_loop)
        self.recording_thread.start()
        
    def on_hotkey_release(self):
        """Called when hotkey is released - stop recording and process"""
        if not self.is_recording:
            return
            
        print("\n" + "="*60)
        print("🛑 PROCESSING - Transcribing and styling...")
        print("="*60)
        
        self.is_recording = False
        
        # Wait for recording thread to finish
        if self.recording_thread:
            self.recording_thread.join()
            
        # Process in background thread
        threading.Thread(target=self._process_audio).start()
        
    def _record_loop(self):
        """Record audio in chunks while hotkey is held"""
        while self.is_recording:
            self.audio.record_chunk(duration=0.1)  # 100ms chunks
            
    def _process_audio(self):
        """Process recorded audio through the pipeline"""
        start_time = time.time()
        
        try:
            # Stop recording and get audio bytes
            audio_bytes = self.audio.stop()
            
            if not audio_bytes:
                self.notify.error("No audio recorded")
                return
                
            duration = self.audio.get_duration()
            print(f"📊 Audio duration: {duration:.2f}s")
            
            # Show processing notification
            self.notify.processing()
            
            # Step 1: Transcribe with Deepgram
            print("\n📡 Transcribing...")
            t1 = time.time()
            transcript = self.transcriber.transcribe_sync(audio_bytes)
            transcribe_ms = (time.time() - t1) * 1000
            
            if not transcript:
                self.notify.error("No transcript generated")
                return
            
            print(f"✓ Transcript ({transcribe_ms:.0f}ms): {transcript}")
                
            # Step 2: Style with LLM (GPT or MiniMax) or pass through
            if self.styler:
                print("\n🤖 Styling...")
                t2 = time.time()
                command = self.styler.style(transcript)
                style_ms = (time.time() - t2) * 1000
                
                if not command:
                    self.notify.error("No command generated")
                    return
                
                print(f"✓ Command ({style_ms:.0f}ms): {command}")
            else:
                command = transcript
                style_ms = 0
                print(f"✓ Using transcript directly: {command}")
                
            # Step 3: Copy to clipboard
            print("\n📋 Copying to clipboard...")
            self.clipboard.copy(command)
            
            # Show ready notification
            self.notify.ready(preview=command[:100])  # First 100 chars
            
            # Calculate total latency
            total_latency = (time.time() - start_time) * 1000
            print("\n" + "="*60)
            print(f"✅ READY ({total_latency:.0f}ms total)")
            print(f"   • Transcribe: {transcribe_ms:.0f}ms")
            print(f"   • Style: {style_ms:.0f}ms")
            print(f"📝 Command ready to paste!")
            print("="*60 + "\n")
            
            # Check latency budget
            if total_latency > 3000:
                print(f"⚠️  Latency over target: {total_latency:.0f}ms > 3000ms")
            else:
                print(f"✅ Latency within target: {total_latency:.0f}ms ≤ 3000ms")
                
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
            self.notify.error(str(e))
            
    def run(self):
        """Start the pipeline"""
        print("\n" + "="*60)
        print("🎙️  VoiceFlow - Voice-to-Text Command Assistant")
        print("="*60)
        print(f"⌨️  Hotkey: {self.config.hotkey}")
        print(f"🎤 Sample rate: {self.config.sample_rate}Hz")
        print(f"📡 STT: Deepgram {self.config.deepgram_model}")
        print(f"🤖 LLM: MiniMax {self.config.minimax_model}")
        print("="*60)
        # Print available mics so we can see which one is being used
        self.audio.print_devices()
        # Set up hotkey listener
        if _platform.system() == "Darwin":
            # Mac: Use SmartHotkeyListener with Fn key
            from smart_hotkey import SmartHotkeyListener
            self.hotkey = SmartHotkeyListener(
                on_press=self.on_hotkey_press,
                on_release=self.on_hotkey_release
            )
            print("\n🧇 Waffler ready! Hold Fn to dictate, Fn+Space for hands-free mode\n")
        else:
            # Windows: Use existing HotkeyListener with Ctrl+Space
            self.hotkey = HotkeyListener(
                combination=self.config.hotkey,
                on_press=self.on_hotkey_press,
                on_release=self.on_hotkey_release
            )
            print(f"\n✅ Ready! Press and hold {self.config.hotkey} to record.\n")
        
        # Start listening
        self.hotkey.start()
        
        try:
            # Block main thread
            self.hotkey.join()
        except KeyboardInterrupt:
            print("\n\n🛑 Shutting down...")
            self.hotkey.stop()
            

def main():
    """Entry point"""
    try:
        # Load configuration
        config = Config()
        
        # Create and run pipeline
        pipeline = VoiceFlow(config)
        pipeline.run()
        
    except FileNotFoundError as e:
        print(f"❌ Error: {e}")
        print("💡 Make sure config.yaml and .env exist")
        sys.exit(1)
        
    except ValueError as e:
        print(f"❌ Error: {e}")
        print("💡 Make sure .env has required API keys:")
        print("   DEEPGRAM_API_KEY=...")
        print("   MINIMAX_API_KEY=...")
        sys.exit(1)
        
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
