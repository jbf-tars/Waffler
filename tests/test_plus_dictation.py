"""Dictation through Waffler Plus: the third way speech becomes text.

With Plus chosen (settings.json "dictation_plus") and an account that has it,
a dictation's audio goes to the account service (POST /api/dictate) and the
answer's text is pasted the way every other dictation is. The branch sits
where "on this computer" sits: WhisperTranscriber._refresh_backend and
_dispatch_one (src/transcribe_whisper.py), and the clean-up step of
WafflerPipeline._process (app.py).

The real transcriber and the real _process run here (tests/_pipeline_harness.py)
against a stand-in for the account service (tests/_account_fake.py): no
network, no provider, no model.
"""
import io
import json
import sys
import types
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _pipeline_harness as H  # noqa: E402
import local_stt  # noqa: E402
import transcribe_whisper as tw  # noqa: E402
import unsent  # noqa: E402
import user_messages as um  # noqa: E402
from _account_fake import FakeAccountServer  # noqa: E402
from src import account  # noqa: E402

SERVER = "https://notes.example.test"
EMAIL = "ada@example.com"
RAW, TEXT = "um so zebra quartz alpha you know", "Zebra quartz, alpha."


@pytest.fixture
def plus(monkeypatch, _isolated_data_dir):
    """Signed in to an account that has Plus, with Plus the chosen way."""
    fake = FakeAccountServer(contract="new")
    monkeypatch.setenv(account.SERVER_ENV, SERVER)
    monkeypatch.setattr(account, "_transport", fake.transport())
    monkeypatch.setattr(account, "_notice", "")
    monkeypatch.setattr(account, "_plan", {})
    assert account.start(EMAIL)["ok"]
    assert account.verify(EMAIL, fake.mailbox[EMAIL], "Ada's Mac")["ok"]
    fake.give_plus(EMAIL)
    assert account.refresh_plan()["plan"] == "plus"
    fake.data = _isolated_data_dir
    fake.settings = {"language": "en"}
    fake.vocab = ["Zebra", "quartz"]
    monkeypatch.setattr(tw, "load_settings", lambda: dict(fake.settings))
    monkeypatch.setattr(tw, "load_vocab", lambda: list(fake.vocab))
    monkeypatch.setattr(local_stt, "transcribe", lambda *a, **k: "heard on this computer")
    monkeypatch.setattr(local_stt, "model_status", lambda stored=None: {"state": fake.model})
    fake.model = "not_downloaded"
    for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    choose(fake, True)
    return fake


def choose(fake, on, **more):
    stored = {**fake.settings, **more}
    if on:
        stored[account.PLUS_SETTING] = True
    else:
        stored.pop(account.PLUS_SETTING, None)
    fake.settings = stored
    (fake.data / "settings.json").write_text(json.dumps(stored), encoding="utf-8")


class Cloud:
    """The person's own key: counts what it is asked."""

    def __init__(self):
        self.calls = 0


def transcriber(monkeypatch, groq="gsk_test_not_a_real_key"):
    t = tw.WhisperTranscriber(api_key="", groq_api_key=groq)
    cloud = Cloud()
    if groq:
        t._groq_client = cloud

    def heard(self, audio):
        cloud.calls += 1
        return "from my own key"
    monkeypatch.setattr(tw.WhisperTranscriber, "_transcribe_groq", heard)
    t.plus_allowed = True                                 # what a dictation sets (app.py)
    return t, cloud


def wav(seconds=4.0):
    return H.speech_wav(seconds)


def seconds_of(upload: dict) -> float:
    """How long the audio the service was sent is (WAV or FLAC)."""
    if upload["type"] == "audio/wav":
        with wave.open(io.BytesIO(upload["bytes"]), "rb") as w:
            return w.getnframes() / w.getframerate()
    import soundfile as sf
    return sf.info(io.BytesIO(upload["bytes"])).duration


# ── the transcriber ──────────────────────────────────────────────────────────

def test_with_plus_chosen_the_audio_goes_to_the_service_and_no_provider_is_asked(plus, monkeypatch):
    t, cloud = transcriber(monkeypatch)
    assert t.transcribe_sync(wav()) == RAW
    assert cloud.calls == 0
    assert t._backend == tw.BACKEND_PLUS == "plus" and t._last_cloud_provider == "plus"
    assert t.last_plus == {"text": TEXT, "cleaned": True, "code": ""} and t.last_plus_gone == ""
    sent = plus.calls("POST", "/api/dictate")
    assert len(sent) == 1 and sent[0].auth.startswith("Bearer natter_")
    form = plus.dictations[0]
    assert (form["clean"], form["language"], form["words"]) == ("true", "en", "Zebra\nquartz")
    assert form["file"]["filename"].endswith((".wav", ".flac"))
    assert form["file"]["type"] in ("audio/wav", "audio/flac"), "a container the service takes"
    assert 4.0 <= seconds_of(form["file"]) <= 5.0, "the take as recorded, with the usual padding"


def test_the_clean_up_setting_and_the_language_setting_are_followed(plus, monkeypatch):
    t, _ = transcriber(monkeypatch)
    choose(plus, True, cleanup="off", language="auto")    # Settings, Style: clean-up Off
    assert t.transcribe_sync(wav()) == RAW
    form = plus.dictations[0]
    assert form["clean"] == "false" and "language" not in form
    assert t.last_plus == {"text": RAW, "cleaned": False, "code": ""}


def test_plus_is_for_dictation_only(plus, monkeypatch):
    t, cloud = transcriber(monkeypatch)
    t.plus_allowed = False                                # a recording the app says is not for Plus
    assert t.transcribe_sync(wav()) == "from my own key"
    assert plus.dictations == [] and t._backend == "groq" and t.last_plus is None
    t.plus_allowed = True
    assert t.transcribe_sync(wav()) == RAW and t._backend == "plus"
    t.plus_allowed = False
    assert t.transcribe_sync(wav()) == "from my own key" and t._backend == "groq"
    assert tw.WhisperTranscriber.plus_allowed is False, "off unless a dictation says so"


def test_plus_not_chosen_changes_nothing(plus, monkeypatch):
    choose(plus, False)
    t, cloud = transcriber(monkeypatch)
    assert t.transcribe_sync(wav()) == "from my own key"
    assert plus.dictations == [] and t.last_plus is None and t.last_plus_gone == ""


def test_a_long_take_goes_up_in_pieces_the_service_takes(plus, monkeypatch):
    t, _ = transcriber(monkeypatch)
    out = t.transcribe_sync(wav(400.0))
    assert len(plus.dictations) >= 2 and out == " ".join([RAW] * len(plus.dictations))
    assert all(seconds_of(d["file"]) <= 300.0 for d in plus.dictations), "under the five-minute cap"
    assert t.last_plus["text"] == " ".join([TEXT] * len(plus.dictations)) and t.last_plus["cleaned"] is True


def test_the_words_still_come_back_when_the_service_could_not_tidy(plus, monkeypatch):
    plus.clean_trouble = "clean_failed"
    t, _ = transcriber(monkeypatch)
    assert t.transcribe_sync(wav()) == RAW
    assert t.last_plus == {"text": RAW, "cleaned": False, "code": "clean_failed"}


@pytest.mark.parametrize("refusal, sentence", [
    ((402, "Used up.", "minutes_cap"), account.PLUS_MINUTES_USED),
    ((402, "Used up.", "month_used"), account.PLUS_MONTH_USED),
    ((429, "Too many."), account.DICTATE_TOO_OFTEN),
    ((413, "Too long.", "audio_too_long"), account.DICTATE_TOO_LONG),
])
def test_a_refusal_is_raised_as_its_sentence_and_no_provider_is_tried(plus, monkeypatch, refusal, sentence):
    # A 5xx is the service's own fault and goes on another way now
    # (tests/test_plus_service_fault.py); these are about the plan and the allowance.
    plus.dictate_refusal = refusal
    t, cloud = transcriber(monkeypatch)
    with pytest.raises(tw.PlusError) as e:
        t.transcribe_sync(wav())
    assert str(e.value) == sentence and cloud.calls == 0, "Plus was chosen: the key is not used behind its back"
    assert t.last_plus_gone == ""


def test_no_internet_and_this_one_goes_through_the_key(plus, monkeypatch):
    """It was a failure at first. Unreachable is temporary: this dictation
    falls back, and Plus is tried again for the next (tests/test_plus_rough_edges.py)."""
    plus.offline = True
    t, cloud = transcriber(monkeypatch)
    assert t.transcribe_sync(wav()) == "from my own key" and cloud.calls == 1
    assert t.last_plus_gone == "offline"


def test_the_plan_ended_mid_use_and_the_dictation_goes_through_the_key(plus, monkeypatch):
    t, cloud = transcriber(monkeypatch)
    plus.end_plus(EMAIL)                                  # the app still believes it has Plus
    assert t.transcribe_sync(wav()) == "from my own key"
    assert cloud.calls == 1 and t.last_plus_gone == "ended" and t.last_plus is None
    assert t._backend == "groq"
    # The app now knows: the next one does not ask the service at all.
    before = len(plus.requests)
    assert t.transcribe_sync(wav()) == "from my own key"
    assert len(plus.requests) == before and t.last_plus_gone == "ended"


def test_signed_out_and_the_dictation_goes_through_the_key(plus, monkeypatch):
    t, cloud = transcriber(monkeypatch)
    plus.sessions.clear()                                 # removed from another device
    assert t.transcribe_sync(wav()) == "from my own key"
    assert t.last_plus_gone == "signed_out" and cloud.calls == 1
    account.forget()
    assert t.transcribe_sync(wav()) == "from my own key" and t.last_plus_gone == "signed_out"


def test_with_no_key_the_fallback_is_the_model_on_this_computer(plus, monkeypatch):
    plus.model = "ready"
    t, cloud = transcriber(monkeypatch, groq="")
    plus.end_plus(EMAIL)
    assert t.transcribe_sync(wav()) == "heard on this computer"
    assert t.last_plus_gone == "ended" and t._backend == "local" and cloud.calls == 0


def test_with_nothing_to_fall_back_to_the_recording_is_kept_with_the_reason(plus, monkeypatch):
    t, _ = transcriber(monkeypatch, groq="")
    plus.end_plus(EMAIL)
    with pytest.raises(tw.PlusError) as e:
        t.transcribe_sync(wav())
    assert str(e.value) == account.PLUS_REQUIRED and t.last_plus_gone == "ended"


# ── the whole dictation (app.py _process) ───────────────────────────────────

def pipeline(plus, monkeypatch, styler=None, groq="gsk_test_not_a_real_key"):
    plus.vocab = []                                       # nothing for the Vocabulary step to respell
    t, cloud = transcriber(monkeypatch, groq)
    t.plus_allowed = False                                # the app sets it, not the test
    p = H.make_pipeline(plus.data, transcriber=t, styler=styler or H.FakeStyler(), wav=wav(3.0))
    p.cloud = cloud
    p.stored = []
    p.ns["_store_setting"] = lambda key, value: p.stored.append((key, value))
    return p


def dictate(p):
    worker = H.run_process(p)
    worker.join(10)
    assert not worker.is_alive()
    return p


def test_the_services_text_is_pasted_and_nothing_else_is_asked(plus, monkeypatch):
    p = dictate(pipeline(plus, monkeypatch))
    assert p.clipboard.pastes == [TEXT], "pasted as every dictation is"
    assert p.styler.calls == 0 and p.cloud.calls == 0, "one request did the speech and the tidying"
    assert len(plus.dictations) == 1 and p.overlay.toasts() == []
    entry = H.history(p)[-1]
    assert entry["styled"] == TEXT and entry["text"] == RAW
    assert p.stored == [], "Plus is still the chosen way"
    assert p._speech_provider_name() == "Waffler Plus"


def test_cleaned_false_is_pasted_without_the_clean_up_and_says_so(plus, monkeypatch):
    plus.clean_trouble = "model_calls_cap"
    p = dictate(pipeline(plus, monkeypatch))
    assert p.clipboard.pastes == [RAW] and p.styler.calls == 0
    (toast,) = p.overlay.toasts()
    assert toast["heading"] == um.CLEANUP_SKIPPED
    assert toast["body"] == um.cleanup_skipped_message("PLUS|model_calls_cap")[1] == um.PLUS_CLEANUP_USED
    plus.clean_trouble = "clean_failed"
    assert um.cleanup_skipped_message("PLUS|clean_failed") == (um.CLEANUP_SKIPPED, um.PLUS_CLEANUP_FAILED)
    for sentence in (um.PLUS_CLEANUP_USED, um.PLUS_CLEANUP_FAILED):
        assert "as you said them" in sentence and "Groq" not in sentence and "key" not in sentence


def test_with_the_clean_up_off_the_words_go_in_as_heard(plus, monkeypatch):
    choose(plus, True, cleanup="off")
    p = dictate(pipeline(plus, monkeypatch))
    assert plus.dictations[0]["clean"] == "false"
    assert p.clipboard.pastes == [RAW] and p.overlay.toasts() == [] and p.styler.calls == 0


def test_a_refusal_keeps_the_recording_and_says_why_in_one_sentence(plus, monkeypatch):
    # A refusal about the plan (a 5xx goes on another way: tests/test_plus_service_fault.py).
    plus.dictate_refusal = (402, "Used up.", "minutes_cap")
    p = dictate(pipeline(plus, monkeypatch))
    assert p.clipboard.pastes == [] and p.cloud.calls == 0
    entry = H.history(p)[-1]
    assert entry["failed"] and entry["unsent_id"] and Path(entry["audio_path"]).exists(), "nothing is lost"
    assert entry["provider_name"] == "Waffler Plus"
    (toast,) = p.overlay.toasts()
    assert toast["body"] == account.PLUS_MINUTES_USED + " Your recording is saved in the Journal."
    assert p.stored == [], "Plus is still chosen: it was only this month's minutes"
    # Sent again later, it goes through Plus again, into its card.
    plus.dictate_refusal = None
    out = p.resend_unsent(entry["unsent_id"])
    assert out["ok"] and out["item"]["styled"] == TEXT and p.styler.calls == 0


# Plus gone, with something to fall back to: tests/test_plus_rough_edges.py
# (it stays the chosen way now, and comes back by itself).


def test_plus_gone_with_nothing_to_use_keeps_the_recording_and_says_why(plus, monkeypatch):
    p = pipeline(plus, monkeypatch, groq="")
    plus.end_plus(EMAIL)
    dictate(p)
    entry = H.history(p)[-1]
    assert entry["failed"] and Path(entry["audio_path"]).exists()
    (toast,) = p.overlay.toasts()
    assert toast["body"] == account.PLUS_REQUIRED + " Your recording is saved in the Journal."
    assert p.stored == [], "Plus stays the chosen way"


def test_command_mode_goes_through_plus_too(plus, monkeypatch):
    """It did not at first (a Plus user with no key could not use it). The
    spoken instruction is heard by the service and tidied by nobody; what it
    asks for is written by the service (tests/test_plus_write.py)."""
    p = pipeline(plus, monkeypatch)
    text, info = p._command_hooks().transcribe(wav())
    assert text == RAW and info["backend"] == "plus" and p.cloud.calls == 0
    assert plus.dictations[-1]["clean"] == "false"
    text, info = p._transcribe_with_provenance(wav())
    assert text == RAW and info["backend"] == "plus" and info["last_plus"]["text"] == TEXT


def test_the_branch_sits_where_on_this_computer_sits():
    src = (ROOT / "src" / "transcribe_whisper.py").read_text(encoding="utf-8")
    dispatch = src[src.index("    def _dispatch_one("):src.index("    # ── Which cloud provider failed")]
    assert dispatch.index("BACKEND_PLUS") < dispatch.index("in LOCAL_BACKENDS") < dispatch.index("# Cloud path")
    refresh = src[src.index("    def _refresh_backend("):src.index("    def _call_with_transient_retries(")]
    assert "mode_is_local()" in refresh and "BACKEND_PLUS" in refresh
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    process = app[app.index("            _whole_snippet = self._whole_snippet(transcript)"):]
    process = process[:process.index("_sty = run.call(self.styler.style")]
    assert process.index("self._plus_words(") < process.index(
        "_style_layer.cleanup_is_off(_settings) or self._no_cleanup_key(_settings)")
    words = app[app.index("    def _plus_words("):app.index("    def _plus_gone(")]
    assert "_style_layer.cleanup_is_off(settings)" in words, "clean-up Off stays Off through Plus"
