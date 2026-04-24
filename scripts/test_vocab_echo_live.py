"""End-to-end test of the _is_vocab_echo guard.

Feeds silent audio through the real WhisperTranscriber (with vocab.json
populated) and verifies that if Whisper echoes the vocab prompt, the
guard discards it — the final output must be empty, not a paste of the
user's vocabulary list.

Two cases:
  1. Silent 2s WAV + vocab prompt. Typical hallucination territory.
  2. Silent 2s WAV + NO vocab (control). Confirms baseline behaviour.

30s pause between real API calls.
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
from transcribe_whisper import WhisperTranscriber, load_vocab, _is_vocab_echo  # noqa: E402


def silent_wav(seconds: float = 2.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)          # 16-bit
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(rate * seconds * 2))
    return buf.getvalue()


groq_key = os.environ.get("GROQ_API_KEY", "")
openai_key = os.environ.get("OPENAI_API_KEY", "")
if not openai_key and not groq_key:
    print("ERROR: need GROQ_API_KEY or OPENAI_API_KEY in ~/.waffler-hosted/.env")
    sys.exit(1)

vocab = load_vocab()
print(f"vocab.json = {vocab}")

trx = WhisperTranscriber(api_key=openai_key, groq_api_key=groq_key)
print(f"transcriber backend = {trx._backend}")
print()


def run(label: str, vocab_override_env: bool = False):
    """Run one transcription case. If vocab_override_env, temporarily
    blank the vocab so the transcriber sees an empty list."""
    print(f"=== {label} ===")
    original = None
    if vocab_override_env:
        # Temporarily rename vocab.json so load_vocab() returns [].
        vpath = Path.home() / ".waffler-hosted" / "vocab.json"
        if vpath.exists():
            original = vpath.read_text()
            vpath.write_text("[]")
    try:
        audio = silent_wav(2.0)
        t0 = time.time()
        out = trx.transcribe_sync(audio)
        dt = (time.time() - t0) * 1000
        print(f"  elapsed:  {dt:.0f}ms")
        print(f"  output:   {out!r}")
        if out == "":
            print(f"  verdict:  PASS — empty (Whisper was silent or guard caught it)")
        elif _is_vocab_echo(out, vocab):
            # Should never reach here — transcribe_sync runs the guard itself.
            print(f"  verdict:  FAIL — guard did NOT suppress a vocab echo in the pipeline")
        elif any(w.lower() in out.lower() for w in vocab):
            print(f"  verdict:  FAIL — vocab words leaked into output: {out!r}")
        else:
            print(f"  verdict:  PASS — non-empty but no vocab words present ({out!r})")
    finally:
        if vocab_override_env and original is not None:
            (Path.home() / ".waffler-hosted" / "vocab.json").write_text(original)


run("CASE 1: silent audio WITH vocab (Ashkan / COBieQC / COBie) loaded")
print("-- sleep 30s --", flush=True)
time.sleep(30)
run("CASE 2: silent audio WITHOUT vocab (control — vocab.json blanked)", vocab_override_env=True)

print("\n=== done ===")
