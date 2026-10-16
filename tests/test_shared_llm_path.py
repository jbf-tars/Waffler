"""The shared way to ask the language model (S5): ``OpenAIStyler._chat``.

One path for the provider order the user chose, each provider's client, the
cooldowns a limit or a retired model start, the backup Groq model, the
reasoning flags, the reading of a failure and a deadline, with the state of a
call passed in rather than kept on the styler. The clean-up's three provider
methods now share its per-provider bodies, so these also pin that nothing about
what the clean-up sends changed.

Offline: the clients are stubs.
"""
import hashlib
import os
import sys
import threading
import time
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import cleanup_pause  # noqa: E402
import style_openai as so  # noqa: E402
import user_messages as um  # noqa: E402
from style_openai import ChatError, ChatReply, OpenAIStyler  # noqa: E402

PRIMARY = "openai/gpt-oss-120b"
BACKUP = "qwen/qwen3.8-27b"
TEXT = ("ok so I wanted to talk about the budget for the project and what we "
        "can ship by the end of next month")
CLEAN = "Ok, so I wanted to talk about the budget for the project and what we can ship by the end of next month."


def _answer(text="Done.", finish="stop", tokens=(5, 3)):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text), finish_reason=finish)],
        usage=types.SimpleNamespace(prompt_tokens=tokens[0], completion_tokens=tokens[1]) if tokens else None)


class Client:
    """A provider client. ``outcomes`` is a list used in turn (the last one
    repeats), or a dict by model. An exception is raised, a response returned;
    every call's keyword arguments are kept."""

    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.calls.append(kw)
        if isinstance(self.outcomes, dict):
            out = self.outcomes[kw["model"]]
        else:
            out = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if isinstance(out, BaseException):
            raise out
        return out


def styler(order=("groq", "cerebras", "openai"), groq=None, cerebras=None, openai=None, models=(PRIMARY,)):
    """A styler built like the existing tests build one: no __init__, no keys."""
    s = object.__new__(OpenAIStyler)
    s._provider_order = list(order)
    s._use_groq, s._use_cerebras = groq is not None, cerebras is not None
    s._groq_client, s._cerebras_client, s.client = groq, cerebras, openai
    s.model = "gpt-4.1-mini"
    s._groq_models, s._groq_model = tuple(models), models[0]
    s._cerebras_model = "gpt-oss-120b"
    s._groq_skip_until = s._cerebras_skip_until = 0.0
    s.prompt_template = "{transcript} {dialect_instruction}"
    return s


def chat(s, system="SYS", **over):
    args = dict(max_tokens=1024, temperature=0.2, deadline=time.monotonic() + 30, purpose="transform")
    args.update(over)
    return s._chat(system, "USER", **args)


def fake_clock(monkeypatch, start=1000.0):
    """The styler's own clock, as a box: ``now[0]`` is the time. (The styler's
    module reference only; the real ``time`` module is left alone.)"""
    now = [start]
    monkeypatch.setattr(so, "time", types.SimpleNamespace(
        monotonic=lambda: now[0], time=time.time, sleep=time.sleep))
    return now


RATE = RuntimeError("Error code: 429 - Rate limit reached for model `openai/gpt-oss-120b` on tokens per day "
                    "(TPD): Limit 200000, Used 199404, Requested 5236. Please try again in 16m12s.")
GONE = RuntimeError("Error code: 404 - The model `x` does not exist or you do not have access to it. "
                    "code: model_not_found")


# ── what the clean-up sends has not changed ──────────────────────────────────

def test_the_clean_up_system_messages_are_byte_for_byte_what_they_always_were():
    sha = lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest()      # noqa: E731
    assert sha(so._CLEANUP_SYSTEM) == "0514293ea569b9e1ab409cbd0d82eb219e9c52c4f25b9168b358d509d03e50a2"
    assert sha(so._CLEANUP_SYSTEM_OPENAI) == "29b83d6ed13624ab97235d0a44a1a015230dbc9e027f63071f8712b251e9526a"
    assert so._CLEANUP_TEMPERATURE == 0.1


def test_a_clean_up_request_to_each_provider_has_the_same_shape_as_before():
    answer = _answer(CLEAN)
    g, c, o = Client([answer]), Client([answer]), Client([answer])
    s = styler(groq=g, cerebras=c, openai=o)
    s._max_out_tokens, s._last_raw, s._deadline_at = 2048, TEXT, time.monotonic() + 30
    s._style_groq("PROMPT", time.time())
    s._style_cerebras("PROMPT", time.time())
    s._style_openai("PROMPT", TEXT, time.time())
    for client, system in ((g, so._CLEANUP_SYSTEM), (c, so._CLEANUP_SYSTEM), (o, so._CLEANUP_SYSTEM_OPENAI)):
        [sent] = client.calls
        assert sent["messages"] == [{"role": "system", "content": system},
                                    {"role": "user", "content": "PROMPT"}]
        assert sent["max_tokens"] == 2048 and sent["temperature"] == 0.1
        assert 0.1 <= sent["timeout"] <= so._STYLE_TIMEOUT_S
        assert "response_format" not in sent, "the clean-up never asks for JSON"
    assert g.calls[0]["model"] == PRIMARY and g.calls[0]["extra_body"] == {"reasoning_effort": "low"}
    assert c.calls[0]["model"] == "gpt-oss-120b" and c.calls[0]["extra_body"] == {"reasoning_effort": "low"}
    assert o.calls[0]["model"] == "gpt-4.1-mini" and "extra_body" not in o.calls[0]


def test_the_clean_up_answer_and_usage_keep_their_shape():
    s = styler(groq=Client([_answer("Hello there — fine.", finish="stop", tokens=(9, 4))]))
    s._max_out_tokens, s._last_raw, s._deadline_at = 2048, "hello there fine", time.monotonic() + 30
    styled, usage = s._style_groq("p", time.time())
    assert "—" not in styled, "the dash rule still runs on every clean-up"
    assert usage == {"input_tokens": 9, "output_tokens": 4, "api_used": True, "provider": "groq",
                     "finish_reason": "stop"}


def test_a_full_style_call_sends_the_template_prompt_to_the_first_provider():
    g = Client([_answer(CLEAN)])
    s = styler(order=("groq",), groq=g)
    styled, usage = s.style(TEXT)
    assert styled == CLEAN and usage["provider"] == "groq"
    [sent] = g.calls
    assert sent["messages"][1]["content"].startswith(TEXT + " ")
    assert sent["max_tokens"] == 2048


def test_style_takes_the_app_and_it_changes_nothing_yet():
    ctx = types.SimpleNamespace(name="Mail", category="email")
    results = []
    for kwargs in ({}, {"app_context": ctx}, {"app_context": None}):
        g = Client([_answer(CLEAN)])
        s = styler(order=("groq",), groq=g)
        results.append((s.style(TEXT, **kwargs), g.calls[0]["messages"]))
    assert results[0] == results[1] == results[2]


# ── nothing about a call is left on the styler ───────────────────────────────

def test_style_leaves_no_per_call_state_on_the_styler():
    s = styler(order=("groq",), groq=Client([_answer(CLEAN)]))
    s.style(TEXT)
    for name in ("_last_raw", "_max_out_tokens", "_deadline_at"):
        assert not hasattr(s, name), f"{name} is back on the styler"


def test_a_provider_method_called_directly_still_reads_the_hand_set_attributes():
    c = Client([_answer(CLEAN)])
    s = styler(groq=c)
    s._max_out_tokens, s._last_raw, s._deadline_at = 777, TEXT, time.monotonic() + 30
    s._style_groq("p", time.time())
    assert c.calls[0]["max_tokens"] == 777
    bare = styler(groq=Client([_answer(CLEAN)]))                     # nothing set at all
    bare._style_groq("p", time.time())
    assert bare._groq_client.calls[0]["max_tokens"] == 4096, "the old default"


def test_two_clean_ups_at_once_each_use_their_own_transcript():
    """B runs to the end while A waits for its provider. In the old styler B's
    transcript had replaced A's by then, so A's restored swear word (taken
    from what was said) was looked up in B's words and lost."""
    said_a = "ok so that is fucking brilliant and I really mean it for the whole project plan"
    censored = "Ok, so that is brilliant and I really mean it for the whole project plan."
    said_b = "right so the second thing I wanted to mention is the budget for the whole project plan"
    in_a, release_a = threading.Event(), threading.Event()

    class Slow(Client):
        def create(self, **kw):
            self.calls.append(kw)
            if said_a in kw["messages"][1]["content"]:
                in_a.set()
                release_a.wait(10)
                return _answer(censored)
            return _answer("Right, so the second thing I wanted to mention is the budget for the whole project plan.")

    s = styler(order=("groq",), groq=Slow([]))
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("a", s.style(said_a)))
    t.start()
    assert in_a.wait(5)
    b_styled, _ = s.style(said_b)                    # a whole other clean-up, meanwhile
    assert b_styled.startswith("Right, so")
    release_a.set()
    t.join(10)
    a_styled, a_usage = out["a"]
    assert "fucking" in a_styled, "A's swear word was restored from A's own transcript"
    assert a_usage["provider"] == "groq"


def test_two_clean_ups_at_once_each_keep_their_own_budget():
    seen = {}
    release = threading.Event()
    inside = threading.Event()
    long_said = " ".join(f"word{i}" for i in range(800))

    class Spy(Client):
        def create(self, **kw):
            key = "long" if "word799" in kw["messages"][1]["content"] else "short"
            seen[key] = (kw["max_tokens"], kw["timeout"])
            if key == "long":
                inside.set()
                release.wait(10)
            return _answer("fine " * 800)

    s = styler(order=("groq",), groq=Spy([]))
    t = threading.Thread(target=lambda: s.style(long_said))
    t.start()
    assert inside.wait(5)
    s.style(TEXT)
    release.set()
    t.join(10)
    assert seen["long"][0] == 2400 and seen["short"][0] == 2048     # 3 per word, floor 2048
    assert seen["long"][1] <= so._STYLE_TIMEOUT_S and seen["short"][1] <= so._STYLE_TIMEOUT_S


def test_the_budget_guard_uses_the_calls_own_clock(monkeypatch):
    """A provider that uses up the whole budget: the next ones are not started."""
    now = fake_clock(monkeypatch)
    tried = []

    def eat(self_, prompt, start):
        tried.append("groq")
        now[0] += 60.0
        raise RuntimeError("provider hung")

    def other(name):
        def run(self_, *a):
            tried.append(name)
            return ("x", {})
        return run
    monkeypatch.setattr(OpenAIStyler, "_style_groq", eat)
    monkeypatch.setattr(OpenAIStyler, "_style_cerebras", other("cerebras"))
    monkeypatch.setattr(OpenAIStyler, "_style_openai", other("openai"))
    monkeypatch.setattr(OpenAIStyler, "_log_provider_failure", lambda *a, **k: None)
    s = styler(groq=object(), cerebras=object(), openai=object())
    styled, usage = s.style(TEXT)
    assert tried == ["groq"], "the clock ran out, so the rest were skipped"
    assert usage["provider"] == "basic_clean" and "TIMEOUT" in usage["fallback_reason"]
    assert "ship by the end of next month" in styled


# ── _chat: the provider order and what comes back ────────────────────────────

def test_the_first_provider_in_the_users_order_answers():
    g, c, o = Client([_answer("from groq")]), Client([_answer("from cerebras")]), Client([_answer("from openai")])
    for order, who in ((("groq", "cerebras", "openai"), "groq"), (("openai", "groq", "cerebras"), "openai"),
                       (("cerebras", "openai", "groq"), "cerebras")):
        s = styler(order=order, groq=g, cerebras=c, openai=o)
        reply = chat(s)
        assert reply.provider == who and reply.text == f"from {who}"


def test_a_reply_carries_the_text_the_provider_the_model_and_the_tokens():
    s = styler(order=("groq",), groq=Client([_answer("  Hello.  ", finish="length", tokens=(12, 6))]))
    reply = chat(s)
    assert isinstance(reply, ChatReply)
    assert (reply.text, reply.provider, reply.model) == ("Hello.", "groq", PRIMARY)
    assert (reply.input_tokens, reply.output_tokens, reply.finish_reason) == (12, 6, "length")
    assert reply.usage == {"input_tokens": 12, "output_tokens": 6, "api_used": True, "provider": "groq",
                           "finish_reason": "length"}
    with pytest.raises(Exception):
        reply.text = "changed"


def test_no_usage_from_the_provider_counts_as_zero():
    s = styler(order=("groq",), groq=Client([_answer("ok", tokens=None)]))
    assert chat(s).usage["input_tokens"] == 0 and chat(s).usage["output_tokens"] == 0


def test_it_does_no_clean_up_of_its_own():
    wild = "Here is the cleaned text: ok — it is what it is"
    reply = chat(styler(order=("groq",), groq=Client([_answer(wild)])))
    assert reply.text == wild, "dashes, preambles and the rest are the caller's business"


def test_a_failing_provider_hands_over_to_the_next_in_the_order():
    g, o = Client([RuntimeError("Error code: 500")]), Client([_answer("from openai")])
    s = styler(order=("groq", "openai"), groq=g, openai=o)
    assert chat(s).provider == "openai" and len(g.calls) == 1 and len(o.calls) == 1


def test_openai_failing_first_does_not_stop_the_others():
    o, g = Client([RuntimeError("Error code: 500")]), Client([_answer("from groq")])
    s = styler(order=("openai", "groq"), groq=g, openai=o)
    assert chat(s).provider == "groq"


def test_providers_without_a_key_are_skipped_and_nothing_configured_says_so():
    g = Client([_answer("from groq")])
    assert chat(styler(order=("cerebras", "openai", "groq"), groq=g)).provider == "groq"
    with pytest.raises(ChatError) as ei:
        chat(styler(order=("groq", "cerebras", "openai")))
    assert "No styling providers configured" in ei.value.reason and ei.value.failures == []


def test_an_empty_answer_is_a_failure_and_the_next_provider_is_tried():
    g, o = Client([_answer("")]), Client([_answer("from openai")])
    s = styler(order=("groq", "openai"), groq=g, openai=o)
    assert chat(s).provider == "openai"
    only = styler(order=("groq",), groq=Client([_answer("   ")]))
    with pytest.raises(ChatError) as ei:
        chat(only)
    assert "returned no text" in ei.value.reason


def test_a_provider_answering_nothing_at_all_is_a_failure_too():
    g = Client([_answer(None)])
    with pytest.raises(ChatError):
        chat(styler(order=("groq",), groq=g))


def test_each_provider_can_have_its_own_system_message():
    g, c, o = Client([_answer("g")]), Client([_answer("c")]), Client([_answer("o")])
    s = styler(order=("groq",), groq=g, cerebras=c, openai=o)
    system = {"groq": "for groq", "cerebras": "for cerebras", "openai": "for openai"}
    chat(s, system=system)
    s._provider_order = ["cerebras"]
    chat(s, system=system)
    s._provider_order = ["openai"]
    chat(s, system=system)
    assert [x.calls[0]["messages"][0]["content"] for x in (g, c, o)] == ["for groq", "for cerebras", "for openai"]
    s._provider_order = ["groq"]
    chat(s, system={"default": "everyone"})
    assert g.calls[-1]["messages"][0]["content"] == "everyone"
    chat(s, system="a plain string")
    assert g.calls[-1]["messages"] == [{"role": "system", "content": "a plain string"},
                                       {"role": "user", "content": "USER"}]


def test_the_requests_own_arguments_are_what_is_sent():
    g = Client([_answer("ok")])
    chat(styler(order=("groq",), groq=g), max_tokens=321, temperature=0.7)
    [sent] = g.calls
    assert sent["max_tokens"] == 321 and sent["temperature"] == 0.7


# ── reasoning flags and JSON mode ────────────────────────────────────────────

def test_each_model_is_told_how_much_to_reason():
    for model, extras in ((PRIMARY, {"reasoning_effort": "low"}), (BACKUP, {"reasoning_effort": "none"}),
                          ("llama-3.3-70b-versatile", None)):
        g = Client([_answer("ok")])
        chat(styler(order=("groq",), groq=g, models=(model,)))
        assert g.calls[0].get("extra_body") == extras, model
    c, o = Client([_answer("ok")]), Client([_answer("ok")])
    chat(styler(order=("cerebras",), cerebras=c))
    chat(styler(order=("openai",), openai=o))
    assert c.calls[0]["extra_body"] == {"reasoning_effort": "low"} and "extra_body" not in o.calls[0]


@pytest.mark.parametrize("provider", ["groq", "cerebras", "openai"])
def test_json_mode_is_passed_through_only_when_asked_for(provider):
    client = Client([_answer('{"a": 1}')])
    s = styler(order=(provider,), **{provider: client})
    chat(s)
    chat(s, json_mode=True)
    assert "response_format" not in client.calls[0]
    assert client.calls[1]["response_format"] == {"type": "json_object"}
    assert client.calls[1]["messages"] == client.calls[0]["messages"]


def test_a_json_error_is_not_mistaken_for_a_rate_limit():
    """Groq says "Failed to generate JSON"; "generate" contains "rate"."""
    boom = RuntimeError("Error code: 400 - {'error': {'message': 'Failed to generate JSON. Please adjust "
                        "your prompt.', 'code': 'json_validate_failed'}}")
    g, o = Client([boom]), Client([_answer('{"ok": true}')])
    s = styler(order=("groq", "openai"), groq=g, openai=o)
    assert chat(s, json_mode=True).provider == "openai"
    assert s._groq_skip_until == 0.0, "Groq is not paused for a minute over it"
    chat(s, json_mode=True)
    assert len(g.calls) == 2, "and the next call still tries Groq"


@pytest.mark.parametrize("message,limit", [
    ("Rate limit reached", True), ("rate_limit_exceeded", True),
    ("exceeded the request rates", True), ("Failed to generate JSON", False), ("please migrate to another model", False),
    ("cannot separate accurate moderate output", False), ("", False),
])
def test_rate_is_read_as_a_word(message, limit):
    assert so._mentions_rate(message) is limit
    assert so._mentions_rate(message.upper()) is limit


def test_a_retired_cerebras_model_is_a_missing_model_not_a_limit():
    msg = "The model has been decommissioned and is no longer supported. Please migrate to another model."
    s = styler(order=("cerebras",), cerebras=Client([RuntimeError(msg)]))
    with pytest.raises(ChatError) as ei:
        chat(s)
    assert "MODEL_UNAVAILABLE" in ei.value.reason and "RATE_LIMIT" not in ei.value.reason
    assert s._cerebras_skip_until - time.monotonic() > 3000


# ── cooldowns ────────────────────────────────────────────────────────────────

def test_after_a_limit_the_provider_is_skipped_and_the_reason_says_why():
    g, o = Client([RATE]), Client([RuntimeError("Error code: 500")])
    s = styler(order=("groq", "openai"), groq=g, openai=o)
    with pytest.raises(ChatError) as first:
        chat(s)
    assert first.value.reason.startswith("RATE_LIMIT|tokens per day (TPD)|16m12s|Groq: ")
    assert s._groq_skip_until - time.monotonic() > 900
    with pytest.raises(ChatError) as second:
        chat(s)
    assert len(g.calls) == 1, "Groq is not asked again while it is cooling down"
    assert second.value.reason.startswith("RATE_LIMIT|")
    assert um.cleanup_skipped_message(second.value.reason)[0].startswith("Clean-up paused")
    assert cleanup_pause.from_reason(second.value.reason, __import__("datetime").datetime.now()) is not None


def test_a_cooldown_in_one_call_path_is_the_other_ones_too():
    """It is the provider's state, shared on purpose."""
    g = Client([RATE])
    s = styler(order=("groq",), groq=g)
    with pytest.raises(ChatError):
        chat(s)
    styled, usage = s.style(TEXT)
    assert usage["provider"] == "basic_clean" and usage["fallback_reason"].startswith("RATE_LIMIT|")
    assert len(g.calls) == 1, "the clean-up did not ask Groq either"


def test_a_connection_problem_pauses_a_provider_briefly():
    g = Client([RuntimeError("Connection error.")])
    s = styler(order=("groq",), groq=g)
    with pytest.raises(ChatError) as ei:
        chat(s)
    assert ei.value.reason.startswith("Groq: CONNECTION")
    assert 20 < s._groq_skip_until - time.monotonic() <= 30


def test_an_auth_failure_pauses_a_provider_for_the_session():
    g = Client([RuntimeError("Error code: 401 - invalid_api_key")])
    s = styler(order=("groq",), groq=g)
    with pytest.raises(ChatError) as ei:
        chat(s)
    assert ei.value.reason.startswith("Groq: AUTH")
    assert s._groq_skip_until - time.monotonic() > 3000


def test_the_most_useful_failure_wins_when_every_provider_fails():
    g = Client([RuntimeError("Error code: 500")])
    c = Client([RuntimeError("Connection error.")])
    o = Client([RuntimeError("Error code: 500")])
    s = styler(groq=g, cerebras=c, openai=o)
    with pytest.raises(ChatError) as ei:
        chat(s)
    assert ei.value.reason.startswith("Cerebras: CONNECTION"), "a connection problem beats a bare 500"
    assert [f[0] for f in ei.value.failures] == ["Groq", "Cerebras", "OpenAI"]
    s2 = styler(groq=Client([RATE]), cerebras=Client([RuntimeError("Error code: 500")]),
                openai=Client([RuntimeError("Error code: 500")]))
    with pytest.raises(ChatError) as ei2:
        chat(s2)
    assert ei2.value.reason.startswith("RATE_LIMIT|"), "a limit is the most useful thing to tell the user"


# ── a retired Groq model ─────────────────────────────────────────────────────

def test_a_retired_groq_model_moves_to_the_backup_and_is_remembered():
    g = Client({PRIMARY: GONE, BACKUP: _answer("from the backup")})
    s = styler(order=("groq",), groq=g, models=(PRIMARY, BACKUP))
    reply = chat(s)
    assert reply.text == "from the backup" and reply.model == BACKUP and reply.provider == "groq"
    assert [c["model"] for c in g.calls] == [PRIMARY, BACKUP]
    assert g.calls[1]["extra_body"] == {"reasoning_effort": "none"}, "the backup gets its own flag"
    assert s._groq_model == BACKUP and s._groq_skip_until == 0.0
    chat(s)
    assert [c["model"] for c in g.calls][-1] == BACKUP and len(g.calls) == 3


def test_a_retired_model_with_no_backup_is_a_cooldown_that_says_so():
    g = Client({PRIMARY: GONE})
    s = styler(order=("groq",), groq=g)
    with pytest.raises(ChatError) as first:
        chat(s)
    assert "MODEL_UNAVAILABLE" in first.value.reason
    with pytest.raises(ChatError) as second:
        chat(s)
    assert "MODEL_UNAVAILABLE" in second.value.reason and "RATE_LIMIT" not in second.value.reason
    assert len(g.calls) == 1
    assert cleanup_pause.from_reason(second.value.reason, __import__("datetime").datetime.now()) is None


def test_the_move_to_a_backup_model_respects_the_deadline(monkeypatch):
    now = fake_clock(monkeypatch)

    class Slowly(Client):
        def create(self, **kw):
            now[0] += 1.0                            # finding out took a second
            return super().create(**kw)
    g = Slowly({PRIMARY: GONE, BACKUP: _answer("late")})
    s = styler(order=("groq",), groq=g, models=(PRIMARY, BACKUP))
    with pytest.raises(ChatError) as ei:
        chat(s, deadline=now[0] + 2.0)               # enough to start, not enough to start again
    assert "TIMEOUT" in ei.value.reason
    assert [c["model"] for c in g.calls] == [PRIMARY]
    assert s._groq_model == BACKUP, "and the backup is remembered for the next call"


# ── deadlines ────────────────────────────────────────────────────────────────

def test_no_provider_is_started_with_too_little_time_left():
    g = Client([_answer("ok")])
    s = styler(order=("groq",), groq=g)
    with pytest.raises(ChatError) as ei:
        chat(s, deadline=time.monotonic() + 0.5)
    assert ei.value.reason.startswith("deadline: TIMEOUT|transform budget exhausted")
    assert g.calls == []
    with pytest.raises(ChatError):
        chat(s, deadline=time.monotonic() - 5)
    assert g.calls == []


def test_a_providers_timeout_is_what_is_left_of_the_deadline():
    g = Client([_answer("ok")])
    s = styler(order=("groq",), groq=g)
    chat(s, deadline=time.monotonic() + 4.0)
    assert 2.5 < g.calls[0]["timeout"] <= 4.0
    chat(s, deadline=time.monotonic() + 500.0)
    assert g.calls[1]["timeout"] == so._STYLE_TIMEOUT_S, "never more than the per-provider cap"
    chat(s, deadline=None)
    assert g.calls[2]["timeout"] == so._STYLE_TIMEOUT_S


def test_the_deadline_covers_every_provider_tried(monkeypatch):
    now = fake_clock(monkeypatch)

    class Eats(Client):
        def create(self, **kw):
            self.calls.append(kw)
            now[0] += 9.0
            raise RuntimeError("Error code: 500")
    first, second = Eats([]), Client([_answer("late")])
    s = styler(order=("groq", "openai"), groq=first, openai=second)
    with pytest.raises(ChatError) as ei:
        chat(s, deadline=now[0] + 10.0)
    assert second.calls == [] and "TIMEOUT" in ei.value.reason, "1 s left is not enough to start OpenAI"


# ── state belongs to the call ────────────────────────────────────────────────

def test_two_chats_at_once_keep_their_own_arguments():
    release = threading.Event()
    inside = threading.Event()

    class Hold(Client):
        def create(self, **kw):
            self.calls.append(kw)
            if kw["messages"][1]["content"] == "first":
                inside.set()
                release.wait(10)
            return _answer(kw["messages"][1]["content"] + " done")

    g = Hold([])
    s = styler(order=("groq",), groq=g)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("first", s._chat(
        "SYS-1", "first", max_tokens=111, temperature=0.1, deadline=time.monotonic() + 60, purpose="a")))
    t.start()
    assert inside.wait(5)
    second = s._chat("SYS-2", "second", max_tokens=222, temperature=0.9, deadline=time.monotonic() + 5, purpose="b")
    release.set()
    t.join(10)
    assert out["first"].text == "first done" and second.text == "second done"
    by_user = {c["messages"][1]["content"]: c for c in g.calls}
    assert (by_user["first"]["max_tokens"], by_user["first"]["temperature"], by_user["first"]["messages"][0]["content"]) == (111, 0.1, "SYS-1")
    assert (by_user["second"]["max_tokens"], by_user["second"]["temperature"], by_user["second"]["messages"][0]["content"]) == (222, 0.9, "SYS-2")
    assert by_user["first"]["timeout"] > by_user["second"]["timeout"], "each has its own clock"
    for name in ("_last_raw", "_max_out_tokens", "_deadline_at"):
        assert not hasattr(s, name)


def test_a_chat_does_not_disturb_a_clean_up_running_on_another_thread():
    said = "ok so that is fucking brilliant and I really mean it for the whole project plan"
    censored = "Ok, so that is brilliant and I really mean it for the whole project plan."
    in_clean_up, release = threading.Event(), threading.Event()

    class Slow(Client):
        def create(self, **kw):
            self.calls.append(kw)
            if said in kw["messages"][1]["content"]:
                in_clean_up.set()
                release.wait(10)
                return _answer(censored)
            return _answer("a transform result")

    s = styler(order=("groq",), groq=Slow([]))
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("a", s.style(said)))
    t.start()
    assert in_clean_up.wait(5)
    assert chat(s).text == "a transform result"
    release.set()
    t.join(10)
    assert "fucking" in out["a"][0]


# ── logging ──────────────────────────────────────────────────────────────────

def test_a_failure_is_logged_under_the_features_name_without_any_text(_isolated_data_dir):
    boom = RuntimeError("Error code: 500 - boom")
    s = styler(order=("groq", "openai"), groq=Client([boom]), openai=Client([_answer("ok")]))
    s._chat("SYS", "SECRET-USER-WORDS", max_tokens=100, temperature=0.1, deadline=None, purpose="transform")
    log = (_isolated_data_dir / "app.log").read_text(encoding="utf-8")
    assert "[transform] Groq FAILED" in log
    assert "SECRET-USER-WORDS" not in log
    s2 = styler(order=("groq",), groq=Client([boom]))
    s2.style(TEXT)
    assert "[styling] Groq FAILED" in (_isolated_data_dir / "app.log").read_text(encoding="utf-8")


def test_the_log_helper_keeps_its_old_call():
    s = styler()
    s._log_provider_failure("probe", RuntimeError("x"))             # tests/test_data_dir_isolation.py
