"""The account: sign in with an emailed code, devices, the Claude link, sign
out (src/account.py and the Api methods in app.py that the page calls).

A stand-in for the account service answers (tests/_account_fake.py): nothing
here touches the network. The Api methods are lifted out of app.py and run as
they are, as the other bridge tests do, so what is checked is what the page
gets.
"""
import ast
import json
import os
import sys
import threading
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from src import account                               # noqa: E402
from _account_fake import FakeAccountServer           # noqa: E402

SERVER = "https://notes.example.test"
EMAIL = "ada@example.com"

API_METHODS = ("account_status", "account_start", "account_verify", "account_sign_out",
               "account_devices", "account_remove_device", "account_connector",
               "account_connector_create", "account_connector_rotate", "account_connector_off",
               "account_sync_now")
APP_HELPERS = ("_account_mod", "_account_sync_state", "_account_call", "_account_begin_sync")


@pytest.fixture(autouse=True)
def _nothing_left_over(monkeypatch):
    monkeypatch.setattr(account, "_notice", "")


@pytest.fixture
def server(monkeypatch, _isolated_data_dir):
    fake = FakeAccountServer()
    monkeypatch.setenv(account.SERVER_ENV, SERVER)
    monkeypatch.setattr(account, "_transport", fake.transport())
    monkeypatch.setattr(account, "default_device_name", lambda: "Ada's Mac")
    fake.data = _isolated_data_dir
    return fake


def sign_in(fake, email=EMAIL, name=None):
    assert account.start(email)["ok"]
    out = account.verify(email, fake.mailbox[email], name)
    assert out["ok"], out
    return out


def token_on_disk(fake):
    return json.loads((fake.data / account.SESSION_FILE).read_text(encoding="utf-8"))["token"]


# ── switched off until a service is named ───────────────────────────────────

def test_with_no_service_configured_the_feature_is_off_and_nothing_is_contacted(monkeypatch, _isolated_data_dir):
    def refuse(request):
        raise AssertionError(f"contacted {request.url}")
    monkeypatch.delenv(account.SERVER_ENV, raising=False)
    monkeypatch.setattr(account, "_transport", httpx.MockTransport(refuse))
    assert account.server_url() == ""
    assert account.status() == {"ok": True, "available": False, "signed_in": False,
                                "email": "", "device_name": "", "device_id": "", "notice": ""}
    for out in (account.start(EMAIL), account.verify(EMAIL, "123456"), account.devices(),
                account.remove_device("0123456789abcdef"), account.connector(),
                account.connector_create(), account.connector_rotate(), account.connector_off()):
        assert out == {"ok": False, "error": account.NOT_AVAILABLE, "reason": "unavailable"}
    assert account.sign_out()["ok"] and account.sync_credentials() is None
    assert "coming soon" in account.NOT_AVAILABLE


def test_the_address_comes_from_the_environment_then_the_setting(monkeypatch, _isolated_data_dir):
    monkeypatch.delenv(account.SERVER_ENV, raising=False)
    (_isolated_data_dir / "settings.json").write_text(
        json.dumps({account.SERVER_SETTING: "https://from-settings.example.test/"}), encoding="utf-8")
    assert account.server_url() == "https://from-settings.example.test"
    monkeypatch.setenv(account.SERVER_ENV, "https://from-env.example.test")
    assert account.server_url() == "https://from-env.example.test"


@pytest.mark.parametrize("address, kept", [
    ("https://notes.example.test", True),
    ("http://127.0.0.1:8790", True),              # a service on this computer, for development
    ("http://localhost:8790/", True),
    ("http://notes.example.test", False),         # the session token would cross the network in the clear
    ("ftp://notes.example.test", False),
    ("notes.example.test", False),
    ("https://user:pw@notes.example.test", False),
    ("", False),
])
def test_only_an_encrypted_address_or_this_computer_is_accepted(monkeypatch, address, kept):
    monkeypatch.setenv(account.SERVER_ENV, address)
    assert bool(account.server_url()) is kept
    assert account.status()["available"] is kept


# ── signing in ──────────────────────────────────────────────────────────────

def test_sign_in_with_the_emailed_code(server):
    assert account.status()["signed_in"] is False
    sent = account.start("  Ada@Example.com ")
    assert sent == {"ok": True, "email": EMAIL, "expires_in": 600}
    code = server.mailbox[EMAIL]
    out = account.verify(EMAIL, f"{code[:3]} {code[3:]}")          # as people type it
    assert out["ok"] and out["signed_in"] and out["email"] == EMAIL and out["device_name"] == "Ada's Mac"
    assert account.status() == {"ok": True, "available": True, "signed_in": True, "email": EMAIL,
                                "device_name": "Ada's Mac", "device_id": out["device_id"], "notice": ""}
    sent_name = json.loads(server.calls("POST", "/auth/email/verify")[0].body)["device_name"]
    assert sent_name == "Ada's Mac"


def test_the_token_is_kept_in_an_owner_only_file_and_nowhere_else(server):
    out = sign_in(server)
    token = token_on_disk(server)
    assert token.startswith("natter_") and token in server.sessions
    path = server.data / account.SESSION_FILE
    if os.name != "nt":
        assert (path.stat().st_mode & 0o777) == 0o600
    assert token not in json.dumps(out)
    for other in server.data.rglob("*"):
        if other.is_file() and other != path:
            assert token not in other.read_text(encoding="utf-8", errors="replace"), other


def test_a_wrong_code_says_so_plainly_and_signs_nobody_in(server):
    account.start(EMAIL)
    wrong = "000000" if server.mailbox[EMAIL] != "000000" else "111111"
    out = account.verify(EMAIL, wrong)
    assert out == {"ok": False, "error": account.WRONG_CODE, "reason": "wrong_code"}
    assert not account.status()["signed_in"] and not (server.data / account.SESSION_FILE).exists()
    assert account.verify(EMAIL, server.mailbox[EMAIL])["ok"]       # the right one still works


def test_a_code_that_is_not_six_digits_is_never_sent(server):
    account.start(EMAIL)
    for bad in ("", "12345", "1234567", "abcdef", None):
        assert account.verify(EMAIL, bad) == {"ok": False, "error": account.BAD_CODE, "reason": "bad_code"}
    assert server.calls("POST", "/auth/email/verify") == []


def test_something_that_is_not_an_email_address_is_never_sent(server):
    for bad in ("", "ada", "ada@", "a b@example.com", None, 7):
        assert account.start(bad) == {"ok": False, "error": account.BAD_EMAIL, "reason": "bad_email"}
    assert server.requests == []


def test_no_internet_is_one_plain_sentence(server):
    server.offline = True
    assert account.start(EMAIL) == {"ok": False, "error": account.NO_INTERNET, "reason": "offline"}
    server.offline = False
    sign_in(server)
    server.offline = True
    for out in (account.devices(), account.connector(), account.connector_create()):
        assert out == {"ok": False, "error": account.NO_INTERNET, "reason": "offline"}
    assert account.status()["signed_in"], "being offline signs nobody out"


def test_too_many_tries_says_how_long_to_wait(server):
    server.too_many = True
    out = account.start(EMAIL)
    assert out["reason"] == "too_many" and out["error"] == "Too many tries. Wait 10 minutes, then try again."
    server.mailbox[EMAIL] = "123456"
    assert account.verify(EMAIL, "123456")["error"] == "Too many tries. Wait a minute, then try again."


def test_a_disabled_account_is_told_so(server):
    account.start(EMAIL)
    server.disabled.add(EMAIL)
    out = account.verify(EMAIL, server.mailbox[EMAIL])
    assert out == {"ok": False, "error": account.DISABLED, "reason": "disabled"}
    server.disabled.clear()
    sign_in(server)
    server.disabled.add(EMAIL)
    assert account.devices() == {"ok": False, "error": account.DISABLED, "reason": "disabled"}
    assert account.status()["signed_in"], "a disabled account stays signed in: enabling it restores everything"


def test_over_the_allowance_is_the_services_own_sentence(server):
    assert account._refusal(402, {"detail": "This month's minutes are used up.", "code": "minutes_cap"},
                            {}, signed=True, what="").sentence == "This month's minutes are used up."
    assert account._refusal(402, {}, {}, signed=True, what="").sentence == account.OVER_ALLOWANCE
    assert account._refusal(402, {}, {}, signed=True, what="").reason == "over_allowance"
    assert account.sync_refusal(402, "") == account.OVER_ALLOWANCE


# ── signed in: devices ──────────────────────────────────────────────────────

def test_devices_are_listed_and_another_one_can_be_removed(server):
    sign_in(server, name="Old laptop")
    old = token_on_disk(server)
    old_session = dict(server.sessions[old])
    (server.data / account.SESSION_FILE).unlink()                    # as if on another computer
    me = sign_in(server, name="Ada's Mac")
    server.sessions[old] = old_session                               # that other computer is still signed in
    listed = account.devices()
    assert listed["ok"] and {d["name"] for d in listed["devices"]} == {"Old laptop", "Ada's Mac"}
    assert [d["name"] for d in listed["devices"] if d["current"]] == ["Ada's Mac"]
    assert all(set(d) == {"id", "name", "created_at", "last_used_at", "signed_in_with", "current"}
               for d in listed["devices"])
    other = next(d for d in listed["devices"] if not d["current"])
    after = account.remove_device(other["id"])
    assert after["ok"] and [d["name"] for d in after["devices"]] == ["Ada's Mac"]
    assert old not in server.sessions
    assert account.remove_device(me["device_id"]) == {
        "ok": False, "error": account.REMOVE_SELF, "reason": "self"}


@pytest.mark.parametrize("bad", ["", "../connector", "0123456789abcdef/../x", "ZZZZ", None, "0123456789ABCDEF0"])
def test_a_device_id_is_only_ever_an_id(server, bad):
    sign_in(server)
    before = len(server.requests)
    assert account.remove_device(bad)["ok"] is False
    assert len(server.requests) == before, "nothing that is not an id reaches an address"


def test_a_computer_removed_from_elsewhere_finds_out_and_forgets_its_token(server):
    sign_in(server)
    server.sessions.clear()                                           # removed from another device
    out = account.devices()
    assert out == {"ok": False, "error": account.SIGNED_OUT, "reason": "signed_out"}
    assert not (server.data / account.SESSION_FILE).exists()
    s = account.status()
    assert s["signed_in"] is False and s["notice"] == account.SIGNED_OUT
    account.start(EMAIL)
    assert account.status()["notice"] == "", "asking for a new code clears the notice"


def test_a_refusal_of_an_old_token_does_not_sign_out_a_newer_session(server):
    """A sync that began before signing out and in again comes back refused:
    it is the old token that is dead, not the one in use now."""
    sign_in(server)
    old = token_on_disk(server)
    sign_in(server)
    assert account.sync_refusal(401, token=old) == account.SIGNED_OUT
    assert account.status()["signed_in"] is True and token_on_disk(server) != old
    assert account.sync_refusal(401, token=token_on_disk(server)) == account.SIGNED_OUT
    assert account.status()["signed_in"] is False


def test_signing_in_again_signs_the_earlier_session_out(server):
    sign_in(server)
    first = token_on_disk(server)
    sign_in(server)
    assert token_on_disk(server) != first and first not in server.sessions
    assert len(server.sessions) == 1


# ── signed in: the Claude link ──────────────────────────────────────────────

def test_the_claude_link_is_made_replaced_and_turned_off(server):
    sign_in(server)
    assert account.connector() == {"ok": True, "exists": False, "created_at": None, "last_used_at": None}
    made = account.connector_create()
    assert made["ok"] and made["exists"] and set(made) == {"ok", "exists", "created_at", "last_used_at", "url"}
    assert made["url"].startswith(SERVER + "/connect/natterc_") and made["url"].endswith("/mcp")
    status = account.connector()
    assert status["exists"] and "url" not in status and "token" not in status
    again = account.connector_rotate()
    assert again["ok"] and again["url"] != made["url"]
    assert account.connector_off() == {"ok": True, "exists": False, "created_at": None, "last_used_at": None}
    assert account.connector()["exists"] is False


def test_a_link_given_only_as_a_path_is_built_on_the_services_address(server, monkeypatch):
    sign_in(server)

    def answer(body):
        monkeypatch.setattr(account, "_transport", httpx.MockTransport(
            lambda request: httpx.Response(200, json=body)))
        return account.connector_create()
    made = answer({"exists": True, "created_at": "x", "last_used_at": None, "token": "natterc_abc",
                   "path": "/connect/natterc_abc/mcp"})
    assert made["url"] == SERVER + "/connect/natterc_abc/mcp" and "token" not in made and "path" not in made
    for bad in ({"exists": True}, {"exists": True, "path": "/connect/../x/mcp"}, {"exists": True, "url": 7}):
        assert answer(bad) == {"ok": False, "error": account.FAILED, "reason": "failed"}


# ── signing out ─────────────────────────────────────────────────────────────

def test_sign_out_forgets_the_token_here_and_on_the_service(server):
    sign_in(server)
    token = token_on_disk(server)
    out = account.sign_out()
    assert out["ok"] and out["signed_in"] is False and out["told_service"] is True and out["note"] == ""
    assert not (server.data / account.SESSION_FILE).exists()
    assert token not in server.sessions
    assert account.sync_credentials() is None
    assert account.devices() == {"ok": False, "error": account.NOT_SIGNED_IN, "reason": "signed_out"}


def test_sign_out_with_no_internet_still_forgets_the_token_and_says_what_is_left(server):
    sign_in(server)
    server.offline = True
    out = account.sign_out()
    assert out["ok"] and out["signed_in"] is False and out["told_service"] is False
    assert out["note"] == account.SIGNED_OUT_OFFLINE
    assert not (server.data / account.SESSION_FILE).exists()


def test_a_token_is_only_ever_shown_to_the_service_that_issued_it(server, monkeypatch):
    sign_in(server)
    token = token_on_disk(server)
    monkeypatch.setenv(account.SERVER_ENV, "https://another.example.test")
    assert account.status()["signed_in"] is False and account.sync_credentials() is None
    assert account.devices()["reason"] == "signed_out"
    assert all(token not in r.auth for r in server.requests[-1:]) and len(server.calls("GET", "/account/devices")) == 0
    monkeypatch.setenv(account.SERVER_ENV, SERVER)
    assert account.status()["signed_in"] is True, "back on its own service, still signed in"


def test_a_damaged_session_file_is_signed_out_not_a_crash(server):
    (server.data / account.SESSION_FILE).write_text("{not json", encoding="utf-8")
    assert account.status()["signed_in"] is False
    (server.data / account.SESSION_FILE).write_text(json.dumps({"server": SERVER, "token": 7}), encoding="utf-8")
    assert account.status()["signed_in"] is False and account.devices()["reason"] == "signed_out"


def test_what_the_service_sends_back_never_raises(server, monkeypatch):
    for reply in (httpx.Response(200, text="not json"), httpx.Response(200, json=[1, 2]),
                  httpx.Response(500, text="<html>"), httpx.Response(418, json={"detail": ["x"]}),
                  httpx.Response(200, json={"token": "", "user": {}, "device": {}}),
                  httpx.Response(200, json={"devices": "many"})):
        monkeypatch.setattr(account, "_transport", httpx.MockTransport(lambda request, r=reply: r))
        for out in (account.start(EMAIL), account.verify(EMAIL, "123456")):
            assert out["ok"] is False or out == {"ok": True, "email": EMAIL, "expires_in": 600}
            if not out["ok"]:
                assert isinstance(out["error"], str) and out["error"].endswith(".")
        assert account.status()["signed_in"] is False


# ── the Api the page calls (app.py), as it is written ───────────────────────

class FakeMeetings:
    """What the Api asks of the meetings controller for sync."""

    def __init__(self):
        self.started = threading.Event()
        self.state = {"syncing": False, "last_synced_at": "", "error": ""}

    def start_sync(self):
        self.started.set()

    def sync_state(self):
        return dict(self.state)

    def sync_now(self):
        self.state["last_synced_at"] = "2026-11-21T10:00:00.000+00:00"
        return {"ok": True, "sync": self.sync_state()}

    def account_signed_out(self):
        self.state = {"syncing": False, "last_synced_at": "", "error": ""}


@pytest.fixture
def api(server):
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in API_METHODS]
    assert [n.name for n in methods] == list(API_METHODS), "app.py Api account methods moved"
    helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in APP_HELPERS]
    assert {n.name for n in helpers} == set(APP_HELPERS)
    logged, meetings = [], FakeMeetings()

    def log(msg):
        logged.append(msg)
        print(msg)                                    # as app.py's _log_to_file does
        with open(server.data / "app.log", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    ns = {"_log_to_file": log, "threading": threading, "time": __import__("time"),
          "_meetings": meetings, "_get_meetings": lambda: meetings}
    exec(compile(ast.Module(helpers + methods, []), "<app.py account>", "exec"), ns)
    Api = type("Api", (), {n.name: ns[n.name] for n in methods})
    api = Api()
    api.logged, api.meetings, api.server, api.ns = logged, meetings, server, ns
    return api


def test_every_api_method_answers_ok_and_never_the_token(api, capsys):
    server = api.server
    answers = [api.account_status(), api.account_start(EMAIL)]
    answers.append(api.account_verify(EMAIL, "not-a-code"))
    answers.append(api.account_verify(EMAIL, server.mailbox[EMAIL]))
    assert answers[-1]["ok"] and answers[-1]["signed_in"] and answers[-1]["sync"] == api.meetings.sync_state()
    assert api.meetings.started.wait(5), "signing in starts syncing"
    token = token_on_disk(server)
    answers += [api.account_status(), api.account_devices(), api.account_connector(),
                api.account_connector_create(), api.account_connector_rotate(), api.account_connector(),
                api.account_sync_now(), api.account_remove_device("0000000000000000"),
                api.account_remove_device("../../etc"), api.account_connector_off()]
    assert server.connectors == {}
    answers.append(api.account_connector_create())
    link = answers[-1]["url"]
    answers += [api.account_sign_out(), api.account_status(), api.account_devices(), api.account_sync_now()]
    for a in answers:
        assert isinstance(a, dict) and isinstance(a["ok"], bool)
        assert a["ok"] or (isinstance(a["error"], str) and a["error"])
        assert token not in json.dumps(a)
    # Nothing secret in the log, the console, or any file but the session file (already deleted).
    printed = capsys.readouterr()
    everything = "\n".join(api.logged) + printed.out + printed.err
    for f in server.data.rglob("*"):
        if f.is_file():
            everything += f.read_text(encoding="utf-8", errors="replace")
    assert token not in everything and "natter_" not in everything
    assert link not in everything and "natterc_" not in everything and EMAIL not in everything
    assert any(line.startswith("[account] verify: ok") for line in api.logged)
    assert any(line.startswith("[account] verify: refused (bad_code)") for line in api.logged)
    assert not any("status" in line for line in api.logged), "the page asks for the status often: it is not logged"


def test_the_api_never_raises_even_when_the_module_does(api, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("natter_secret-looking detail")
    for name in ("status", "start", "verify", "sign_out", "devices", "remove_device", "connector",
                 "connector_create", "connector_rotate", "connector_off"):
        monkeypatch.setattr(account, name, boom)
    monkeypatch.setattr(api.meetings, "sync_now", boom)
    monkeypatch.setattr(api.meetings, "sync_state", boom)
    for out in (api.account_status(), api.account_start(EMAIL), api.account_verify(EMAIL, "123456"),
                api.account_sign_out(), api.account_devices(), api.account_remove_device("x"),
                api.account_connector(), api.account_connector_create(), api.account_connector_rotate(),
                api.account_connector_off(), api.account_sync_now()):
        assert out["ok"] is False and out["error"] == account.FAILED
    assert "natter_" not in "\n".join(api.logged), "an error's own words never reach the log"


def test_signing_out_through_the_api_stops_the_sync_state(api):
    api.account_start(EMAIL)
    api.account_verify(EMAIL, api.server.mailbox[EMAIL])
    api.meetings.state["error"] = "old"
    out = api.account_sign_out()
    assert out["ok"] and out["signed_in"] is False and out["sync"]["error"] == ""


def test_status_while_off_needs_no_engine(api, monkeypatch):
    monkeypatch.delenv(account.SERVER_ENV)
    out = api.account_status()
    assert out["ok"] and out["available"] is False and out["signed_in"] is False
    assert api.account_sync_now() == {"ok": False, "error": account.NOT_AVAILABLE, "reason": "unavailable"}
    assert not api.meetings.started.is_set()


def test_when_waffler_opens_sync_starts_only_if_signed_in(api):
    """app.py main() calls _account_begin_sync: signed out, the meetings
    engine is not even made; signed in, syncing starts."""
    engines = []
    api.ns["_get_meetings"] = lambda: engines.append(1) or api.meetings
    api.ns["_account_begin_sync"]()
    for t in threading.enumerate():
        if t.name == "AccountSync":
            t.join(5)
    assert engines == [] and not api.meetings.started.is_set() and api.server.requests == []
    sign_in(api.server)
    api.ns["_account_begin_sync"]()
    assert api.meetings.started.wait(5) and engines == [1]
    app_py = (ROOT / "app.py").read_text(encoding="utf-8")
    main = app_py[app_py.index("\ndef main():"):]
    assert main.index("_account_begin_sync(delay_s=5.0)") < main.index("webview.start(debug=False)")
    assert "on_account_change=_account_changed" in app_py
