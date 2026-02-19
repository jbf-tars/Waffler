#!/usr/bin/env python3
"""
Automated testing with pre-generated audio files
No microphone needed - uses test audio samples
"""

import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import Config
from transcribe import DeepgramTranscriber
from style import MinimaxStyler
from clipboard import ClipboardManager
import wave
import struct


def generate_test_audio(filename: str, text: str, duration: float = 2.0):
    """Generate a simple test audio file (silence for now, can use TTS later)"""
    sample_rate = 16000
    channels = 1
    
    # Generate silence for now (you can replace with actual TTS)
    num_samples = int(sample_rate * duration)
    
    with wave.open(filename, 'w') as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        
        # Write silence (zeros)
        for _ in range(num_samples):
            wav_file.writeframes(struct.pack('h', 0))
    
    print(f"✅ Generated test audio: {filename}")


def test_pipeline_with_file(audio_file: str):
    """Test the full pipeline with a pre-recorded audio file"""
    
    print("\n" + "="*60)
    print("🧪 VoiceFlow Automated Test")
    print("="*60)
    
    # Load config
    config = Config()
    
    # Initialize components
    transcriber = DeepgramTranscriber(
        api_key=config.deepgram_api_key,
        model=config.deepgram_model,
        language=config.deepgram_language
    )
    
    styler = MinimaxStyler(
        api_key=config.minimax_api_key,
        model=config.minimax_model,
        max_tokens=config.minimax_max_tokens,
        prompt_style=config.prompt_style
    )
    
    clipboard = ClipboardManager()
    
    # Read audio file
    print(f"\n📁 Reading audio file: {audio_file}")
    with open(audio_file, 'rb') as f:
        audio_bytes = f.read()
    
    print(f"📊 Audio size: {len(audio_bytes)} bytes")
    
    # Test 1: Transcription
    print("\n📡 Testing Deepgram transcription...")
    t1 = time.time()
    transcript = transcriber.transcribe_sync(audio_bytes)
    transcribe_ms = (time.time() - t1) * 1000
    
    print(f"✓ Transcript ({transcribe_ms:.0f}ms): {transcript}")
    
    if not transcript:
        print("❌ No transcript generated - audio might be empty")
        return False
    
    # Test 2: Styling
    print("\n🤖 Testing MiniMax styling...")
    t2 = time.time()
    command = styler.style(transcript)
    style_ms = (time.time() - t2) * 1000
    
    print(f"✓ Command ({style_ms:.0f}ms): {command}")
    
    if not command:
        print("❌ No command generated")
        return False
    
    # Test 3: Clipboard
    print("\n📋 Testing clipboard...")
    clipboard.copy(command)
    clipboard_text = clipboard.paste()
    
    if clipboard_text == command:
        print(f"✓ Clipboard: {clipboard_text}")
    else:
        print(f"❌ Clipboard mismatch: got '{clipboard_text}', expected '{command}'")
        return False
    
    # Summary
    total_ms = transcribe_ms + style_ms
    print("\n" + "="*60)
    print(f"✅ ALL TESTS PASSED ({total_ms:.0f}ms total)")
    print(f"   • Transcribe: {transcribe_ms:.0f}ms")
    print(f"   • Style: {style_ms:.0f}ms")
    print("="*60 + "\n")
    
    return True


def main():
    """Run automated tests"""
    
    # Test audio files directory
    test_dir = Path(__file__).parent / "test_audio"
    test_dir.mkdir(exist_ok=True)
    
    # Generate test audio files
    print("🎤 Generating test audio files...")
    
    test_cases = [
        ("hello.wav", "Hello, this is a test", 2.0),
        ("ramble.wav", "So um, I was thinking, like, we should probably, uh, build that thing", 4.0),
        ("command.wav", "Create a Python script that reads CSV files", 3.0),
    ]
    
    for filename, text, duration in test_cases:
        audio_path = test_dir / filename
        if not audio_path.exists():
            generate_test_audio(str(audio_path), text, duration)
    
    print("\n⚠️  NOTE: Generated files are silent (placeholder)")
    print("   To get real audio, record yourself saying the test phrases")
    print("   Or use a TTS service to generate them")
    
    # For now, use a real audio file if available
    # Otherwise, skip the test
    print("\n" + "="*60)
    print("To run a real test:")
    print("1. Record a short audio file (WAV, 16kHz, mono)")
    print("2. Save it as test_audio/sample.wav")
    print("3. Run this script again")
    print("="*60 + "\n")
    
    # Check if user provided an audio file
    if len(sys.argv) > 1:
        audio_file = sys.argv[1]
        if Path(audio_file).exists():
            success = test_pipeline_with_file(audio_file)
            sys.exit(0 if success else 1)
        else:
            print(f"❌ Audio file not found: {audio_file}")
            sys.exit(1)
    
    # Check for sample file
    sample_file = test_dir / "sample.wav"
    if sample_file.exists():
        print(f"✅ Found test audio: {sample_file}")
        success = test_pipeline_with_file(str(sample_file))
        sys.exit(0 if success else 1)
    else:
        print("ℹ️  No test audio file found")
        print("   Create test_audio/sample.wav to test the pipeline")
        sys.exit(0)


if __name__ == "__main__":
    main()
