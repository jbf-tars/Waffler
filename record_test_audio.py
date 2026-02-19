#!/usr/bin/env python3
"""
Record a test audio file for automated testing
"""

import sounddevice as sd
import numpy as np
import wave
import sys

def record_test_audio(filename="test_audio/sample.wav", duration=5):
    """Record audio from microphone and save to file"""
    
    sample_rate = 16000
    channels = 1
    
    print(f"\n🎤 Recording {duration} seconds of test audio...")
    print("📢 Say: 'Hello, this is a test of the VoiceFlow application'")
    print("⏱️  Starting in 1 second...")
    
    import time
    time.sleep(1)
    
    print("🔴 RECORDING...")
    
    # Record audio
    recording = sd.rec(
        int(duration * sample_rate),
        samplerate=sample_rate,
        channels=channels,
        dtype='int16'
    )
    sd.wait()
    
    print("✅ Recording complete!")
    
    # Save to WAV file
    with wave.open(filename, 'w') as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(recording.tobytes())
    
    print(f"💾 Saved to: {filename}")
    print(f"📊 Duration: {duration}s, Sample rate: {sample_rate}Hz")
    
    return filename


if __name__ == "__main__":
    import os
    os.makedirs("test_audio", exist_ok=True)
    
    duration = 5
    if len(sys.argv) > 1:
        duration = int(sys.argv[1])
    
    filename = record_test_audio(duration=duration)
    
    print("\n✅ Test audio ready!")
    print(f"   Run: python3 test_with_audio.py {filename}")
