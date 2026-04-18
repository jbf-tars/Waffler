"""Verify Private Mode routing decisions.

Two halves:
  1. When private_mode=False, behavior is byte-for-byte unchanged from v3.9.0.
  2. When private_mode=True, transcription forces local backend and refuses
     to fall back to cloud.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _stub_wav_bytes():
    """1 second of silence as a minimal valid WAV (so _pad_audio_with_silence
    doesn't choke)."""
    import io, wave, struct
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 16000, *([0] * 16000)))
    return buf.getvalue()


def test_private_mode_false_uses_groq_when_configured(monkeypatch):
    """Regression: private_mode=False MUST behave exactly like v3.9.0."""
    import settings_store
    monkeypatch.setattr(settings_store, "is_private_mode", lambda: False)

    from transcribe_whisper import WhisperTranscriber
    t = WhisperTranscriber(api_key="", groq_api_key="fake-key")
    t._groq_client = MagicMock()
    t._groq_client.audio.transcriptions.create.return_value = "hello world"

    result = t.transcribe_sync(_stub_wav_bytes())
    assert result == "hello world"
    t._groq_client.audio.transcriptions.create.assert_called_once()


def test_private_mode_true_forces_local_and_never_calls_groq(monkeypatch):
    import settings_store
    monkeypatch.setattr(settings_store, "is_private_mode", lambda: True)

    from transcribe_whisper import WhisperTranscriber
    from errors import LocalUnavailableError
    t = WhisperTranscriber(api_key="", groq_api_key="fake-key")
    t._groq_client = MagicMock()

    try:
        t.transcribe_sync(_stub_wav_bytes())
        assert False, "should have raised"
    except LocalUnavailableError:
        pass

    # Critical: Groq was never called even though a key was present
    t._groq_client.audio.transcriptions.create.assert_not_called()


def test_cleanup_private_mode_false_uses_groq_when_configured(monkeypatch):
    """Regression: private_mode=False cleanup routes to Groq as before."""
    import settings_store
    monkeypatch.setattr(settings_store, "is_private_mode", lambda: False)

    with patch("style_openai.OpenAIStyler._style_groq") as mock_groq, \
         patch("local_backend.clean_text") as mock_local:
        mock_groq.return_value = ("cloud result", {"input_tokens": 1, "output_tokens": 1, "api_used": True})

        from style_openai import OpenAIStyler
        styler = OpenAIStyler(groq_api_key="fake")
        styler._use_groq = True  # matches dispatcher check at line 128

        text, usage = styler.style("hi there, please clean this up for me, I said so so so.")
        assert text == "cloud result"
        assert mock_groq.called
        assert not mock_local.called


def test_cleanup_private_mode_true_uses_local_and_never_calls_cloud(monkeypatch):
    import settings_store
    monkeypatch.setattr(settings_store, "is_private_mode", lambda: True)

    with patch("style_openai.OpenAIStyler._style_groq") as mock_groq, \
         patch("style_openai.OpenAIStyler._style_openai") as mock_openai, \
         patch("style_openai.OpenAIStyler._style_gemini") as mock_gemini, \
         patch("local_backend.clean_text", return_value="local cleaned") as mock_local:

        from style_openai import OpenAIStyler
        styler = OpenAIStyler(groq_api_key="fake")
        styler._use_groq = True

        text, usage = styler.style("hi there, please clean this up for me, I said so so so.")

        assert text == "local cleaned"
        assert usage["provider"] == "local"
        assert usage["api_used"] is False
        assert mock_local.called
        # None of the cloud paths were invoked
        assert not mock_groq.called
        assert not mock_openai.called
        assert not mock_gemini.called
