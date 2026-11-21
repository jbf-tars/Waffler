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
