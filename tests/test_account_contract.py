"""The account service is being hardened while the one that is deployed stays
as it was, so the client speaks both: the newer contract (an 8-digit code,
said by the service; a fresh emailed code to delete the account; refusals
named in "error") and the older one (6 digits, the word DELETE typed).

The service is the stand-in (tests/_account_fake.py) in each of its two
contracts: nothing here touches the network. The page's decisions are the
pure functions in ui/logic.js (run in Node); the rest is read from the source.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from src import account                               # noqa: E402
from _account_fake import FakeAccountServer           # noqa: E402
from test_ui_logic import code_only, js, needs_node, read  # noqa: E402

SERVER = "https://notes.example.test"
EMAIL = "ada@example.com"


@pytest.fixture(autouse=True)
def _nothing_left_over(monkeypatch):
    monkeypatch.setattr(account, "_notice", "")
    monkeypatch.setattr(account, "_digits", {})


def _server(monkeypatch, data, contract):
    fake = FakeAccountServer(contract=contract)
    monkeypatch.setenv(account.SERVER_ENV, SERVER)
    monkeypatch.setattr(account, "_transport", fake.transport())
    monkeypatch.setattr(account, "default_device_name", lambda: "Ada's Mac")
    fake.data = data
    return fake


@pytest.fixture
def new(monkeypatch, _isolated_data_dir):
    return _server(monkeypatch, _isolated_data_dir, "new")


@pytest.fixture
def old(monkeypatch, _isolated_data_dir):
    return _server(monkeypatch, _isolated_data_dir, "old")


def sign_in(fake, email=EMAIL):
    assert account.start(email)["ok"]
    out = account.verify(email, fake.mailbox[email])
    assert out["ok"], out
    return out


def section():
    html = read("index.html")
    start = html.index('<section class="s-sec" id="secAccount"')
    return html[start:html.index("</section>", start)]


def text_of(html):
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# ── the sign-in code is as long as the service says ─────────────────────────

def test_a_newer_service_says_eight_digits_and_the_code_is_checked_against_that(new):
    sent = account.start(EMAIL)
    assert sent == {"ok": True, "email": EMAIL, "expires_in": 600, "digits": 8}
    code = new.mailbox[EMAIL]
    assert len(code) == 8
    for bad in (code[:6], code[:7], code + "1", "abcdefgh", "", None):
        assert account.verify(EMAIL, bad) == {"ok": False, "error": "Enter the 8-digit code from the email.",
                                              "reason": "bad_code"}, bad
    assert new.calls("POST", "/auth/email/verify") == [], "a code of the wrong length is never sent"
    out = account.verify(EMAIL, f"{code[:4]} {code[4:]}")          # as people type it
    assert out["ok"] and out["signed_in"]
    assert json.loads(new.calls("POST", "/auth/email/verify")[0].body)["code"] == code


def test_an_older_service_does_not_say_and_six_digits_still_sign_in(old):
    sent = account.start(EMAIL)
    assert sent["digits"] == 6 and "digits" not in json.loads(
        old.handle(__import__("httpx").Request("POST", SERVER + "/auth/email/start",
                                               json={"email": "grace@example.com"})).content)
    code = old.mailbox[EMAIL]
    assert len(code) == 6
    assert account.verify(EMAIL, code + "12")["error"] == "Enter the 6-digit code from the email."
    assert account.verify(EMAIL, code)["ok"]


@pytest.mark.parametrize("said", [None, "8", 8.0, True, 0, 3, 13, 400, -8, [8]])
def test_a_length_that_is_not_a_sensible_number_is_taken_as_six(new, monkeypatch, said):
    assert account._digits_of({"digits": said}) == 6
    assert account._digits_of({}) == 6 and account._digits_of({"digits": 8}) == 8


def test_a_code_typed_before_any_was_asked_for_names_no_number(new):
    out = account.verify(EMAIL, "12345")
    assert out == {"ok": False, "error": "Enter the code from the email.", "reason": "bad_code"}
    assert new.calls("POST", "/auth/email/verify") == []


@needs_node
def test_the_page_cleans_and_checks_a_code_by_the_number_it_was_given():
    assert js("L.accountDigits(8)") == 8 and js("L.accountDigits(6)") == 6
    for odd in ("undefined", "null", "'8'", "8.5", "0", "3", "13", "true"):
        assert js(f"L.accountDigits({odd})") == 6, odd
    assert js("L.accountCode('48291357', 8)") == "48291357"
    assert js("L.accountCode(' 4829 1357 ', 8)") == "48291357"
    assert js("L.accountCode('4829-1357', 8)") == "48291357"
    assert js("L.accountCode('Your Waffler code is: 48291357. It works once and for 10 minutes.', 8)") == "48291357"
    assert js("L.accountCode('Use within 10 minutes: 4829 1357', 8)") == "48291357"
    assert js("L.accountCode('123456789', 8)") == "12345678", "typed too far: no more than the code holds"
    assert js("L.accountCode('482913')") == "482913" and js("L.accountCode('482 913', 6)") == "482913"
    assert js("L.accountCodeReady('48291357', 8)") is True
    assert js("L.accountCodeReady('482913', 8)") is False, "six digits are not an 8-digit code"
    assert js("L.accountCodeReady('48291357', 6)") is False and js("L.accountCodeReady('482913')") is True


@needs_node
def test_the_field_names_the_number_only_once_the_service_has_said():
    assert js("L.accountCodeLabel(null)") == js("L.accountCodeLabel(undefined)") == "Enter the code"
    assert js("L.accountCodeLabel(8)") == "Enter the 8-digit code"
    assert js("L.accountCodeLabel(6)") == "Enter the 6-digit code"
    assert js("L.accountCodeExample(8)") == "12345678" and js("L.accountCodeExample(6)") == "123456"
    assert js("L.accountCodeExample(null)") == ""
    assert js("L.accountBadCode(8)") == "Enter the 8-digit code from the email."
    assert js("L.accountBadCode(undefined)") == "Enter the 6-digit code from the email."
    assert js("L.accountCodeLine('you@example.com', 8)") == \
        "Check you@example.com for an 8-digit code. It works for 10 minutes."


def test_nothing_in_the_page_says_six_digits_as_a_fixed_fact():
    html = read("index.html")
    assert not re.search(r"(?i)\b(6|six)[- ]digit", html)
    assert 'placeholder="123456"' not in html, "an example of six digits says six"
    assert html.count("We email you a code.") == 2, "Settings and setup, before the code is asked for"
    assert "There is no password: we email you a code." in text_of(section())
    page, logic = code_only(read("account.js")), code_only(read("logic.js"))
    assert not re.search(r"(?i)\b(6|six)[- ]digit", page + logic)
    assert "\\d{6}" not in page + logic and "slice(0, 6)" not in logic
    flow = page[page.index("function signInFlow("):]
    flow = flow[:flow.index("\n  }\n")]
    # What the service said goes into the label, the example, the hint, the paste and the check.
    assert "digits = L.accountDigits(r.digits)" in flow
    for use in ("L.accountCodeLabel(digits)", "L.accountCodeExample(digits)", "L.accountCodeLine(email, digits)",
                "L.accountCode(text, digits)", "L.accountCodeReady(code, digits)", "L.accountBadCode(digits)"):
        assert use in flow, use
    assert "title: $('acctCodeLabel')" in page and "title: $('obTitleAccountCode')" in page
    assert '<label for="acctCode" id="acctCodeLabel">Enter the code</label>' in html
    assert re.search(r'<h1 class="ob-title" id="obTitleAccountCode"[^>]*>Enter the code</h1>', html)
    client = (ROOT / "src" / "account.py").read_text(encoding="utf-8")
    assert "6-digit" not in client and r"\d{6}\"" not in client


# ── deleting the account needs a fresh emailed code ─────────────────────────

def test_delete_with_the_emailed_code(new):
    sign_in(new)
    assert account.connector_create()["ok"]
    started = account.delete_start()
    assert started == {"ok": True, "code": True, "digits": 8, "expires_in": 600}
    asked = new.calls("POST", "/account/delete/start")
    assert len(asked) == 1 and asked[0].auth.startswith("Bearer natter_")
    code = new.delete_codes[EMAIL]
    assert len(code) == 8 and new.deleted == []
    out = account.delete_account("", f"{code[:4]}-{code[4:]}")
    assert out["ok"] and out["deleted"] is True and out["signed_in"] is False
    sent = new.calls("DELETE", "/account")
    assert len(sent) == 1 and json.loads(sent[0].body) == {"code": code}
    assert new.deleted == [EMAIL] and new.sessions == {} and new.connectors == {}
    assert not (new.data / account.SESSION_FILE).exists()
    assert out["notice"] == "Your account is deleted. The meetings on this computer are still here."


def test_a_wrong_delete_code_deletes_nothing_and_says_so(new):
    sign_in(new)
    account.delete_start()
    code = new.delete_codes[EMAIL]
    wrong = "00000000" if code != "00000000" else "11111111"
    out = account.delete_account("", wrong)
    assert out == {"ok": False, "reason": "wrong_code",
                   "error": "Your account was not deleted. That code is wrong or has expired. "
                            "Check it, or send a new one."}
    assert new.deleted == [] and account.status()["signed_in"]
    assert account.delete_account("", code)["ok"], "the right one still works"


def test_a_delete_code_of_the_wrong_length_is_never_sent(new):
    sign_in(new)
    account.delete_start()
    for bad in ("123456", "123456789", "abcdefgh"):
        assert account.delete_account("", bad) == {
            "ok": False, "error": "Enter the 8-digit code from the email.", "reason": "bad_code"}
    assert new.calls("DELETE", "/account") == [] and new.deleted == []


def test_the_sign_in_code_does_not_delete_the_account(new):
    sign_in(new)
    account.start(EMAIL)                                   # a sign-in code, asked for from somewhere
    account.delete_start()
    out = account.delete_account("", new.mailbox[EMAIL])
    assert out["ok"] is False and out["reason"] == "wrong_code" and new.deleted == []


def test_a_newer_service_refuses_the_typed_word_alone_in_a_plain_sentence(new):
    sign_in(new)
    out = account.delete_account("DELETE")
    assert out == {"ok": False, "reason": "code_required",
                   "error": "Your account was not deleted. Deleting your account needs a code from your "
                            "email. Ask for one, then try again."}
    assert new.deleted == [] and account.status()["signed_in"]
    assert account.delete_account("", "")["reason"] == "unconfirmed", "neither a code nor the word: nothing is asked"


def test_an_older_service_has_no_code_step_and_the_typed_word_still_deletes(old):
    sign_in(old)
    assert account.delete_start() == {"ok": True, "code": False}
    assert len(old.calls("POST", "/account/delete/start")) == 1 and old.delete_codes == {}
    assert account.status()["signed_in"], "a 404 for a step it does not have is not a sign-out"
    out = account.delete_account("DELETE", "")
    assert out["ok"] and out["deleted"] is True and old.deleted == [EMAIL]
    assert old.calls("DELETE", "/account")[0].body == b"", "nothing is sent that the older service does not know"


def test_asking_for_the_delete_code_signed_out_or_offline_is_a_sentence(new):
    assert account.delete_start() == {"ok": False, "error": account.NOT_SIGNED_IN, "reason": "signed_out"}
    sign_in(new)
    new.offline = True
    assert account.delete_start() == {"ok": False, "error": account.NO_INTERNET, "reason": "offline"}
    new.offline = False
    new.sessions.clear()                                   # removed from another device
    assert account.delete_start() == {"ok": False, "error": account.SIGNED_OUT, "reason": "signed_out"}


def test_app_py_has_the_delete_start_method_and_passes_the_code_on():
    app_py = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '    def account_delete_start(self) -> dict:' in app_py
    assert 'return _account_call("delete_start")' in app_py
    assert '    def account_delete(self, confirm: str = "", code: str = "") -> dict:' in app_py
    assert '_account_call("delete_account", confirm, code)' in app_py


@needs_node
def test_which_step_follows_email_me_a_code():
    assert js("L.accountDeleteStep({ok: true, code: true, digits: 8})") == "code"
    assert js("L.accountDeleteStep({ok: true, code: false})") == "word", "an older service: type DELETE"
    assert js("L.accountDeleteStep({ok: false, error: 'x'})") == "ask"
    assert js("L.accountDeleteStep(null)") == "ask"
    assert js("L.accountDeleteReady('48291357', 'code', 8)") is True
    assert js("L.accountDeleteReady('482913', 'code', 8)") is False
    assert js("L.accountDeleteReady('DELETE', 'code', 8)") is False, "the word is not asked for with a code"
    assert js("L.accountDeleteReady('DELETE', 'word')") is True
    assert js("L.accountDeleteReady('48291357', 'word')") is False


def test_the_delete_question_explains_then_emails_a_code_then_deletes():
    sec = section()
    ask = sec[sec.index('id="acctDeleteConfirm"'):]
    ask = ask[:ask.index("</form>")]
    # The same explanation of what is and is not deleted, first.
    assert ("This deletes the account and everything stored in it on the server, for every device signed in "
            "to it. Meetings already on this computer stay. It can't be undone.") in text_of(ask[ask.index(">") + 1:])
    order = [ask.index(i) for i in ('id="acctDeleteSays"', 'id="acctDeleteCodeAsk"', 'id="acctDeleteCode"',
                                    'id="acctDeleteNo"', 'id="acctDeleteSend"', 'id="acctDeleteYes"')]
    assert order == sorted(order)
    assert ('<button type="button" class="btn btn-pri btn-sm" id="acctDeleteSend">Email me a code to confirm'
            "</button>") in ask
    assert '<label for="acctDeleteCode" id="acctDeleteCodeLabel">Enter the code</label>' in ask
    assert re.search(r'<input type="text" id="acctDeleteCode" class="input input-mono"[^>]*inputmode="numeric"'
                     r'[^>]*autocomplete="one-time-code"[^>]* hidden>', ask)
    assert '<div class="row-d" id="acctDeleteCodeDesc" role="status"></div>' in ask
    assert '<button type="button" class="link-btn" id="acctDeleteResend">Send a new code</button>' in ask
    # Until the code is asked for there is no field, no word to type, and no destructive button.
    for hidden in ('id="acctDeleteCodeAsk" hidden', 'id="acctDeleteWordAsk" hidden', 'id="acctDeleteMore" hidden',
                   'id="acctDeleteYes" disabled hidden'):
        assert hidden in ask, hidden
    assert "DELETE" not in text_of(ask[:ask.index('id="acctDeleteWordAsk"')])

    page = code_only(read("account.js"))
    send = page[page.index("async function deleteSend("):]
    send = send[:send.index("\n  }\n")]
    assert "call('account_delete_start')" in send and "drawDelete(L.accountDeleteStep(r))" in send
    for use in ("L.accountCodeLabel(deleteDigits)", "L.accountCodeExample(deleteDigits)",
                "L.accountCodeLine(status && status.email, deleteDigits)"):
        assert use in send, use
    draw = page[page.index("function drawDelete("):]
    draw = draw[:draw.index("\n  }\n")]
    assert "$('acctDeleteSend').hidden = step !== 'ask';" in draw
    assert "$('acctDeleteYes').hidden = step === 'ask';" in draw and "$('acctDeleteYes').disabled = true;" in draw
    assert "$('acctDeleteWordAsk').hidden = $('acctDeleteWord').hidden = step !== 'word';" in draw
    assert "$('acctDeleteSend').addEventListener('click', () => busy($('acctDeleteSend'), deleteSend))" in page
    # A pasted code fills the field; only the button deletes.
    assert "$('acctDeleteCode').addEventListener('paste'" not in page
    assert page.count("deleteAccount)") == 1 and "addEventListener('submit', (e) => { e.preventDefault(); busy($('acctDeleteYes'), deleteAccount); })" in page


# ── the newer refusals, each as one plain sentence ──────────────────────────

def test_too_many_wrong_codes_says_to_wait_and_ask_for_a_new_code(new):
    account.start(EMAIL)
    code = new.mailbox[EMAIL]
    wrong = "00000000" if code != "00000000" else "11111111"
    for _ in range(new.wrong_limit):
        assert account.verify(EMAIL, wrong)["reason"] == "wrong_code"
    out = account.verify(EMAIL, code)
    assert out == {"ok": False, "reason": "too_many_wrong_codes",
                   "error": "Too many wrong codes for this address. Wait up to an hour, then ask for a new code."}
    assert not account.status()["signed_in"]
    said = account._refusal(429, {"error": "too_many_wrong_codes"}, {"retry-after": "1500"},
                            signed=False, what="verify")
    assert said.sentence == "Too many wrong codes for this address. Wait 25 minutes, then ask for a new code."


def test_too_many_wrong_delete_codes_says_the_account_was_not_deleted(new):
    sign_in(new)
    account.delete_start()
    code = new.delete_codes[EMAIL]
    wrong = "00000000" if code != "00000000" else "11111111"
    for _ in range(new.wrong_limit):
        assert account.delete_account("", wrong)["reason"] == "wrong_code"
    out = account.delete_account("", code)
    assert out == {"ok": False, "reason": "too_many_wrong_codes",
                   "error": "Your account was not deleted. Too many wrong codes for this address. "
                            "Wait up to an hour, then ask for a new code."}
    assert new.deleted == [] and account.status()["signed_in"]


def test_too_many_codes_requested_says_when_to_try_again_if_the_service_does(new):
    for _ in range(new.codes_limit):
        assert account.start(EMAIL)["ok"]
    out = account.start(EMAIL)
    assert out == {"ok": False, "reason": "too_many_codes",
                   "error": "Too many codes have been requested for this address. Try again in 25 minutes."}
    new.codes_retry_after = None                           # the service does not say when
    assert account.start(EMAIL)["error"] == \
        "Too many codes have been requested for this address. Try again later."
    new.codes_retry_after = 3600
    assert account.start(EMAIL)["error"].endswith("Try again in an hour.")
    new.codes_retry_after = 9000
    assert account.start(EMAIL)["error"].endswith("Try again in 3 hours.")


def test_too_many_delete_codes_requested(new):
    sign_in(new)                                           # one code asked for already
    for _ in range(new.codes_limit - 1):
        assert account.delete_start()["ok"]
    assert account.delete_start() == {
        "ok": False, "reason": "too_many_codes",
        "error": "Too many codes have been requested for this address. Try again in 25 minutes."}


def test_an_older_services_too_many_tries_is_said_as_before(old):
    old.too_many = True
    assert account.start(EMAIL) == {"ok": False, "reason": "too_many",
                                    "error": "Too many tries. Wait 10 minutes, then try again."}


def test_material_too_large_for_a_question_or_a_rewrite():
    for body in ({"error": "too_large"}, {"detail": "Request body too large"}, {}, None):
        said = account._refusal(413, body, {}, signed=True, what="")
        assert (said.sentence, said.reason) == ("That is too long to send in one go. Try it with less text.",
                                                "too_large")


def test_the_days_writing_allowance_used_up(new):
    sign_in(new)
    new.writing_cap = True
    out = account.devices()
    assert out == {"ok": False, "reason": "writing_cap",
                   "error": "Today's writing allowance is used up. There is more tomorrow."}
    assert account.status()["signed_in"], "not a sign-out"
    assert account.sync_refusal(402, "Today's writing is used up.", "writing_cap") == account.WRITING_CAP


def test_a_402_shows_the_services_reason_only_when_it_is_a_known_one():
    def said(body):
        return account._refusal(402, body, {}, signed=True, what="").sentence
    assert said({"error": "model_calls_cap"}) == said({"code": "model_calls_cap"}) == account.MODEL_CALLS_CAP
    assert said({"error": "daily_writing_cap", "detail": "anything at all"}) == account.WRITING_CAP
    # Storage: the service's own sentence, as before.
    assert said({"error": "storage_cap", "detail": "Your account's storage is full."}) == \
        "Your account's storage is full."
    assert said({"code": "storage_cap", "detail": "Your account's storage is full."}) == \
        "Your account's storage is full."
    # A reason this Waffler does not know: a general sentence, never the service's text.
    assert said({"error": "something_new", "detail": "Visit http://elsewhere.example to carry on"}) == \
        "Your account's allowance is used up for now. Try again tomorrow."
    assert said({}) == account.OVER_ALLOWANCE


@pytest.mark.parametrize("status, body, headers, what", [
    (429, {"error": "too_many_wrong_codes"}, {}, "verify"), (429, {"error": "too_many_attempts"}, {}, "delete"),
    (429, {"error": "too_many_codes"}, {"retry-after": "nonsense"}, "start"),
    (429, {"error": "too_many_codes"}, {"retry-after": "inf"}, "delete_start"),
    (429, {"error": "too_many_codes"}, {"retry-after": "nan"}, "start"),
    (429, {"error": "rate_limited"}, {"retry-after": "-5"}, "start"),
    (429, {"error": "rate_limited"}, {"retry-after": "nan"}, "start"), (503, {}, {"retry-after": "inf"}, "start"),
    (413, {"error": "too_large"}, {}, ""), (402, {"error": "writing_cap"}, {}, ""),
    (402, {"error": "who_knows"}, {}, ""), (403, {"error": "code_required"}, {}, "delete"),
    (403, {"error": "wrong_code"}, {}, "delete"), (404, {}, {}, "delete_start"),
    (403, {"error": 7}, {}, ""), (429, {"error": ["x"]}, {}, "verify"), (402, "not json", {}, ""),
])
def test_every_newer_refusal_is_a_sentence_a_person_can_read(status, body, headers, what):
    said = account._refusal(status, body, headers, signed=True, what=what, token="not-the-one-on-disk")
    assert said.sentence and said.sentence[0].isupper() and said.sentence.endswith(".")
    assert said.reason and re.fullmatch(r"[a-z_]+", said.reason)
    for raw in ("HTTP", str(status), "detail", "{", "_", "error", "inf", "nan"):
        assert raw not in said.sentence, raw
