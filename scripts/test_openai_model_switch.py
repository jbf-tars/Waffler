"""Smoke-test the OpenAI Whisper model swap.

Confirms:
  1. Default model is now gpt-4o-mini-transcribe (no env override).
  2. OPENAI_WHISPER_MODEL env var override works.
  3. A real API call to gpt-4o-mini-transcribe succeeds with our
     response_format='text' + prompt= + language= kwargs (i.e. the
     endpoint accepts the model name and our call shape).
"""
import io
import os
import sys
import time
import wave
from pathlib import Path

env = Path.home() / ".waffler-hosted" / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Force OpenAI path regardless of whether Groq key is set — we want to test the
# new model against the OpenAI endpoint.
os.environ.pop("GROQ_API_KEY", None)

from transcribe_whisper import WhisperTranscriber  # noqa: E402

openai_key = os.environ.get("OPENAI_API_KEY", "")
if not openai_key:
    print("ERROR: need OPENAI_API_KEY in ~/.waffler-hosted/.env")
    sys.exit(1)


def silent_wav(seconds: float = 1.5, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00" * int(rate * seconds * 2))
    return buf.getvalue()


# 1. Default resolution
t = WhisperTranscriber(api_key=openai_key)
print(f"1. default model: {t.model!r}")
assert t.model == "gpt-4o-mini-transcribe", f"expected gpt-4o-mini-transcribe, got {t.model!r}"

# 2. Env override
os.environ["OPENAI_WHISPER_MODEL"] = "gpt-4o-transcribe"
t2 = WhisperTranscriber(api_key=openai_key)
print(f"2. env-override model: {t2.model!r}")
assert t2.model == "gpt-4o-transcribe"
os.environ.pop("OPENAI_WHISPER_MODEL")

# 3. Explicit constructor argument still wins
t3 = WhisperTranscriber(api_key=openai_key, model="whisper-1")
print(f"3. explicit arg model: {t3.model!r}")
assert t3.model == "whisper-1"

# 4. Live API round-trip with default model
print("\n4. live API test — silent 1.5s WAV -> gpt-4o-mini-transcribe ...")
t4 = WhisperTranscriber(api_key=openai_key)
try:
    result = t4.transcribe_sync(silent_wav(1.5))
    print(f"   model: {t4.model}")
    print(f"   output: {result!r}")
    print("   OK — endpoint accepted the model name and returned successfully")
except Exception as e:
    print(f"   FAIL: {e}")
    sys.exit(1)

# 5. Same for gpt-4o-transcribe
print("\n5. live API test — silent 1.5s WAV -> gpt-4o-transcribe ...")
os.environ["OPENAI_WHISPER_MODEL"] = "gpt-4o-transcribe"
t5 = WhisperTranscriber(api_key=openai_key)
try:
    result = t5.transcribe_sync(silent_wav(1.5))
    print(f"   model: {t5.model}")
    print(f"   output: {result!r}")
    print("   OK — endpoint accepted the model name and returned successfully")
except Exception as e:
    print(f"   FAIL: {e}")
    sys.exit(1)

print("\n=== all checks pass ===")
