"""Creating an account and signing in, made obvious, in the window: one flow in
Settings, Account; the offer at the end of first-run setup and the one card on
Home; what the signed-in page shows; Delete my account; the questions asked
before a device is removed or the Claude link is changed.

The page's decisions are pure functions in ui/logic.js (run in Node); the rest
is read from the source, as the other window tests do. The Python side is in
tests/test_account_flow.py.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from test_ui_logic import code_only, js, needs_node, read  # noqa: E402

NOW = "Date.parse('2026-11-21T12:00:00.000+00:00')"
OUT = "{ok: true, available: true, signed_in: false}"
IN = "{ok: true, available: true, signed_in: true, email: 'ada@example.com'}"
OFF = "{ok: true, available: false, signed_in: false}"


def section():
    html = read("index.html")
    start = html.index('<section class="s-sec" id="secAccount"')
    return html[start:html.index("</section>", start)]


def wizard_step():
    html = read("index.html")
    start = html.index('<section class="ob-step" id="obStepAccount"')
    return html[start:html.index("</section>", start)]


def text_of(html):
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# ── the one card on Home ────────────────────────────────────────────────────

@needs_node
def test_who_sees_the_home_card():
    def show(status, meetings):
        return js(f"L.accountOfferView({status}, {meetings})")
    assert show(OUT, 1) is True and show(OUT, 12) is True
    assert show(OUT, 0) is False, "nothing to keep in step yet"
    assert show(IN, 3) is False, "signed in"
    assert show(OFF, 3) is False, "no account service: accounts are not offered anywhere"
    assert show("{ok: true, available: true, signed_in: false, offer_dismissed: true}", 3) is False
    assert show("null", 3) is False and show("{ok: false, error: 'x'}", 3) is False
    assert show(OUT, "undefined") is False


def test_the_home_card_is_one_quiet_card_that_can_be_put_away():
    page = code_only(read("account.js"))
    assert "L.accountOfferView(" in page and "account_offer_dismiss" in page
    assert "makeNotice(" in page and "id: 'acctOffer'" in page
    assert "$('acctOffer')" in page, "one card: an existing one is found, never added twice"
    assert "'Don’t show this again'" in page, "the close button says it is for good"
    assert "btn btn-sec btn-sm" in page[page.index("function drawOffer"):page.index("function drawOffer") + 1800]
    assert "showSettingsSection('account')" in page
    app = code_only(read("app.js"))
    feed = app[app.index("meetings = (await window.pywebview.api.meetings_list(query || ''))"):]
    assert "window.accountOffer" in feed[:700], "Home tells the card how many meetings there are"


# ── the offer at the end of first-run setup ─────────────────────────────────

@needs_node
def test_setup_offers_an_account_only_where_one_can_be_made():
    assert js(f"L.accountSetupOffered({OUT})") is True
    assert js(f"L.accountSetupOffered({IN})") is False, "already signed in: nothing to offer"
    assert js(f"L.accountSetupOffered({OFF})") is False, "no account service: the step is skipped"
    assert js("L.accountSetupOffered(null)") is False
    assert js("L.accountSetupOffered({ok: false})") is False
    # The steps people are counted through are unchanged: the offer is an extra.
    assert js("L.setupSteps(true)") == ["connect", "permissions", "try", "anywhere"]
    assert js("L.setupSteps(false)") == ["connect", "try", "anywhere"]


def test_the_setup_step_says_what_an_account_gives_and_how_to_skip_it():
    step = wizard_step()
    offer = step[step.index('data-when="offer"'):]
    # One sentence says what it gives; "Optional" above it and Not now beside
    # the main button say it can be skipped.
    assert '<p class="ob-lede ob-acct-list" data-when="offer">Your meetings on every device, and in Claude.</p>' in step
    assert "<li>" not in offer[:offer.index("obAcctNo")]
    yes = re.search(r'<button type="button" class="btn (btn-\w+) btn-lg ob-acct-btn" id="obAcctYes"[^>]*>'
                    r"Create account or sign in</button>", step)
    no = re.search(r'<button type="button" class="btn (btn-\w+) btn-lg ob-acct-btn" id="obAcctNo"[^>]*>'
                   r"Not now</button>", step)
    assert yes and no, "two buttons of one size, side by side"
    assert (yes.group(1), no.group(1)) == ("btn-pri", "btn-sec")
    assert "btn-quiet" not in offer[:offer.index("obAcctNo")], "Not now is a real button, not a small link"
    assert 'onclick="wizAccountSkip()"' in no.group(0)
    assert "Optional" in text_of(step)
    assert "style=" not in step.replace('style="display:none"', "", 1)
    assert "—" not in step and "–" not in step


def test_the_setup_step_has_the_same_fields_as_settings():
    step = wizard_step()
    assert '<h1 class="ob-title" id="obTitleAccountEmail" tabindex="-1" data-when="email">' \
           "Create your account or sign in</h1>" in step
    assert re.search(r'<input type="email" id="obAcctEmail" class="input"[^>]*autocomplete="email"', step)
    assert re.search(r'<input type="text" id="obAcctCode" class="input input-mono"[^>]*inputmode="numeric"'
                     r'[^>]*autocomplete="one-time-code"', step)
    for ident in ("obAcctEmailForm", "obAcctCodeForm", "obAcctSend", "obAcctSignIn", "obAcctResend",
                  "obAcctOther", "obAcctError", "obAcctCodeDesc", "obAcctDone"):
        assert f'id="{ident}"' in step, ident
    assert '<p class="ob-small ob-acct-err" id="obAcctError" role="alert" hidden></p>' in step
    assert step.count("Not now") >= 2, "there is a way out on the email and code screens too"
    page = code_only(read("account.js"))
    assert "signInFlow('acct'" in page and "signInFlow('obAcct'" in page, "one flow, used in both places"


def test_setup_shows_the_step_only_when_offered_and_never_blocks_finishing():
    app = code_only(read("app.js"))
    assert "account: 'obStepAccount'" in app
    assert "_wizAccountOffer = WL.accountSetupOffered(" in app
    nxt = app[app.index("async function wizNext()"):]
    nxt = nxt[:nxt.index("\n}\n")]
    assert "if (_wizardStep === 'anywhere' && _wizAccountOffer) { wizShowStep('account'); return; }" in nxt
    assert "if (_wizardStep === 'anywhere' || _wizardStep === 'account') { await wizCompleteSetup(); return; }" in nxt
    skip = app[app.index("function wizAccountSkip()"):]
    assert "wizCompleteSetup()" in skip[:skip.index("\n}\n")]
    # Finishing setup is the same call whether or not an account was made.
    done = app[app.index("async function wizCompleteSetup()"):]
    done = done[:done.index("\n}\n")]
    assert "account" not in done
    # With no service the last step's button still says Done.
    assert "step === 'anywhere' && !_wizAccountOffer ? 'Done'" in app or \
           "(step === 'anywhere' && !_wizAccountOffer) || step === 'account' ? 'Done' : 'Continue'" in app
    html = read("index.html")
    assert html.index('id="obStepAnywhere"') < html.index('id="obStepAccount"') < html.index('class="wiz-nav ob-foot"')
    assert '<section class="ob-step" id="obStepAccount" data-step="account" data-state="offer" style="display:none">' in html


# ── Settings, signed out: one clear flow ────────────────────────────────────

def test_signed_out_is_one_heading_one_field_one_button_and_one_plain_line():
    sec = section()
    out = sec[sec.index('id="acctOut"'):sec.index('id="acctIn"')]
    assert '<h2 class="acct-title" id="acctOutTitle">Create your account or sign in</h2>' in out
    email = out[out.index('id="acctEmailRow"'):out.index('id="acctCodeRow"')]
    assert email.count("<input") == 1 and email.count("<button") == 1
    assert '<button type="submit" class="btn btn-pri btn-sm" id="acctSend">Email me a code</button>' in email
    # One line under the field (the heading already says it creates or signs in).
    assert re.findall(r"<li>(.*?)</li>", email) == []
    assert '<div class="row-d acct-how" id="acctEmailDesc">No password. We email you a code.</div>' in email
    assert re.search(r'<input type="email" id="acctEmail"[^>]*aria-describedby="acctEmailDesc"', email)
    # That one address does both is said in full under "How it works".
    how = text_of(sec[sec.index('id="acctHow"'):])
    assert ("New here? Your email creates your free account. Already have one? It signs you in. "
            "There is no password: we email you a code.") in how
    assert "password" not in text_of(out).replace("No password.", "").lower()


def test_the_code_step_takes_a_paste_enter_a_resend_and_a_change_of_address():
    sec = section()
    code = sec[sec.index('id="acctCodeRow"'):sec.index('id="acctError"')]
    assert '<label for="acctCode" id="acctCodeLabel">Enter the code</label>' in code
    assert re.search(r'<form class="tx-add" id="acctCodeForm" novalidate>\s*<input type="text" id="acctCode"', code)
    assert '<button type="submit" class="btn btn-pri btn-sm" id="acctSignIn">Continue</button>' in code
    assert '<button type="button" class="link-btn" id="acctResend" disabled>Send it again</button>' in code
    assert '<button type="button" class="link-btn" id="acctOther">Use a different email</button>' in code
    page = code_only(read("account.js"))
    flow = page[page.index("function signInFlow("):]
    flow = flow[:flow.index("\n  }\n")]
    assert "addEventListener('submit'" in flow and "addEventListener('paste'" in flow
    assert "L.accountCode(" in flow and "L.accountCodeReady(" in flow
    assert "L.accountResendView(" in flow and "L.accountCodeLine(" in flow
    assert "account_start" in flow and "account_verify" in flow
    assert "L.accountSignedInLine(" in page


@needs_node
def test_send_it_again_waits_thirty_seconds():
    def view(seconds):
        return js(f"L.accountResendView({NOW} - {seconds} * 1000, {NOW})")
    assert js("L.ACCOUNT_RESEND_S") == 30
    assert view(0) == {"ready": False, "text": "Send it again in 30 seconds"}
    assert view(12.2) == {"ready": False, "text": "Send it again in 18 seconds"}
    assert view(29) == {"ready": False, "text": "Send it again in 1 second"}
    assert view(29.9) == {"ready": False, "text": "Send it again in 1 second"}
    assert view(30) == {"ready": True, "text": "Send it again"}
    assert view(600) == {"ready": True, "text": "Send it again"}
    assert view(-5) == {"ready": False, "text": "Send it again in 30 seconds"}, "a clock that moved back"
    assert js(f"L.accountResendView(0, {NOW})") == {"ready": True, "text": "Send it again"}, "nothing sent yet"


@needs_node
def test_what_the_page_says_at_each_point():
    assert js("L.accountCodeLine('ada@example.com', 8)") == \
        "Check ada@example.com for an 8-digit code. It works for 10 minutes."
    assert js("L.accountCodeLine('ada@example.com')") == \
        "Check ada@example.com for a 6-digit code. It works for 10 minutes.", "an older service does not say"
    assert js("L.accountCodeLine('', 6)") == "Check your email for a 6-digit code. It works for 10 minutes."
    assert js("L.accountSignedInLine(true)") == "Your account is ready."
    assert js("L.accountSignedInLine(false)") == "You’re signed in."
    assert js("L.accountSignedInLine(null)") == "You’re in."
    assert js("L.accountSignedInLine(undefined)") == "You’re in."


def test_settings_has_no_footer_to_say_anything_about_an_account():
    # The side menu ended in a mark, "Made by one person" and a line about the
    # account ("Free, no account needed" / "MIT licence, signed in"). It is gone,
    # with the code that wrote the line; nothing replaced it.
    html = read("index.html")
    for gone in ("snav-colophon", "snavAccountLine", "Made by one person", "no account needed",
                 "MIT licence, signed in", "MIT licence, no account<"):
        assert gone not in html, gone
    assert "accountFooterLine" not in read("logic.js")
    assert "accountFooterLine" not in read("account.js") and "snavAccountLine" not in read("account.js")


def test_with_no_service_nothing_offers_an_account():
    sec = section()
    off = sec[sec.index('id="acctOff"'):sec.index('id="acctOut"')]
    assert "Accounts are coming soon" in off and "<input" not in off and "<button" not in off
    html = read("index.html")
    assert 'id="acctOffer"' not in html, "the Home card is only ever made by the page, when it is due"


# ── Settings, signed in ─────────────────────────────────────────────────────

@needs_node
def test_the_sync_line_says_when():
    def line(sync):
        return js(f"L.accountSyncLine({sync}, {NOW})")
    last = "'2026-11-21T11:59:00.000+00:00'"
    assert line(f"{{syncing: false, last_synced_at: {last}, error: ''}}") == "Synced a minute ago."
    assert line(f"{{syncing: true, last_synced_at: {last}, error: ''}}") == "Syncing now."
    assert line("{syncing: false, last_synced_at: '', error: ''}") == "Not synced yet."


@needs_node
def test_each_device_says_when_it_was_last_used_and_this_computer_is_marked():
    def line(d):
        return js(f"L.accountDeviceLine({d}, {NOW})")
    assert line("{current: true, last_used_at: '2026-11-21T11:10:00+00:00'}") == "In use now"
    assert line("{current: false, last_used_at: '2026-11-21T09:00:00+00:00'}") == "Last used 3 hours ago"
    assert line("{current: false, last_used_at: '2026-11-16T09:00:00+00:00'}") == "Last used 5 days ago"
    assert line("{current: false, last_used_at: ''}") == "Signed in"
    page = code_only(read("account.js"))
    draw = page[page.index("function drawDevices("):]
    draw = draw[:draw.index("\n  }\n")]
    assert "'This computer'" in draw and "chip" in draw, "this computer carries a mark beside its name"
    assert "L.accountDeviceLine(d)" in draw


def test_signed_in_shows_sign_out_and_delete_with_what_each_leaves():
    sec = section()
    inn = sec[sec.index('id="acctIn"'):]
    assert '<p class="s-note" id="acctWelcome" role="status" hidden></p>' in inn
    assert ('<div class="row-t">Sign out</div><div class="row-d" id="acctSignOutDesc">Everything stays on '
            "this computer.</div>") in inn
    assert ('<div class="row-t">Delete my account</div><div class="row-d" id="acctDeleteDesc">For good. '
            "Meetings on this computer stay.</div>") in inn
    how = text_of(inn[inn.index('id="acctHow"'):])
    assert ("When you sign out, your meetings and dictations stay on this computer. They stop syncing until "
            "you sign in again.") in how
    assert ("Delete my account removes the account and everything stored in it, for good. Meetings already on "
            "this computer stay.") in how
    assert re.search(r'<button type="button" class="btn btn-sec btn-sm" id="acctSignOut" '
                     r'aria-describedby="acctSignOutDesc">Sign out</button>', inn)
    assert re.search(r'<button type="button" class="btn btn-danger btn-sm" id="acctDelete" '
                     r'aria-describedby="acctDeleteDesc" aria-expanded="false" '
                     r'aria-controls="acctDeleteConfirm">Delete my account…</button>', inn)
    ask = inn[inn.index('id="acctDeleteConfirm"'):]
    ask = ask[:ask.index("</form>")]
    said = text_of(ask[ask.index(">") + 1:])
    assert "Delete your account?" in said
    assert ("This deletes the account and everything stored in it on the server, for every device signed in "
            "to it. Meetings already on this computer stay. It can't be undone.") in said
    assert '<label for="acctDeleteWord">Type DELETE to confirm</label>' in ask
    assert re.search(r'<input type="text" id="acctDeleteWord" class="input input-mono"[^>]*autocomplete="off"', ask)
    assert '<button type="button" class="btn btn-sec btn-sm" id="acctDeleteNo">Keep my account</button>' in ask
    assert ('<button type="submit" class="btn btn-danger-solid btn-sm" id="acctDeleteYes" disabled hidden>'
            "Delete my account</button>") in ask
    # The word is only asked for by an older service; first comes "Email me a code to confirm"
    # (tests/test_account_contract.py).
    assert '<div class="row-d acct-word" id="acctDeleteWordAsk" hidden>' in ask
    assert re.search(r'<input type="text" id="acctDeleteWord"[^>]* hidden>', ask)
    assert re.search(r'<div class="row reset-confirm" id="acctDeleteConfirm" hidden>', inn)


@needs_node
def test_the_delete_button_wakes_only_for_the_word():
    for typed, ready in (("DELETE", True), (" delete ", True), ("Delete", True), ("DELET", False),
                         ("", False), ("DELETE it", False), ("yes", False)):
        assert js(f"L.accountDeleteReady({json.dumps(typed)})") is ready, typed
    assert js("L.accountDeleteReady(null)") is False


def test_deleting_from_the_page_goes_through_the_confirmation():
    page = code_only(read("account.js"))
    assert len(re.findall(r"'account_delete'", page)) == 1 and len(re.findall(r"'account_delete_start'", page)) == 1
    fn = page[page.index("async function deleteAccount("):]
    fn = fn[:fn.index("\n  }\n")]
    assert "L.accountDeleteReady(" in fn and fn.index("L.accountDeleteReady(") < fn.index("account_delete")
    assert "if (deleteStep === 'ask') return;" in fn, "nothing is deleted before the code or the word is asked for"
    assert "const word = deleteStep === 'word' ? $('acctDeleteWord').value : '';" in fn
    assert "call('account_delete', word, code)" in fn
    assert "$('acctDeleteYes').disabled = !L.accountDeleteReady(" in page
    assert "$('acctDeleteForm').addEventListener('submit'" in page
    assert "$('acctDelete').addEventListener('click'" in page
    assert "$('acctDelete').addEventListener('click', () => busy($('acctDelete'), deleteAccount))" not in page


# ── the three things that ask once before acting ────────────────────────────

@needs_node
def test_each_confirmation_says_what_will_happen():
    remove = js("L.accountConfirm('remove', 'iPhone 16')")
    assert remove == {"title": "Remove iPhone 16?",
                      "desc": "It is signed out at once. Meetings already on it stay there.",
                      "yes": "Remove", "no": "Keep it"}
    new = js("L.accountConfirm('new')")
    assert new == {"title": "Make a new link?",
                   "desc": "The link Claude uses now stops working at once. Paste the new one into Claude "
                           "to carry on.",
                   "yes": "Make a new link", "no": "Keep this link"}
    off = js("L.accountConfirm('off')")
    assert off == {"title": "Turn off the Claude link?",
                   "desc": "Claude can no longer read your meetings. You can make a new link any time.",
                   "yes": "Turn off", "no": "Keep it on"}
    assert js("L.accountConfirm('remove', '')")["title"] == "Remove this device?"
    assert js("L.accountConfirm('nonsense')") is None


def test_remove_new_link_and_turn_off_each_ask_first():
    page = code_only(read("account.js"))
    # The buttons open a question; only its Yes reaches the app.
    assert "remove.addEventListener('click', () => askRemove(d, row, remove))" in page
    assert "$('acctClaudeNew').addEventListener('click', () => askClaude('new'))" in page
    assert "$('acctClaudeOff').addEventListener('click', () => askClaude('off'))" in page
    for direct in ("busy(remove, () => removeDevice(d))",
                   "busy($('acctClaudeNew'), () => makeLink('account_connector_rotate'",
                   "busy($('acctClaudeOff'), turnOff)"):
        assert direct not in page, direct
    assert page.count("L.accountConfirm(") >= 2
    # Making the first link changes nothing that exists, so it does not ask.
    assert "$('acctClaudeCreate').addEventListener('click', () => busy($('acctClaudeCreate')" in page
    sec = section()
    ask = sec[sec.index('id="acctClaudeConfirm"'):]
    ask = ask[:ask.index("</div>\n            </div>")]
    assert re.search(r'<div class="row row-confirm" id="acctClaudeConfirm" hidden>', sec)
    for ident in ("acctClaudeConfirmTitle", "acctClaudeConfirmDesc", "acctClaudeNo", "acctClaudeYes"):
        assert f'id="{ident}"' in ask, ident
    assert "Escape" in page, "Esc closes a question without acting"


# ── keyboard and screen reader basics; light and dark ───────────────────────

def test_errors_are_alerts_statuses_are_statuses_and_every_field_has_a_label():
    for part in (section(), wizard_step()):
        for field in re.findall(r'<input [^>]*id="(\w+)"', part):
            assert f'<label for="{field}">' in part or re.search(rf'<label [^>]*for="{field}"', part), field
        for alert in re.findall(r'<p class="[^"]*" id="(\w+)" role="alert"', part):
            assert re.search(rf'id="{alert}" role="alert" hidden', part), alert
        assert not re.search(r"<div[^>]*onclick", part), "only buttons and links are pressed"
    page = code_only(read("account.js"))
    assert ".focus()" in page and "innerHTML" not in page


def test_the_new_pieces_use_the_themes_own_colours():
    css = code_only(read("style.css")) + code_only(read("setup.css"))
    mine = "\n".join(m.group(0) for m in re.finditer(r"[^{}]*\.(?:acct|ob-acct)-[^{}]*\{[^}]*\}", css))
    assert mine, "the account pieces have their own few rules"
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", mine), "no colour of its own: light and dark come from the tokens"
    assert "rgb(" not in mine and "rgba(" not in mine
