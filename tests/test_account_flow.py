"""Creating an account, signing in and deleting the account, on the Python
side (src/account.py and the Api methods in app.py): every answer the service
can give as a sentence, which of "new account" and "signed in" happened,
Delete my account, and Home's card put away for good.

The service is the stand-in (tests/_account_fake.py): nothing here touches the
network. The page is in tests/test_account_flow_ui.py.
"""
import ast
import json
import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from src import account                               # noqa: E402
from _account_fake import FakeAccountServer           # noqa: E402
from _settings_fake import settings_api               # noqa: E402

SERVER = "https://notes.example.test"
EMAIL = "ada@example.com"


@pytest.fixture(autouse=True)
def _nothing_left_over(monkeypatch):
    monkeypatch.setattr(account, "_notice", "")
    monkeypatch.setattr(account, "_digits", {})


@pytest.fixture
def server(monkeypatch, _isolated_data_dir):
    fake = FakeAccountServer()
    monkeypatch.setenv(account.SERVER_ENV, SERVER)
    monkeypatch.setattr(account, "_transport", fake.transport())
    monkeypatch.setattr(account, "default_device_name", lambda: "Ada's Mac")
    fake.data = _isolated_data_dir
    return fake


def sign_in(fake, email=EMAIL):
    assert account.start(email)["ok"]
    out = account.verify(email, fake.mailbox[email])
    assert out["ok"], out
    return out


# ── every answer the service can give, as one plain sentence ────────────────

def test_a_new_account_and_a_returning_one_are_told_apart(server):
    first = sign_in(server)
    assert first["new_account"] is True, "the account was made by this sign-in"
    account.sign_out()
    server.user_created[server.users[EMAIL]] = "2026-10-01T09:00:00+00:00"   # made weeks ago
    again = sign_in(server)
    assert again["new_account"] is False
    assert "new_account" not in account.status(), "only the sign-in itself says which happened"
    # The service keeps times to the second: one second apart is the same request, three is not.
    assert account._made_just_now({"created_at": "2026-11-21T12:13:57+00:00"},
                                  {"created_at": "2026-11-21T12:13:58+00:00"}) is True
    assert account._made_just_now({"created_at": "2026-11-21T12:13:57+00:00"},
                                  {"created_at": "2026-11-21T12:14:00+00:00"}) is False
    assert account._made_just_now({"created_at": "soon"}, {"created_at": "2026-11-21T12:14:00+00:00"}) is None
    assert account._made_just_now({}, {}) is None


def test_when_the_service_does_not_say_the_app_does_not_guess(server, monkeypatch):
    account.start(EMAIL)
    real = server.handle

    def no_dates(request):
        r = real(request)
        if request.url.path == "/auth/email/verify" and r.status_code == 200:
            body = json.loads(r.content)
            body["user"].pop("created_at")
            return server._json(200, body)
        return r
    monkeypatch.setattr(account, "_transport", __import__("httpx").MockTransport(no_dates))
    out = account.verify(EMAIL, server.mailbox[EMAIL])
    assert out["ok"] and out["signed_in"] and out["new_account"] is None


def test_invite_only_is_said_in_those_words(server):
    server.signups_closed = True
    account.start(EMAIL)
    out = account.verify(EMAIL, server.mailbox[EMAIL])
    assert out == {"ok": False, "error": "Waffler accounts are invite-only for now.", "reason": "signups_closed"}
    assert account.status()["signed_in"] is False
    assert not (server.data / account.SESSION_FILE).exists()


def test_someone_with_an_account_still_signs_in_while_signups_are_closed(server):
    sign_in(server)
    account.sign_out()
    server.signups_closed = True
    assert sign_in(server)["signed_in"]


def test_when_the_sign_in_email_cannot_be_sent(server):
    server.email_off = 0                                   # not set up, or it failed: no wait is given
    out = account.start(EMAIL)
    assert out == {"ok": False, "error": account.CODE_NOT_SENT, "reason": "busy"}
    assert out["error"] == "The code couldn't be sent just now. Try again in a moment."
    server.email_off = 30
    assert account.start(EMAIL)["error"] == account.CODE_NOT_SENT
    server.email_off = 2700                                # the hour's emails are used up
    out = account.start(EMAIL)
    assert out["error"] == "Waffler has sent all the sign-in emails it can for now. Try again in an hour."
    assert out["reason"] == "busy" and EMAIL not in server.mailbox


def test_todays_questions_used_up_is_plain(server):
    sign_in(server)
    server.model_calls_cap = True
    out = account.devices()
    assert out == {"ok": False, "reason": "model_calls_cap",
                   "error": "Today's questions and rewrites are used up. There are more tomorrow."}
    assert "UTC" not in out["error"] and "cap" not in out["error"]
    assert account.sync_refusal(402, "Today's questions… (midnight UTC).", "model_calls_cap") == account.MODEL_CALLS_CAP
    assert account.status()["signed_in"], "an allowance signs nobody out"


def test_a_full_server_is_not_the_persons_doing_and_nothing_is_lost(server):
    sentence = account.sync_refusal(507, "The server is full at the moment.", "server_full")
    assert sentence == account.SERVER_FULL
    assert sentence == ("The account service is full just now. Nothing is lost: your meetings stay on this "
                        "computer and sync when there is room.")
    refusal = account._refusal(507, {"detail": "x", "code": "server_full"}, {}, signed=True, what="")
    assert (refusal.sentence, refusal.reason) == (account.SERVER_FULL, "server_full")
    assert account.sync_refusal(503) == account.BUSY, "any other trouble at the service stays 'busy'"


@pytest.mark.parametrize("status, body, what, signed", [
    (400, {"detail": "x"}, "start", False), (401, {}, "verify", False), (401, {}, "", False),
    (402, {"code": "minutes_cap"}, "", True), (402, {"code": "model_calls_cap"}, "", True),
    (403, {"code": "account_disabled"}, "", True), (403, {"code": "signups_closed"}, "verify", False),
    (403, {}, "", True), (404, {}, "remove_device", True), (404, {}, "", True), (408, {}, "", True),
    (413, {}, "", True), (422, {"detail": [{"loc": ["body"]}]}, "", True), (429, {}, "start", False),
    (500, {}, "", True), (503, {}, "start", False), (503, {}, "", True), (507, {"code": "server_full"}, "", True),
])
def test_every_refusal_is_a_sentence_a_person_can_read(status, body, what, signed):
    said = account._refusal(status, body, {}, signed=signed, what=what, token="not-the-one-on-disk")
    assert said.sentence and said.sentence[0].isupper() and said.sentence.endswith(".")
    assert said.reason and re.fullmatch(r"[a-z_]+", said.reason)
    for raw in ("HTTP", str(status), "detail", "{", "_cap", "signups_closed", "server_full"):
        assert raw not in said.sentence


# ── deleting the account ────────────────────────────────────────────────────

def test_delete_my_account_asks_the_service_and_clears_this_computer(server):
    sign_in(server)
    assert account.connector_create()["ok"]
    path = server.data / account.SESSION_FILE
    assert path.exists()
    out = account.delete_account("DELETE")
    assert out["ok"] and out["deleted"] is True and out["signed_in"] is False and out["email"] == ""
    asked = server.calls("DELETE", "/account")
    assert len(asked) == 1 and asked[0].auth.startswith("Bearer natter_")
    assert server.deleted == [EMAIL] and EMAIL not in server.users
    assert server.sessions == {} and server.connectors == {}
    assert not path.exists() and account.sync_credentials() is None
    assert out["notice"] == account.status()["notice"] == \
        "Your account is deleted. The meetings on this computer are still here."
    # The same address afterwards is a new, empty account.
    assert sign_in(server)["new_account"] is True


def test_delete_needs_the_word_typed_and_asks_nothing_without_it(server):
    sign_in(server)
    for typed in ("", "yes", "DELET", "DELETE!", None, 7, "delete my account"):
        out = account.delete_account(typed)
        assert out == {"ok": False, "error": "Type DELETE to confirm.", "reason": "unconfirmed"}, typed
    assert server.calls("DELETE", "/account") == [] and account.status()["signed_in"]
    assert account.delete_account(" delete ")["ok"], "the word, however it is cased or spaced"


def test_a_delete_that_did_not_reach_the_service_deletes_nothing_and_says_so(server):
    sign_in(server)
    server.offline = True
    out = account.delete_account("DELETE")
    assert out["ok"] is False and out["reason"] == "offline"
    assert out["error"] == "Your account was not deleted. " + account.NO_INTERNET
    assert account.status()["signed_in"], "still signed in: nothing happened"
    server.offline = False
    assert server.deleted == []


def test_delete_on_a_computer_that_was_already_signed_out(server):
    sign_in(server)
    server.sessions.clear()                                # removed from another device
    out = account.delete_account("DELETE")
    assert out == {"ok": False, "error": account.SIGNED_OUT, "reason": "signed_out"}
    assert account.status()["signed_in"] is False
    assert account.delete_account("DELETE")["reason"] == "signed_out"


# ── the Api: delete, the Home card put away for good ────────────────────────

API_METHODS = ("account_status", "account_delete_start", "account_delete", "account_offer_dismiss")
APP_HELPERS = ("_account_mod", "_account_sync_state", "_account_call", "_account_begin_sync")


class FakeMeetings:
    def __init__(self):
        self.signed_out = 0
        self.state = {"syncing": False, "last_synced_at": "2026-11-21T10:00:00.000+00:00", "error": "old"}

    def sync_state(self):
        return dict(self.state)

    def account_signed_out(self):
        self.signed_out += 1
        self.state = {"syncing": False, "last_synced_at": "", "error": ""}


@pytest.fixture
def api(server):
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in API_METHODS]
    assert {n.name for n in methods} == set(API_METHODS), "app.py Api account methods moved"
    helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in APP_HELPERS]
    logged, meetings = [], FakeMeetings()
    ns = {"_log_to_file": logged.append, "threading": threading, "time": __import__("time"),
          "_meetings": meetings, "_get_meetings": lambda: meetings}
    exec(compile(ast.Module(helpers + methods, []), "<app.py account>", "exec"), ns)
    api = settings_api(server.data)
    for n in methods:
        setattr(type(api), n.name, ns[n.name])
    api.logged, api.meetings, api.server = logged, meetings, server
    return api


def test_deleting_through_the_api_signs_out_here_and_keeps_the_meetings(api):
    sign_in(api.server)
    refused = api.account_delete("nope")
    assert refused["ok"] is False and refused["reason"] == "unconfirmed" and api.meetings.signed_out == 0
    out = api.account_delete("DELETE")
    assert out["ok"] and out["deleted"] and out["signed_in"] is False
    assert out["sync"] == {"syncing": False, "last_synced_at": "", "error": ""}
    assert api.meetings.signed_out == 1, "sync stops; the meetings store itself is never touched"
    assert api.server.deleted == [EMAIL]
    assert not (api.server.data / account.SESSION_FILE).exists()
    assert "[account] delete_account: ok" in api.logged
    assert EMAIL not in "\n".join(api.logged)
    app_py = (ROOT / "app.py").read_text(encoding="utf-8")
    body = app_py[app_py.index("    def account_delete(self"):]
    body = body[:body.index("\n    def ", 10)]
    assert "rmtree" not in body and "unlink" not in body and "delete_note" not in body


def test_the_api_delete_never_raises(api, monkeypatch):
    sign_in(api.server)
    monkeypatch.setattr(account, "delete_account", lambda *a: 1 / 0)
    assert api.account_delete("DELETE") == {"ok": False, "error": account.FAILED, "reason": "failed"}


def test_the_home_card_is_put_away_for_good(api):
    assert api.account_status()["offer_dismissed"] is False
    assert api.account_offer_dismiss() == {"ok": True}
    assert api.data["account_offer_dismissed"] is True, "kept in settings.json: the page's own storage is wiped"
    assert api.account_status()["offer_dismissed"] is True
    # Signing in and out, or a second press, never brings it back.
    sign_in(api.server)
    account.sign_out()
    assert api.account_offer_dismiss() == {"ok": True}
    assert api.account_status()["offer_dismissed"] is True
    # A fresh page asks again and is told the same.
    again = settings_api(api.server.data)
    assert again.data["account_offer_dismissed"] is True


def test_putting_the_card_away_keeps_every_other_setting(api):
    with api._editing_settings() as stored:
        stored.update({"theme": "dark", "hotkey": ["fn"]})
    api.account_offer_dismiss()
    assert api.data == {"theme": "dark", "hotkey": ["fn"], "account_offer_dismissed": True}
