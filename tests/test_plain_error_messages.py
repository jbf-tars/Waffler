"""Errors returned to the UI are plain sentences, not exception text.

Before: checking a Groq key offline showed "Connection error:
HTTPSConnectionPool(host='api.groq.com', port=443): Max retries exceeded...",
a VPN block said the key "may be expired or revoked", the update check showed
"GitHub API returned HTTP 403", a failed download showed curl output, and a
release with no installer for this computer said "Refusing to download from an
untrusted URL."

The key-check sentences are the setup copy from the onboarding review. app.py's
bridge methods are lifted out of the source and run with fake SDKs and a fake
requests module: no network, no keys.
"""
import ast
import os
import sys
import types
from pathlib import Path

import httpx
import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import groq  # noqa: E402
import openai  # noqa: E402

import user_messages as um  # noqa: E402

_REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/models")


def _status(cls, code, body_text="Error"):
    return cls(f"Error code: {code} - {body_text}", response=httpx.Response(code, request=_REQ),
               body=None)


OFFLINE = [
    groq.APIConnectionError(request=_REQ),
    groq.APITimeoutError(request=_REQ),
    openai.APIConnectionError(request=_REQ),
    requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='api.groq.com', port=443): Max retries exceeded with url: "
        "/openai/v1/models (Caused by NameResolutionError(\"getaddrinfo failed\"))"),
    requests.exceptions.Timeout("Read timed out."),
    RuntimeError("Connection error: HTTPSConnectionPool(host='api.groq.com', port=443)"),
]

GROQ_COPY = {
    "offline": "Couldn't reach Groq. Check you're online. If you use a VPN, turn it off and try again.",
    "401": "Groq didn't accept that key. Make a new one and click Copy again.",
    "403": "Groq blocked this connection. This usually means a VPN is on. Turn it off and try again.",
    "429": "Groq is busy for a moment. Try again in a few seconds.",
}


# ── the classifier and the copy ──────────────────────────────────────────────

@pytest.mark.parametrize("exc", OFFLINE)
def test_offline_errors_read_as_offline(exc):
    assert um.classify_request_error(exc) == "offline"
    assert um.key_check_error("Groq", exc) == GROQ_COPY["offline"]


@pytest.mark.parametrize("exc, key", [
    (_status(groq.AuthenticationError, 401, "Invalid API Key"), "401"),
    (_status(groq.PermissionDeniedError, 403, "Forbidden"), "403"),
    (_status(groq.RateLimitError, 429, "rate_limit_exceeded"), "429"),
])
def test_groq_key_check_uses_the_setup_copy(exc, key):
    assert um.key_check_error("Groq", exc) == GROQ_COPY[key]


def test_openai_with_no_credit_says_so():
    exc = _status(openai.RateLimitError, 429,
                  "{'error': {'code': 'insufficient_quota', 'message': 'You exceeded...'}}")
    assert um.classify_request_error(exc) == "no_credit"
    assert "no credit" in um.key_check_error("OpenAI", exc)


def test_an_unknown_error_is_still_a_sentence():
    msg = um.key_check_error("OpenAI", ValueError("weird internal thing"))
    assert msg == "Couldn't check that key with OpenAI. Try again in a moment."


_JARGON = ("HTTPSConnectionPool", "HTTP 403", "fallback", "provider key", "untrusted",
           "Refusing", "Traceback", "Error code", chr(0x2014))


def _all_messages():
    msgs = [m.format(p="Groq") for m in um._KEY_MESSAGES.values()]
    msgs += [um.UPDATE_CHECK_FAILED, um.UPDATE_CHECK_OFFLINE, um.UPDATE_NO_INSTALLER,
             um.UPDATE_DOWNLOAD_FAILED, um.UPDATE_INSTALL_FAILED]
    return msgs


@pytest.mark.parametrize("msg", _all_messages())
def test_no_message_carries_jargon_or_an_em_dash(msg):
    for word in _JARGON:
        assert word not in msg, (word, msg)
    assert msg.endswith(".")


# ── app.py's bridge methods ──────────────────────────────────────────────────

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))


def _api_method(name):
    klass = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    fn = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name)
    logged = []
    ns = {
        "os": os, "_log_to_file": logged.append,
        "key_check_error": um.key_check_error,
        "classify_request_error": um.classify_request_error,
        "update_check_error": um.update_check_error,
        "UPDATE_NO_INSTALLER": um.UPDATE_NO_INSTALLER,
        "UPDATE_DOWNLOAD_FAILED": um.UPDATE_DOWNLOAD_FAILED,
        "UPDATE_INSTALL_FAILED": um.UPDATE_INSTALL_FAILED,
        "DOWNLOAD_PAGE": um.DOWNLOAD_PAGE,
    }
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    return ns[name], logged


def _fake_groq(exc):
    class _Models:
        def list(self):
            raise exc

    class Groq:
        def __init__(self, **kw):
            self.models = _Models()

    return types.SimpleNamespace(Groq=Groq)


@pytest.mark.parametrize("exc, key", [
    (OFFLINE[0], "offline"),
    (OFFLINE[3], "offline"),
    (_status(groq.AuthenticationError, 401, "Invalid API Key"), "401"),
    (_status(groq.PermissionDeniedError, 403), "403"),
    (_status(groq.RateLimitError, 429), "429"),
])
def test_validate_groq_key_returns_the_setup_copy(monkeypatch, exc, key):
    fn, logged = _api_method("validate_groq_key")
    monkeypatch.setitem(sys.modules, "groq", _fake_groq(exc))
    api = types.SimpleNamespace(_update_env_var=lambda k, v: pytest.fail("saved a bad key"))
    result = fn(api, "gsk_" + "x" * 20)
    assert result == {"ok": False, "error": GROQ_COPY[key]}
    assert logged and "[keys] Groq key check failed" in logged[0]


def _fake_openai(exc):
    class _Models:
        def list(self):
            raise exc

    class OpenAI:
        def __init__(self, **kw):
            self.models = _Models()

    return types.SimpleNamespace(OpenAI=OpenAI)


def test_validate_openai_key_offline(monkeypatch):
    fn, _ = _api_method("validate_api_key")
    monkeypatch.setitem(sys.modules, "openai", _fake_openai(OFFLINE[2]))
    api = types.SimpleNamespace(_update_env_var=lambda k, v: pytest.fail("saved"))
    result = fn(api, "sk-" + "x" * 20)
    assert result["error"] == ("Couldn't reach OpenAI. Check you're online. If you use a VPN, "
                               "turn it off and try again.")


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _fake_requests(response=None, raises=None):
    def get(*a, **k):
        if raises:
            raise raises
        return response
    return types.SimpleNamespace(get=get)


def test_update_check_rate_limited_by_github(monkeypatch):
    fn, logged = _api_method("check_for_updates")
    monkeypatch.setitem(sys.modules, "requests", _fake_requests(_Resp(403)))
    r = fn(object())
    assert r["error"] == "Couldn't check for updates. Try again later."
    assert r["update_available"] is False
    assert any("HTTP 403" in m for m in logged)  # detail kept for diagnosis


def test_update_check_offline(monkeypatch):
    fn, _ = _api_method("check_for_updates")
    monkeypatch.setitem(sys.modules, "requests", _fake_requests(
        raises=requests.exceptions.ConnectionError("HTTPSConnectionPool(host='api.github.com')")))
    r = fn(object())
    assert r["error"] == "Couldn't check for updates. Check you're online and try again."


def test_a_release_without_an_installer_is_flagged(monkeypatch):
    fn, _ = _api_method("check_for_updates")
    release = {"tag_name": "v999.0.0", "draft": False, "prerelease": False,
               "html_url": "https://github.com/jbf-tars/waffler/releases/tag/v999.0.0",
               "assets": [{"name": "notes.txt", "browser_download_url": "https://x"}]}
    monkeypatch.setitem(sys.modules, "requests", _fake_requests(_Resp(200, [release])))
    r = fn(object())
    assert r["update_available"] is True
    assert r["download_url"] == ""
    assert r["no_installer"] is True
    assert r["no_installer_message"] == um.UPDATE_NO_INSTALLER
    assert r["release_url"].endswith("/v999.0.0")


def test_downloading_with_no_installer_url_is_not_called_untrusted():
    fn, _ = _api_method("start_update_download")
    r = fn(object(), "")
    assert r["ok"] is False
    assert r["error"] == um.UPDATE_NO_INSTALLER
    assert r["download_page"] == um.DOWNLOAD_PAGE


def test_an_untrusted_url_is_still_refused_in_plain_words():
    fn, logged = _api_method("start_update_download")
    r = fn(object(), "https://evil.example.com/Waffler-Setup.exe")
    assert r["ok"] is False and r["error"] == um.UPDATE_DOWNLOAD_FAILED
    assert any("refused untrusted" in m for m in logged)


# ── the download worker ──────────────────────────────────────────────────────

def test_a_failed_download_reports_one_sentence(monkeypatch):
    import updater
    updater._reset_state()

    def boom(url, partial, dest):
        raise IOError("curl exited 22: curl: (22) The requested URL returned error: 404")

    monkeypatch.setattr(updater, "_download_with_curl", boom)
    monkeypatch.setattr(updater, "_download_with_requests", boom)
    monkeypatch.setattr(updater, "_expected_digest_for_url", lambda url: None)
    updater._download_worker("https://github.com/jbf-tars/waffler/releases/download/v1/W.exe")
    state = updater.get_progress()
    assert state["error"] == um.UPDATE_DOWNLOAD_FAILED
    assert "curl exited 22" in state["error_detail"]
    assert state["download_page"] == um.DOWNLOAD_PAGE
    assert state["active"] is False
    updater._reset_state()
    assert updater.get_progress()["error_detail"] is None


# ── The pill's messages after a dictation (QW8, review of Round A) ───────────
# After a limit or a clean-up that failed, the pill said "Groq limit hit ·
# resets in about 16 minutes / Pasted raw", "Add a Cerebras key for
# fallback", "Auth blocked" and "Try another provider key in Settings", and a
# clean-up that ran out of its own time was called "Connection failed".

# The styler raises its reasons with a dash, so the cases do too.
_DASH = chr(0x2014)
_WORDS_WENT_IN = ("pasted as you said them", "went in as you said them")
_PILL_JARGON = _JARGON + ("Pasted raw", "raw text", "styling", "Rate limit", "Auth ",
                          "API Keys", chr(0xb7), "provider")

# (the styler's fallback_reason, the heading, words the body must hold)
_CLEANUP_CASES = [
    ("RATE_LIMIT|tokens per day (TPD)|16m12.5s|Groq: Rate limit reached for model",
     "Clean-up paused for about 17 minutes", "Groq says you've reached your limit"),
    ("RATE_LIMIT|cooldown|45s|Groq: Groq still in cooldown from previous limit",
     "Clean-up paused for about 45 seconds", "Groq says"),
    ("RATE_LIMIT|Cerebras|30s|Cerebras: too many requests",
     "Clean-up paused for about 30 seconds", "Cerebras says"),
    ("RATE_LIMIT|requests per day (RPD)||Groq: limit reached",
     "Clean-up paused until tomorrow", "Groq says"),
    ("RATE_LIMIT|rate limit|2h3m|OpenAI: limit reached",
     "Clean-up paused for about 2 hours", "OpenAI says"),
    ("deadline: TIMEOUT|styling budget exhausted after 30s - pasted raw",
     "Pasted without the clean-up", "took too long"),
    ("TIMEOUT|clean-up took longer than 40s - pasted raw",
     "Pasted without the clean-up", "took too long"),
    (f"Groq: CONNECTION: Groq connection failed {_DASH} Connection error.",
     "Pasted without the clean-up", "couldn't reach Groq"),
    ("OpenAI: Request timed out.", "Pasted without the clean-up", "couldn't reach OpenAI"),
    (f"Groq: AUTH: Groq auth/network blocked {_DASH} Error code: 403 - Access denied",
     "Pasted without the clean-up", "Groq refused the connection"),
    ("OpenAI: Error code: 401 - {'error': {'code': 'invalid_api_key'}}",
     "Pasted without the clean-up", "OpenAI refused the connection"),
    ("No styling providers configured. Add a key in Settings, API Keys.",
     "Pasted without the clean-up", "Add a free Groq key in Settings"),
    ("ValueError: something unexpected", "Pasted without the clean-up", "didn't work this time"),
]


@pytest.mark.parametrize("reason,heading,says", _CLEANUP_CASES)
def test_a_skipped_clean_up_is_said_plainly(reason, heading, says):
    got_heading, body = um.cleanup_skipped_message(reason)
    assert got_heading == heading
    assert says in body and body.endswith(".")
    if "No styling providers" not in reason:
        assert any(w in body for w in _WORDS_WENT_IN), body
    for word in _PILL_JARGON:
        assert word not in got_heading and word not in body, (word, got_heading, body)
    # One line on the pill's message: its heading is not wrapped.
    assert len(got_heading) <= 38


def test_a_groq_limit_no_longer_sends_people_to_cerebras():
    _h, body = um.cleanup_skipped_message(
        "RATE_LIMIT|tokens per day (TPD)|16m12s|Groq: Rate limit reached")
    assert "Cerebras" not in body and "key" not in body


def test_the_real_styler_reasons_read_as_what_happened():
    """Through the styler's own chooser: its time budget running out used to
    be reported as "Connection failed"."""
    from style_openai import OpenAIStyler
    pick = OpenAIStyler._pick_best_failure_reason
    budget = pick([("deadline", "TIMEOUT|styling budget exhausted after 30s - pasted raw")])
    assert um.cleanup_skipped_message(budget)[1].startswith("The clean-up took too long")
    limit = pick([("Cerebras", "CONNECTION: Cerebras connection failed"),
                  ("Groq", "RATE_LIMIT|tokens per day (TPD)|16m12s|Rate limit reached")])
    assert um.cleanup_skipped_message(limit)[0] == "Clean-up paused for about 17 minutes"
    blocked = pick([("Groq", "AUTH: Groq auth/network blocked - Error code: 403")])
    assert "Groq refused the connection" in um.cleanup_skipped_message(blocked)[1]
    assert um.cleanup_skipped_message(pick([]))[1].startswith("No key is set up")


@pytest.mark.parametrize("error,body", [
    ("RATE_LIMIT|tokens per day (TPD)|16m12s|Groq: Rate limit reached",
     "Groq says you've reached your limit for now. Try again in about 17 minutes."),
    ("RATE_LIMIT|requests per day (RPD)||Groq: limit",
     "Groq says you've reached your limit for now. Try again tomorrow."),
    ("Error code: 429 - Too Many Requests",
     "Your speech service says you've reached your limit for now. Wait a moment and try again."),
])
def test_a_limit_that_stopped_a_dictation_is_said_plainly(error, body):
    heading, got = um.limit_reached_message(error)
    assert heading == "Limit reached" and got == body
    for word in _PILL_JARGON:
        assert word not in got, word


def test_no_pill_message_in_the_app_carries_jargon_or_an_em_dash():
    """Every overlay message app.py writes out in full."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "show_toast"]
    assert len(calls) > 10
    texts = [c.value for call in calls for c in ast.walk(call)
             if isinstance(c, ast.Constant) and isinstance(c.value, str)]
    for text in texts:
        for word in ("provider key", "instant fallback", "fallback key", "Pasted raw",
                     "styling provider", "Rate limit reached", chr(0x2014)):
            assert word not in text, (word, text)
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "cleanup_skipped_message(reason)" in src
    assert "limit_reached_message(error_msg)" in src
