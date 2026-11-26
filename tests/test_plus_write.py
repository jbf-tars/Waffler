"""Transforms, Command mode and their "Try it here" boxes through Waffler Plus.

With Plus the chosen way and an account that has it, the one model call a
transform and a command make (the styler's ``_chat``, src/style_openai.py)
sends their existing prompt to the account service (POST /api/plus/write)
where it would have asked the clean-up provider. The spoken instruction of a
command is heard by the service too (POST /api/dictate, with no tidying).

A stand-in for the account service answers (tests/_account_fake.py): no
network. Keys ARE set and each provider raises if asked.
"""
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import command_mode as cm  # noqa: E402
import transforms  # noqa: E402
import try_it  # noqa: E402
import user_messages as um  # noqa: E402
from _account_fake import FakeAccountServer  # noqa: E402
from src import account  # noqa: E402
from style_openai import ChatError, OpenAIStyler  # noqa: E402
from test_plus_dictation import RAW, pipeline, wav  # noqa: E402
from test_plus_dictation import plus as _dictation_plus  # noqa: E402,F401

SERVER = "https://notes.example.test"
EMAIL = "ada@example.com"


class Asked(Exception):
    pass


class Provider:
    """A key's provider: counts what it is asked, and answers or fails."""

    def __init__(self, text=None):
        self.calls, self.text = 0, text
        self.chat = self
        self.completions = self

    def create(self, **request):
        self.calls += 1
        if self.text is None:
            raise AssertionError("a key's provider was asked while Plus is chosen")
        message = type("M", (), {"content": self.text})()
        choice = type("C", (), {"message": message, "finish_reason": "stop"})()
        return type("R", (), {"choices": [choice], "usage": None})()


def styler(groq=None):
    """A styler with a Groq key (or none), built as the shared-path tests build one."""
    s = object.__new__(OpenAIStyler)
    s._provider_order = ["groq", "cerebras", "openai"]
    s._use_groq, s._use_cerebras = groq is not None, False
    s._groq_client, s._cerebras_client, s.client = groq, None, None
    s.model = "gpt-6-luna"
    s._groq_models, s._groq_model = ("openai/gpt-oss-120b",), "openai/gpt-oss-120b"
    s._cerebras_model = "gpt-oss-120b"
    s._groq_skip_until = s._cerebras_skip_until = 0.0
    return s


@pytest.fixture
def plus(monkeypatch, _isolated_data_dir):
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
    (fake.data / "settings.json").write_text(json.dumps({account.PLUS_SETTING: True}), encoding="utf-8")
    fake.write_reply = lambda body: "Could you send the file, please?"
    return fake


def chat(s, system="SYS", **over):
    args = dict(max_tokens=1024, temperature=0.2, deadline=time.monotonic() + 30, purpose="transform")
    args.update(over)
    return s._chat(system, "USER", **args)


# ── the one model call ───────────────────────────────────────────────────────

def test_the_prompt_goes_to_the_service_and_no_key_is_asked(plus):
    groq = Provider()
    reply = chat(styler(groq))
    assert (reply.text, reply.provider) == ("Could you send the file, please?", "plus")
    assert reply.usage["provider"] == "plus" and groq.calls == 0
    assert plus.writes == [{"system": "SYS", "messages": [{"role": "user", "content": "USER"}],
                            "max_tokens": 1024, "effort": "low"}]
    # A provider's own wording of the system message: the general one goes.
    chat(styler(groq), system={"groq": "FOR GROQ", "default": "GENERAL"})
    assert plus.writes[-1]["system"] == "GENERAL"
    chat(styler(groq), system={"groq": "FOR GROQ", "openai": "FOR OPENAI"})
    assert plus.writes[-1]["system"] == "FOR OPENAI"


def test_it_works_with_no_key_at_all(plus):
    assert chat(styler()).text == "Could you send the file, please?"


def test_plus_not_chosen_changes_nothing(plus):
    (plus.data / "settings.json").write_text("{}", encoding="utf-8")
    groq = Provider("From my own key.")
    assert chat(styler(groq)).provider == "groq" and plus.writes == []


REFUSALS = [
    ((402, "Today's requests are used.", "model_calls_cap"), account.MODEL_CALLS_CAP),
    ((402, "Too many characters today.", "model_chars_cap"), account.WRITING_CAP),
    ((429, "Too many.", "", {"Retry-After": "20"}), account.WRITE_TOO_OFTEN),
    ((413, "Too many characters.", "too_long"), account.TOO_LARGE),
    ((422, "The model declined.", "write_refused"), account.WRITE_REFUSED),
]
# A 5xx is the service's own fault: with a key it is used for that one request
# (test_a_fault_on_the_services_side_uses_the_key_for_that_one_request).


@pytest.mark.parametrize("refusal, sentence", REFUSALS)
def test_a_refusal_is_its_sentence_and_no_key_is_tried_instead(plus, refusal, sentence):
    plus.write_refusals = [refusal]
    groq = Provider()
    with pytest.raises(ChatError) as e:
        chat(styler(groq))
    assert e.value.reason == "PLUS|" + sentence and groq.calls == 0
    assert um.transform_failed_message(e.value.reason) == ("Couldn't rewrite", sentence + " Your text is unchanged.")
    assert um.command_failed_message(e.value.reason) == ("Couldn't run that", sentence + " Your text is unchanged.")


def test_an_empty_answer_is_a_failure(plus):
    plus.write_reply = lambda body: "  "
    with pytest.raises(ChatError) as e:
        chat(styler(Provider()))
    assert e.value.reason == "PLUS|" + account.WRITE_FAILED


def test_only_a_sentence_of_the_accounts_own_reaches_the_screen():
    assert um.transform_failed_message("PLUS|<script>") == \
        ("Couldn't rewrite", "Something went wrong with the service. Your text is unchanged.")


@pytest.mark.parametrize("gone", ["ended", "signed_out"])
def test_plus_gone_and_the_key_is_asked_as_before_and_plus_stays_chosen(plus, gone):
    if gone == "ended":
        plus.end_plus(EMAIL)                              # the app still believes it has Plus
    else:
        plus.sessions.clear()
    groq = Provider("From my own key.")
    assert chat(styler(groq)).text == "From my own key." and groq.calls == 1
    before = len(plus.requests)
    assert chat(styler(groq)).text == "From my own key."
    assert len(plus.requests) == before, "known now: the service is not asked again"
    assert json.loads((plus.data / "settings.json").read_text())[account.PLUS_SETTING] is True
    if gone == "ended":                                   # given again: used again, with nothing re-picked
        plus.give_plus(EMAIL)
        account.refresh_plan()
        assert chat(styler(Provider())).provider == "plus"


def test_unreachable_uses_the_key_for_that_one_request(plus):
    plus.offline = True
    groq = Provider("From my own key.")
    assert chat(styler(groq)).text == "From my own key."
    plus.offline = False
    assert chat(styler(Provider())).provider == "plus", "and Plus again as soon as it answers"


def test_a_fault_on_the_services_side_uses_the_key_for_that_one_request(plus):
    plus.write_refusals = [(503, "The model could not be reached.", "write_failed")]
    groq = Provider("From my own key.")
    assert chat(styler(groq)).text == "From my own key." and groq.calls == 1
    assert chat(styler(Provider())).provider == "plus", "and Plus again for the next"


def test_a_fault_on_the_services_side_with_no_key_says_its_sentence(plus):
    plus.write_refusals = [(503, "The model could not be reached.", "write_failed")]
    with pytest.raises(ChatError) as e:
        chat(styler())
    assert e.value.reason == "PLUS|" + account.WRITE_FAILED


def test_unreachable_with_no_key_says_so(plus):
    plus.offline = True
    with pytest.raises(ChatError) as e:
        chat(styler())
    assert e.value.reason == "PLUS|" + account.PLUS_OFFLINE


# ── a transform and a command, and their Try it here boxes ───────────────────

def hooks(s):
    return dict(chat=lambda *a, **k: s._chat(*a, **k), dashes=lambda t: t, profanity=lambda styled, raw: styled)


def test_try_it_here_is_ready_with_plus_and_no_key(plus):
    assert try_it.status(styler()) == {"ok": True, "ready": True, "needs": ""}
    (plus.data / "settings.json").write_text("{}", encoding="utf-8")
    assert try_it.status(styler()) == {"ok": True, "ready": False, "needs": "key"}
    assert try_it.status(styler(Provider("x")))["ready"] is True


def test_polish_it_sends_the_transforms_own_prompt(plus):
    plus.write_reply = lambda body: "Could you send me the file, please?"
    source = "can you send me the file please"
    out = try_it.transform(source, stored={}, **hooks(styler(Provider())))
    assert out == {"ok": True, "changed": True, "text": "Could you send me the file, please?"}
    want = transforms.build_request(transforms.spec_for({}, transforms.POLISH), source)
    assert plus.writes == [{"system": want.system, "messages": [{"role": "user", "content": want.user}],
                            "max_tokens": want.max_tokens, "effort": "low"}]


def test_do_it_sends_the_commands_own_prompt(plus):
    plus.write_reply = lambda body: "ACTION: edit\nCould you send me the file, please?"
    out = try_it.command("can you send me the file please", "make this polite", stored={},
                         **hooks(styler(Provider())))
    assert out["ok"] and out["action"] == "edit" and out["text"] == "Could you send me the file, please?"
    want = cm.build_request(cm.tidy_instruction("make this polite"), "can you send me the file please", "")
    assert plus.writes[0]["system"] == want.system and plus.writes[0]["messages"][0]["content"] == want.user


def test_a_refused_try_says_the_sentence(plus):
    plus.write_refusals = [(402, "Today's requests are used.", "model_calls_cap")]
    out = try_it.transform("can you send me the file please", stored={}, **hooks(styler(Provider())))
    assert out == {"ok": False, "error": account.MODEL_CALLS_CAP + " Your text is unchanged."}


# ── the real shortcut paths (app.py) ─────────────────────────────────────────

plus_dictation = _dictation_plus      # the dictation tests' fixture: the app's pipeline with Plus chosen


def test_a_spoken_command_is_heard_by_the_service_with_no_tidying(plus_dictation, monkeypatch):
    p = pipeline(plus_dictation, monkeypatch)
    text, info = p._command_hooks().transcribe(wav())
    assert text == RAW and info["backend"] == "plus" and p.cloud.calls == 0
    assert plus_dictation.dictations[-1]["clean"] == "false", "the instruction as said: no model is asked for it"
    # A dictation after it is tidied as before.
    p._transcribe_with_provenance(wav())
    assert plus_dictation.dictations[-1]["clean"] == "true"


def test_a_command_the_service_could_not_hear_says_its_sentence(plus_dictation, monkeypatch):
    p = pipeline(plus_dictation, monkeypatch)
    kind = p._command_hooks().classify_speech_error(Exception(account.DICTATE_BUSY))
    assert um.command_speech_failed_message(kind, "Waffler Plus") == \
        ("Couldn't hear the command", account.DICTATE_BUSY + " Your text is unchanged.")
    kind = p._command_hooks().classify_speech_error(Exception("Connection error."))
    assert um.command_speech_failed_message(kind, "Groq")[1].startswith("Waffler couldn't reach Groq")


def test_the_transform_and_the_command_use_the_same_call(plus):
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert app.count("chat=lambda *a, **k: self.styler._chat(*a, **k)") == 2          # the two shortcuts
    assert "chat=lambda *a, **k: styler._chat(*a, **k)" in app                         # the two Try it boxes
    src = (ROOT / "src" / "style_openai.py").read_text(encoding="utf-8")
    body = src[src.index("    def _chat(self, system, user: str"):src.index("    def _chat_one(")]
    assert body.index("self._chat_plus(") < body.index("for name in self._provider_order")
