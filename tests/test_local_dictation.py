"""Dictation with ``transcription_mode: "local"``: the model on this computer
does the work (src/local_stt.py, replaced here), no provider is ever asked,
and the pipeline gets the same text, the same provenance and the same
safety nets it gets from a provider.
"""
import io
import json
import sys
import types
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import local_stt  # noqa: E402
import pipeline_watchdog as pw  # noqa: E402
import style_openai  # noqa: E402
import transcribe_whisper as tw  # noqa: E402
from _pipeline_harness import (  # noqa: E402
    FakeStyler, FakeTranscriber, history, make_pipeline, wait_for)

SR = 16000


def _wav(seconds, amp=6000, freq=180.0):
    t = np.arange(int(seconds * SR)) / SR
    samples = (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples.tobytes())
    return out.getvalue()


def _silent_wav(seconds):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.zeros(int(seconds * SR), dtype=np.int16).tobytes())
    return out.getvalue()


def _set_mode(data_dir, mode, **more):
    stored = dict(more)
    if mode is not None:
        stored["transcription_mode"] = mode
    (data_dir / "settings.json").write_text(json.dumps(stored), encoding="utf-8")


class _Cloud:
    """A provider client that must not be called (or counts if it is)."""

    def __init__(self):
        self.calls = 0
        self.audio = self
        self.transcriptions = self

    def create(self, **kw):
        self.calls += 1
        return "from the cloud"


@pytest.fixture
def local(monkeypatch, _isolated_data_dir):
    """The local model, replaced: records what it was asked."""
    asked = []
    state = types.SimpleNamespace(text="Ship it on Monday, then tell Sarah.", error=None, asked=asked)

    def transcribe(audio, language=None, prompt=None, background=False):
        asked.append({"bytes": len(audio), "language": language, "prompt": prompt,
                      "background": background})
        if state.error is not None:
            raise state.error
        return state.text
    monkeypatch.setattr(local_stt, "transcribe", transcribe)
    monkeypatch.setattr(tw, "load_vocab", lambda: [])
    monkeypatch.setattr(tw, "load_settings", lambda: {"language": "en"})
    state.data = _isolated_data_dir
    return state


def _transcriber(groq="gsk_test_not_a_real_key", openai="sk-test-not-a-real-key"):
    t = tw.WhisperTranscriber(api_key=openai, groq_api_key=groq)
    cloud = _Cloud()
    if groq:
        t._groq_client = cloud
    if openai:
        t.client = cloud
    return t, cloud


# ── the transcriber ──────────────────────────────────────────────────────────

def test_local_mode_uses_the_model_here_and_never_a_provider(local):
    _set_mode(local.data, "local")
    t, cloud = _transcriber()
    assert t._backend == "local"
    out = t.transcribe_sync(_wav(4.0))
    assert out == "Ship it on Monday, then tell Sarah."
    assert cloud.calls == 0 and len(local.asked) == 1
    assert local.asked[0]["language"] == "en" and local.asked[0]["background"] is False
    # The provenance the pipeline snapshots (app.py _transcribe_with_provenance).
    assert t._backend == "local" and t._last_cloud_provider == "local"
    assert t.last_asr_response == out and t.last_asr_filtered is False
    assert t.last_failovers == [] and t.last_retry_fired is False
    assert t.last_speech_seconds > 0


def test_the_mode_is_read_for_every_recording_so_no_restart(local, monkeypatch):
    t, cloud = _transcriber()
    assert t._backend == "groq"
    monkeypatch.setattr(tw.WhisperTranscriber, "_transcribe_groq",
                        lambda self, ab: cloud.create() and "from the cloud")
    assert t.transcribe_sync(_wav(4.0)) == "from the cloud" and local.asked == []
    _set_mode(local.data, "local")
    assert t.transcribe_sync(_wav(4.0)) == "Ship it on Monday, then tell Sarah."
    assert t._backend == "local" and cloud.calls == 1
    _set_mode(local.data, "cloud")
    assert t.transcribe_sync(_wav(4.0)) == "from the cloud"
    assert t._backend == "groq" and t._last_cloud_provider == "groq" and len(local.asked) == 1


def test_local_mode_needs_no_key_at_all(local):
    _set_mode(local.data, "local")
    t, _cloud = _transcriber(groq="", openai="")
    assert t._groq_client is None and t.client is None
    assert t.transcribe_sync(_wav(4.0)) == "Ship it on Monday, then tell Sarah."


def test_a_local_failure_is_raised_and_never_sent_to_a_provider(local):
    _set_mode(local.data, "local")
    t, cloud = _transcriber()
    local.error = local_stt.LocalSTTError(local_stt.NOT_DOWNLOADED, "not_downloaded")
    with pytest.raises(local_stt.LocalSTTError) as e:
        t.transcribe_sync(_wav(4.0))
    assert str(e.value) == local_stt.NOT_DOWNLOADED
    assert cloud.calls == 0, "the audio was to stay on this computer"


def test_the_vocabulary_and_the_language_reach_the_local_model(local, monkeypatch):
    _set_mode(local.data, "local")
    monkeypatch.setattr(tw, "load_vocab", lambda: ["Kenobi", "Waffler"])
    monkeypatch.setattr(tw, "load_settings", lambda: {"language": "auto"})
    t, _cloud = _transcriber()
    t.transcribe_sync(_wav(4.0))
    assert local.asked[0]["language"] is None
    assert "Kenobi" in local.asked[0]["prompt"] and "Waffler" in local.asked[0]["prompt"]


def test_the_hallucination_filters_still_run_on_local_text(local):
    """Whisper says "Thank you." over silence wherever it runs: the local
    model measured it too (docs/OFFLINE-TRANSCRIPTION.md)."""
    _set_mode(local.data, "local")
    t, _cloud = _transcriber()
    local.text = "Thank you."
    assert t.transcribe_sync(_silent_wav(5.0)) == ""
    assert t.last_asr_response == "Thank you." and t.last_asr_filtered is True
    local.text = "So that is the plan for the sprint. Thanks for watching!"
    assert t.transcribe_sync(_wav(12.0)) == "So that is the plan for the sprint."


def test_a_short_local_transcript_is_not_retried_on_a_provider(local):
    """The cloud path re-sends a suspiciously short transcript to the other
    provider. On this computer there is no other provider, and the audio does
    not leave."""
    _set_mode(local.data, "local")
    t, cloud = _transcriber()
    local.text = "Move the meeting to Friday."        # five words for 40 s of sound
    assert t.transcribe_sync(_wav(40.0)) == "Move the meeting to Friday."
    assert cloud.calls == 0 and t.last_retry_fired is False


def test_a_long_take_goes_to_the_local_model_in_pieces_and_is_joined(local, monkeypatch):
    _set_mode(local.data, "local")
    t, _cloud = _transcriber()
    monkeypatch.setattr(tw, "_split_audio_on_silence", lambda ab: [ab[:len(ab) // 2], ab[len(ab) // 2:]])
    local.text = "One piece of it."
    assert t.transcribe_sync(_wav(8.0)) == "One piece of it. One piece of it."
    assert len(local.asked) == 2


# ── the pipeline (the real _process, fakes around it) ────────────────────────

class LocalTranscriber(FakeTranscriber):
    """What the real transcriber looks like to the pipeline in local mode."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._backend = "local"

    def transcribe_sync(self, audio_bytes):
        text = super().transcribe_sync(audio_bytes)
        self._last_cloud_provider = "local"
        return text


def _dictate(p):
    p.is_recording = True
    p.on_hotkey_release()
    assert wait_for(lambda: len(history(p)) >= 1, timeout=10)
    return history(p)[-1]


def test_a_local_dictation_is_pasted_and_journalled_as_local(tmp_path, monkeypatch):
    usage = []
    p = make_pipeline(tmp_path, transcriber=LocalTranscriber())
    p.ns["record_usage_safely"] = lambda kind, **kw: usage.append((kind, kw))
    entry = _dictate(p)
    assert p.clipboard.pastes == ["Ok so ship it on monday."]
    assert entry["text"] == "ok so ship it on monday"
    # The metadata-only quality log names where the speech was turned into text.
    quality = [json.loads(line) for line in
               (tmp_path / "quality.jsonl").read_text(encoding="utf-8").splitlines()]
    assert quality[-1]["asr_provider"] == "local"
    assert ("whisper", {"duration_seconds": pytest.approx(3.0, abs=0.1), "provider": "local"}) in usage
    assert p._stt_is_local() is True and p._speech_provider_name() == ""


def test_the_local_model_gets_the_longer_deadline(tmp_path, monkeypatch):
    seen = []
    real = pw.DictationRun.call

    def call(run, fn, *a, stage=None, deadline=None, **k):
        seen.append((stage, deadline, run.local_stt))
        return real(run, fn, *a, stage=stage, deadline=deadline, **k)
    monkeypatch.setattr(pw.DictationRun, "call", call)
    p = make_pipeline(tmp_path, transcriber=LocalTranscriber())
    _dictate(p)
    stage, deadline, local_flag = next(s for s in seen if s[0] == pw.TRANSCRIBING)
    assert local_flag is True and deadline == pw.transcribe_deadline_s(3.0, True)
    assert deadline > pw.transcribe_deadline_s(3.0, False)


def test_a_model_that_is_not_downloaded_keeps_the_recording_and_says_so(tmp_path):
    error = local_stt.LocalSTTError(local_stt.NOT_DOWNLOADED, "not_downloaded")
    p = make_pipeline(tmp_path, transcriber=LocalTranscriber(error=error))
    entry = _dictate(p)
    assert entry["failed"] is True and entry["unsent_id"], "the recording is kept as a Not sent card"
    assert entry["provider_name"] == "" and entry["error"] == local_stt.NOT_DOWNLOADED[:200]
    assert p.clipboard.pastes == []
    toast = p.overlay.toasts()[-1]
    assert toast["heading"] == "Not sent"
    assert toast["body"] == local_stt.NOT_DOWNLOADED + " Your recording is saved in the Journal."
    assert "Groq" not in toast["body"] and "OpenAI" not in toast["body"]


def test_only_the_local_models_own_sentences_replace_the_generic_message():
    for sentence in (local_stt.NOT_DOWNLOADED, local_stt.NOT_INSTALLED, local_stt.LOAD_FAILED,
                     local_stt.TRANSCRIBE_FAILED):
        assert local_stt.plain_reason(sentence) == sentence
        assert chr(0x2014) not in sentence
    for other in ("", None, "Error code: 403", "deadline: no answer in 20s", local_stt.DOWNLOAD_FAILED):
        assert local_stt.plain_reason(other) == ""


def _keyless_styler():
    return style_openai.OpenAIStyler()          # no key: no provider to ask


def test_with_no_key_at_all_the_words_go_in_as_heard_without_a_nag(tmp_path):
    """Local speech and no key: the clean-up has no model. That is treated as
    clean-up off (the words as heard), not as a clean-up that failed with a
    message after every dictation."""
    _set_mode(tmp_path, "local")
    styler = _keyless_styler()
    said = ("um so I think the second section needs a clearer example and we should meet "
            "on thursday to go through all of it together")
    p = make_pipeline(tmp_path, transcriber=LocalTranscriber(text=said), styler=styler)
    assert p._no_cleanup_key({"transcription_mode": "local"}) is True
    entry = _dictate(p)
    assert p.clipboard.pastes == [said] and entry["styled"] == said
    assert not [t for t in p.overlay.toasts() if "lean-up" in (t.get("heading") or "")]
    assert "styling_fallback" not in json.dumps(entry)


def test_the_no_key_shortcut_is_only_for_local_speech_with_no_key(tmp_path):
    p = make_pipeline(tmp_path, styler=_keyless_styler())
    assert p._no_cleanup_key({}) is False                               # cloud: unchanged
    assert p._no_cleanup_key({"transcription_mode": "cloud"}) is False
    assert p._no_cleanup_key({"transcription_mode": "local"}) is True
    keyed = style_openai.OpenAIStyler(groq_api_key="gsk_test_not_a_real_key")
    p = make_pipeline(tmp_path / "keyed", styler=keyed)
    assert p._no_cleanup_key({"transcription_mode": "local"}) is False  # a key: the clean-up runs
    p = make_pipeline(tmp_path / "fake", styler=FakeStyler())
    assert p._no_cleanup_key({"transcription_mode": "local"}) is False
