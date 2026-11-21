"""Settings, Account: the page and how it is wired (ui/account.js, the section
in ui/index.html, the small decisions in ui/logic.js, Api.account_* in app.py).

The decisions run in Node; the rest is read from the source, as the other
window tests do.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from test_ui_logic import code_only, js, needs_node, read  # noqa: E402

API_METHODS = ("account_status", "account_offer_dismiss", "account_start", "account_verify",
               "account_sign_out", "account_delete", "account_devices", "account_remove_device",
               "account_connector", "account_connector_create", "account_connector_rotate",
               "account_connector_off", "account_sync_now")
NOW = "Date.parse('2026-11-21T12:00:00.000+00:00')"


def section():
    html = read("index.html")
    start = html.index('<section class="s-sec" id="secAccount"')
    return html[start:html.index("</section>", start)]


# ── where it is ─────────────────────────────────────────────────────────────

def test_account_is_the_first_section_in_the_list():
    html = read("index.html")
    nav = html[html.index('aria-label="Settings sections"'):]
    nav = nav[:nav.index("</nav>")]
    items = re.findall(r'<button type="button" class="snav-item" data-sec="(\w+)" '
                       r"onclick=\"showSettingsSection\('(\w+)'\)\">([^<]+)</button>", nav)
    assert items[0] == ("account", "account", "Account")
    assert len(items) == html.count('class="snav-item"') == 12
    assert all(a == b for a, b, _ in items)
    sections = re.findall(r'<section class="s-sec" id="sec\w+" data-sec="(\w+)"', html)
    assert sections[0] == "account" and sections == [a for a, _, _ in items]
    assert '<section class="s-sec" id="secAccount" data-sec="account" hidden>' in html
    assert '<section class="s-sec" id="secGeneral" data-sec="general">' in html, "Settings still opens on General"


def test_the_window_knows_the_section_and_loads_it_when_opened():
    app = code_only(read("app.js"))
    assert ("const SETTINGS_SECTIONS = ['account', 'general', 'style', 'words', 'transforms', 'command', 'keys', "
            "'claude', 'hotkey', 'usage', 'privacy', 'about'];") in app
    assert "if (sec === 'account' && window.loadAccount) window.loadAccount();" in app
    html = read("index.html")
    assert '<script src="account.js"></script>' in html
    assert html.index('<script src="logic.js"></script>') < html.index('<script src="account.js"></script>')
    page = read("account.js")
    assert "window.loadAccount = " in page and "window.waffler_account_changed = " in page
    assert "window.waffler_account_changed && window.waffler_account_changed()" in read_app_py()


def read_app_py():
    return (ROOT / "app.py").read_text(encoding="utf-8")


def test_every_api_method_exists_and_the_page_calls_each():
    app_py, page = read_app_py(), code_only(read("account.js"))
    for method in API_METHODS:
        assert re.search(rf"\n    def {method}\(self[,)]", app_py), method
        assert re.search(rf"\b{method}\b", page), method
    called = set(re.findall(r"\b(account_[a-z_]+)\b", page))
    assert called == set(API_METHODS), "the page calls nothing the app does not have"


# ── the three faces: off, signed out, signed in ─────────────────────────────

def test_the_section_follows_the_settings_look():
    sec = section()
    assert '<h1 class="ptitle">Account</h1>' in sec and '<p class="pdesc">' in sec
    for ident in ("acctOff", "acctOut", "acctIn"):
        assert re.search(rf'<div id="{ident}"[^>]* hidden>', sec), ident
    assert sec.count('class="sheet"') >= 4 and 'class="row-t"' in sec and 'class="row-d"' in sec
    assert 'class="btn btn-pri btn-sm"' in sec
    assert not re.search(r'style="', sec), "no styling of its own"
    assert "—" not in sec and "–" not in sec
    ids = re.findall(r'id="(\w+)"', sec)
    assert len(ids) == len(set(ids))
    page = read("account.js")
    named = set(re.findall(r'(?:for|aria-describedby|aria-labelledby|aria-controls)="(\w+)"', sec))
    # The sign-in fields are reached by their ending: the same flow runs here
    # ('acct' + 'Email') and on setup's last screen ('obAcct' + 'Email').
    flow = page[page.index("function signInFlow("):]
    flow = flow[:flow.index("\n  }\n")]
    assert "const el = (name) => $(p + name);" in flow and "signInFlow('acct'," in page
    for ident in ids:
        # (acctHowBtn is the "How it works" disclosure: app.js toggleHow opens it.)
        if ident not in ("secAccount", "acctHowBtn") and ident not in named:
            assert f"'{ident}'" in page or f"el('{ident.removeprefix('acct')}')" in flow, \
                f"{ident} is in the page but account.js never uses it"
    for ident in named:
        assert ident in ids, f"{ident} is pointed at but is not in the section"
    assert "showToast(" in page


def test_with_no_service_the_page_says_accounts_are_coming_soon():
    sec = section()
    off = sec[sec.index('id="acctOff"'):sec.index('id="acctOut"')]
    assert "Accounts are coming soon" in off
    assert "<input" not in off and "<button" not in off


def test_signed_out_is_an_email_then_a_code_and_enter_works():
    sec = section()
    out = sec[sec.index('id="acctOut"'):sec.index('id="acctIn"')]
    # Two forms: Enter in a field submits it, and the button is the form's own.
    email = out[out.index('<form class="tx-add" id="acctEmailForm"'):]
    email = email[:email.index("</form>")]
    assert '<label for="acctEmail">' in out
    assert re.search(r'<input type="email" id="acctEmail" class="input"[^>]*autocomplete="email"', email)
    assert '<button type="submit" class="btn btn-pri btn-sm" id="acctSend">Email me a code</button>' in email
    code = out[out.index('<form class="tx-add" id="acctCodeForm"'):]
    code = code[:code.index("</form>")]
    assert '<label for="acctCode" id="acctCodeLabel">' in out
    assert re.search(r'<input type="text" id="acctCode" class="input input-mono"[^>]*inputmode="numeric"'
                     r'[^>]*autocomplete="one-time-code"', code)
    assert "maxlength" not in code, "a pasted '123 456' must not be cut short before it is cleaned"
    assert '<button type="submit" class="btn btn-pri btn-sm" id="acctSignIn">Continue</button>' in code
    assert re.search(r'<div class="row" id="acctCodeRow" hidden>', out), "the code comes after the email"
    assert '<p class="s-error" id="acctError" role="alert" hidden></p>' in out
    assert 'id="acctNotice" role="status"' in out
    page = code_only(read("account.js"))
    assert "el('EmailForm').addEventListener('submit'" in page
    assert "el('CodeForm').addEventListener('submit'" in page
    assert "el('Code').addEventListener('paste'" in page and "L.accountCode(" in page


def test_signed_in_shows_who_sync_devices_claude_and_sign_out():
    sec = section()
    inn = sec[sec.index('id="acctIn"'):]
    assert 'id="acctWho"' in inn
    assert '<div class="row-d" id="acctSync" role="status">' in inn
    assert '<button type="button" class="btn btn-sec btn-sm" id="acctSyncNow">Sync now</button>' in inn
    assert '<div class="sheet" id="acctDevices" role="list" aria-label="Devices signed in to your account">' in inn
    assert '<div class="row-d" id="acctClaudeState" role="status">' in inn
    for ident, label in (("acctClaudeCreate", "Create link"), ("acctClaudeCopy", "Copy"),
                         ("acctClaudeNew", "New link"), ("acctClaudeOff", "Turn off"),
                         ("acctSignOut", "Sign out")):
        assert re.search(rf'<button type="button" class="btn btn-\w+ btn-sm" id="{ident}"[^>]*>{label}</button>',
                         inn), ident
    assert '<p class="s-note" id="acctClaudeNote">Keep the link secret, and paste it only into Claude.</p>' in inn
    # Why, in full, is under "How it works".
    how = inn[inn.index('id="acctHow"'):]
    assert ("The Claude link is a secret. Anyone who has it can read every meeting in your account, so paste "
            "it only into Claude.") in how
    assert '<p class="s-error" id="acctInError" role="alert" hidden></p>' in inn


def test_the_page_never_puts_the_link_or_a_name_into_markup():
    """A device's name comes from the service: it is set as text, never as HTML.
    The Claude link is copied, never drawn (the window may be on a shared screen)."""
    page = code_only(read("account.js"))
    assert "innerHTML" not in page
    assert "textContent = link" not in page and "value = link" not in page
    assert "copy_item(link)" in page
    assert "localStorage" not in page and "sessionStorage" not in page


def test_each_remove_button_names_its_device_and_this_computer_has_none():
    page = code_only(read("account.js"))
    assert "'Remove ' + d.name" in page and "setAttribute('aria-label'" in page
    assert "if (!d.current)" in page
    assert "setAttribute('role', 'listitem')" in page


# ── the small decisions (ui/logic.js) ───────────────────────────────────────

@needs_node
def test_which_face_is_shown():
    assert js("L.accountView(null)") == "off"
    assert js("L.accountView({ok: true, available: false, signed_in: false})") == "off"
    assert js("L.accountView({ok: true, available: true, signed_in: false})") == "out"
    assert js("L.accountView({ok: true, available: true, signed_in: true})") == "in"
    assert js("L.accountView({ok: false, error: 'x'})") == "off"


@needs_node
def test_a_typed_or_pasted_code_becomes_its_six_digits():
    assert js("L.accountCode('123456')") == "123456"
    assert js("L.accountCode(' 123 456 ')") == "123456"
    assert js("L.accountCode('123-456')") == "123456"
    assert js("L.accountCode('Your Waffler sign-in code is: 482913. It works once and for 10 minutes.')") == "482913"
    assert js("L.accountCode('Use within 10 minutes: 482 913')") == "482913"
    assert js("L.accountCode('12ab34')") == "1234"
    assert js("L.accountCode('1234567')") == "123456"
    assert js("L.accountCode(null)") == "" and js("L.accountCode('abc')") == ""
    assert js("L.accountCodeReady('482913')") is True and js("L.accountCodeReady('48291')") is False


@needs_node
def test_how_long_ago_in_plain_words():
    def ago(seconds):
        return js(f"L.accountAgo(new Date({NOW} - {seconds} * 1000).toISOString(), {NOW})")
    assert ago(5) == "just now"
    assert ago(60) == "a minute ago"
    assert ago(240) == "4 minutes ago"
    assert ago(3600) == "an hour ago"
    assert ago(3 * 3600) == "3 hours ago"
    assert ago(30 * 3600) == "yesterday"
    assert ago(5 * 86400) == "5 days ago"
    assert ago(-30) == "just now", "a clock a little ahead is not 'in the future'"
    assert js(f"L.accountAgo('', {NOW})") == "" and js(f"L.accountAgo('nonsense', {NOW})") == ""
    # The service writes +00:00, never Z; both are read.
    assert js(f"L.accountAgo('2026-11-21T11:59:00.000+00:00', {NOW})") == "a minute ago"


@needs_node
def test_the_sync_line():
    def line(sync):
        return js(f"L.accountSyncLine({sync}, {NOW})")
    last = "'2026-11-21T11:59:00.000+00:00'"
    assert line(f"{{syncing: false, last_synced_at: {last}, error: ''}}") == "Synced a minute ago."
    assert line(f"{{syncing: true, last_synced_at: {last}, error: ''}}") == "Syncing now."
    assert line("{syncing: false, last_synced_at: '', error: ''}") == "Not synced yet."
    assert line(f"{{syncing: false, last_synced_at: {last}, error: 'No internet.'}}") == \
        "Not syncing. No internet. Last synced a minute ago."
    assert line("{syncing: false, last_synced_at: '', error: 'No internet.'}") == "Not syncing. No internet."
    assert line("null") == "Not synced yet."


@needs_node
def test_a_devices_second_line():
    assert js(f"L.accountDeviceLine({{current: true, last_used_at: 'x'}}, {NOW})") == "In use now"
    assert js(f"L.accountDeviceLine({{current: false, last_used_at: '2026-11-21T09:00:00+00:00'}}, {NOW})") == \
        "Last used 3 hours ago"
    assert js(f"L.accountDeviceLine({{current: false, last_used_at: ''}}, {NOW})") == "Signed in"


@needs_node
def test_the_claude_row_offers_what_fits():
    def view(connector, link="''"):
        return js(f"L.accountClaudeView({connector}, {link})")
    off = view("{ok: true, exists: false}")
    assert off["buttons"] == ["create"] and off["text"].startswith("Off.")
    fresh = view("{ok: true, exists: true}", "'https://x/connect/natterc_a/mcp'")
    assert fresh["buttons"] == ["copy", "new", "off"] and "natterc_" not in fresh["text"]
    assert "Copy" in fresh["text"] and "custom connector" in fresh["text"]
    old = view("{ok: true, exists: true}")
    assert old["buttons"] == ["new", "off"], "a link made earlier cannot be copied again: the service shows it once"
    assert "new link" in old["text"] and "stops working" in old["text"]
    failed = view("{ok: false, error: 'No internet.'}")
    assert failed == {"text": "No internet.", "buttons": []}
    assert view("null") == {"text": "Checking.", "buttons": []}
