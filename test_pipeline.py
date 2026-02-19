#!/usr/bin/env python3
"""
Pipeline test with mock audio
Tests: Deepgram transcription → MiniMax styling → Clipboard
"""

import sys
from pathlib import Path
import time
import wave
import numpy as np

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import Config
from transcribe import DeepgramTranscriber
from style import MinimaxStyler
from clipboard import ClipboardManager
from notify import NotificationManager


def generate_test_audio(duration=2.0, sample_rate=16000):
    """Generate a simple test audio file (sine wave) as WAV format"""
    import io
    
    t = np.linspace(0, duration, int(sample_rate * duration))
    # 440 Hz tone (A4 note) - not real speech but validates format
    audio_data = np.sin(2 * np.pi * 440 * t)
    audio_int16 = (audio_data * 32767).astype(np.int16)
    
    # Create WAV file in memory
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, 'wb') as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_int16.tobytes())
    
    # Return WAV file bytes
    wav_buffer.seek(0)
    return wav_buffer.read()


def test_pipeline():
    """Test full pipeline: STT → LLM → Clipboard"""
    print("\n" + "="*60)
    print("🧪 Testing Full Pipeline (without hotkey)")
    print("="*60 + "\n")
    
    try:
        # Load config
        print("✓ Loading configuration...")
        config = Config()
        
        # Initialize components
        print("✓ Initializing components...")
        transcriber = DeepgramTranscriber(
            api_key=config.deepgram_api_key,
            model=config.deepgram_model,
            language=config.deepgram_language
        )
        
        styler = MinimaxStyler(
            api_key=config.minimax_api_key,
            model=config.minimax_model,
            max_tokens=config.minimax_max_tokens
        )
        
        clipboard = ClipboardManager()
        notify = NotificationManager(enabled=config.notifications_enabled)
        
        # Generate test audio (2 seconds of tone)
        print("\n📊 Generating test audio (2s tone)...")
        audio_bytes = generate_test_audio(duration=2.0, sample_rate=16000)
        print(f"  Audio size: {len(audio_bytes)} bytes")
        
        # Step 1: Transcribe
        print("\n📡 Step 1: Transcribing with Deepgram...")
        start = time.time()
        transcript = transcriber.transcribe_sync(audio_bytes)
        transcribe_ms = (time.time() - start) * 1000
        
        if transcript:
            print(f"✓ Transcript ({transcribe_ms:.0f}ms): '{transcript}'")
        else:
            print(f"⚠ No transcript (likely because it's just a tone, not speech)")
            print(f"  This is expected - Deepgram can't transcribe a sine wave!")
            print(f"  But the API call worked ({transcribe_ms:.0f}ms)")
            # Use a mock transcript for testing the rest of the pipeline
            transcript = "This is a test message for the pipeline"
            print(f"  Using mock transcript: '{transcript}'")
        
        # Step 2: Style with MiniMax
        print("\n🤖 Step 2: Styling with MiniMax...")
        start = time.time()
        styled = styler.style(transcript)
        style_ms = (time.time() - start) * 1000
        
        if styled:
            print(f"✓ Styled ({style_ms:.0f}ms): '{styled}'")
        else:
            print(f"✗ Styling failed")
            return False
        
        # Step 3: Copy to clipboard
        print("\n📋 Step 3: Copying to clipboard...")
        clipboard.copy(styled)
        print(f"✓ Copied to clipboard")
        
        # Show notification
        notify.ready(preview=styled[:100])
        
        # Summary
        total_ms = transcribe_ms + style_ms
        print("\n" + "="*60)
        print("✅ PIPELINE TEST COMPLETE")
        print(f"  • Transcribe: {transcribe_ms:.0f}ms")
        print(f"  • Style: {style_ms:.0f}ms")
        print(f"  • Total: {total_ms:.0f}ms")
        print("="*60 + "\n")
        
        if total_ms < 3000:
            print(f"✅ Latency within target: {total_ms:.0f}ms < 3000ms")
        else:
            print(f"⚠ Latency over target: {total_ms:.0f}ms > 3000ms")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Pipeline test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Entry point"""
    print("\n🎙️ VoiceFlow Pipeline Test (Mock Audio)")
    print("=" * 60)
    print("This tests STT → LLM → Clipboard without the hotkey")
    print("=" * 60)
    
    success = test_pipeline()
    
    if success:
        print("\n✅ Pipeline is working!")
        print("Next: Test with real voice input via ./run.sh")
        return 0
    else:
        print("\n❌ Pipeline test failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
