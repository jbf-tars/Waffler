"""Waffler Plus, the account's side: what plan the account has, and one
dictation sent to the service (src/account.py: refresh_plan, plan, status,
plus_state, dictate).

A stand-in for the account service answers (tests/_account_fake.py, which
speaks the contract in the service's docs/HOSTED.md, "Waffler Plus (server
side)"). Nothing here touches the network.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from src import account                               # noqa: E402
from _account_fake import FakeAccountServer           # noqa: E402

SERVER = "https://notes.example.test"
EMAIL = "ada@example.com"
WAV = b"RIFF" + bytes(range(256)) * 8                 # every byte value: the upload must arrive as it left


@pytest.fixture
def server(monkeypatch, _isolated_data_dir):
    fake = FakeAccountServer(contract="new")
    monkeypatch.setenv(account.SERVER_ENV, SERVER)
    monkeypatch.setattr(account, "_transport", fake.transport())
    monkeypatch.setattr(account, "_notice", "")
    monkeypatch.setattr(account, "_plan", {})
    fake.data = _isolated_data_dir
    return fake


def sign_in(fake, email=EMAIL):
    assert account.start(email)["ok"]
    out = account.verify(email, fake.mailbox[email], "Ada's Mac")
    assert out["ok"], out
    return out


def later(days=30):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")


# ── the plan ────────────────────────────────────────────────────────────────

def test_the_status_says_plus_once_the_service_has_been_asked(server):
    sign_in(server)
    until = later()
    server.give_plus(EMAIL, until)
    assert account.status()["plan"] == "free", "not known yet: nothing is shown"
    assert account.refresh_plan() == {"ok": True, "plan": "plus", "plus_until": until}
    s = account.status()
    assert (s["plan"], s["plus_until"], s["plus_state"]) == ("plus", until, "ready")
    assert server.calls("GET", "/account")[0].auth.startswith("Bearer ")
    before = len(server.requests)
    account.status()
    account.plan()
    assert len(server.requests) == before, "the status is read from this computer"


def test_plus_with_no_end(server):
    sign_in(server)
    server.give_plus(EMAIL)
    assert account.refresh_plan() == {"ok": True, "plan": "plus", "plus_until": None}
    assert account.status()["plus_until"] is None


def test_a_free_account_and_an_older_service_are_free(server):
    sign_in(server)
    assert account.refresh_plan() == {"ok": True, "plan": "free", "plus_until": None}
    assert account.status()["plan"] == "free" and account.status()["plus_state"] == "ended"
    server.give_plus(EMAIL)
    server.has_plan = False                           # a service from before Plus says nothing of a plan
    assert account.refresh_plan()["plan"] == "free"
    assert account.status()["plan"] == "free"


@pytest.mark.parametrize("answer", [{"plan": "gold"}, {"plan": 1}, {"plan": "plus", "plus_until": 7},
                                    {"plan": "plus", "plus_until": "soon"}])
def test_a_plan_this_waffler_does_not_know_is_free(server, monkeypatch, answer):
    sign_in(server)
    monkeypatch.setattr(account, "_request", lambda *a, **k: answer)
    assert account.refresh_plan()["plan"] == "free"


def test_a_plus_whose_end_has_passed_reads_as_free_without_asking_again(server):
    sign_in(server)
    server.give_plus(EMAIL, later())
    account.refresh_plan()
    account._plan["plus_until"] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert account.plan() == {"plan": "free", "plus_until": None}
    assert account.status()["plus_state"] == "ended"


def test_signed_out_there_is_no_plan(server):
    sign_in(server)
    server.give_plus(EMAIL)
    account.refresh_plan()
    account.sign_out()
    s = account.status()
    assert (s["plan"], s["plus_until"], s["plus_state"]) == ("free", None, "signed_out")
    assert account.refresh_plan()["reason"] == "signed_out"


def test_one_accounts_plan_is_never_shown_for_another(server):
    sign_in(server)
    server.give_plus(EMAIL)
    account.refresh_plan()
    sign_in(server, "bob@example.com")                # another account on this computer
    assert account.status()["plan"] == "free"


def test_no_internet_keeps_the_plan_and_says_the_service_cannot_be_reached(server):
    sign_in(server)
    server.give_plus(EMAIL)
    account.refresh_plan()
    server.offline = True
    out = account.refresh_plan()
    assert out == {"ok": False, "error": account.NO_INTERNET, "reason": "offline"}
    s = account.status()
    assert (s["plan"], s["plus_state"]) == ("plus", "offline")
    server.offline = False
    account.refresh_plan()
    assert account.status()["plus_state"] == "ready"


def test_the_plan_is_asked_for_again_only_when_it_is_old(server, monkeypatch):
    assert account.plan_due() is False, "signed out: nothing to ask"
    sign_in(server)
    assert account.plan_due() is True
    assert account.plan_due() is False, "one asker at a time"
    account.refresh_plan()
    assert account.plan_due() is False
    clock = account.time.monotonic()
    monkeypatch.setattr(account.time, "monotonic", lambda: clock + account.PLAN_MAX_AGE_S + 1)
    assert account.plan_due() is True


# ── choosing Plus ───────────────────────────────────────────────────────────

def test_plus_is_chosen_only_by_exactly_true(server):
    assert account.plus_chosen({}) is False and account.plus_chosen({account.PLUS_SETTING: "yes"}) is False
    assert account.plus_chosen({account.PLUS_SETTING: True}) is True
    assert account.plus_chosen() is False             # settings.json as it is: nothing chosen


# ── a dictation ─────────────────────────────────────────────────────────────

def test_a_dictation_is_sent_signed_with_its_fields_and_the_text_comes_back(server):
    sign_in(server)
    server.give_plus(EMAIL)
    out = account.dictate(WAV, filename="audio.wav", content_type="audio/wav", clean=True, language="pt-BR",
                          words=["Zebra", "quartz"])
    assert out == {"ok": True, "text": "Zebra quartz, alpha.", "raw": "um so zebra quartz alpha you know",
                   "seconds": 3.0, "cleaned": True, "code": ""}
    sent = server.calls("POST", "/api/dictate")
    assert len(sent) == 1 and sent[0].auth == "Bearer " + next(iter(server.sessions))
    form = server.dictations[0]
    assert form["file"] == {"filename": "audio.wav", "type": "audio/wav", "bytes": WAV}
    assert (form["clean"], form["language"], form["words"]) == ("true", "pt-BR", "Zebra\nquartz")


def test_clean_up_off_no_language_and_no_words(server):
    sign_in(server)
    server.give_plus(EMAIL)
    out = account.dictate(WAV, clean=False, language="auto", words=[])
    assert out["ok"] and out["cleaned"] is False and out["text"] == out["raw"]
    form = server.dictations[0]
    assert form["clean"] == "false"
    assert "language" not in form and "words" not in form, "left out: the service detects the language"


@pytest.mark.parametrize("language", ["en_GB!", "x" * 13, "auto", "", None, 5])
def test_a_language_the_service_would_refuse_is_left_out(server, language):
    sign_in(server)
    server.give_plus(EMAIL)
    assert account.dictate(WAV, language=language)["ok"]
    assert "language" not in server.dictations[0]


def test_the_vocabulary_is_cut_to_what_the_service_takes(server):
    sign_in(server)
    server.give_plus(EMAIL)
    words = [f"word{i}" for i in range(260)] + ["x" * 90]
    words[3] = "a, b\nc"                              # a comma or a new line would make it three entries
    assert account.dictate(WAV, words=words)["ok"]
    sent = server.dictations[0]["words"].split("\n")
    assert len(sent) == account.DICTATE_WORDS == 200 and sent[3] == "a b c"
    assert all(len(w) <= account.DICTATE_WORD_CHARS == 40 for w in sent)


def test_the_words_come_back_when_the_tidying_could_not_be_done(server):
    sign_in(server)
    server.give_plus(EMAIL)
    server.clean_trouble = "model_calls_cap"
    out = account.dictate(WAV)
    assert out["ok"] and out["cleaned"] is False and out["code"] == "model_calls_cap"
    assert out["text"] == out["raw"] == server.heard["raw"]


REFUSALS = [
    ((402, "The month's minutes are used up.", "minutes_cap"), account.PLUS_MINUTES_USED, "minutes_cap"),
    ((402, "You have used this month's Waffler Plus.", "month_used"), account.PLUS_MONTH_USED, "month_used"),
    ((402, "Something newer.", "something_new"), account.ALLOWANCE_USED, "over_allowance"),
    ((429, "Too many dictations.", "", {"Retry-After": "20"}), account.DICTATE_TOO_OFTEN, "too_many"),
    ((413, "That recording is too large (the limit is 200 MB).", "audio_too_large"),
     account.DICTATE_TOO_LARGE, "too_large"),
    ((413, "Dictation takes at most 5 minutes of audio at a time.", "audio_too_long"),
     account.DICTATE_TOO_LONG, "too_long"),
    ((400, "That file is not audio."), account.DICTATE_UNREADABLE, "refused"),
    ((408, "The upload took too long."), account.DICTATE_SLOW, "slow"),
    ((503, "The service couldn't take this dictation just now.", "", {"Retry-After": "5"}),
     account.DICTATE_BUSY, "busy"),
    ((507, "The server is full.", "server_full", {"Retry-After": "3600"}), account.DICTATE_BUSY, "busy"),
    ((500, "Oops."), account.DICTATE_BUSY, "busy"),
    ((418, "A teapot."), account.FAILED, "failed"),
]


@pytest.mark.parametrize("refusal, sentence, reason", REFUSALS)
def test_every_refusal_is_one_plain_sentence(server, refusal, sentence, reason):
    sign_in(server)
    server.give_plus(EMAIL)
    server.dictate_refusal = refusal
    assert account.dictate(WAV) == {"ok": False, "error": sentence, "reason": reason}
    assert account.status()["signed_in"], "only a 401 signs this computer out"


def test_a_free_account_is_refused_and_the_plan_is_free_from_then_on(server):
    sign_in(server)
    server.give_plus(EMAIL)
    account.refresh_plan()
    server.end_plus(EMAIL)                            # the plan ended since it was last read
    assert account.dictate(WAV) == {"ok": False, "error": account.PLUS_REQUIRED, "reason": "plus_required"}
    assert account.status()["plan"] == "free" and account.status()["plus_state"] == "ended"
    assert server.dictations == []


def test_a_disabled_account_and_a_computer_that_was_signed_out(server):
    sign_in(server)
    server.give_plus(EMAIL)
    server.disabled.add(EMAIL)
    assert account.dictate(WAV) == {"ok": False, "error": account.DISABLED, "reason": "disabled"}
    server.disabled.clear()
    server.sessions.clear()                           # removed from another device
    assert account.dictate(WAV) == {"ok": False, "error": account.SIGNED_OUT, "reason": "signed_out"}
    assert not account.status()["signed_in"], "the token is forgotten, as for every signed request"
    assert account.dictate(WAV) == {"ok": False, "error": account.NOT_SIGNED_IN, "reason": "signed_out"}


def test_no_internet_and_an_answer_that_is_not_the_contract(server, monkeypatch):
    sign_in(server)
    server.give_plus(EMAIL)
    server.offline = True
    assert account.dictate(WAV) == {"ok": False, "error": account.PLUS_OFFLINE, "reason": "offline"}
    server.offline = False
    server.heard = {"raw": "ok", "text": 5}
    assert account.dictate(WAV) == {"ok": False, "error": account.FAILED, "reason": "failed"}


def test_every_sentence_is_short_plain_and_sells_nothing():
    for sentence in (account.PLUS_REQUIRED, account.PLUS_MINUTES_USED, account.DICTATE_TOO_OFTEN,
                     account.DICTATE_TOO_LARGE, account.DICTATE_TOO_LONG, account.DICTATE_UNREADABLE,
                     account.DICTATE_SLOW, account.DICTATE_BUSY, account.PLUS_OFFLINE):
        assert sentence.endswith(".") and len(sentence.split()) <= 16, sentence
        assert account.plus_sentence(sentence) == sentence
        for word in ("buy", "upgrade", "subscribe", "pay", "price", "£", "$", "402", "503"):
            assert word not in sentence.lower(), sentence
    assert account.plus_sentence("Connection error") == ""


def test_the_upload_gets_the_time_it_is_given_and_goes_only_to_the_service(server, monkeypatch):
    sign_in(server)
    server.give_plus(EMAIL)
    seen = []
    real = account.http_client
    monkeypatch.setattr(account, "http_client", lambda s, timeout=account.TIMEOUT_S: seen.append((s, timeout))
                        or real(s, timeout))
    assert account.dictate(WAV, timeout=144.0)["ok"]
    assert seen == [(SERVER, 144.0)]


# ── a whole recording, and the app's own prompt (the two routes that keep nothing) ──

def _flac(seconds=2.0):
    import io
    import numpy as np
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(seconds * 16000), dtype="float32"), 16000, format="FLAC", subtype="PCM_16")
    return buf.getvalue()


MESSAGES = [{"role": "user", "content": "Make this friendlier: send the file."}]


def test_a_recording_is_sent_signed_and_its_segments_come_back(server):
    sign_in(server)
    server.give_plus(EMAIL)
    server.hear = lambda seconds, n: [{"start_s": 0.5, "end_s": 2.0, "text": "Zebra quartz alpha."},
                                      {"start_s": 15.86, "end_s": 17.36, "text": "And then the budget.", "x": 1}]
    audio = _flac(3.0)
    out = account.transcribe(audio, language="pt-BR", words=["Zebra", "quartz"])
    assert out == {"ok": True, "seconds": 3.0, "language": "pt-BR", "segments": [
        {"start_s": 0.5, "end_s": 2.0, "text": "Zebra quartz alpha."},
        {"start_s": 15.86, "end_s": 17.36, "text": "And then the budget."}]}
    sent = server.calls("POST", "/api/plus/transcribe")
    assert len(sent) == 1 and sent[0].auth == "Bearer " + next(iter(server.sessions))
    form = server.transcriptions[0]
    assert form["file"] == {"filename": "audio.flac", "type": "audio/flac", "bytes": audio}
    assert (form["language"], form["words"]) == ("pt-BR", "Zebra\nquartz")
    assert account.transcribe(audio, language="auto")["ok"]
    assert "language" not in server.transcriptions[1] and "words" not in server.transcriptions[1]


def test_a_prompt_is_sent_as_it_is_and_the_text_comes_back(server):
    sign_in(server)
    server.give_plus(EMAIL)
    server.write_reply = lambda body: "Could you send the file, please?"
    out = account.write("You rewrite text as asked.", MESSAGES, max_tokens=2000, effort="low")
    assert out == {"ok": True, "text": "Could you send the file, please?"}
    sent = server.calls("POST", "/api/plus/write")
    assert len(sent) == 1 and sent[0].auth == "Bearer " + next(iter(server.sessions))
    assert server.writes == [{"system": "You rewrite text as asked.", "messages": MESSAGES,
                              "max_tokens": 2000, "effort": "low"}]
    assert account.write("", MESSAGES)["ok"]
    assert server.writes[1] == {"system": "", "messages": MESSAGES}, "left out: the service uses its own"


TRANSCRIBE_REFUSALS = [
    ((402, "The month's minutes are used up.", "minutes_cap"), account.PLUS_MINUTES_USED, "minutes_cap"),
    ((402, "You have used this month's Waffler Plus.", "month_used"), account.PLUS_MONTH_USED, "month_used"),
    ((402, "Something newer.", "something_new"), account.ALLOWANCE_USED, "over_allowance"),
    ((413, "That recording is too large (the limit is 200 MB).", "audio_too_large"),
     account.DICTATE_TOO_LARGE, "too_large"),
    ((413, "That recording is too long.", "audio_too_long"), account.TRANSCRIBE_TOO_LONG, "too_long"),
    ((400, "That file is not audio."), account.DICTATE_UNREADABLE, "refused"),
    ((408, "The upload took too long."), account.DICTATE_SLOW, "slow"),
    ((503, "The server is busy.", "", {"Retry-After": "5"}), account.TRANSCRIBE_BUSY, "busy"),
    ((503, "The transcription failed part-way.", "transcribe_failed", {"Retry-After": "5"}),
     account.TRANSCRIBE_FAILED, "transcribe_failed"),
    ((504, "Send it in shorter parts.", "transcribe_timeout"), account.TRANSCRIBE_TIMEOUT, "transcribe_timeout"),
    ((507, "The server is full.", "server_full", {"Retry-After": "3600"}), account.PLUS_FULL, "server_full"),
    ((500, "Oops."), account.TRANSCRIBE_BUSY, "busy"),
    ((418, "A teapot."), account.FAILED, "failed"),
]
WRITE_REFUSALS = [
    ((402, "Today's requests to the AI are used.", "model_calls_cap"), account.MODEL_CALLS_CAP, "model_calls_cap"),
    ((402, "That would pass today's allowance of text.", "model_chars_cap"), account.WRITING_CAP, "model_chars_cap"),
    ((402, "Something newer.", "something_new"), account.ALLOWANCE_USED, "over_allowance"),
    ((413, "That request is too large."), account.TOO_LARGE, "too_large"),
    ((413, "Too many characters.", "too_long"), account.TOO_LARGE, "too_large"),
    ((400, "messages must be a list."), account.FAILED, "refused"),
    ((422, "The model declined to write it.", "write_refused"), account.WRITE_REFUSED, "write_refused"),
    ((503, "The model could not be reached.", "write_failed", {"Retry-After": "5"}),
     account.WRITE_FAILED, "write_failed"),
    ((500, "Oops."), account.WRITE_FAILED, "write_failed"),
    ((418, "A teapot."), account.FAILED, "failed"),
]


@pytest.mark.parametrize("refusal, sentence, reason", TRANSCRIBE_REFUSALS)
def test_every_refusal_of_a_recording_is_one_plain_sentence(server, refusal, sentence, reason):
    sign_in(server)
    server.give_plus(EMAIL)
    server.transcribe_refusals = [refusal]
    assert account.transcribe(_flac()) == {"ok": False, "error": sentence, "reason": reason}
    assert account.status()["signed_in"], "only a 401 signs this computer out"


@pytest.mark.parametrize("refusal, sentence, reason", WRITE_REFUSALS)
def test_every_refusal_of_a_prompt_is_one_plain_sentence(server, refusal, sentence, reason):
    sign_in(server)
    server.give_plus(EMAIL)
    server.write_refusals = [refusal]
    assert account.write("s", MESSAGES) == {"ok": False, "error": sentence, "reason": reason}
    assert account.status()["signed_in"]


@pytest.mark.parametrize("send, sentence", [
    (lambda: account.transcribe(_flac()), account.TRANSCRIBE_TOO_OFTEN),
    (lambda: account.write("s", MESSAGES), account.WRITE_TOO_OFTEN)])
def test_too_many_in_a_minute_says_how_long_to_wait(server, send, sentence):
    sign_in(server)
    server.give_plus(EMAIL)
    server.transcribe_refusals = [(429, "Too many.", "", {"Retry-After": "20"})]
    server.write_refusals = [(429, "Too many.", "", {"Retry-After": "20"})]
    assert send() == {"ok": False, "error": sentence, "reason": "too_many", "wait_s": 20.0}


@pytest.mark.parametrize("send", [lambda: account.transcribe(_flac()), lambda: account.write("s", MESSAGES)])
def test_both_routes_free_disabled_signed_out_and_offline(server, send):
    sign_in(server)
    assert send() == {"ok": False, "error": account.PLUS_REQUIRED, "reason": "plus_required"}
    assert account.status()["plus_state"] == "ended", "the plan is free from then on"
    assert server.transcriptions == [] and server.writes == [], "nothing of the body was read"
    server.give_plus(EMAIL)
    server.offline = True
    assert send() == {"ok": False, "error": account.PLUS_OFFLINE, "reason": "offline"}
    server.offline = False
    server.disabled.add(EMAIL)
    assert send() == {"ok": False, "error": account.DISABLED, "reason": "disabled"}
    server.disabled.clear()
    server.sessions.clear()                           # removed from another device
    assert send() == {"ok": False, "error": account.SIGNED_OUT, "reason": "signed_out"}
    assert not account.status()["signed_in"]
    assert send() == {"ok": False, "error": account.NOT_SIGNED_IN, "reason": "signed_out"}


def test_an_answer_that_is_not_the_contract_is_a_failure(server):
    sign_in(server)
    server.give_plus(EMAIL)
    server.hear = lambda seconds, n: [{"start_s": "soon", "end_s": 2.0, "text": "x"}]
    assert account.transcribe(_flac()) == {"ok": False, "error": account.FAILED, "reason": "failed"}
    server.write_reply = lambda body: 5
    assert account.write("s", MESSAGES) == {"ok": False, "error": account.FAILED, "reason": "failed"}


def test_the_new_sentences_are_short_plain_and_sell_nothing():
    for sentence in (account.TRANSCRIBE_TOO_OFTEN, account.TRANSCRIBE_TOO_LONG, account.TRANSCRIBE_BUSY,
                     account.TRANSCRIBE_FAILED, account.TRANSCRIBE_TIMEOUT, account.PLUS_FULL,
                     account.WRITE_TOO_OFTEN, account.WRITE_REFUSED, account.WRITE_FAILED,
                     account.MODEL_CALLS_CAP, account.WRITING_CAP, account.TOO_LARGE):
        assert sentence.endswith(".") and len(sentence.split()) <= 16, sentence
        assert account.plus_sentence(sentence) == sentence
        for word in ("buy", "upgrade", "subscribe", "pay", "price", "£", "$", "402", "503"):
            assert word not in sentence.lower(), sentence


def test_plus_is_ready_when_it_is_chosen_and_there_to_use(server):
    chosen = {account.PLUS_SETTING: True}
    assert not account.plus_ready(chosen), "signed out"
    sign_in(server)
    assert account.plus_ready(chosen), "not asked yet: it is tried"
    assert not account.plus_ready({}), "not the chosen way"
    account.refresh_plan()
    assert not account.plus_ready(chosen), "the account is free"
    server.give_plus(EMAIL)
    account.refresh_plan()
    assert account.plus_ready(chosen)
    server.offline = True
    account.refresh_plan()
    assert account.plus_ready(chosen), "unreachable is temporary: it stays the way, and is tried"


def test_a_long_answer_is_waited_for_but_finding_the_service_is_not(server, monkeypatch):
    sign_in(server)
    server.give_plus(EMAIL)
    seen = []
    real = account.http_client
    monkeypatch.setattr(account, "http_client", lambda s, timeout=account.TIMEOUT_S: seen.append(timeout)
                        or real(s, timeout))
    assert account.transcribe(_flac())["ok"] and account.write("s", MESSAGES, timeout=600.0)["ok"]
    assert [(t.read, t.connect) for t in seen] == [(account.PLUS_TRANSCRIBE_TIMEOUT_S, account.TIMEOUT_S),
                                                   (600.0, account.TIMEOUT_S)]
    assert account.PLUS_TRANSCRIBE_TIMEOUT_S > 600, "the service stops a transcription at ten minutes"
