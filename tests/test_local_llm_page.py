"""What the page shows and calls for the writing model on this computer
(src/local_llm.py): the second row under "On this computer" in Settings, Keys
and providers; the optional extra on setup's free card; who the page says
tidies; and app.py's bridge methods.

No network and no model: the page's decisions are pure functions in
ui/logic.js (run in Node where it is installed), and app.py's methods are
lifted out of its source and run against fakes.
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import local_llm  # noqa: E402
import local_stt  # noqa: E402
from _settings_fake import settings_api  # noqa: E402

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")
GB5 = 5154941280
ABOUT = ("Optional. For an Apple Silicon Mac with 16 GB of memory or more: about two seconds to tidy a "
         "dictation, a minute or two for a long meeting’s notes. A key is quicker, and better with long "
         "meetings.")


def read(name):
    return (UI / name).read_text(encoding="utf-8")


def js(expr):
    code = (f"const L = require({json.dumps(str(UI / 'logic.js'))});\n"
            f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def func(src, name):
    body = src[src.index(f"function {name}("):]
    return body[:body.index("\n}\n")]


def visible_text(html):
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<(script|style|svg)[^>]*>.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def llm(**over):
    """local_llm_status() as the page gets it, on a Mac that is offered it."""
    base = {"ok": True, "mode": "cloud", "available": True, "model": "gemma-4-e4b-q4", "label": "Gemma 4 E4B",
            "state": "not_downloaded", "percent": 0, "bytes_done": 0, "bytes_total": GB5, "size_on_disk": 0,
            "error": "", "cancelled": False, "loaded": False, "suits_machine": True,
            "machine_reason": "apple_silicon"}
    base.update(over)
    return base


# ── Settings: the second row under "On this computer" ────────────────────────

@needs_node
def test_the_row_offers_the_download_with_its_real_size_and_one_honest_line():
    v = js(f"L.writingRowView({json.dumps(llm())}, false)")
    assert v["show"] is True and v["title"] == "Tidy text and write notes on this computer too"
    assert v["download"] == "Download the writing model (5.2 GB)"
    assert v["about"] == ABOUT
    assert (v["bar"], v["stop"], v["remove"], v["choice"]) == (False, False, False, False)
    # The size is the model's own, not a number written on the page.
    spec = local_llm.MODELS[local_llm.DEFAULT_MODEL]
    assert spec.size == GB5 and js(f"L.modelSize({spec.size})") == "5.2 GB"


@needs_node
def test_the_row_while_it_downloads_stopped_part_way_and_after_a_failure():
    v = js(f"L.writingRowView({json.dumps(llm(state='downloading', percent=41, bytes_done=2100000000))}, false)")
    assert v["state"] == "Downloading the writing model: 41% of 5.2 GB. You can carry on meanwhile."
    assert v["bar"] and v["stop"] and v["percent"] == 41 and not v["download"]
    v = js(f"L.writingRowView({json.dumps(llm(bytes_done=900000000, cancelled=True))}, false)")
    assert v["state"] == "The download was stopped part-way. It carries on from where it got to."
    assert v["download"] == "Download the writing model (5.2 GB)"
    v = js(f"L.writingRowView({json.dumps(llm(error=local_llm.DOWNLOAD_FAILED))}, false)")
    assert v["state"] == local_llm.DOWNLOAD_FAILED and v["download"]


@needs_node
def test_once_ready_with_no_key_it_is_in_use_and_with_a_key_the_person_chooses():
    ready = llm(state="ready", percent=100, bytes_done=GB5, size_on_disk=GB5 + 90)
    v = js(f"L.writingRowView({json.dumps(ready)}, false)")
    assert v["state"] == "The writing model is ready (5.2 GB). Text is tidied and notes are written on this computer."
    assert v["remove"] and not v["choice"] and not v["download"]
    v = js(f"L.writingRowView({json.dumps(ready)}, true)")
    assert v["choice"] is True and v["chosen"] is False
    assert v["state"].endswith("Your key is still doing the tidying and the notes.")
    v = js(f"L.writingRowView({json.dumps({**ready, 'mode': 'local'})}, true)")
    assert v["chosen"] is True and "on this computer." in v["state"]


@needs_node
def test_a_computer_that_is_not_offered_it_does_not_see_a_download():
    low = llm(suits_machine=False, machine_reason="low_memory")
    v = js(f"L.writingRowView({json.dumps(low)}, false)")
    assert v["show"] is True and v["download"] == "" and "16 GB" in v["about"] and "Not offered" in v["about"]
    for other in (llm(suits_machine=False, machine_reason="not_measured"), llm(available=False),
                  {"ok": False}, None):
        assert js(f"L.writingRowView({json.dumps(other)}, false)")["show"] is False
        assert js(f"L.writingOffered({json.dumps(other)})") is False
    # One that is here already is shown, wherever it is, so it can be removed.
    here = llm(state="ready", suits_machine=False, machine_reason="low_memory")
    assert js(f"L.writingRowView({json.dumps(here)}, false)")["remove"] is True


@needs_node
def test_the_line_under_the_cards_stays_true():
    covers = "This choice covers dictation and meetings."
    assert js(f"L.howItRunsNote({json.dumps(llm(available=False))}, false)") == \
        f"{covers} Writing a meeting’s notes still needs a key, either way."
    assert js(f"L.howItRunsNote({json.dumps(llm())}, false)") == \
        f"{covers} Tidying text and writing a meeting’s notes need a key, or the second download."
    ready = llm(state="ready")
    assert js(f"L.howItRunsNote({json.dumps(ready)}, false)") == \
        f"{covers} Text is tidied and meeting notes are written on this computer too."
    assert "need a key, or the second download" in js(f"L.howItRunsNote({json.dumps(ready)}, true)"), \
        "with a key and no choice made, the key still does it"
    assert "on this computer too" in js(f"L.howItRunsNote({json.dumps({**ready, 'mode': 'local'})}, true)")


def test_the_row_is_in_the_free_card_hidden_until_the_page_knows():
    html = read("index.html")
    card = html[html.index('<div class="hir-card" id="hirLocal"'):html.index('<div class="hir-card" id="hirCloud"')]
    assert card.index('id="hirModel"') < card.index('id="hirLlm"'), "second, under the speech model"
    assert re.search(r'<div class="hir-model hir-extra" id="hirLlm" role="group" aria-labelledby="hirLlmT" hidden>', card)
    assert '<span class="hir-extra-t" id="hirLlmT">Tidy text and write notes on this computer too</span>' in card
    assert ABOUT in visible_text(card)
    assert '<span class="hir-state" id="hirLlmState" role="status" hidden></span>' in card
    for button in ('id="hirLlmDownload" hidden>Download the writing model</button>',
                   'id="hirLlmCancel" hidden>Stop the download</button>',
                   'id="hirLlmRemove" hidden>Remove the writing model</button>'):
        assert button in card, button
    assert '<input type="checkbox" id="hirLlmChoice">' in card
    assert "Use this computer for tidying and notes, not my key" in visible_text(card)
    assert '<p class="hir-note" id="hirNote">' in html
    css = read("meetings.css")
    for rule in (".hir-extra {", ".hir-extra-t {", ".hir-extra-d {", ".hir-extra-pick {"):
        assert rule in css, rule


@needs_node
def test_the_words_on_the_page_are_the_ones_the_logic_uses():
    assert js("L.WRITING_ABOUT") == ABOUT
    assert js("L.WRITING_TITLE") == "Tidy text and write notes on this computer too"


def test_settings_calls_the_bridge_and_saves_the_choice_like_any_setting():
    hir = read("howitruns.js")
    for call in ("a.local_llm_status()", "a.local_llm_download()", "a.local_llm_cancel()", "a.local_llm_remove()",
                 "a.save_settings({ writing_mode: on ? 'local' : 'cloud' })"):
        assert call in hir, call
    assert "L.writingRowView(s, hasKey)" in hir and "L.howItRunsNote(s, hasKey)" in hir
    assert "st.groq_key_set || st.api_key_set || st.cerebras_key_set" in hir
    assert "window.loadHowItRuns = () => { refresh(); refreshLlm(false); };" in hir
    assert "pollLlm = window.setTimeout(() => refreshLlm(true), 700);" in hir, "polled while it downloads"
    assert "window.confirm('Remove the writing model from this computer?" in hir


# The row drawn for real: howitruns.js run in Node against a stand-in page and
# a stand-in bridge, so a slip in the script is caught and not only its text.
_ROW_HARNESS = r"""
const els = {};
const el = (id) => els[id] || (els[id] = { id, hidden: false, textContent: '', checked: false, disabled: false,
  style: {}, classList: { toggle() {} }, listeners: {}, addEventListener(t, f) { this.listeners[t] = f; } });
['howItRuns','hirLocalRadio','hirCloudRadio','hirLocal','hirCloud','hirState','hirBar','hirBarFill','hirDownload',
 'hirCancel','hirRemove','hirLlm','hirLlmAbout','hirLlmState','hirLlmBar','hirLlmBarFill','hirLlmDownload',
 'hirLlmCancel','hirLlmRemove','hirLlmPick','hirLlmChoice','hirNote'].forEach(el);
const ready = [];
const calls = [], toasts = [];
global.document = { getElementById: (id) => els[id] || null, addEventListener: (t, f) => ready.push(f) };
global.showToast = (m) => toasts.push(m);
global.window = global;
window.setTimeout = () => 0; window.clearTimeout = () => {};
window.confirm = () => true;
window.WafflerLogic = require(LOGIC);
window.loadSettings = () => calls.push('loadSettings');
let llm = LLM, settings = SETTINGS;
window.pywebview = { api: {
  local_stt_status: async () => ({ ok: true, mode: 'local', available: true, state: 'ready', bytes_total: 574041195, size_on_disk: 574041195 }),
  get_settings: async () => settings,
  local_llm_status: async () => llm,
  local_llm_download: async () => { calls.push('download'); llm = { ...llm, state: 'downloading', percent: 3 }; return { ok: true, started: true }; },
  local_llm_cancel: async () => { calls.push('cancel'); llm = { ...llm, state: 'not_downloaded', bytes_done: 9e8 }; return { ok: true }; },
  local_llm_remove: async () => { calls.push('remove'); llm = { ...llm, state: 'not_downloaded', bytes_done: 0 }; return { ok: true }; },
  save_settings: async (p) => { calls.push(p); llm = { ...llm, mode: p.writing_mode }; return { ok: true }; },
} };
require(HIR);
ready.forEach((f) => f());
const tick = () => new Promise((r) => setImmediate(r));
const settle = async () => { for (let i = 0; i < 12; i++) await tick(); };
const shot = () => ({ shown: !els.hirLlm.hidden, about: els.hirLlmAbout.textContent, state: els.hirLlmState.textContent,
  stateHidden: els.hirLlmState.hidden, bar: !els.hirLlmBar.hidden, width: els.hirLlmBarFill.style.width,
  download: els.hirLlmDownload.hidden ? '' : els.hirLlmDownload.textContent, stop: !els.hirLlmCancel.hidden,
  remove: !els.hirLlmRemove.hidden, pick: !els.hirLlmPick.hidden, ticked: els.hirLlmChoice.checked,
  note: els.hirNote.textContent });
(async () => {
  const out = {};
  window.loadHowItRuns(); await settle(); out.first = shot();
  els.hirLlmDownload.listeners.click(); await settle(); out.downloading = shot();
  els.hirLlmCancel.listeners.click(); await settle(); out.stopped = shot();
  llm = { ...llm, state: 'ready', percent: 100, size_on_disk: 5154941370 };
  window.waffler_local_llm_changed(llm); await settle(); out.ready = shot();
  settings = { groq_key_set: true };
  window.loadHowItRuns(); await settle(); out.readyWithKey = shot();
  els.hirLlmChoice.listeners.change({ target: { checked: true } }); await settle(); out.chosen = shot();
  els.hirLlmRemove.listeners.click(); await settle(); out.removed = shot();
  out.calls = calls; out.toasts = toasts;
  process.stdout.write(JSON.stringify(out));
})();
"""


def _row(status, settings=None):
    code = (f"const LOGIC = {json.dumps(str(UI / 'logic.js'))}, HIR = {json.dumps(str(UI / 'howitruns.js'))};\n"
            f"const LLM = {json.dumps(status)}, SETTINGS = {json.dumps(settings or {})};\n" + _ROW_HARNESS)
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_the_settings_row_drawn_for_real_from_download_to_remove():
    seen = _row(llm())
    first = seen["first"]
    assert first["shown"] and first["download"] == "Download the writing model (5.2 GB)"
    assert first["about"] == ABOUT and first["stateHidden"] and not first["bar"] and not first["remove"]
    assert first["note"].endswith("need a key, or the second download.")
    down = seen["downloading"]
    assert down["bar"] and down["stop"] and down["width"] == "3%" and not down["download"]
    assert down["state"].startswith("Downloading the writing model: 3% of 5.2 GB.")
    assert seen["stopped"]["state"] == "The download was stopped part-way. It carries on from where it got to."
    ready = seen["ready"]
    assert ready["remove"] and not ready["pick"] and not ready["download"]
    assert ready["note"].endswith("Text is tidied and meeting notes are written on this computer too.")
    with_key = seen["readyWithKey"]
    assert with_key["pick"] and with_key["ticked"] is False
    assert with_key["state"].endswith("Your key is still doing the tidying and the notes.")
    assert seen["chosen"]["ticked"] is True and seen["chosen"]["note"].endswith("on this computer too.")
    assert seen["removed"]["download"] == "Download the writing model (5.2 GB)"
    assert seen["calls"].count("download") == 1 and "cancel" in seen["calls"] and "remove" in seen["calls"]
    assert {"writing_mode": "local"} in seen["calls"]
    assert "loadSettings" in seen["calls"], "the rest of Settings is told who tidies now"
    assert seen["toasts"] == ["Text is tidied and notes are written on this computer now."]


@needs_node
def test_the_settings_row_stays_hidden_where_it_is_not_offered():
    for status in (llm(available=False), llm(suits_machine=False, machine_reason="not_measured"), {"ok": False}):
        first = _row(status)["first"]
        assert first["shown"] is False, status
        assert first["note"] in ("This choice covers dictation and meetings. Writing a meeting’s notes still "
                                 "needs a key, either way.", ""), "nothing is promised"
    low = _row(llm(suits_machine=False, machine_reason="low_memory"))["first"]
    assert low["shown"] and low["download"] == "" and "needs 16 GB of memory or more" in low["about"]


# Setup's extra, drawn for real: the three functions lifted out of app.js.
_EXTRA_HARNESS = r"""
const els = {};
['obWayExtra','obTldrExtra','obWayExtraLine','obWayExtraBtn'].forEach((id) => { els[id] = { hidden: true, textContent: '' }; });
global.document = { getElementById: (id) => els[id] || null };
const WL = require(LOGIC);
const toasts = [], calls = [];
const showToast = (m) => toasts.push(m);
const setTimeout = () => 0, clearTimeout = () => {};
let llm = LLM;
const pywebview = { api: {
  local_llm_status: async () => llm,
  local_llm_download: async () => { calls.push('download'); llm = { ...llm, state: 'downloading', percent: 7 }; return { ok: true }; },
  local_llm_cancel: async () => { calls.push('cancel'); llm = { ...llm, state: 'not_downloaded', bytes_done: 4e8 }; return { ok: true }; },
} };
let _wizWay = '';
FUNCS
const shot = () => ({ shown: !els.obWayExtra.hidden, line: els.obWayExtraLine.textContent,
  button: els.obWayExtraBtn.hidden ? '' : els.obWayExtraBtn.textContent, more: !els.obTldrExtra.hidden });
(async () => {
  const out = {};
  await wizRefreshExtra(); out.beforeChoosing = shot();
  _wizWay = 'local'; wizDrawExtra(); out.chosen = shot();
  await wizExtra(); out.downloading = shot();
  await wizExtra(); out.stopped = shot();
  _wizWay = 'key'; wizDrawExtra(); out.keyWay = shot();
  out.calls = calls;
  process.stdout.write(JSON.stringify(out));
})();
"""


def _extra(status):
    app = read("app.js")
    start = app.index("let _wizLlm = null;")
    funcs = app[start:app.index("// \"Download and start\"", start)]
    code = (f"const LOGIC = {json.dumps(str(UI / 'logic.js'))};\nconst LLM = {json.dumps(status)};\n"
            + _EXTRA_HARNESS.replace("FUNCS", funcs))
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_setups_extra_drawn_for_real():
    seen = _extra(llm())
    assert seen["beforeChoosing"]["shown"] is False, "not until the free way is chosen"
    assert seen["beforeChoosing"]["more"] is True, "the short version says it exists, where it is offered"
    chosen = seen["chosen"]
    assert chosen["shown"] and chosen["button"] == "Add it" and chosen["line"].startswith("Optional extra (5.2 GB):")
    assert seen["downloading"]["button"] == "Stop" and "7% of 5.2 GB" in seen["downloading"]["line"]
    assert seen["stopped"]["button"] == "Carry on"
    assert seen["keyWay"]["shown"] is False
    assert seen["calls"] == ["download", "cancel"]
    # A computer that is not offered it sees nothing, and nothing is said about it.
    nowhere = _extra(llm(suits_machine=False, machine_reason="low_memory"))
    assert nowhere["chosen"] == {"shown": False, "line": "", "button": "", "more": False}


# ── who the page says tidies ─────────────────────────────────────────────────

FREE_PLUS = {"transcription_mode": "local", "transcription_backend": "mlx", "styling_backend": "ondevice"}


@needs_node
def test_with_the_writing_model_the_page_says_this_computer_tidies_and_names_no_provider():
    flow = js(f"L.flowView({json.dumps(FREE_PLUS)})")
    assert flow["cleanup"] == {"by": "This computer", "model": "Gemma 4"}
    assert js(f"L.backendsLine({json.dumps(FREE_PLUS)})") == "Speech to text: on this Mac · Clean-up: on this computer"
    assert js(f"L.usesView({json.dumps(FREE_PLUS)})")["cleanup"] == "Gemma 4, on this computer"
    assert js(f"L.privacyFlow({json.dumps(FREE_PLUS)})") is None, "nothing leaves the computer"
    # Speech with a key, tidying here: only the voice goes to the provider.
    mixed = {"transcription_mode": "cloud", "transcription_backend": "groq", "styling_backend": "ondevice",
             "groq_key_set": True}
    assert js(f"L.privacyFlow({json.dumps(mixed)})")["what"] == "your voice, while you hold"
    rows = js(f"L.keyRows(['groq','openai','cerebras'], {json.dumps(mixed)})")
    assert [r["status"] for r in rows] == ["In use", "Not set", "Not set"]


# ── setup's first screen: a clearly optional extra ───────────────────────────

@needs_node
def test_the_extra_on_the_free_card_in_each_state():
    v = js(f"L.setupExtraView({json.dumps(llm())})")
    assert v == {"show": True, "button": "Add it", "stop": False,
                 "line": "Optional extra (5.2 GB): tidy your text and write meeting notes on this computer too. "
                         "It suits this Mac; it can also be added later in Settings."}
    v = js(f"L.setupExtraView({json.dumps(llm(state='downloading', percent=12))})")
    assert v["line"] == "Fetching the optional extra: 12% of 5.2 GB. Nothing waits for it."
    assert v["button"] == "Stop" and v["stop"] is True
    v = js(f"L.setupExtraView({json.dumps(llm(bytes_done=700000000))})")
    assert v["button"] == "Carry on"
    v = js(f"L.setupExtraView({json.dumps(llm(state='ready'))})")
    assert v["button"] == "" and v["line"].startswith("Added:")
    for other in (llm(suits_machine=False, machine_reason="low_memory"), llm(available=False), None):
        assert js(f"L.setupExtraView({json.dumps(other)})")["show"] is False


def test_the_first_screen_says_what_the_free_way_now_does_and_offers_the_extra_as_optional():
    html = read("index.html")
    a = html.index('<section class="ob-step" id="obStepConnect"')
    step = html[a:html.index("</section>", a)]
    card = step[step.index('id="obWayLocal"'):step.index('id="obWayKey"')]
    # The extra: inside the free card, after its one real button, hidden until offered.
    assert card.index('id="obWayLocalBtn"') < card.index('id="obWayExtra"')
    assert '<div class="ob-way-extra" id="obWayExtra" hidden>' in card
    assert '<p class="ob-way-extra-line" id="obWayExtraLine" role="status"></p>' in card
    assert re.search(r'<button type="button" class="btn btn-quiet btn-sm" id="obWayExtraBtn" onclick="wizExtra\(\)" hidden>Add it</button>', card)
    assert card.count("btn-pri") == 1, "still one main button: the extra is a quiet one"
    # The free card's own two promises are untouched: they are still true.
    text = visible_text(step)
    assert "then it works with no key, no account and no internet." in text
    assert "Your voice never leaves this computer." in text
    # The short version, as it is now.
    tldr = text[text.index("The short version."):]
    tldr = tldr[:tldr.index("You can change this any time in Settings.") + 45]
    assert "On this computer your words are typed as you said them, with punctuation." in tldr
    assert ("The free download does not tidy them or write meeting notes (a meeting still gets its full "
            "transcript). Those need a key") in tldr
    assert "Transforms and Command mode need a key." in tldr
    assert '<span id="obTldrExtra" hidden>, or the optional second download offered on the free card</span>' in step
    assert len(tldr.split()) < 85, "three lines, not a manual"


def test_setup_draws_the_extra_only_once_the_free_way_is_chosen_and_waits_for_nothing():
    app = read("app.js")
    draw = func(app, "wizDrawExtra")
    assert "WL.setupExtraView(_wizLlm)" in draw
    assert "box.hidden = !(v.show && (_wizWay === 'local' || under));" in draw
    assert "more.hidden = !v.show;" in draw, "the short version mentions it only where it is offered"
    extra = func(app, "wizExtra")
    assert "pywebview.api.local_llm_download()" in extra and "pywebview.api.local_llm_cancel()" in extra
    refresh = func(app, "wizRefreshExtra")
    assert "pywebview.api.local_llm_status()" in refresh
    assert "_wizLlmTimer = setTimeout(wizRefreshExtra, 700);" in refresh
    # Choosing the free way does not start it; choosing a key stops it.
    choose = func(app, "wizChooseLocal")
    assert "local_llm_download" not in choose and "wizRefreshExtra();" in choose
    assert "pywebview.api.local_llm_cancel()" in func(app, "_wizChoseKey")
    # Nothing that lets setup continue looks at it.
    for name in ("wizUpdateNextButton", "wizDrawLocal"):
        body = func(app, name)
        assert "_wizLlm" not in body.replace("wizDrawExtra();", "")
    css = read("setup.css")
    assert ".ob-way-extra {" in css and ".ob-way-extra-line {" in css


# ── app.py's bridge ──────────────────────────────────────────────────────────

_SRC = (ROOT / "app.py").read_text(encoding="utf-8")
_TREE = ast.parse(_SRC)
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
_PIPELINE = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
STATUS = llm()
STATUS.pop("ok"), STATUS.pop("mode")


def _lift(cls, *names, **ns_extra):
    nodes = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names), "an app.py method moved or was renamed"
    logged = []
    ns = {"os": os, "json": json, "threading": threading, "_pipeline": None, "_local_llm": local_llm,
          "_local_stt": local_stt, "_log_to_file": logged.append, "_local_llm_changed": lambda: None}
    ns.update(ns_extra)
    exec(compile(ast.Module(nodes, []), "<app.py>", "exec"), ns)
    ns["logged"] = logged
    return ns


def test_the_page_has_the_four_methods_and_they_never_raise(monkeypatch):
    names = ("local_llm_status", "local_llm_download", "local_llm_cancel", "local_llm_remove")
    ns = _lift(_API, *names)
    this = types.SimpleNamespace(_load_settings_file=lambda: {})

    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    for attr in ("model_status", "start_download", "cancel_download", "remove_model"):
        monkeypatch.setattr(local_llm, attr, boom)
    for name in names:
        result = ns[name](this)
        assert result["ok"] is False and result["error"], name
        assert "disk on fire" not in result["error"]


def test_status_is_the_models_state_plus_the_persons_choice(monkeypatch):
    ns = _lift(_API, "local_llm_status")
    monkeypatch.setattr(local_llm, "model_status", lambda: dict(STATUS))
    this = types.SimpleNamespace(_load_settings_file=lambda: {})
    assert ns["local_llm_status"](this) == {"ok": True, "mode": "cloud", **STATUS}
    this._load_settings_file = lambda: {"writing_mode": "local"}
    assert ns["local_llm_status"](this)["mode"] == "local"


def test_download_starts_in_the_background_tells_the_page_and_then_loads_the_model(monkeypatch):
    told, warmed = [], []
    ns = _lift(_API, "local_llm_download", _local_llm_changed=lambda: told.append(1))
    monkeypatch.setattr(local_llm, "warm", lambda: warmed.append(1))

    def start(progress_cb, done_cb):
        progress_cb(10, 1, 10)
        done_cb({"ok": True, "state": "ready"})
        return {**STATUS, "ok": True, "started": True, "state": "downloading"}
    monkeypatch.setattr(local_llm, "start_download", start)
    result = ns["local_llm_download"](types.SimpleNamespace())
    assert result["started"] is True and len(told) == 2 and warmed == [1]
    assert any("download started: gemma-4-e4b-q4 (5155 MB)" in line for line in ns["logged"])


def test_the_page_is_handed_the_new_state_as_json(monkeypatch):
    fn = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_local_llm_changed")
    posted = []
    env = {"json": json, "_local_llm": local_llm, "_post_js": posted.append}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), env)
    monkeypatch.setattr(local_llm, "model_status", lambda: dict(STATUS))
    env["_local_llm_changed"]()
    assert posted[0].startswith("window.waffler_local_llm_changed && window.waffler_local_llm_changed(")
    assert json.loads(posted[0][posted[0].rindex("(") + 1:-1])["state"] == "not_downloaded"


def test_the_choice_is_saved_refused_when_unknown_and_reported_back(tmp_path, monkeypatch):
    for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    warmed = []
    monkeypatch.setattr(local_llm, "warm", lambda: warmed.append(1))
    save = _SRC[_SRC.index("    def save_settings("):]
    assert 'problem = _lllm.settings_problem(settings)' in save
    assert "stored[_lllm.MODE_SETTING] = settings[_lllm.MODE_SETTING]" in save
    assert "if settings.get(_lllm.MODE_SETTING) == _lllm.MODE_LOCAL:" in save
    get = _SRC[_SRC.index("    def get_settings("):_SRC.index("    def save_settings(")]
    assert '"writing_mode":          _lllm.mode_of(stored),' in get
    assert "if _lllm.should_use(has_key=bool(key or groq_key or cerebras_key), stored=stored):" in get
    assert "styling_backend = _lllm.PROVIDER" in get


def test_the_no_key_shortcut_steps_aside_when_the_writing_model_is_here(monkeypatch, _isolated_data_dir):
    ns = _lift(_PIPELINE, "_no_cleanup_key")
    styler = types.SimpleNamespace(_use_groq=False, _use_cerebras=False, client=None)
    this = types.SimpleNamespace(styler=styler)
    local = {"transcription_mode": "local"}
    monkeypatch.setattr(local_llm, "is_available", lambda: True)
    monkeypatch.setattr(local_llm, "is_ready", lambda: False)
    assert ns["_no_cleanup_key"](this, local) is True, "no key, no model: the words as heard, as before"
    monkeypatch.setattr(local_llm, "is_ready", lambda: True)
    assert ns["_no_cleanup_key"](this, local) is False, "the model here does the clean-up"
    styler._use_groq = True
    assert ns["_no_cleanup_key"](this, local) is False


def test_the_model_is_loaded_again_as_a_recording_starts_only_when_it_is_the_one_in_use(monkeypatch):
    started = []

    class Thread:
        def __init__(self, target=None, **kw):
            self.target = target

        def start(self):
            started.append(self.target)
    ns = _lift(_PIPELINE, "_warm_writing_model", threading=types.SimpleNamespace(Thread=Thread))
    styler = types.SimpleNamespace(_local_cleanup=lambda: True)
    this = types.SimpleNamespace(styler=styler)
    monkeypatch.setattr(local_llm, "is_loaded", lambda: False)
    ns["_warm_writing_model"](this)
    assert started == [local_llm.warm]
    monkeypatch.setattr(local_llm, "is_loaded", lambda: True)
    ns["_warm_writing_model"](this)
    assert len(started) == 1, "already in memory"
    monkeypatch.setattr(local_llm, "is_loaded", lambda: False)
    styler._local_cleanup = lambda: False
    ns["_warm_writing_model"](this)
    assert len(started) == 1, "a key does the clean-up: the model is left alone"
    ns["_warm_writing_model"](types.SimpleNamespace())           # no styler yet: nothing, no error
    press = _SRC[_SRC.index("    def on_hotkey_press("):_SRC.index("    def on_hotkey_press(") + 3000]
    assert '_warm = getattr(self, "_warm_writing_model", None)' in press
