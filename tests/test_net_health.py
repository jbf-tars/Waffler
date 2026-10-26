"""The building blocks of VPN-proof failover (src/net_health.py).

* the timeouts a request is given, short to connect only when another provider
  can take it;
* the pause that doubles while a provider keeps failing, and starts again
  when it answers;
* what kind of failure an SDK error is, read through the chain of causes;
* the `[net]` line and the notice's candidates.

No network: the errors are the SDKs' own classes, raised the way they raise
them (an APITimeoutError FROM an httpx.ConnectTimeout).
"""
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import groq  # noqa: E402
import openai  # noqa: E402

import net_health as nh  # noqa: E402

_REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/audio/transcriptions")


def _status(cls, code):
    return cls(f"Error code: {code}", response=httpx.Response(code, request=_REQ), body=None)


def _from(sdk_error, cause):
    """The SDK's way: ``raise APITimeoutError(...) from httpx_error``."""
    sdk_error.__cause__ = cause
    return sdk_error


# ── timeouts ─────────────────────────────────────────────────────────────────

def test_with_another_provider_connecting_is_given_seconds_not_a_minute():
    t = nh.request_timeout(60.0, fallback=True)
    assert isinstance(t, httpx.Timeout)
    assert t.connect == nh.CONNECT_TIMEOUT_FAST_S == 6.0
    assert t.write == nh.WRITE_STALL_FAST_S == 20.0
    assert t.read == pytest.approx(25.0)
    assert t.pool == 60.0


def test_a_stalled_answer_is_given_up_on_sooner_only_with_another_provider():
    small = nh.request_timeout(60.0, fallback=True, size_mb=0.0)
    big = nh.request_timeout(144.0, fallback=True, size_mb=18.0)
    assert small.read == 25.0
    assert big.read == pytest.approx(25.0 + 4.0 * 18.0)
    assert big.read < 144.0, "still shorter than the whole allowance"
    assert nh.request_timeout(60.0, fallback=True, size_mb=500.0).read == 60.0, "never longer"


def test_the_sole_provider_is_waited_on():
    t = nh.request_timeout(60.0, fallback=False, size_mb=3.0)
    assert t.connect == nh.CONNECT_TIMEOUT_SOLE_S == 20.0
    assert t.read == 60.0 and t.write == 60.0 and t.pool == 60.0
    big = nh.request_timeout(144.0, fallback=False, size_mb=18.0)
    assert (big.connect, big.read, big.write) == (20.0, 144.0, 144.0)


def test_the_sole_provider_gets_a_longer_connect_than_one_that_has_a_fallback():
    assert nh.CONNECT_TIMEOUT_SOLE_S > nh.CONNECT_TIMEOUT_FAST_S


def test_a_request_that_never_needed_two_numbers_still_sends_one():
    """The clean-up's 15 s: with no fallback it is a plain number (the connect
    could not be longer than the whole wait anyway)."""
    assert nh.request_timeout(15.0, fallback=False) == 15.0
    assert type(nh.request_timeout(15.0, fallback=False)) is float
    assert type(nh.request_timeout(4.0, fallback=True)) is float, "shorter than the connect allowance"
    fast = nh.request_timeout(15.0, fallback=True)
    assert fast.connect == 6.0 and fast.read == 15.0 and fast.write == 15.0


def test_the_timeout_still_reads_as_the_number_of_seconds_it_waits():
    """Code and tests written when a timeout was a float keep working."""
    t = nh.request_timeout(15.0, fallback=True)
    assert 0.1 <= t <= 15.0 and t > 14.0 and t >= 15.0 and t <= 15.0 and not (t < 15.0)
    assert t == 15.0 and not (t != 15.0)
    assert float(t) == 15.0
    assert t != nh.request_timeout(10.0, fallback=True)
    assert (t == "15") is False
    assert 14.0 < t and 15.0 >= t and 16.0 > t
    with pytest.raises(TypeError):
        t < "15"


def test_it_equals_a_plain_timeout_with_the_same_parts():
    fast = nh.request_timeout(60.0, fallback=True, size_mb=1.0)
    same = httpx.Timeout(60.0, connect=6.0, read=fast.read, write=20.0)
    assert fast == same and same == fast and not (fast != same)
    assert fast != httpx.Timeout(60.0)
    assert (fast == object()) is False


def test_httpx_takes_the_timeout_and_keeps_every_part():
    t = nh.request_timeout(60.0, fallback=True, size_mb=1.0)
    again = httpx.Timeout(t)
    assert (again.connect, again.read, again.write, again.pool) == (t.connect, t.read, t.write, t.pool)
    request = httpx.Client().build_request("POST", "https://example.invalid/x", timeout=t)
    assert request.extensions["timeout"] == {"connect": 6.0, "read": t.read, "write": 20.0, "pool": 60.0}


# ── pauses that grow ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("base,expected", [
    (30.0, [30, 60, 120, 240, 300, 300, 300]),
    (60.0, [60, 120, 240, 300, 300, 300]),
])
def test_the_pause_doubles_to_five_minutes(base, expected):
    assert [nh.escalated(base, n) for n in range(1, len(expected) + 1)] == expected


def test_the_pause_has_a_floor_and_a_ceiling_whatever_the_count():
    assert nh.escalated(30.0, 0) == 30.0 and nh.escalated(30.0, -4) == 30.0
    assert nh.escalated(30.0, 10 ** 6) == nh.COOLDOWN_CAP_S == 300.0


def test_the_first_pauses_are_the_ones_the_app_always_had():
    assert nh.ERROR_COOLDOWN_BASE_S == 30.0 and nh.AUTH_COOLDOWN_BASE_S == 60.0


def test_a_streak_counts_failures_and_an_answer_ends_it():
    s = nh.FailureStreak()
    assert [s.failed(100.0 + i) for i in range(4)] == [1, 2, 3, 4]
    assert s.succeeded() == 4
    assert s.succeeded() == 0
    assert s.failed(200.0) == 1


def test_a_failure_long_after_the_last_starts_a_new_streak():
    """A new network, a new VPN server: this is not the fifth failure."""
    s = nh.FailureStreak()
    for i in range(5):
        s.failed(1000.0 + i)
    assert s.failed(1004.0 + nh.STREAK_FORGET_S) == 6, "exactly at the limit still counts"
    assert s.failed(1004.0 + nh.STREAK_FORGET_S + nh.STREAK_FORGET_S + 1) == 1


# ── what kind of failure ─────────────────────────────────────────────────────

CASES = [
    # (the error, kind, the class named in the log)
    (_status(groq.PermissionDeniedError, 403), nh.BLOCKED, "PermissionDeniedError"),
    (_status(openai.PermissionDeniedError, 403), nh.BLOCKED, "PermissionDeniedError"),
    (_status(groq.AuthenticationError, 401), nh.KEY, "AuthenticationError"),
    (_status(groq.RateLimitError, 429), nh.RATE_LIMIT, "RateLimitError"),
    (_status(groq.InternalServerError, 503), nh.SERVER, "InternalServerError"),
    (_status(groq.BadRequestError, 400), nh.OTHER, "BadRequestError"),
    (_from(groq.APITimeoutError(request=_REQ), httpx.ConnectTimeout("t", request=_REQ)),
     nh.UNREACHABLE, "ConnectTimeout"),
    (_from(groq.APITimeoutError(request=_REQ), httpx.WriteTimeout("t", request=_REQ)),
     nh.UNREACHABLE, "WriteTimeout"),
    (_from(groq.APITimeoutError(request=_REQ), httpx.ReadTimeout("t", request=_REQ)),
     nh.SLOW, "ReadTimeout"),
    (_from(groq.APIConnectionError(request=_REQ), httpx.ConnectError("reset", request=_REQ)),
     nh.UNREACHABLE, "ConnectError"),
    (_from(openai.APIConnectionError(request=_REQ), httpx.ReadError("reset", request=_REQ)),
     nh.UNREACHABLE, "ReadError"),
    (_from(openai.APIConnectionError(request=_REQ), httpx.RemoteProtocolError("closed", request=_REQ)),
     nh.UNREACHABLE, "RemoteProtocolError"),
    (groq.APITimeoutError(request=_REQ), nh.SLOW, "APITimeoutError"),
    (groq.APIConnectionError(request=_REQ), nh.UNREACHABLE, "APIConnectionError"),
    (ConnectionResetError("reset by peer"), nh.UNREACHABLE, "ConnectionResetError"),
    (TimeoutError("read timed out"), nh.SLOW, "TimeoutError"),
    (ValueError("bad audio"), nh.OTHER, "ValueError"),
]


@pytest.mark.parametrize("error,kind,named", CASES, ids=[c[2] + "/" + c[1] for c in CASES])
def test_classify(error, kind, named):
    f = nh.classify(error)
    assert (f.kind, f.error) == (kind, named)


def test_the_status_is_carried_for_the_log():
    assert nh.classify(_status(groq.PermissionDeniedError, 403)).status == 403
    assert nh.classify(_status(groq.InternalServerError, 502)).status == 502
    assert nh.classify(ValueError("x")).status is None


def test_the_styler_wraps_what_the_sdk_raised_and_the_cause_is_still_found():
    """`raise RuntimeError("CONNECTION: ...")` inside the except block keeps
    the SDK's error as its `__context__`."""
    try:
        try:
            raise _from(groq.APITimeoutError(request=_REQ), httpx.ConnectTimeout("t", request=_REQ))
        except Exception:
            raise RuntimeError("CONNECTION: Groq connection failed - Request timed out.")
    except RuntimeError as wrapped:
        f = nh.classify(wrapped)
    assert (f.kind, f.error) == (nh.UNREACHABLE, "ConnectTimeout")


def test_the_styler_s_own_words_are_read_when_there_is_no_cause():
    assert nh.classify(RuntimeError("CONNECTION: Groq connection failed")).kind == nh.UNREACHABLE
    assert nh.classify(RuntimeError("AUTH: Groq auth/network blocked - Error code: 403")).kind == nh.BLOCKED
    assert nh.classify(RuntimeError("Error code: 401 - invalid_api_key")).kind == nh.KEY
    assert nh.classify(RuntimeError("Request timeout while waiting")).kind == nh.SLOW
    assert nh.classify(RuntimeError("Access denied by upstream")).kind == nh.BLOCKED


def test_only_a_block_or_no_connection_is_the_networks_doing():
    assert set(nh.NETWORK_KINDS) == {nh.BLOCKED, nh.UNREACHABLE}


def test_classify_never_raises_and_survives_a_cycle():
    class Odd(Exception):
        def __str__(self):
            raise RuntimeError("no string for you")
    a, b = Exception("a"), Exception("b")
    a.__cause__, b.__cause__ = b, a
    assert nh.classify(a).kind == nh.OTHER
    assert nh.classify(Odd()).kind == nh.OTHER


# ── the [net] line ───────────────────────────────────────────────────────────

def test_the_net_line():
    f = nh.Failure(nh.UNREACHABLE, "ConnectTimeout")
    assert nh.failover_line("speech", "groq", f, 6.04, 30.0, 1, "openai", "full tunnel via utun4") == (
        "[net] speech groq failed: ConnectTimeout (unreachable) after 6.0s; "
        "paused 30s (failure 1 in a row); next: openai; vpn: full tunnel via utun4")


def test_the_net_line_for_a_block_names_the_status_and_the_longer_pause():
    f = nh.Failure(nh.BLOCKED, "PermissionDeniedError", 403)
    line = nh.failover_line("cleanup", "groq", f, 0.2, 3600.0, 1, None, "off")
    assert line == ("[net] cleanup groq failed: PermissionDeniedError (blocked, HTTP 403) after "
                    "0.2s; paused 3600s (failure 1 in a row); next: none; vpn: off")


def test_a_pause_the_provider_asked_for_has_no_run_count():
    f = nh.Failure(nh.RATE_LIMIT, "RateLimitError", 429)
    assert nh.failover_line("cleanup", "groq", f, 0.2, 972.0, 0, "openai", "off") == (
        "[net] cleanup groq failed: RateLimitError (rate_limit, HTTP 429) after 0.2s; "
        "paused 972s; next: openai; vpn: off")


def test_the_net_line_when_nothing_was_paused():
    f = nh.Failure(nh.SERVER, "InternalServerError", 503)
    line = nh.failover_line("speech", "groq", f, 0.3, None, 0, "openai", "off",
                            note="not paused (the only speech provider)")
    assert "not paused (the only speech provider)" in line
    assert nh.failover_line("speech", "groq", f, 0.3, None, 0, "openai", "off").count("not paused") == 1


# ── the notice's candidates ──────────────────────────────────────────────────

def _ev(provider, used, kind="unreachable", stage="speech"):
    return nh.failover_dict(provider, used, kind, stage)


def test_one_candidate_per_provider_speech_first():
    got = nh.notice_candidates([_ev("groq", "openai", stage="cleanup"),
                                _ev("cerebras", "groq", stage="cleanup"),
                                _ev("groq", "openai", "blocked", stage="speech")])
    assert [(e["provider"], e["stage"]) for e in got] == [("groq", "speech"), ("cerebras", "cleanup")]


@pytest.mark.parametrize("event", [
    _ev("groq", "openai", "slow"), _ev("groq", "openai", "server"), _ev("groq", "openai", "rate_limit"),
    _ev("groq", "openai", "key"), _ev("groq", "openai", "other"),
    _ev("groq", "groq"), _ev("nobody", "openai"), _ev("groq", ""), "not a dict", None,
])
def test_what_is_not_the_networks_doing_has_no_notice(event):
    assert nh.notice_candidates([event]) == []


def test_every_provider_has_a_notifier_kind_of_its_own():
    assert nh.FAILOVER_KINDS == {"groq": "failover_groq", "openai": "failover_openai",
                                 "cerebras": "failover_cerebras"}


def test_an_event_is_plain_data_without_text():
    ev = _ev("groq", "openai")
    assert set(ev) == {"provider", "used", "kind", "stage"}
    import json
    json.dumps(ev)
