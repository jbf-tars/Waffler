#!/bin/bash
# VoiceFlow pipeline health check — runs silently, reports errors to Telegram
cd "$(dirname "$0")"

unset AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT MINIMAX_API_KEY DEEPGRAM_API_KEY
export OPENAI_API_KEY='REDACTED_OPENAI_KEY'

python3 -c "
import sys, os
sys.path.insert(0, 'src')
os.environ.pop('AZURE_OPENAI_API_KEY', None)

from config import Config
from transcribe_whisper import WhisperTranscriber
from style_openai import OpenAIStyler
import wave, struct, math, time

errors = []

try:
    config = Config()
    t = WhisperTranscriber(api_key=config.openai_api_key)
    s = OpenAIStyler(api_key=config.openai_api_key)

    # Generate test audio
    with wave.open('/tmp/vf_test.wav', 'w') as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
        samples = [int(32767 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(32000)]
        wf.writeframes(struct.pack('<' + 'h' * len(samples), *samples))

    with open('/tmp/vf_test.wav', 'rb') as f:
        audio = f.read()

    transcript = t.transcribe_sync(audio)
    styled = s.style(transcript)
    print(f'OK|Whisper:{transcript[:30]}|GPT:{styled[:30]}')
except Exception as e:
    print(f'ERROR|{e}')
    sys.exit(1)
"
