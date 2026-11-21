"""Try it here (Settings, Transforms and Command mode): the real rewrite, run
on the text typed into the page's box.

src/try_it.py is the path a transform or a command takes once the text has
been read (the request, the styler's ``_chat``, the gate on the answer), with
the box standing in for the selection. Nothing is read from another app,
pasted, copied or kept in the Journal. The model call is faked here.

The three ``Api`` methods are lifted out of app.py's source and run against
fakes, as the other bridge tests do: they never raise and always answer
``{"ok": bool, ...}``.
"""
import ast
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import command_mode as cm  # noqa: E402
import transforms  # noqa: E402
import try_it  # noqa: E402
import user_messages as um  # noqa: E402

RAMBLE = ("so um i wanted to ask if we could maybe move the meeting to thursday because "
          "tuesday is not going to work for me anymore sorry about that")
POLISHED = ("I wanted to ask if we could move the meeting to Thursday, because Tuesday is "
            "not going to work for me any more. Sorry about that.")


class Reply:
    def __init__(self, text, finish_reason="stop"):
        self.text = text
        self.provider = "groq"
        self.model = "fake"
        self.input_tokens = 40
        self.output_tokens = 30
        self.finish_reason = finish_reason


class Reason(RuntimeError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def chat_saying(text, calls=None, finish_reason="stop"):
    def chat(system, user, *, max_tokens, temperature, deadline, purpose):
        if calls is not None:
            calls.append({"system": system, "user": user, "max_tokens": max_tokens,
                          "temperature": temperature, "deadline": deadline, "purpose": purpose})
        return Reply(text, finish_reason)
    return chat


def chat_failing(error):
    def chat(*_a, **_k):
        raise error
    return chat


# ── Transforms: Polish it ────────────────────────────────────────────────────

def test_polish_sends_the_same_request_a_transform_does_and_returns_the_rewrite():
    calls, used = [], []
    out = try_it.transform(RAMBLE, stored={}, chat=chat_saying(POLISHED, calls), record_usage=used.append,
                           monotonic=lambda: 100.0)
    assert out == {"ok": True, "changed": True, "text": POLISHED}
    spec = transforms.spec_for({}, transforms.POLISH)
    request = transforms.build_request(spec, RAMBLE)
    assert len(calls) == 1
    assert calls[0]["system"] == request.system and calls[0]["user"] == request.user
    assert calls[0]["max_tokens"] == request.max_tokens and calls[0]["temperature"] == request.temperature
    assert calls[0]["purpose"] == "transform"
    assert 100.0 < calls[0]["deadline"] <= 100.0 + try_it.ASK_DEADLINE_S
    assert len(used) == 1 and used[0].provider == "groq"          # counted in Usage, like any transform


def test_polish_uses_the_persons_own_polish_rules_whatever_the_shortcut_runs():
    stored = {"transforms": {"default": transforms.PROMPT_ENGINEER,
                             "polish": {"custom_rules": ["Use British spelling"]}}}
    calls = []
    try_it.transform(RAMBLE, stored=stored, chat=chat_saying(POLISHED, calls))
    assert "Use British spelling" in calls[0]["system"]
    assert calls[0]["system"] == transforms.system_message(transforms.spec_for(stored, transforms.POLISH))


def test_the_answer_goes_through_the_transforms_own_gate():
    # A preamble is dropped, the dash rule applies: transforms.finish did it.
    out = try_it.transform(RAMBLE, stored={}, chat=chat_saying("Here is the polished version:\n" + POLISHED))
    assert out["ok"] and out["text"] == POLISHED
    # An answer that is not the person's text replaces nothing.
    unrelated = "The quick brown fox jumps over the lazy dog near the river bank every single morning."
    out = try_it.transform(RAMBLE, stored={}, chat=chat_saying(unrelated))
    assert out == {"ok": False, "error": um.TRANSFORM_UNRELATED[1]}
    out = try_it.transform(RAMBLE, stored={}, chat=chat_saying(POLISHED, finish_reason="length"))
    assert out == {"ok": False, "error": um.TRANSFORM_CUT_SHORT[1]}


def test_text_that_needs_nothing_says_so_and_changes_nothing():
    out = try_it.transform(POLISHED, stored={}, chat=chat_saying(POLISHED))
    assert out == {"ok": True, "changed": False, "text": POLISHED, "message": um.TRANSFORM_NO_CHANGE[0] + "."}


@pytest.mark.parametrize("text", ["", "   \n ", None, 5])
def test_an_empty_box_is_refused_before_anything_is_sent(text):
    calls = []
    out = try_it.transform(text, stored={}, chat=chat_saying(POLISHED, calls))
    assert out == {"ok": False, "error": try_it.EMPTY_BOX}
    assert calls == []


def test_too_much_text_is_refused_before_anything_is_sent():
    calls = []
    out = try_it.transform("word " * (transforms.MAX_WORDS + 1), stored={}, chat=chat_saying(POLISHED, calls))
    assert out == {"ok": False, "error": try_it.TOO_LONG}
    assert calls == []


def test_a_provider_failure_is_one_plain_sentence_and_never_raises():
    out = try_it.transform(RAMBLE, stored={}, chat=chat_failing(Reason("CONNECTION: Groq timed out")))
    assert out == {"ok": False, "error": um.transform_failed_message("CONNECTION: Groq timed out")[1]}
    out = try_it.transform(RAMBLE, stored={}, chat=chat_failing(ValueError("boom")))
    assert out == {"ok": False, "error": um.transform_failed_message("")[1]}
    for said in (out["error"],):
        assert "boom" not in said and "Traceback" not in said


def test_a_usage_row_that_cannot_be_written_does_not_cost_the_result():
    def broken(_reply):
        raise OSError("disk full")
    out = try_it.transform(RAMBLE, stored={}, chat=chat_saying(POLISHED), record_usage=broken)
    assert out["ok"] and out["text"] == POLISHED


# ── Command mode: Do it ──────────────────────────────────────────────────────

SHORT = "Can we move the meeting to Thursday? Tuesday no longer works for me."


def test_a_command_sends_the_request_command_mode_builds_and_edits_the_text():
    calls, used = [], []
    out = try_it.command(RAMBLE, "make this shorter", stored={},
                         chat=chat_saying("ACTION: edit\n" + SHORT, calls), record_usage=used.append)
    assert out == {"ok": True, "action": "edit", "changed": True, "text": SHORT}
    request = cm.build_request("make this shorter", RAMBLE, "")
    assert calls[0]["system"] == request.system and calls[0]["user"] == request.user
    assert calls[0]["max_tokens"] == request.max_tokens and calls[0]["purpose"] == "command"
    assert len(used) == 1


def test_a_question_comes_back_as_the_search_it_would_open_and_opens_nothing(monkeypatch):
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: opened.append(a) or True)
    out = try_it.command("", "what is the capital of France?",
                         stored={"command_mode": {"web_engine": "duckduckgo"}},
                         chat=chat_saying("ACTION: ask\ncapital of France"))
    query = cm.ask_query("capital of France", "what is the capital of France?")
    assert out == {"ok": True, "action": "ask", "query": query, "engine": "DuckDuckGo",
                   "url": cm.search_url("duckduckgo", query)}
    assert opened == []


def test_with_an_empty_box_a_command_writes_new_text():
    out = try_it.command("", "write a reply saying I'll be ten minutes late", stored={},
                         chat=chat_saying("ACTION: draft\nI'll be about ten minutes late, sorry."))
    assert out == {"ok": True, "action": "draft", "changed": True, "text": "I'll be about ten minutes late, sorry."}


def test_the_reply_goes_through_command_modes_own_gates():
    out = try_it.command(RAMBLE, "make this shorter", stored={}, chat=chat_saying("ACTION: edit\n" + RAMBLE))
    assert out == {"ok": True, "action": "edit", "changed": False, "text": RAMBLE,
                   "message": um.COMMAND_NO_CHANGE[1]}
    out = try_it.command(RAMBLE, "make this shorter", stored={},
                         chat=chat_saying("ACTION: edit\n" + SHORT, finish_reason="length"))
    assert out == {"ok": False, "error": um.COMMAND_CUT_SHORT[1]}
    out = try_it.command(RAMBLE, "make this shorter", stored={},
                         chat=chat_saying("ACTION: edit\nI'm sorry, but I can't help with that."))
    assert out == {"ok": False, "error": um.COMMAND_REFUSED[1]}


@pytest.mark.parametrize("instruction", ["", "  ", "...", None])
def test_a_command_with_no_instruction_is_refused_before_anything_is_sent(instruction):
    calls = []
    out = try_it.command(RAMBLE, instruction, stored={}, chat=chat_saying("ACTION: edit\n" + SHORT, calls))
    assert out == {"ok": False, "error": try_it.NO_INSTRUCTION}
    assert calls == []


def test_a_speech_for_an_instruction_and_too_much_text_are_refused():
    calls = []
    out = try_it.command(RAMBLE, "please " * (cm.INSTRUCTION_MAX_WORDS + 1), stored={},
                         chat=chat_saying("ACTION: edit\n" + SHORT, calls))
    assert out == {"ok": False, "error": um.COMMAND_TOO_LONG[1]}
    out = try_it.command("word " * (transforms.MAX_WORDS + 1), "make this shorter", stored={},
                         chat=chat_saying("ACTION: edit\n" + SHORT, calls))
    assert out == {"ok": False, "error": try_it.TOO_LONG}
    assert calls == []


def test_a_command_the_provider_does_not_answer_is_one_plain_sentence():
    out = try_it.command(RAMBLE, "make this shorter", stored={}, chat=chat_failing(Reason("AUTH: Groq 401")))
    assert out == {"ok": False, "error": um.command_failed_message("AUTH: Groq 401")[1]}
    out = try_it.command(RAMBLE, "make this shorter", stored={}, chat=chat_failing(KeyError("x")))
    assert out == {"ok": False, "error": um.command_failed_message("")[1]}


# ── can it run? ──────────────────────────────────────────────────────────────

def styler(groq=False, cerebras=False, openai=False):
    return types.SimpleNamespace(
        _provider_ready=lambda name: {"groq": groq, "cerebras": cerebras, "openai": openai}[name])


def test_it_is_ready_with_any_key_and_says_a_key_is_needed_with_none():
    assert try_it.status(styler(groq=True)) == {"ok": True, "ready": True, "needs": ""}
    assert try_it.status(styler(openai=True)) == {"ok": True, "ready": True, "needs": ""}
    assert try_it.status(styler(cerebras=True)) == {"ok": True, "ready": True, "needs": ""}
    assert try_it.status(styler()) == {"ok": True, "ready": False, "needs": "key"}
    assert try_it.status(None) == {"ok": True, "ready": False, "needs": "key"}


def test_a_styler_that_cannot_be_asked_reads_as_not_ready():
    def boom(_name):
        raise RuntimeError("no")
    assert try_it.status(types.SimpleNamespace(_provider_ready=boom)) == {"ok": True, "ready": False, "needs": "key"}


# ── the Api methods (app.py) ─────────────────────────────────────────────────

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
_NAMES = ("try_it_status", "try_transform", "try_command", "open_command_search", "_try_it_hooks")


def _api(pipeline, stored=None, **extra):
    nodes = [n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name in _NAMES]
    assert {n.name for n in nodes} == set(_NAMES), "an Api method moved or was renamed"
    logged, used = [], []
    ns = {"_pipeline": pipeline, "_try_it": try_it, "_command_mode": cm, "_log_to_file": logged.append,
          "record_usage_safely": lambda *a, **k: used.append((a, k))}
    ns.update(extra)
    exec(compile(ast.Module(nodes, []), "<app.py Api>", "exec"), ns)
    this = types.SimpleNamespace(_load_settings_file=lambda: dict(stored or {}))
    for name in _NAMES:
        setattr(this, name, types.MethodType(ns[name], this))
    this.logged, this.used = logged, used
    return this


def _pipeline(chat, ready=True):
    return types.SimpleNamespace(styler=types.SimpleNamespace(
        _chat=chat, _provider_ready=lambda name: ready and name == "groq",
        _strip_em_dashes=lambda text: text, _restore_censored_profanity=lambda styled, raw: styled))


def test_the_page_can_ask_whether_try_it_can_run():
    assert _api(_pipeline(chat_saying(POLISHED))).try_it_status() == {"ok": True, "ready": True, "needs": ""}
    assert _api(_pipeline(chat_saying(POLISHED), ready=False)).try_it_status() == {"ok": True, "ready": False, "needs": "key"}
    assert _api(None).try_it_status() == {"ok": True, "ready": False, "needs": "key"}


def test_try_transform_runs_the_stylers_chat_and_counts_the_usage():
    calls = []
    api = _api(_pipeline(chat_saying(POLISHED, calls)))
    assert api.try_transform(RAMBLE) == {"ok": True, "changed": True, "text": POLISHED}
    assert calls[0]["purpose"] == "transform"
    assert api.used and api.used[0][1]["purpose"] == "transform" and api.used[0][1]["provider"] == "groq"


def test_try_command_runs_the_stylers_chat_and_counts_the_usage():
    calls = []
    api = _api(_pipeline(chat_saying("ACTION: edit\n" + SHORT, calls)))
    assert api.try_command(RAMBLE, "make this shorter") == {"ok": True, "action": "edit", "changed": True, "text": SHORT}
    assert calls[0]["purpose"] == "command"
    assert api.used and api.used[0][1]["purpose"] == "command"


def test_with_no_key_the_methods_say_what_is_needed_and_send_nothing():
    calls = []
    for pipeline in (None, _pipeline(chat_saying(POLISHED, calls), ready=False)):
        api = _api(pipeline)
        assert api.try_transform(RAMBLE) == {"ok": False, "error": try_it.NEEDS_KEY}
        assert api.try_command(RAMBLE, "make this shorter") == {"ok": False, "error": try_it.NEEDS_KEY}
    assert calls == []


def test_the_methods_never_raise():
    class Broken:
        @property
        def styler(self):
            raise RuntimeError("gone")
    api = _api(Broken())
    assert api.try_it_status() == {"ok": True, "ready": False, "needs": "key"}
    assert api.try_transform(RAMBLE)["ok"] is False
    assert api.try_command(RAMBLE, "make this shorter")["ok"] is False

    def bad_settings():
        raise OSError("unreadable")
    api = _api(_pipeline(chat_saying(POLISHED)))
    api._load_settings_file = bad_settings
    assert api.try_transform(RAMBLE) == {"ok": False, "error": try_it.FAILED}
    assert api.try_command(RAMBLE, "make this shorter") == {"ok": False, "error": try_it.FAILED}
    assert api.open_command_search("anything") == {"ok": False}


def test_the_search_link_opens_the_chosen_engine_with_only_the_question(monkeypatch):
    opened = []
    browser = types.SimpleNamespace(open=lambda url, new=0: opened.append((url, new)) or True)
    monkeypatch.setitem(sys.modules, "webbrowser", browser)
    api = _api(None, stored={"command_mode": {"web_engine": "google"}})
    assert api.open_command_search("capital of France") == {"ok": True}
    assert opened == [(cm.search_url("google", "capital of France"), 2)]
    # The page hands over the words of the question, never an address to open.
    assert api.open_command_search("") == {"ok": False}
    assert api.open_command_search(None) == {"ok": False}
    assert len(opened) == 1


def test_nothing_in_the_try_path_touches_the_journal_the_clipboard_or_another_app():
    src = (ROOT / "src" / "try_it.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#")[0] for line in src.split("\n"))
    code = code.split('"""', 2)[2]          # past the module's own description
    for word in ("history", "clipboard", "paste", "read_selection", "text_context", "webbrowser", "open_url"):
        assert word not in code, f"src/try_it.py must not use {word}"
