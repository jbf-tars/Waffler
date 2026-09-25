"""The recommended free setup (a Groq key alone) must survive a network blip.

Both SDK clients run with max_retries=0. Before this, one transient Groq error
put Groq on a 30 s cooldown (an hour for anything that looked like an auth
error), and with no OpenAI key to fall back on, every dictation in that window
failed with "no transcription backend available". Real use logged 7 Groq
connection errors and 5 lost dictations.

Now the last provider left gets up to two retries on a timeout, a 5xx or a
connection error, with jittered backoff inside a time budget; Groq is never
paused when it is the only speech provider; and the auth cooldown is 60 s,
not an hour. No network: the provider calls are fakes raising the SDKs' own
exception classes.
"""
import os
import sys
import time

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import groq  # noqa: E402
import openai  # noqa: E402

import transcribe_whisper as tw  # noqa: E402
from transcribe_whisper import WhisperTranscriber, _classify_asr_error  # noqa: E402

_REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/audio/transcriptions")


def _status(cls, code):
    return cls(f"Error code: {code}", response=httpx.Response(code, request=_REQ), body=None)


def _server_error():
    return _status(groq.InternalServerError, 500)


def _connection_error():
    return groq.APIConnectionError(request=_REQ)


def _timeout():
    return groq.APITimeoutError(request=_REQ)


def _transcriber(*, openai_key: bool):
    t = object.__new__(WhisperTranscriber)
    t._backend = "groq"
    t._cloud_order = ["groq", "openai"]
    t._groq_skip_until = 0.0
    t._groq_client = object()
    t.client = object() if openai_key else None
    return t


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(tw, "_retry_sleep", waits.append)
    return waits


def _script(monkeypatch, name, outcomes, calls):
    """Make WhisperTranscriber.<name> raise/return each outcome in turn."""
    seq = list(outcomes)

    def fake(self, audio_bytes):
        calls.append(name)
        item = seq.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(WhisperTranscriber, name, fake)


AUDIO = b"\x00" * 1024


# ── the verify list from the plan ────────────────────────────────────────────

def test_one_500_then_success_returns_the_text(monkeypatch, no_real_sleep):
    t = _transcriber(openai_key=False)
    calls = []
    _script(monkeypatch, "_transcribe_groq", [_server_error(), "hello there"], calls)
    assert t._dispatch_one(AUDIO) == "hello there"
    assert calls == ["_transcribe_groq"] * 2
    assert len(no_real_sleep) == 1


def test_a_connection_error_is_retried(monkeypatch):
    t = _transcriber(openai_key=False)
    calls = []
    _script(monkeypatch, "_transcribe_groq", [_connection_error(), "words"], calls)
    assert t._dispatch_one(AUDIO) == "words"
    assert len(calls) == 2


def test_a_timeout_inside_the_budget_is_retried(monkeypatch):
    t = _transcriber(openai_key=False)
    calls = []
    _script(monkeypatch, "_transcribe_groq", [_timeout(), "words"], calls)
    assert t._dispatch_one(AUDIO) == "words"


def test_groq_only_is_never_skipped_even_inside_a_cooldown(monkeypatch):
    t = _transcriber(openai_key=False)
    t._groq_skip_until = time.monotonic() + 3600
    calls = []
    _script(monkeypatch, "_transcribe_groq", ["still works"], calls)
    assert t._dispatch_one(AUDIO) == "still works"
    assert calls == ["_transcribe_groq"]


def test_groq_only_failure_sets_no_cooldown(monkeypatch):
    """The dictation after a failure must try Groq again, not fail unseen."""
    t = _transcriber(openai_key=False)
    calls = []
    _script(monkeypatch, "_transcribe_groq",
            [_status(groq.PermissionDeniedError, 403), "next one works"], calls)
    with pytest.raises(groq.PermissionDeniedError):
        t._dispatch_one(AUDIO)
    assert t._groq_skip_until == 0.0
    assert t._dispatch_one(AUDIO) == "next one works"


def test_groq_only_takes_an_oversized_clip_rather_than_failing(monkeypatch):
    t = _transcriber(openai_key=False)
    calls = []
    _script(monkeypatch, "_transcribe_groq", ["long dictation"], calls)
    big = b"\x00" * (tw._GROQ_MAX_UPLOAD_BYTES + 1)
    assert t._dispatch_one(big) == "long dictation"


def test_no_transcription_backend_error_is_gone_for_groq_only(monkeypatch):
    """The exact message users saw during a cooldown."""
    t = _transcriber(openai_key=False)
    t._groq_skip_until = time.monotonic() + 30
    _script(monkeypatch, "_transcribe_groq", ["ok"], [])
    assert t._dispatch_one(AUDIO) == "ok"


# ── limits: retries are bounded and only for transient errors ────────────────

def test_at_most_two_retries(monkeypatch, no_real_sleep):
    t = _transcriber(openai_key=False)
    calls = []
    _script(monkeypatch, "_transcribe_groq",
            [_connection_error(), _connection_error(), _connection_error(), "never"], calls)
    with pytest.raises(groq.APIConnectionError):
        t._dispatch_one(AUDIO)
    assert len(calls) == 3
    assert len(no_real_sleep) == 2
    # Exponential with jitter: the second wait's range starts above the first's.
    assert 0 < no_real_sleep[0] <= tw._TRANSIENT_BACKOFF_S * 1.5
    assert tw._TRANSIENT_BACKOFF_S * 2 * 0.5 <= no_real_sleep[1] <= tw._TRANSIENT_BACKOFF_S * 2 * 1.5


@pytest.mark.parametrize("err", [
    _status(groq.BadRequestError, 400),
    _status(groq.RateLimitError, 429),
    _status(groq.AuthenticationError, 401),
    _status(groq.PermissionDeniedError, 403),
])
def test_non_transient_errors_are_not_retried(monkeypatch, err):
    t = _transcriber(openai_key=False)
    calls = []
    _script(monkeypatch, "_transcribe_groq", [err, "never"], calls)
    with pytest.raises(type(err)):
        t._dispatch_one(AUDIO)
    assert len(calls) == 1


def test_no_retry_once_the_time_budget_is_spent(monkeypatch, no_real_sleep):
    """A request that already ran into a long timeout is not repeated."""
    t = _transcriber(openai_key=False)
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(tw.time, "monotonic", lambda: clock[0])

    def slow_timeout(self, audio_bytes):
        calls.append(1)
        clock[0] += 60.0
        raise _timeout()

    monkeypatch.setattr(WhisperTranscriber, "_transcribe_groq", slow_timeout)
    with pytest.raises(groq.APITimeoutError):
        t._dispatch_one(AUDIO)
    assert len(calls) == 1
    assert no_real_sleep == []


# ── with a second provider, fall over first, and keep the circuit-breaker ────

def test_with_openai_a_groq_blip_falls_over_without_waiting(monkeypatch, no_real_sleep):
    t = _transcriber(openai_key=True)
    calls = []
    _script(monkeypatch, "_transcribe_groq", [_connection_error()], calls)
    _script(monkeypatch, "_transcribe_api", ["from openai"], calls)
    assert t._dispatch_one(AUDIO) == "from openai"
    assert calls == ["_transcribe_groq", "_transcribe_api"]
    assert no_real_sleep == []


def test_with_openai_the_last_provider_is_retried(monkeypatch):
    t = _transcriber(openai_key=True)
    calls = []
    _script(monkeypatch, "_transcribe_groq", [_connection_error()], calls)
    _script(monkeypatch, "_transcribe_api",
            [_status(openai.InternalServerError, 503), "second try"], calls)
    assert t._dispatch_one(AUDIO) == "second try"
    assert calls == ["_transcribe_groq", "_transcribe_api", "_transcribe_api"]


def test_auth_cooldown_is_a_minute_not_an_hour(monkeypatch):
    t = _transcriber(openai_key=True)
    _script(monkeypatch, "_transcribe_groq", [_status(groq.PermissionDeniedError, 403)], [])
    _script(monkeypatch, "_transcribe_api", ["fallback"], [])
    before = time.monotonic()
    assert t._dispatch_one(AUDIO) == "fallback"
    remaining = t._groq_skip_until - before
    assert 0 < remaining <= 61, remaining


def test_with_openai_groq_is_still_skipped_during_its_cooldown(monkeypatch):
    t = _transcriber(openai_key=True)
    t._groq_skip_until = time.monotonic() + 30
    calls = []
    _script(monkeypatch, "_transcribe_groq", ["unused"], calls)
    _script(monkeypatch, "_transcribe_api", ["openai"], calls)
    assert t._dispatch_one(AUDIO) == "openai"
    assert calls == ["_transcribe_api"]


# ── the classifier ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("exc, kind", [
    (_server_error(), "transient"),
    (_status(openai.InternalServerError, 502), "transient"),
    (_connection_error(), "transient"),
    (_timeout(), "transient"),
    (openai.APIConnectionError(request=_REQ), "transient"),
    (TimeoutError("read timed out"), "transient"),
    (ConnectionResetError("reset by peer"), "transient"),
    (_status(groq.PermissionDeniedError, 403), "auth"),
    (_status(groq.AuthenticationError, 401), "auth"),
    (RuntimeError("Access denied by upstream"), "auth"),
    (_status(groq.RateLimitError, 429), "other"),
    (_status(groq.BadRequestError, 400), "other"),
    (ValueError("bad audio"), "other"),
])
def test_classifier(exc, kind):
    assert _classify_asr_error(exc) == kind
