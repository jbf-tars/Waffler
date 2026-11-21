"""Waffler Plus in the window: shown and usable only by an account that has it.

Settings, Account says "Waffler Plus" in one quiet line; Keys and providers
gains a third card above the other two, selectable like them. For a free or
signed-out account neither exists in the page at all: the page as written
(ui/index.html) says nothing of Plus, and what the scripts add is added only
for a signed-in account whose plan is "plus". Nothing anywhere offers to sell
it: Plus is given by the owner by hand.

The page's decisions are functions in ui/logic.js (run in Node); the card is
drawn by ui/howitruns.js, run here in Node against a small stand-in for the
page. The Api methods are lifted out of app.py, as the other bridge tests do.
"""
import ast
import json
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import local_stt  # noqa: E402
from _account_fake import FakeAccountServer  # noqa: E402
from _settings_fake import settings_api  # noqa: E402
from src import account  # noqa: E402
from test_local_stt_api import _api  # noqa: E402
from test_ui_logic import NODE, code_only, js, needs_node, read  # noqa: E402

SERVER = "https://notes.example.test"
EMAIL = "ada@example.com"
NOW = "Date.parse('2026-11-21T12:00:00.000+00:00')"
FREE = "{ok: true, available: true, signed_in: true, email: 'ada@example.com', plan: 'free', plus_until: null, plus_state: 'ended', plus_chosen: false}"
OLDER = "{ok: true, available: true, signed_in: true, email: 'ada@example.com'}"
OUT = "{ok: true, available: true, signed_in: false, plan: 'free', plus_state: 'signed_out'}"
PLUS = "{ok: true, available: true, signed_in: true, plan: 'plus', plus_until: null, plus_state: 'ready', plus_chosen: false}"
PLUS_UNTIL = "{ok: true, available: true, signed_in: true, plan: 'plus', plus_until: '2027-01-31T12:00:00+00:00', plus_state: 'ready', plus_chosen: true}"
SELLING = ("buy", "upgrade", "subscribe", "subscription", "price", "pricing", "trial", "per month", "£", "$")


@pytest.fixture
def server(monkeypatch, _isolated_data_dir):
    fake = FakeAccountServer(contract="new")
    monkeypatch.setenv(account.SERVER_ENV, SERVER)
    monkeypatch.setattr(account, "_transport", fake.transport())
    monkeypatch.setattr(account, "_notice", "")
    monkeypatch.setattr(account, "_plan", {})
    assert account.start(EMAIL)["ok"]
    assert account.verify(EMAIL, fake.mailbox[EMAIL], "Ada's Mac")["ok"]
    fake.data = _isolated_data_dir
    return fake


# ── the account status the page polls ───────────────────────────────────────

def status_api(server, inline=True):
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    names = ("_account_mod", "_account_sync_state", "_account_call", "_account_plan_soon")
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    nodes += [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "account_status"]
    changed, logged = [], []

    class Now:                                            # a thread that has already run
        def __init__(self, target, **kw):
            self.target = target

        def start(self):
            self.target()
    ns = {"_log_to_file": logged.append, "_meetings": None, "_account_changed": lambda: changed.append(1),
          "threading": type("T", (), {"Thread": Now}) if inline else threading}
    exec(compile(ast.Module(nodes, []), "<app.py account>", "exec"), ns)
    api = type("Api", (), {"account_status": ns["account_status"],
                           "_load_settings_file": lambda self: {}})()
    api.changed, api.logged = changed, logged
    return api


def test_the_status_carries_the_plan_and_asks_the_service_when_it_is_old(server):
    api = status_api(server)
    server.give_plus(EMAIL, "2031-01-31T12:00:00+00:00")
    first = api.account_status()
    assert first["plan"] == "free", "what this computer knew when it was asked"
    assert len(server.calls("GET", "/account")) == 1 and api.changed == [1], "asked, and the page told to redraw"
    s = api.account_status()
    assert (s["plan"], s["plus_until"], s["plus_state"], s["plus_chosen"]) == (
        "plus", "2031-01-31T12:00:00+00:00", "ready", False)
    api.account_status()
    assert len(server.calls("GET", "/account")) == 1, "not again until it is old"
    assert not any("natter_" in line or EMAIL in line for line in api.logged)


def test_a_free_account_and_a_signed_out_computer_say_free(server):
    api = status_api(server)
    api.account_status()
    assert api.account_status()["plan"] == "free" and api.changed == [], "free before, free after: no redraw"
    account.sign_out()
    before = len(server.requests)
    s = api.account_status()
    assert (s["plan"], s["plus_state"]) == ("free", "signed_out") and len(server.requests) == before


# ── choosing it: stored the way the mode is ─────────────────────────────────

def test_only_an_account_that_has_plus_can_choose_it(server, tmp_path, monkeypatch):
    ns = _api("save_settings", _account_mod=lambda: account)
    api = settings_api(tmp_path, {"language": "en", "transcription_mode": "local"},
                       _apply_transcription_mode=lambda mode: None)
    account.refresh_plan()
    r = ns["save_settings"](api, {"dictation_plus": True, "language": "fr"})
    assert r == {"ok": False, "error": account.PLUS_REQUIRED}
    assert api.data == {"language": "en", "transcription_mode": "local"} and api.saves == 0
    server.give_plus(EMAIL)
    account.refresh_plan()
    assert ns["save_settings"](api, {"dictation_plus": True})["ok"] is True
    assert api.data == {"language": "en", "transcription_mode": "local", "dictation_plus": True}
    assert account.PLUS_SETTING == "dictation_plus" and account.plus_chosen(api.data)
    assert local_stt.mode_is_local(api.data), "what ran before is still there to fall back to"
    assert ns["save_settings"](api, {"dictation_plus": "yes"}) == {"ok": False, "error": account.FAILED}
    account.sign_out()
    assert ns["save_settings"](api, {"dictation_plus": True})["ok"] is False


def test_choosing_another_way_or_saying_false_unchooses_it(server, tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_not_a_real_key")
    ns = _api("save_settings", _account_mod=lambda: account)
    api = settings_api(tmp_path, {"dictation_plus": True}, _apply_transcription_mode=lambda mode: None)
    assert ns["save_settings"](api, {"auto_paste": False})["ok"] is True
    assert api.data == {"dictation_plus": True, "auto_paste": False}, "another setting leaves it alone"
    assert ns["save_settings"](api, {"transcription_mode": "cloud"})["ok"] is True
    assert api.data == {"auto_paste": False, "transcription_mode": "cloud"}, "one way is chosen"
    api = settings_api(tmp_path / "b", {"dictation_plus": True})
    assert ns["save_settings"](api, {"dictation_plus": False})["ok"] is True
    assert api.data == {}


def test_a_plus_dictation_needs_no_key_and_no_model_to_start(server, monkeypatch):
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_plus_chosen_here")
    ns = {"_account_mod": lambda: account}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    here = ns["_plus_chosen_here"]
    assert here() is False
    (server.data / "settings.json").write_text(json.dumps({"dictation_plus": True}), encoding="utf-8")
    assert here() is True
    account.sign_out()
    assert here() is False, "signed out: it is the key or the model again"
    ns["_account_mod"] = lambda: 1 / 0
    assert here() is False, "never raises"
    # Everywhere "on this computer" lets Waffler start with no key, Plus does too.
    assert src.count("_local_stt.mode_is_local() and not _plus_chosen_here()") == 2
    assert src.count("(config.has_api_key or _local_stt.mode_is_local() or _plus_chosen_here())") == 2
    status = src[src.index("    def get_onboarding_status(self)"):]
    assert "can_transcribe = _plus_chosen_here()" in status[:2500]


# ── the page as written says nothing of Plus ────────────────────────────────

def test_the_page_as_written_has_nothing_about_plus():
    html = read("index.html")
    assert "Plus" not in html and "hirPlus" not in html and "acctPlan" not in html
    assert not re.search(r"\bplus\b", html.replace("#i-plus", ""), flags=re.I), "but for the + icon"
    # Nothing in the window sells it, to anyone.
    for name in ("logic.js", "howitruns.js", "account.js"):
        src = code_only(read(name))
        for at in [m.start() for m in re.finditer(r"Waffler Plus", src)]:
            near = src[max(0, at - 200):at + 200].lower()
            assert not any(word in near for word in SELLING if word != "$"), name   # "$" is JavaScript's own


@needs_node
def test_the_account_line_is_there_only_for_plus():
    def line(acct):
        return js(f"L.accountPlanLine({acct}, {NOW})")
    assert line(PLUS) == "Waffler Plus"
    assert line(PLUS_UNTIL) == "Waffler Plus until 31 Jan"
    assert line(PLUS.replace("plus_until: null", "plus_until: '2028-03-05T12:00:00+00:00'")) \
        == "Waffler Plus until 5 Mar 2028", "an end more than a year off says which year"
    assert line(PLUS.replace("plus_until: null", "plus_until: 'soon'")) == "Waffler Plus"
    for acct in (FREE, OLDER, OUT, "null", "{ok: false, error: 'x'}",
                 PLUS.replace("signed_in: true", "signed_in: false"),
                 PLUS.replace("available: true", "available: false")):
        assert line(acct) == "", acct
    assert len(line(PLUS_UNTIL).split()) <= 9


def test_the_account_page_makes_the_line_and_takes_it_away():
    page = code_only(read("account.js"))
    draw = page[page.index("function drawPlan("):]
    draw = draw[:draw.index("\n  }\n")]
    assert "L.accountPlanLine(s)" in draw and "if (!line) { if (plan) plan.remove(); return; }" in draw
    assert "plan.id = 'acctPlan'" in draw and "plan.textContent = line;" in draw, "set as text, never as HTML"
    assert "plan.className = 'row-d acct-plan'" in draw, "the quiet look of every line under a label"
    assert "innerHTML" not in draw
    body = page[page.index("function draw(s) {"):page.index("function drawSync()")]
    assert "drawPlan(s);" in body and "window.wafflerPlusChanged" in body


@needs_node
def test_the_card_is_there_only_for_plus_and_its_words_are_few():
    def card(acct):
        return js(f"L.plusCardView({acct})")
    for acct in (FREE, OLDER, OUT, "null", "undefined", "{ok: false}",
                 PLUS.replace("signed_in: true", "signed_in: false")):
        assert card(acct) is None, acct
    assert card(PLUS) == {"label": "Waffler Plus", "helper": "No key. No download.", "state": "Ready",
                          "chosen": False}
    assert card(PLUS_UNTIL)["chosen"] is True
    states = {"ready": "Ready", "unknown": "Ready", "offline": "Can’t reach the service",
              "signed_out": "Signed out", "ended": "Plan ended"}
    for word, line in states.items():
        got = card(PLUS.replace("plus_state: 'ready'", f"plus_state: '{word}'"))
        assert got["state"] == line and len(line.split()) <= 9
    v = card(PLUS)
    assert len(v["label"].split()) <= 4 and len(v["helper"].split()) <= 9
    assert not any(w in json.dumps(v).lower() for w in SELLING)


# ── the card, drawn by howitruns.js against a stand-in for the page ─────────

DOM = r"""
class El {
  constructor(tag) { this.tag = tag; this.kids = []; this.parent = null; this.id = ''; this.className = '';
    this.own = ''; this.hidden = false; this.style = {}; this.on = {}; this.attrs = {}; this.checked = false;
    this.disabled = false; this.dataset = {};
    const self = this;
    this.classList = { toggle(c, want) { const have = self.className.split(' ').filter(Boolean);
      const has = have.includes(c); const to = want === undefined ? !has : !!want;
      self.className = (to ? (has ? have : have.concat(c)) : have.filter((x) => x !== c)).join(' '); },
      contains(c) { return self.className.split(' ').includes(c); } };
  }
  get textContent() { return this.own + this.kids.map((k) => k.textContent).join(''); }
  set textContent(v) { this.kids = []; this.own = String(v); }
  append(...els) { els.forEach((e) => { e.parent = this; this.kids.push(e); }); }
  prepend(...els) { els.forEach((e) => { e.parent = this; }); this.kids.unshift(...els); }
  after(e) { e.parent = this.parent; this.parent.kids.splice(this.parent.kids.indexOf(this) + 1, 0, e); }
  remove() { if (this.parent) this.parent.kids = this.parent.kids.filter((k) => k !== this); this.parent = null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(name, fn) { (this.on[name] = this.on[name] || []).push(fn); }
  fire(name) { (this.on[name] || []).forEach((fn) => fn({ target: this })); }
  find(id) { if (this.id === id) return this; for (const k of this.kids) { const f = k.find(id); if (f) return f; } return null; }
  all() { return [this].concat(...this.kids.map((k) => k.all())); }
}
function tree(spec) { const [tag, id, kids] = spec; const el = new El(tag); el.id = id; (kids || []).forEach((k) => el.append(tree(k))); return el; }
const body = tree(['body', '', [['div', 'howItRuns', [
  ['div', 'hirLocal', [['input', 'hirLocalRadio'], ['span', 'hirState'], ['span', 'hirBar', [['i', 'hirBarFill']]],
                       ['button', 'hirDownload'], ['button', 'hirCancel'], ['button', 'hirRemove']]],
  ['div', 'hirCloud', [['input', 'hirCloudRadio']]]]]]]);
const ready = [], toasts = [], saves = [];
const document = { getElementById: (id) => body.find(id), createElement: (tag) => new El(tag),
  addEventListener: (name, fn) => { if (name === 'DOMContentLoaded') ready.push(fn); } };
const state = { acct: ACCT, mode: 'cloud' };
const api = {
  local_stt_status: async () => ({ ok: true, mode: state.mode, available: true, state: 'ready', percent: 100, bytes_total: 574e6, size_on_disk: 574e6, error: '' }),
  account_status: async () => state.acct,
  save_settings: async (v) => { saves.push(v);
    if (v.dictation_plus) state.acct = Object.assign({}, state.acct, { plus_chosen: true });
    if (v.transcription_mode) { state.mode = v.transcription_mode; state.acct = Object.assign({}, state.acct, { plus_chosen: false }); }
    return { ok: true }; },
};
const win = { WafflerLogic: L, pywebview: { api }, setTimeout, clearTimeout, confirm: () => true };
require('vm').runInNewContext(require('fs').readFileSync(HIR, 'utf8'),
  { window: win, document, showToast: (m) => toasts.push(m), setTimeout, clearTimeout });
ready.forEach((fn) => fn());
const settle = () => new Promise((r) => setTimeout(r, 30));
const snap = () => { const card = body.find('hirPlus'); return {
  cards: body.find('howItRuns').kids.map((k) => k.id),
  text: body.textContent, ids: body.all().map((e) => e.id).filter(Boolean),
  card: card && { cls: card.className, text: card.kids.map((k) => k.textContent),
    radio: (() => { const r = body.find('hirPlusRadio'); return { type: r.type, name: r.name, value: r.value, checked: r.checked, disabled: r.disabled }; })(),
    stateRole: body.find('hirPlusState').attrs.role },
  local: body.find('hirLocalRadio').checked, cloud: body.find('hirCloudRadio').checked,
  localOn: body.find('hirLocal').classList.contains('is-on'), cloudOn: body.find('hirCloud').classList.contains('is-on') }; };
"""


def page(acct, steps="", result="snap()"):
    """Run ui/howitruns.js for an account, then `steps`; answers `result`."""
    code = (f"const L = require({json.dumps(str(ROOT / 'ui' / 'logic.js'))});\n"
            f"const HIR = {json.dumps(str(ROOT / 'ui' / 'howitruns.js'))};\nconst ACCT = {acct};\n{DOM}\n"
            f"(async () => {{ win.loadHowItRuns(); await settle(); {steps}\n"
            f"process.stdout.write(JSON.stringify({result})); }})();")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
@pytest.mark.parametrize("acct", [FREE, OLDER, OUT, "{ok: false, error: 'x'}", "null"])
def test_for_a_free_or_signed_out_account_the_card_does_not_exist(acct):
    got = page(acct)
    assert got["cards"] == ["hirLocal", "hirCloud"] and got["card"] is None
    assert "Plus" not in got["text"] and not any("Plus" in i for i in got["ids"]), "nothing mentions Plus"
    assert got["cloud"] is True and got["local"] is False, "the two ways are as they always were"


@needs_node
def test_for_a_plus_account_the_card_is_first_and_can_be_chosen():
    got = page(PLUS)
    assert got["cards"] == ["hirPlus", "hirLocal", "hirCloud"], "above the other two"
    card = got["card"]
    assert card["text"] == ["Waffler Plus", "No key. No download.", "Ready"]
    assert card["radio"] == {"type": "radio", "name": "hirMode", "value": "plus", "checked": False,
                             "disabled": False}, "one of the same group: the arrow keys reach it"
    assert card["stateRole"] == "status" and "is-on" not in card["cls"] and "hir-card" in card["cls"]
    assert got["cloud"] is True
    # Chosen: saved the way the mode is, and the other two are no longer the one.
    got = page(PLUS, "body.find('hirPlusRadio').fire('change'); await settle();",
               "Object.assign(snap(), {saves, toasts})")
    assert got["saves"] == [{"dictation_plus": True}]
    assert got["card"]["radio"]["checked"] is True and "is-on" in got["card"]["cls"]
    assert (got["local"], got["cloud"], got["localOn"], got["cloudOn"]) == (False, False, False, False)
    assert got["toasts"] == ["Dictation runs on Waffler Plus now."]
    assert got["cards"].count("hirPlus") == 1, "drawn again, never added twice"


@needs_node
def test_another_way_can_be_chosen_back_and_the_card_goes_when_plus_does():
    got = page(PLUS_UNTIL, "body.find('hirCloudRadio').fire('change'); await settle();",
               "Object.assign(snap(), {saves})")
    assert got["saves"] == [{"transcription_mode": "cloud"}]
    assert got["card"]["radio"]["checked"] is False and got["cloud"] is True and got["cloudOn"] is True
    # The plan ended (or the computer was signed out): the page is told, and the card is gone.
    got = page(PLUS_UNTIL, f"state.acct = {FREE}; win.wafflerPlusChanged(state.acct); await settle();")
    assert got["cards"] == ["hirLocal", "hirCloud"] and "Plus" not in got["text"]
    assert got["cloud"] is True, "what it fell back to is the one shown as chosen"


@needs_node
def test_the_card_says_why_it_is_not_ready():
    got = page(PLUS.replace("plus_state: 'ready'", "plus_state: 'offline'"))
    assert got["card"]["text"][2] == "Can’t reach the service"
    assert got["card"]["radio"]["disabled"] is False, "still selectable: the next dictation tries again"


def test_the_few_words_rules_still_hold_for_the_page_as_written():
    import test_few_words
    assert 'name="hirMode"' in read("index.html")
    card = code_only(read("howitruns.js"))
    assert "radio.name = 'hirMode'" in card and "$('howItRuns').prepend(card)" in card
    assert "innerHTML" not in card
    assert test_few_words.SECTIONS  # the static rules are test_few_words' own; the card's words are checked above
