import sounddevice as sd
import numpy as np
import wave

print('🎤 Recording 3 seconds... SPEAK NOW')
audio = sd.rec(16000 * 3, samplerate=16000, channels=1, dtype='int16')
sd.wait()

rms = np.sqrt(np.mean(audio.astype(np.float32) ** 2))
status = '✅ mic working' if rms > 200 else '❌ silence - wrong mic or permission issue'
print(f'Volume level: {rms:.0f} - {status}')

with wave.open('/Users/james/Desktop/mic_test.wav', 'w') as f:
    f.setnchannels(1)
    f.setsampwidth(2)
    f.setframerate(16000)
    f.writeframes(audio.tobytes())

print('Saved to Desktop as mic_test.wav - open in QuickTime')
