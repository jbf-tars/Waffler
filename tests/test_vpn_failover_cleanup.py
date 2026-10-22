"""The clean-up behind a VPN that blocks, drops or stalls a provider.

The same network as tests/test_vpn_failover.py (tests/_net_fakes.py), under the
clean-up chain (src/style_openai.py): real request code, real SDK clients, a
clock that only moves when something waits. The clean-up already had a
deadline (12 to 30 s for the whole step) and a flat 30 s pause for a failed
connection; a provider that dropped its packets still ate the deadline, and the
pause repeated the loss twice a minute. The pinned tests of the deadline and of
the Groq 403 pause (an hour) are untouched.
"""
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _net_fakes as nf  # noqa: E402
import style_openai as so  # noqa: E402
from style_openai import OpenAIStyler  # noqa: E402

PRIMARY = "openai/gpt-oss-120b"
TEXT = ("ok so I think we should ship the new build on monday after the team has had a "
        "chance to look at it properly")


def chat_works(text=TEXT, took=0.4):
    def mode(p, request, t):
        p.net.now += took
        return httpx.Response(200, content=json.dumps({
            "id": "c1", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40},
        }), headers={"content-type": "application/json"})
    mode.__name__ = "chat_works"
    return mode


def rate_limited():
    def mode(p, request, t):
        p.net.now += 0.2
        return httpx.Response(429, content=json.dumps({"error": {
            "message": "Rate limit reached for model on tokens per day (TPD): Limit 200000. "
                       "Please try again in 16m12s."}}), headers={"content-type": "application/json"})
    return mode


def read_timeout():
    def mode(p, request, t):
        p.net.now += t["read"]
        raise httpx.ReadTimeout("timed out", request=request)
    return mode


@pytest.fixture
def net(monkeypatch):
    return nf.FakeNet(monkeypatch)


@pytest.fixture
def log(monkeypatch):
    lines = []
    monkeypatch.setattr(so, "_log_line", lines.append)
    return lines


def styler(net, order=("groq", "openai"), groq=None, cerebras=None, openai=None):
    """A styler built like the existing tests build one: no __init__, no keys.
    Each argument is a mode (or None for no key); returns the styler and the
    three providers."""
    s = object.__new__(OpenAIStyler)
    g = nf.Provider(net, "groq", groq) if groq else None
    c = nf.Provider(net, "cerebras", cerebras) if cerebras else None
    o = nf.Provider(net, "openai", openai) if openai else None
    s._provider_order = list(order)
    s._use_groq, s._use_cerebras = g is not None, c is not None
    s._groq_client = nf.groq_client(g) if g else None
    s._cerebras_client = nf.openai_client(c) if c else None
    s.client = nf.openai_client(o) if o else None
    s.model = "gpt-4.1-mini"
    s._groq_models, s._groq_model = (PRIMARY,), PRIMARY
    s._cerebras_model = "gpt-oss-120b"
    s._groq_skip_until = s._cerebras_skip_until = 0.0
    s.prompt_template = "{transcript} {dialect_instruction}"
    return s, g, c, o


def left(s, name="groq"):
    import time
    return getattr(s, f"_{name}_skip_until") - time.monotonic()


# ── a black hole costs seconds, not the deadline ─────────────────────────────

def test_a_black_holed_groq_costs_the_connect_timeout_and_openai_cleans_up(net, log):
    s, g, c, o = styler(net, groq=nf.black_hole(), openai=chat_works())
    started = net.now
    styled, usage = s.style(TEXT)
    assert usage["provider"] == "openai" and usage["api_used"] is True
    assert net.now - started == pytest.approx(6.0 + 0.4)
    assert g.calls[0][1]["connect"] == 6.0
    assert usage["failovers"] == [{"provider": "groq", "used": "openai", "kind": "unreachable",
                                   "stage": "cleanup"}]
    assert left(s) == pytest.approx(30.0 - 0.4)


def test_before_this_a_hung_provider_took_the_whole_deadline(net, log):
    """What the deadline alone allowed, so the improvement is on record: the
    attempt timeout is min(15, what is left of the 12 s), so a connect that
    waits for all of it leaves nothing for the next provider."""
    s, g, c, o = styler(net, groq=nf.black_hole(), openai=chat_works())
    s.style(TEXT)
    assert g.calls[0][1]["read"] == 12.0, "the attempt may take what the deadline allows"
    assert g.calls[0][1]["connect"] == 6.0 < 12.0, "but not all of it to connect"


def test_the_failover_line_says_what_a_diagnosis_needs(net, log):
    s, g, c, o = styler(net, groq=nf.black_hole(), openai=chat_works())
    s.style(TEXT)
    [line] = [x for x in log if x.startswith("[net]") and "failed" in x]
    assert line == ("[net] cleanup groq failed: ConnectTimeout (unreachable) after 6.0s; "
                    "paused 30s (failure 1 in a row); next: openai; vpn: unknown")


def test_a_block_is_still_an_hour_and_is_recorded(net, log):
    """The pinned Groq 403 handling is as it was (tests/test_shared_llm_path.py)."""
    s, g, c, o = styler(net, groq=nf.edge_403(), openai=chat_works())
    styled, usage = s.style(TEXT)
    assert usage["provider"] == "openai"
    assert left(s) > 3000
    assert usage["failovers"][0]["kind"] == "blocked"
    [line] = [x for x in log if x.startswith("[net]") and "failed" in x]
    assert "PermissionDeniedError (blocked, HTTP 403)" in line and "paused 3600s" in line


def test_a_block_with_nobody_to_take_over_records_no_failover(net, log):
    s, g, c, o = styler(net, order=("groq",), groq=nf.edge_403())
    styled, usage = s.style(TEXT)
    assert usage["provider"] == "basic_clean" and "failovers" not in usage
    assert usage["fallback_reason"].startswith("Groq: AUTH")


def test_a_reset_falls_over_at_once(net, log):
    s, g, c, o = styler(net, groq=nf.reset(), openai=chat_works())
    started = net.now
    styled, usage = s.style(TEXT)
    assert usage["provider"] == "openai"
    assert usage["failovers"][0]["kind"] == "unreachable"
    assert net.now - started == pytest.approx(0.05 + 0.4)


def test_a_stall_after_connecting_is_bounded_by_the_cleanups_own_deadline(net, log):
    """Only the connect is shortened for the clean-up. A provider that connects
    and then goes quiet still has the whole attempt (what the 12 s deadline
    leaves), so this dictation is pasted as said once the deadline is spent,
    which is the promise the deadline already made. The next ones do not wait
    at all: the provider is paused and OpenAI cleans up."""
    for mode in (nf.stalled_upload(), read_timeout()):
        s, g, c, o = styler(net, groq=mode, openai=chat_works())
        started = net.now
        styled, usage = s.style(TEXT)
        assert usage["provider"] == "basic_clean" and "timed out" in usage["fallback_reason"]
        assert net.now - started == pytest.approx(12.0), "the deadline, not the 60 s of a plain timeout"
        assert left(s) > 0
        started = net.now
        styled, usage = s.style(TEXT)
        assert usage["provider"] == "openai" and net.now - started == pytest.approx(0.4)


def test_a_provider_that_works_has_no_failover_and_no_net_line(net, log):
    s, g, c, o = styler(net, groq=chat_works("Okay."), openai=chat_works())
    s.style(TEXT)
    assert not [x for x in log if x.startswith("[net]")]
    assert "failovers" not in s.style(TEXT)[1]


def test_a_rate_limit_is_not_a_network_failover(net, log):
    s, g, c, o = styler(net, groq=rate_limited(), openai=chat_works())
    styled, usage = s.style(TEXT)
    assert usage["provider"] == "openai" and "failovers" not in usage
    assert left(s) > 900, "its own wait, unchanged"


def test_a_slow_answer_is_not_a_network_failover_but_does_pause_the_cleanup(net, log):
    s, g, c, o = styler(net, groq=read_timeout(), openai=chat_works())
    styled, usage = s.style(TEXT)
    assert "failovers" not in usage
    assert left(s) > 0, "the SDK's 'Request timed out.' has neither 'connection' nor 'timeout' in it"
    assert any("ReadTimeout (slow)" in x for x in log)


# ── the timeouts the clients are given ───────────────────────────────────────

def test_connecting_is_fast_only_when_another_provider_is_next(net, log):
    s, g, c, o = styler(net, order=("groq", "cerebras", "openai"), groq=chat_works(),
                        cerebras=chat_works(), openai=chat_works())
    s._max_out_tokens, s._last_raw, s._deadline_at = 2048, TEXT, __import__("time").monotonic() + 30
    s._style_groq("PROMPT", 0.0)
    s._style_cerebras("PROMPT", 0.0)
    s._style_openai("PROMPT", TEXT, 0.0)
    assert g.calls[0][1]["connect"] == 6.0 and g.calls[0][1]["read"] == 15.0
    assert c.calls[0][1]["connect"] == 6.0
    assert o.calls[0][1] == {"connect": 15.0, "read": 15.0, "write": 15.0, "pool": 15.0}, \
        "the last one in the order is waited on, as before"


def test_the_only_provider_is_given_the_whole_attempt_to_connect(net, log):
    s, g, c, o = styler(net, order=("groq",), groq=chat_works())
    s._max_out_tokens, s._last_raw, s._deadline_at = 2048, TEXT, __import__("time").monotonic() + 30
    s._style_groq("PROMPT", 0.0)
    assert g.calls[0][1] == {"connect": 15.0, "read": 15.0, "write": 15.0, "pool": 15.0}


def test_a_provider_that_is_paused_is_not_a_fallback(net, log):
    s, g, c, o = styler(net, order=("groq", "cerebras"), groq=chat_works(), cerebras=chat_works())
    s._cerebras_skip_until = __import__("time").monotonic() + 100
    s._max_out_tokens, s._last_raw, s._deadline_at = 2048, TEXT, __import__("time").monotonic() + 30
    s._style_groq("PROMPT", 0.0)
    assert g.calls[0][1]["connect"] == 15.0, "nothing can take over, so Groq is waited on"


def test_a_provider_before_it_in_the_order_is_not_a_fallback(net, log):
    s, g, c, o = styler(net, order=("openai", "groq"), groq=chat_works(), openai=chat_works())
    s._max_out_tokens, s._last_raw, s._deadline_at = 2048, TEXT, __import__("time").monotonic() + 30
    s._style_groq("PROMPT", 0.0)
    assert g.calls[0][1]["connect"] == 15.0, "OpenAI has already been tried by then"
    s._style_openai("PROMPT", TEXT, 0.0)
    assert o.calls[0][1]["connect"] == 6.0


def test_the_timeout_is_still_what_is_left_of_the_deadline(net, log):
    s, g, c, o = styler(net, groq=chat_works(), openai=chat_works())
    s._max_out_tokens, s._last_raw = 2048, TEXT
    s._deadline_at = __import__("time").monotonic() + 4.0
    s._style_groq("PROMPT", 0.0)
    t = g.calls[0][1]
    assert t["read"] == pytest.approx(4.0) and t["connect"] == 4.0, "never more than what is left"


def test_a_styler_built_without_its_fields_still_gets_a_timeout():
    s = object.__new__(OpenAIStyler)
    assert s._request_timeout("groq") == so._STYLE_TIMEOUT_S


# ── pauses that grow ─────────────────────────────────────────────────────────

def styled_in_a_row(net, s, g, n):
    pauses = []
    for _ in range(n):
        before = len(g.calls)
        s.style(TEXT)
        assert len(g.calls) == before + 1, "Groq was asked, since its pause was over"
        failed_at = g.calls[-1][0] + 6.0
        pauses.append(round(s._groq_skip_until - failed_at, 1))
        net.pass_time(pauses[-1] + 1)
    return pauses


def test_a_black_holed_groq_is_paused_30_60_120_240_then_five_minutes(net, log):
    s, g, c, o = styler(net, groq=nf.black_hole(), openai=chat_works())
    assert styled_in_a_row(net, s, g, 7) == [30, 60, 120, 240, 300, 300, 300]


def test_cerebras_is_paused_the_same_way(net, log):
    s, g, c, o = styler(net, order=("cerebras", "openai"), cerebras=nf.black_hole(), openai=chat_works())
    pauses = []
    for _ in range(6):
        before = len(c.calls)
        s.style(TEXT)
        assert len(c.calls) == before + 1
        failed_at = c.calls[-1][0] + 6.0
        pauses.append(round(s._cerebras_skip_until - failed_at, 1))
        net.pass_time(pauses[-1] + 1)
    assert pauses == [30, 60, 120, 240, 300, 300]


def test_the_first_answer_starts_the_count_again(net, log):
    s, g, c, o = styler(net, groq=nf.black_hole(), openai=chat_works())
    assert styled_in_a_row(net, s, g, 3) == [30, 60, 120]
    g.mode = chat_works(TEXT)
    styled, usage = s.style(TEXT)
    assert usage["provider"] == "groq"
    assert any("[net] cleanup groq answered again after 3 failed tries in a row" in x for x in log)
    g.mode = nf.black_hole()
    assert styled_in_a_row(net, s, g, 2) == [30, 60]


def test_a_pause_is_skipped_not_asked(net, log):
    s, g, c, o = styler(net, groq=nf.black_hole(), openai=chat_works())
    s.style(TEXT)
    g.mode = chat_works("from groq")
    for _ in range(4):
        net.pass_time(3)
        started = net.now
        assert s.style(TEXT)[1]["provider"] == "openai"
        assert net.now - started == pytest.approx(0.4)
    assert len(g.calls) == 1


def test_the_only_clean_up_provider_is_still_paused_as_it_always_was(net, log):
    """Unlike speech, the clean-up can paste the words as said, so pausing its
    only provider costs a tidy-up, not the dictation (pinned by
    tests/test_shared_llm_path.py)."""
    s, g, c, o = styler(net, order=("groq",), groq=nf.black_hole())
    styled, usage = s.style(TEXT)
    assert usage["provider"] == "basic_clean" and "failovers" not in usage
    assert left(s) == pytest.approx(30.0, abs=0.1)


# ── which failures pause, and which do not ───────────────────────────────────

def chat(s, purpose):
    return s._chat("SYS", "USER", max_tokens=256, temperature=0.2,
                   deadline=__import__("time").monotonic() + 30, purpose=purpose)


def test_a_slow_answer_pauses_the_cleanup_but_not_a_transform_or_a_command(net, log):
    for purpose, paused in (("styling", True), ("transform", False), ("command", False)):
        s, g, c, o = styler(net, order=("groq",), groq=read_timeout())
        with pytest.raises(so.ChatError):
            chat(s, purpose)
        assert (left(s) > 0) is paused, purpose


def test_a_connection_that_failed_pauses_whatever_the_request_was_for(net, log):
    for purpose in ("styling", "transform", "command"):
        s, g, c, o = styler(net, order=("groq",), groq=nf.black_hole())
        with pytest.raises(so.ChatError):
            chat(s, purpose)
        assert left(s) > 0, purpose


def test_a_chat_failure_is_in_the_log_with_its_purpose(net, log):
    s, g, c, o = styler(net, order=("groq", "openai"), groq=nf.black_hole(), openai=chat_works("done"))
    assert chat(s, "transform").text == "done"
    assert any(x.startswith("[net] transform groq failed: ConnectTimeout") and "next: openai" in x
               for x in log)


def test_a_timed_out_message_is_a_connection_problem_for_the_user_s_message():
    reason = OpenAIStyler._pick_best_failure_reason([("OpenAI", "Request timed out."),
                                                     ("Groq", "something odd")])
    assert reason.startswith("OpenAI: Request timed out.")
    from user_messages import cleanup_skipped_message
    assert "couldn't reach OpenAI" in cleanup_skipped_message(reason)[1]


def test_no_key_and_no_text_reach_the_net_line(net, log):
    s, g, c, o = styler(net, groq=nf.black_hole(), openai=chat_works())
    s.style(TEXT)
    joined = "\n".join(x for x in log if x.startswith("[net]"))
    assert nf.GROQ_KEY not in joined and nf.OPENAI_KEY not in joined
    assert "monday" not in joined and "build" not in joined
