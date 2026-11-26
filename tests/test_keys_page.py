"""Settings, Keys and providers: two choices in view, the rest under Advanced.

The page was a pipeline diagram, a list of three providers and their order,
and two cards. It is now the two cards alone: "On this computer" (the
download's state and one button) and "Your own key" (one provider at a time, a
masked box, and what the provider said). Nothing was taken out of the product:
every control the page had is still on it, in view or under the one collapsed
"Advanced", and every call it makes to the app is one the app already had.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_few_words import Page

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run the page's scripts")


def read(name):
    return (UI / name).read_text(encoding="utf-8")


def func(src, name):
    body = src[src.index(f"function {name}("):]
    return body[:body.index("\n}\n")]


def section():
    """Every element of the section as written: its id, and whether it is under Advanced."""
    page = Page()
    page.feed(read("index.html"))
    return {el["id"]: "keysAdv" in el["in_ids"] for el in page.found if el["id"] and "secKeys" in el["in_ids"]}


# What the page had before it was cut down (ui/index.html at 9246d91), by id.
IN_VIEW = ("howItRuns", "hirLocal", "hirCloud", "hirModel", "hirState", "hirBar", "hirBarFill",
           "hirLlm", "hirLlmT", "hirLlmAbout", "hirLlmState", "hirLlmBar", "hirLlmBarFill", "hirLlmPick",
           "groqKeyEditor", "apiKeyEditor", "cerebrasKeyEditor")
UNDER_ADVANCED = ("keysFlow", "providerOrderList", "providerOrderLive", "keysHow", "hirNote", "hirLlmHow")
# Void elements (the parser above keeps none): looked for in the page's text.
INPUTS = ("hirLocalRadio", "hirCloudRadio", "hirLlmChoice", "groqKeyInput", "apiKeyInput", "cerebrasKeyInput")
BUTTONS = ("hirDownload", "hirCancel", "hirRemove", "hirLlmDownload", "hirLlmCancel", "hirLlmRemove")


def test_everything_the_page_had_is_still_on_it():
    where = section()
    for ident in IN_VIEW + BUTTONS:
        assert where.get(ident) is False, f"{ident}: in view"
    for ident in UNDER_ADVANCED:
        assert where.get(ident) is True, f"{ident}: under Advanced"
    html = read("index.html")
    start = html.index('id="secKeys"')
    sec = html[start:html.index("</section>", start)]
    view = sec[:sec.index('<div class="how">')]
    for ident in INPUTS:
        assert f'id="{ident}"' in view, f"{ident}: in view"
    # Each provider: its box, its Save, its Cancel and the link to where a key is made.
    for provider, save in (("groq", "saveGroqKey"), ("openai", "saveApiKey"), ("cerebras", "saveCerebrasKey")):
        assert f'onclick="{save}(this)"' in view, provider
        assert f"onclick=\"openKeyPage('{provider}'); return false;\"" in view, provider
    assert view.count('onclick="closeKeyEditor(true)">Cancel</button>') == 3
    assert ">Get a free Groq key</a>" in view, "the old foot of the page, now under the Groq box"
    # The list under Advanced is drawn by the page: its handle, its place, its state and its key button.
    row = func(read("app.js"), "renderProviderOrder")
    for part in ('class="grip"', 'class="po-rank"', "WL.providerPill(r)", 'class="mkey"', "po-btn-key",
                 "openKeyEditor('${r.id}')", "_onGripKey", "_onGripDown", "escHtml(r.desc)"):
        assert part in row, part
    assert "document.getElementById('keysFlow')" in func(read("app.js"), "_renderBackendInfo")


def test_the_page_calls_the_app_the_way_it_always_did():
    app, hir = read("app.js"), read("howitruns.js")
    for call in ("a.local_stt_status()", "a.local_stt_download()", "a.local_stt_cancel()", "a.local_stt_remove()",
                 "a.save_settings({ transcription_mode: mode })", "a.local_llm_status()", "a.local_llm_download()",
                 "a.local_llm_cancel()", "a.local_llm_remove()",
                 "a.save_settings({ writing_mode: on ? 'local' : 'cloud' })"):
        assert call in hir, call
    assert "pywebview.api.save_settings({ groq_key: val })" in func(app, "saveGroqKey")
    assert "pywebview.api.save_settings({ api_key: val })" in func(app, "saveApiKey")
    assert "pywebview.api.validate_cerebras_key(val)" in func(app, "saveCerebrasKey")
    assert "pywebview.api.save_settings({ provider_order: _providerOrder })" in func(app, "saveProviderOrder")
    # The check before saving uses the app's own methods, which setup already used: nothing new in app.py.
    api = (ROOT / "app.py").read_text(encoding="utf-8")
    for method in ("validate_groq_key", "validate_api_key", "validate_cerebras_key", "local_stt_status",
                   "local_stt_download", "local_stt_cancel", "local_stt_remove", "local_llm_status",
                   "local_llm_download", "local_llm_cancel", "local_llm_remove", "save_settings", "get_settings",
                   "open_url"):
        assert f"    def {method}(self" in api, method


def test_a_key_is_checked_with_its_provider_and_then_saved_as_before():
    app = read("app.js")
    groq, openai = func(app, "saveGroqKey"), func(app, "saveApiKey")
    assert groq.index("_keyChecked('groq', inp, () => pywebview.api.validate_groq_key(val))") \
        < groq.index("save_settings({ groq_key: val })")
    assert openai.index("_keyChecked('openai', inp, () => pywebview.api.validate_api_key(val))") \
        < openai.index("save_settings({ api_key: val })")
    for body, name, provider in ((groq, "Groq", "groq"), (openai, "OpenAI", "openai"),
                                 (func(app, "saveCerebrasKey"), "Cerebras", "cerebras")):
        assert f"await _afterKeySaved(inp, '{name}', '{provider}', val)" in body, \
            "saved: shown at once, used at once (no restart)"
        assert f"WL.keyEmptyLine('{name}', WL.providerHasKey('{provider}', _lastSettings))" in body
    checked = func(app, "_keyChecked")
    assert "_keyVerdicts[provider] = { ok: true };" in checked
    assert "_keyVerdicts[provider] = { ok: false, line: WL.keyRefusedLine(msg) };" in checked
    assert "showToast(msg, 'error', 6000);" in checked, "the whole sentence, as Cerebras always gave it"
    assert "return null;" in checked
    # The page remembers what was said, never the key.
    assert not re.search(r"_keyVerdicts\[[^\]]+\]\s*=\s*\{[^}]*\b(val|key|value)\b", app)


def test_a_key_is_never_shown():
    html = read("index.html")
    for ident in ("groqKeyInput", "apiKeyInput", "cerebrasKeyInput"):
        box = re.search(rf'<input [^>]*id="{ident}"[^>]*>', html).group(0)
        assert 'type="password"' in box and 'autocomplete="off"' in box and 'placeholder="Paste your key"' in box
        assert 'aria-describedby="keyState"' in box, "the line under it is read with it"
    card = func(read("app.js"), "renderKeyCard")
    assert "input.placeholder = v.placeholder;" in card and ".value =" not in card.replace("sel.value = v.id;", "")
    assert '<span class="key-state" id="keyState" role="status" hidden></span>' in html


@needs_node
def test_the_card_shows_one_provider_and_the_others_are_behind_the_picker():
    html = read("index.html")
    picker = html[html.index('<select id="keyProvider"'):]
    picker = picker[:picker.index("</select>")]
    assert re.findall(r'<option value="(\w+)">([^<]+)</option>', picker) == [
        ("groq", "Groq"), ("openai", "OpenAI"), ("cerebras", "Cerebras")]
    assert 'onchange="pickKeyProvider(this.value)"' in picker and 'aria-label="Provider"' in picker
    assert html.count('data-provider="openai" hidden>') == 1 and html.count('data-provider="cerebras" hidden>') == 1
    card = func(read("app.js"), "renderKeyCard")
    assert "ed.hidden = id !== v.id;" in card

    def js(expr):
        code = f"const L = require({json.dumps(str(UI / 'logic.js'))});\nprocess.stdout.write(JSON.stringify({expr}));"
        out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)
    # It opens on the first provider in the person's order that has a key, else the first.
    assert js("L.keyCardPick(null, null)") == "groq"
    assert js("L.keyCardPick(['cerebras','groq','openai'], {})") == "cerebras"
    assert js("L.keyCardPick(null, {api_key_set: true, cerebras_key_set: true})") == "openai"
    v = js("L.keyCardView('openai', null, {api_key_set: true, api_key_masked: 'sk-proj-…0jUe'}, null, false)")
    assert (v["id"], v["name"], v["hasKey"], v["placeholder"]) == ("openai", "OpenAI", True, "sk-proj-…0jUe")
    assert js("L.keyCardView('nobody', null, {}, null, false)")["id"] == "groq", "an unknown name is the first row"
    assert js("L.keyCardView('cerebras', null, {}, null, false)")["placeholder"] == "Paste your key"


# The card drawn for real: the functions lifted out of app.js and run in Node
# against a stand-in page and a stand-in app, from no key to refused to working.
_CARD = r"""
const WL = require(LOGIC);
const mk = (id) => ({ id, hidden: false, value: '', textContent: '', className: '', placeholder: '', dataset: {}, attrs: {},
  classList: { on: {}, toggle(c, v) { this.on[c] = !!v; } },
  setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; }, focus() { focused = id; },
  querySelector() { return null; }, closest() { return null; }, parentElement: null });
let focused = '';
const els = {};
['keyProvider', 'keyCard', 'keyState'].forEach((id) => { els[id] = mk(id); });
const _KEY_EDITORS = { groq: 'groqKeyEditor', openai: 'apiKeyEditor', cerebras: 'cerebrasKeyEditor' };
const INPUTS = { groq: 'groqKeyInput', openai: 'apiKeyInput', cerebras: 'cerebrasKeyInput' };
Object.entries(_KEY_EDITORS).forEach(([p, id]) => {
  const ed = els[id] = mk(id), inp = els[INPUTS[p]] = mk(INPUTS[p]), btn = mk(p + 'Save');
  btn.textContent = 'Save'; ed.dataset.provider = p;
  ed.querySelector = (q) => (q === 'input' ? inp : btn);
  inp.closest = () => ed; inp.parentElement = { querySelector: () => btn };
});
const document = { getElementById: (id) => els[id] || null, querySelectorAll: () => [] };
const toasts = [], calls = [], banners = [];
const showToast = (m) => toasts.push(m);
const showRestartBanner = (m) => banners.push(m);
const console = { warn() {} };
let settings = {};
const said = { groq: { ok: false, kind: 'unauthorized', error: "Groq didn't accept that key. Make a new one and click Copy again." } };
const pywebview = { api: {
  validate_groq_key: async (k) => { calls.push('validate_groq_key'); return said.groq; },
  save_settings: async (p) => { calls.push('save_settings:' + Object.keys(p).join()); settings = { groq_key_set: true, groq_key_masked: 'gsk_abcd…wxyz', transcription_backend: 'groq' }; return { ok: true }; },
} };
const atLoad = [];
const loadSettings = async () => { atLoad.push(shot()); _lastSettings = settings; renderKeyCard(); };
let _providerOrder = WL.DEFAULT_PROVIDER_ORDER.slice(), _lastSettings = null, _openKeyEditor = '', _keyPicked = false;
const _keyVerdicts = {};
let _keyChecking = '';
FUNCS
const shot = () => ({ picked: els.keyProvider.value, shown: Object.keys(_KEY_EDITORS).filter((p) => !els[_KEY_EDITORS[p]].hidden),
  placeholder: els.groqKeyInput.placeholder, typed: els.groqKeyInput.value, invalid: els.groqKeyInput.attrs['aria-invalid'] || '',
  state: els.keyState.hidden ? '' : els.keyState.textContent, cls: els.keyState.className, hasKey: els.keyCard.classList.on['has-key'] });
(async () => {
  const out = {};
  renderKeyCard(); out.first = shot();
  await saveGroqKey(); out.empty = shot();
  els.groqKeyInput.value = 'sk-not-a-groq-key'; await saveGroqKey(); out.wrong = shot();
  els.groqKeyInput.value = 'gsk_refusedrefusedrefused'; await saveGroqKey(); out.refused = shot();
  closeKeyEditor(true); out.cancelled = shot(); out.focus = focused;
  said.groq = { ok: true, services: [] };
  els.groqKeyInput.value = 'gsk_acceptedacceptedaccepted'; await saveGroqKey(); out.working = shot();
  pickKeyProvider('openai'); out.other = shot();
  openKeyEditor('groq'); out.back = shot(); out.focusBack = focused;
  out.calls = calls; out.toasts = toasts; out.banners = banners; out.kept = JSON.stringify(_keyVerdicts);
  out.atLoad = atLoad;
  process.stdout.write(JSON.stringify(out));
})();
"""


@needs_node
def test_the_key_card_drawn_for_real_from_no_key_to_refused_to_working():
    app = read("app.js")
    names = ("renderKeyCard", "pickKeyProvider", "_syncKeyButtons", "openKeyEditor", "closeKeyEditor", "_keyError",
             "_keyChecked", "_afterKeySaved", "_keySaveBusy", "saveGroqKey")
    funcs = "let _keySaving = false;\n" + "\n".join(
        ("async " if f"async function {n}(" in app else "") + func(app, n) + "\n}" for n in names)
    code = f"const LOGIC = {json.dumps(str(UI / 'logic.js'))};\n" + _CARD.replace("FUNCS", funcs)
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout)
    first = seen["first"]
    assert (first["picked"], first["shown"], first["placeholder"], first["state"], first["hasKey"]) == (
        "groq", ["groq"], "Paste your key", "", False)
    assert seen["empty"]["state"] == "The box is empty." and seen["empty"]["cls"] == "key-state is-bad"
    assert seen["wrong"]["state"] == "That isn't a Groq key." and seen["wrong"]["invalid"] == "true"
    refused = seen["refused"]
    assert refused["state"] == "Groq didn't accept that key." and refused["cls"] == "key-state is-bad"
    assert refused["invalid"] == "true" and refused["typed"], "what was typed stays, to be corrected"
    # Cancel drops what was typed and the line about it, and goes back to the picker.
    assert (seen["cancelled"]["typed"], seen["cancelled"]["state"], seen["focus"]) == ("", "", "keyProvider")
    working = seen["working"]
    assert (working["state"], working["cls"], working["placeholder"], working["typed"], working["hasKey"]) == (
        "Saved", "key-state is-ok", "gsk_abcd…wxyz", "", True)
    # Drawn as saved before the settings came back: no "Paste your key" in between.
    (before_reload,) = seen["atLoad"]
    assert (before_reload["state"], before_reload["placeholder"], before_reload["hasKey"]) == (
        "Saved", "gsk_acce…pted", True)
    assert seen["other"]["picked"] == "openai" and seen["other"]["shown"] == ["openai"] and seen["other"]["state"] == ""
    assert seen["back"]["state"] == "Saved" and seen["focusBack"] == "groqKeyInput"
    # Asked twice, saved once: a key the provider refused is not saved, and nothing asks for a restart.
    assert seen["calls"] == ["validate_groq_key", "validate_groq_key", "save_settings:groq_key"]
    assert seen["banners"] == [], "a saved key is used at once: no restart"
    assert seen["toasts"] == ["The box is empty. Paste your Groq key, then click Save.",
                              "That isn't a Groq key. Groq keys start with gsk_.",
                              "Groq didn't accept that key. Make a new one and click Copy again.", "Groq key saved."]
    assert seen["kept"] == '{"groq":{"ok":true,"saved":true}}', "what was said, never the key"


def test_the_two_ways_are_a_stack_with_the_chosen_one_marked():
    css = read("meetings.css")
    assert ".hir { display: grid; grid-template-columns: minmax(0, 1fr);" in css, "one column: a third way is one more card"
    on = re.search(r"\.hir-card\.is-on \{([^}]*)\}", css).group(1)
    assert "background: var(--card)" in on and "inset 0 0 0 2px var(--text)" in on
    hir = read("howitruns.js")
    assert "$('hirLocal').classList.toggle('is-on', local); $('hirCloud').classList.toggle('is-on', !local);" in hir
    assert "const v = L.speechRowView(s);" in hir
    style = read("style.css")
    for rule in (".key-card {", ".key-editor[hidden] { display: none; }", ".key-state.is-ok {", ".key-state.is-bad {",
                 ".key-card.has-key .key-help { display: none; }",
                 ".key-editor .input:placeholder-shown ~ .btn-quiet { display: none; }"):
        assert rule in style, rule
