"""The window's small decisions, checked without opening it.

ui/logic.js holds the pure parts of the UI (labels, hotkey presets, what the
Journal shows for a search, the update messages). These tests run it in Node,
which the CI runners have; without Node those checks are skipped and the
source checks below still run. No network, no keys, no private data.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
LOGIC = UI / "logic.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")


def js(expr, setup=""):
    """Evaluate a JavaScript expression with ui/logic.js loaded as L."""
    code = (f"const L = require({json.dumps(str(LOGIC))});\n{setup}\n"
            f"Promise.resolve({expr}).then((v) => process.stdout.write(JSON.stringify(v === undefined ? null : v)));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60,
                         encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def read(name):
    return (UI / name).read_text(encoding="utf-8")


def code_only(src):
    """JavaScript or CSS with comments removed, so a comment explaining an
    old bug does not count as the bug."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


# ── the status pill (QW1) ────────────────────────────────────────────────────

@needs_node
def test_each_status_has_one_plain_label():
    views = js("['idle','listening','paused','processing','done'].map((s) => L.statusView(s))")
    assert [v["label"] for v in views] == ["Ready", "Recording", "Paused", "Cleaning up", "Done"]
    assert [v["cls"] for v in views] == ["idle", "listening", "paused", "processing", "done"]


@needs_node
def test_an_unknown_status_shows_ready_not_its_raw_name():
    assert js("L.statusView('bogus')") == {"cls": "idle", "label": "Ready"}
    assert js("L.statusView('__proto__')") == {"cls": "idle", "label": "Ready"}


@needs_node
def test_done_goes_back_to_ready_after_three_seconds():
    assert js("L.DONE_RESET_MS") == 3000


@needs_node
def test_every_status_python_sends_has_a_view():
    sent = set(re.findall(r'notify_js_status\("(\w+)"\)', (ROOT / "app.py").read_text(encoding="utf-8")))
    assert sent, "app.py should send statuses to the window"
    known = set(js("Object.keys(L.STATUS_VIEWS)"))
    assert sent <= known, f"statuses with no label: {sorted(sent - known)}"


def test_status_update_keeps_the_pill_class_and_needs_no_missing_element():
    app = code_only(read("app.js"))
    body = app[app.index("window.waffler_status"):]
    body = body[:body.index("\n};") + 3]
    # Assigning className wiped j-listen, the class that styles the pill.
    assert "className" not in body
    assert "classList.remove(...WL.STATUS_CLASSES)" in app
    # #recordingOverlay is not in index.html; touching it threw on every update.
    assert "recordingOverlay" not in app
    assert 'id="recordingOverlay"' not in read("index.html")


def test_the_idle_pill_says_ready():
    html = read("index.html")
    assert "Waiting for activation" not in html
    assert re.search(r'id="statusText"[^>]*>Ready<', html)


def test_logic_loads_before_app():
    html = read("index.html")
    assert html.index('<script src="logic.js"></script>') < html.index('<script src="app.js"></script>')


# ── the setup wizard (QW2) ───────────────────────────────────────────────────

def _css_rules():
    css = code_only(read("style.css"))
    return [(" ".join(m.group(1).split()), m.group(2))
            for m in re.finditer(r"([^{}@;]+)\{([^{}]*)\}", css)]


def _z(selector):
    zs = [int(m.group(1)) for sel, body in _css_rules() if sel == selector
          for m in [re.search(r"z-index:\s*(\d+)", body)] if m]
    assert zs, f"no z-index for {selector}"
    return max(zs)


def test_toasts_and_the_hotkey_dialog_sit_above_the_wizard():
    wizard = _z(".wizard-overlay")
    assert _z(".toast") > _z(".hotkey-modal-overlay") > wizard
    shown = [body for sel, body in _css_rules() if sel == ".toast.visible"]
    assert shown and "pointer-events: auto" in shown[0]


def test_a_message_over_the_wizard_sits_clear_of_its_buttons():
    """Bottom right, "Sent! That's how dictation works in any app." covered
    96% of Finish Setup, so the first click only closed the message (and
    "Hotkey is now Ctrl + Shift" covered Next). Over the wizard a message now
    goes to the top centre, away from Back, Next and Finish Setup."""
    app = code_only(read("app.js"))
    show = app[app.index("function showToast("):]
    show = show[:show.index("\n}")]
    assert "_wizardVisible()" in show and "over-wizard" in show
    rule = [body for sel, body in _css_rules() if sel == ".toast.over-wizard"]
    assert rule, "no placement for a message over the wizard"
    assert re.search(r"top:\s*\d+px", rule[0]) and "bottom: auto" in rule[0]
    assert "left: 50%" in rule[0] and "right: auto" in rule[0]
    shown = [body for sel, body in _css_rules() if sel == ".toast.over-wizard.visible"]
    assert shown and "translate(-50%, 0)" in shown[0]
    # The wizard's buttons sit at the bottom of the window.
    assert '<nav class="wiz-nav">' in read("index.html")


def test_a_disabled_finish_button_looks_disabled():
    rule = [body for sel, body in _css_rules() if sel == ".wiz-btn-next.finish:disabled"]
    assert rule, "Finish Setup had no disabled style, so it looked like the main action"
    assert "background: #E8E4DC" in rule[0] and "cursor: not-allowed" in rule[0]


def test_the_last_step_offers_skip_for_now():
    html = read("index.html")
    assert re.search(r'<button class="wiz-btn-skip" id="wizBtnSkip" onclick="wizSkipTryIt\(\)" hidden>Skip for now</button>', html)
    app = code_only(read("app.js"))
    skip = app[app.index("async function wizSkipTryIt()"):]
    assert "await wizCompleteSetup();" in skip[:skip.index("\n}")]
    assert "skip.hidden = !(_wizardStep === 4 && !_wizardMicTested);" in app


def test_steps_are_shown_without_an_inline_display():
    app = code_only(read("app.js"))
    show = app[app.index("function wizShowStep("):]
    show = show[:show.index("\nfunction ")]
    assert "'block'" not in show, "an inline display:block overrode the Mac permissions grid"
    assert "con.style.removeProperty('display')" in show
    assert "wizResetHotkeyPill();" in show
    assert '<div class="wiz-perm-card" id="wizPermInputMon">' in read("index.html")
    assert '.wiz-step-content:not([style*="none"])' in read("style.css")


def test_keycaps_are_never_overwritten_with_plain_text():
    app = code_only(read("app.js"))
    # wizLoadHotkeyInfo and wizInitTryItStep set the keycap's textContent to
    # the display name, wiping its light label and icon.
    assert not re.search(r"getElementById\('wizTryHotkeyBadge'\)[^;]*;\s*if \(\w+\) \w+\.textContent", app)
    for fn in ("async function wizLoadHotkeyInfo()", "async function wizInitTryItStep()"):
        body = app[app.index(fn):]
        body = body[:body.index("\n}")]
        assert "textContent = info.hotkey" not in body
        assert "wizRefreshHotkey()" in body
    # Keycap icons draw in the label colour, not a hard-coded near-black.
    assert 'fill="#1A1A1A"' not in app and 'stroke="#1A1A1A"' not in app
    assert "wiz-keycap-label {" in read("style.css")


def test_try_it_without_a_key_points_at_the_right_step():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    body = src[src.index("def wizard_start_hotkey_test"):src.index("def wizard_stop_hotkey_test")]
    assert "Step 1" not in body.replace('this used to say "Complete Step 1"', "")
    assert '"No API key found. Go back a step and add your key."' in body
    assert '"error": str(e)' not in body


@needs_node
def test_keycaps_come_from_the_saved_keys_in_one_order():
    caps = js("[L.keycaps(['ctrl','win'], false), L.keycaps(['win','ctrl'], false), L.keycaps(['fn'], true), "
              "L.keycaps(['shift','ctrl'], false), L.keycaps(['cmd','shift'], true), L.keycaps([], false)]")
    assert caps[0] == caps[1] == [{"key": "win", "label": "Win", "icon": "windows"},
                                  {"key": "ctrl", "label": "Ctrl", "icon": None}]
    assert caps[2] == [{"key": "fn", "label": "fn", "icon": "globe"}]
    assert [c["label"] for c in caps[3]] == ["Ctrl", "Shift"]
    assert [c["label"] for c in caps[4]] == ["Command", "Shift"]
    assert [c["label"] for c in caps[5]] == ["Win", "Ctrl"]      # nothing saved yet: the default


@needs_node
def test_hotkey_names_match_the_backend_order():
    names = js("[L.hotkeyName(['ctrl','win'], false), L.hotkeyName(['shift','ctrl'], false), "
               "L.hotkeyName(['k','alt','ctrl'], false), L.hotkeyName(['fn'], true), "
               "L.hotkeyName(['option','shift'], true), L.hotkeyName(['f9','ctrl'], false)]")
    assert names == ["Win + Ctrl", "Ctrl + Shift", "Ctrl + Alt + K", "Fn", "Option + Shift", "Ctrl + F9"]


@needs_node
def test_press_ctrl_first_is_a_tip_not_a_different_name():
    hints = js("[L.pressOrderHint(['win','ctrl'], false), L.pressOrderHint(['ctrl','shift'], false), "
               "L.pressOrderHint(['fn'], true)]")
    assert hints == ["Tip: press Ctrl first, then Win.", "", ""]


# ── hotkey choices per platform (QW3) ────────────────────────────────────────

import string  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, str(ROOT / "src"))
import hotkey_rules as hr  # noqa: E402

# The listeners' key tables, as rebuilt in tests/test_hotkey_save.py.
WIN_KEYS = {"win", "ctrl", "alt", "shift"} | {f"f{i}" for i in range(1, 25)} \
    | set(string.ascii_lowercase) | set(string.digits)
WIN_MODS = {"win", "ctrl", "alt", "shift"}
MAC_MODS = {"cmd", "command", "shift", "option", "alt", "control", "ctrl", "fn"}
MAC_KEYS = MAC_MODS | {"space", "return", "enter", "tab", "delete", "escape", "esc",
                       "f13", "f14", "f15", "f16", "f17", "f18", "f19"} | set(string.ascii_lowercase)


@needs_node
def test_every_hotkey_windows_is_offered_saves_on_windows():
    presets = js("L.hotkeyPresets(false)")
    fixed = [p for p in presets if not p.get("custom")]
    assert [p["label"] for p in fixed] == ["Win + Ctrl", "Ctrl + Shift"]
    assert any(p.get("custom") for p in presets), "Windows offers a custom hotkey"
    for p in fixed:
        verdict = hr.check(p["keys"], hr.WINDOWS, WIN_KEYS, WIN_MODS)
        assert verdict["ok"], (p, verdict)
        # One name everywhere: the button says what the backend will call it.
        assert p["label"] == verdict["display"]
        assert not {"fn", "cmd", "command", "option", "space"} & set(p["keys"])


@needs_node
@pytest.mark.skipif(sys.platform != "win32", reason="windows_hotkey needs Win32")
def test_every_windows_preset_passes_the_real_listener_table():
    import windows_hotkey
    for p in js("L.hotkeyPresets(false)"):
        if not p.get("custom"):
            assert hr.check(p["keys"], hr.WINDOWS, windows_hotkey.KEY_TO_VK,
                            windows_hotkey.MODIFIER_KEYS)["ok"], p


@needs_node
def test_every_hotkey_a_mac_is_offered_saves_on_a_mac():
    presets = js("L.hotkeyPresets(true)")
    assert [p["label"] for p in presets] == ["Fn", "Command + Shift", "Option + Shift"]
    for p in presets:
        verdict = hr.check(p["keys"], hr.MAC, MAC_KEYS, MAC_MODS)
        assert verdict["ok"], (p, verdict)
        assert p["label"] == verdict["display"]


def test_settings_draws_its_hotkey_buttons_per_platform():
    html = read("index.html")
    # The Mac buttons were hard-coded, so Windows showed Fn and Command too.
    assert "changeSettingsHotkey(['fn'])" not in html
    assert 'id="settingsHotkeyPresets"' in html and 'id="settingsHotkeyError"' in html
    app = code_only(read("app.js"))
    assert "renderSettingsHotkeyPresets();" in app
    assert "WL.hotkeyPresets(isMacPlatform)" in app


def _body(app, head):
    body = app[app.index(head):]
    return body[:body.index("\n}\n")]


def test_saving_a_hotkey_checks_the_answer_before_saying_it_worked():
    app = code_only(read("app.js"))
    settings = _body(app, "async function changeSettingsHotkey(keys)")
    assert settings.index("if (!result || !result.ok)") < settings.index("'#4CAF50'")
    assert "_showSettingsHotkeyError(msg)" in settings
    wizard = _body(app, "async function selectHotkeyPreset(keys)")
    assert wizard.index("if (!result || !result.ok)") < wizard.index("_onHotkeySaved(result)")
    # The keycaps are redrawn from the keys the backend saved.
    saved = _body(app, "async function _onHotkeySaved(result)")
    assert "wizRenderHotkey(result.keys)" in saved
    assert "wizHotkeyBadge" not in app


def test_no_screen_calls_the_default_ctrl_plus_win():
    for path in [UI / "app.js", UI / "index.html", UI / "logic.js", ROOT / "app.py"]:
        text = path.read_text(encoding="utf-8")
        text = code_only(text) if path.suffix == ".js" else text
        assert not re.search(r"Ctrl ?\+ ?Win", text), f"{path.name} names the hotkey 'Ctrl + Win'"
        assert "Ctrl + Alt + Space" not in text or path.suffix != ".js"


# ── plain messages on screen (QW8) ───────────────────────────────────────────

import user_messages as um  # noqa: E402


@needs_node
def test_the_screen_fallbacks_are_the_backends_sentences():
    ui = js("({text: L.UPDATE_TEXT, page: L.DOWNLOAD_PAGE})")
    assert ui["page"] == um.DOWNLOAD_PAGE
    assert ui["text"] == {
        "checkFailed": um.UPDATE_CHECK_FAILED,
        "noInstaller": um.UPDATE_NO_INSTALLER,
        "downloadFailed": um.UPDATE_DOWNLOAD_FAILED,
        "installFailed": um.UPDATE_INSTALL_FAILED,
    }


@needs_node
def test_a_failed_update_check_shows_the_backends_sentence():
    for msg in (um.UPDATE_CHECK_FAILED, um.UPDATE_CHECK_OFFLINE):
        v = js(f"L.updateCheckView({{update_available: false, current_version: '3.14.100', error: {json.dumps(msg)}}})")
        assert v["kind"] == "error"
        assert v["title"] == "Couldn't check for updates"
        assert v["subtitle"].startswith(msg.split(". ", 1)[1])
        assert v["subtitle"].endswith("You're on v3.14.100.")
        assert "primary" not in v


@needs_node
def test_a_release_with_no_installer_opens_its_page():
    v = js("L.updateCheckView({update_available: true, latest_version: '3.14.101', current_version: '3.14.100', "
           "download_url: '', release_url: 'https://github.com/jbf-tars/Waffler/releases/tag/v3.14.101', "
           f"no_installer: true, no_installer_message: {json.dumps(um.UPDATE_NO_INSTALLER)}}})")
    assert v["kind"] == "no_installer"
    assert v["subtitle"] == um.UPDATE_NO_INSTALLER
    assert v["primary"] == {"label": "Open release page",
                            "url": "https://github.com/jbf-tars/Waffler/releases/tag/v3.14.101"}
    # Without a release page it falls back to the download page, never a download.
    v = js("L.updateCheckView({update_available: true, latest_version: '9', current_version: '1', download_url: ''})")
    assert v["primary"] == {"label": "Open download page", "url": um.DOWNLOAD_PAGE}


@needs_node
def test_an_update_with_an_installer_downloads_it():
    v = js("L.updateCheckView({update_available: true, latest_version: '3.14.101', current_version: '3.14.100', "
           "download_url: 'https://github.com/x/releases/download/v3.14.101/Waffler-Setup.exe'})")
    assert v["primary"] == {"label": "Download & Install",
                            "download": "https://github.com/x/releases/download/v3.14.101/Waffler-Setup.exe"}
    assert v["browserUrl"] == um.DOWNLOAD_PAGE


@needs_node
def test_a_failed_download_is_one_sentence_and_the_download_page():
    v = js(f"L.updateFailureView({{error: {json.dumps(um.UPDATE_DOWNLOAD_FAILED)}, "
           f"error_detail: 'curl: (28) Operation timed out', download_page: {json.dumps(um.DOWNLOAD_PAGE)}}})")
    assert v == {"icon": "⚠️", "title": "The update didn't download",
                 "subtitle": "Try again, or get it from the download page.", "browserUrl": um.DOWNLOAD_PAGE}
    # A bridge failure with no answer at all still gets the plain sentence.
    v = js("L.updateFailureView(null, L.UPDATE_TEXT.installFailed)")
    assert v["title"] == "The update couldn't be installed"
    assert v["browserUrl"] == um.DOWNLOAD_PAGE


def test_no_raw_error_text_reaches_the_screen():
    app = code_only(read("app.js"))
    # Exception text and the backend's str(e) answers used to be shown as-is.
    assert "'Error: ' +" not in app
    assert "subtitle: String(e)" not in app
    assert "subtitle: p.error" not in app
    assert "throw new Error(r.error" not in app
    html = read("index.html")
    assert "Download in browser" not in html and ">Open download page<" in html
    for text in (app, html, code_only(read("logic.js"))):
        for jargon in ("HTTPSConnectionPool", "HTTP 403", "provider key", "untrusted URL"):
            assert jargon not in text


# ── Settings tells the truth (QW9) ───────────────────────────────────────────

import ast  # noqa: E402
import os  # noqa: E402
import types  # noqa: E402

import style_openai  # noqa: E402


@needs_node
def test_the_ui_starts_from_the_engines_provider_order():
    assert js("L.DEFAULT_PROVIDER_ORDER") == style_openai._DEFAULT_PROVIDER_ORDER
    assert js("L.normalizeProviderOrder(null)") == style_openai._normalize_provider_order(None)


def test_app_js_takes_its_starting_order_from_logic_js():
    app = code_only(read("app.js"))
    assert "let _providerOrder = WL.DEFAULT_PROVIDER_ORDER.slice();" in app
    # No hand-typed order left anywhere in the screen code.
    assert not re.search(r"\[\s*'groq'\s*,\s*'(?:cerebras|openai)'\s*,\s*'(?:cerebras|openai)'\s*\]", app)


@needs_node
@pytest.mark.parametrize("order", [
    [], ["openai"], ["cerebras", "groq"], ["OpenAI", "groq", "groq", "bogus"],
    ["cerebras", "openai", "groq"], [" Groq ", "CEREBRAS"],
])
def test_the_ui_cleans_an_order_the_same_way_as_the_engine(order):
    assert js(f"L.normalizeProviderOrder({json.dumps(order)})") == \
        style_openai._normalize_provider_order(order)


@needs_node
def test_providers_without_a_key_are_marked_in_the_order_list():
    rows = js("L.providerOrderRows(['cerebras','groq','openai'], "
              "{groq_key_set: true, api_key_set: false, cerebras_key_set: false})")
    assert [(r["id"], r["rank"], r["name"], r["hasKey"]) for r in rows] == [
        ("cerebras", 1, "Cerebras", False), ("groq", 2, "Groq", True), ("openai", 3, "OpenAI", False)]
    # Before settings load, nothing claims to have a key.
    assert [r["hasKey"] for r in js("L.providerOrderRows(null, null)")] == [False, False, False]


def test_the_order_list_greys_out_providers_with_no_key():
    app = code_only(read("app.js"))
    assert "WL.providerOrderRows(_providerOrder, _lastSettings)" in app
    assert "po-nokey" in app
    assert re.search(r"\.provider-order-item\.po-nokey\s*\{", code_only(read("style.css")))


@needs_node
@pytest.mark.parametrize("settings, line", [
    ({"transcription_backend": "groq", "styling_backend": "groq"},
     "Speech to text: Groq · Clean-up: Groq"),
    ({"transcription_backend": "api", "styling_backend": "cerebras"},
     "Speech to text: OpenAI · Clean-up: Cerebras"),
    ({"transcription_backend": "mlx", "styling_backend": "openai"},
     "Speech to text: on this Mac · Clean-up: OpenAI"),
    ({"transcription_backend": "none", "styling_backend": "cerebras"},
     "Speech to text: no key yet · Clean-up: Cerebras"),
    # Still starting: worked out from the keys and the order.
    ({"transcription_backend": "unknown", "styling_backend": "unknown", "groq_key_set": True,
      "cerebras_key_set": True, "provider_order": ["cerebras", "groq", "openai"]},
     "Speech to text: Groq · Clean-up: Cerebras"),
    ({"transcription_backend": "unknown", "styling_backend": "unknown"},
     "Speech to text: no key yet · Clean-up: no key yet"),
])
def test_the_in_use_line_says_what_each_stage_uses(settings, line):
    assert js(f"L.backendsLine({json.dumps(settings)})") == line


@needs_node
def test_the_about_line_names_the_models_in_use():
    about = js("L.aboutLine('3.14.100', {transcription_backend: 'groq', styling_backend: 'groq'})")
    assert about == "v3.14.100 · Powered by Whisper large v3 and gpt-oss-120b"
    assert "LLaMA" not in about
    assert js("L.aboutLine('3.14.100', {transcription_backend: 'api', styling_backend: 'openai'})") == \
        "v3.14.100 · Powered by gpt-4o-mini-transcribe and gpt-4.1-mini"
    # Nothing known yet: just the version, never a guess.
    assert js("L.aboutLine('3.14.100', null)") == "v3.14.100"


def test_settings_lists_groq_then_openai_then_cerebras():
    html = read("index.html")
    groq, openai, cerebras = (html.index(f'<div class="settings-row-label">{n} API Key</div>')
                              for n in ("Groq", "OpenAI", "Cerebras"))
    assert groq < openai < cerebras
    assert 'id="cerebrasKeyDesc">Optional.' in html


def test_the_header_and_settings_carry_no_stale_labels():
    html = read("index.html")
    app = code_only(read("app.js"))
    # The one-option mode menu is gone from the header.
    assert 'id="modeSelect"' not in html and "✨ Normal" not in html
    assert "loadMode(" not in app and "onModeChange" not in app
    assert "Active Backends" not in html and "STT: ${" not in app
    for text in (html, app):
        assert "LLaMA" not in text
        assert "Waiting for activation" not in text
    assert "WL.backendsLine(_lastSettings)" in app
    assert "WL.aboutLine(ver, _lastSettings)" in app


# get_settings() in app.py, lifted out of the source and run against the real
# WhisperTranscriber and OpenAIStyler built with made-up keys (no network).

def _get_settings(pipeline, stored=None):
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    fn = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "get_settings")
    ns = {"os": os, "_pipeline": pipeline}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    this = types.SimpleNamespace(_load_settings_file=lambda: dict(stored or {}))
    return ns["get_settings"](this)


def _pipeline(groq="", openai="", cerebras="", order=None):
    import transcribe_whisper as tw
    return types.SimpleNamespace(
        transcriber=tw.WhisperTranscriber(api_key=openai, groq_api_key=groq, provider_order=order),
        styler=style_openai.OpenAIStyler(api_key=openai, groq_api_key=groq,
                                         cerebras_api_key=cerebras, provider_order=order),
    )


FAKE = {"groq": "gsk_test_not_a_real_key", "openai": "sk-test-not-a-real-key",
        "cerebras": "csk-test-not-a-real-key"}


@pytest.mark.parametrize("keys, order, speech, cleanup", [
    # A Cerebras key used to make Settings name Cerebras for clean-up even
    # with Groq first in the order.
    (("groq", "cerebras"), None, "groq", "groq"),
    (("groq", "cerebras"), ["cerebras", "groq", "openai"], "groq", "cerebras"),
    (("groq", "openai"), ["openai", "groq", "cerebras"], "api", "openai"),
    (("openai",), None, "api", "openai"),
    (("cerebras",), None, "none", "cerebras"),
])
def test_get_settings_names_the_provider_each_stage_tries_first(monkeypatch, keys, order, speech, cleanup):
    monkeypatch.delenv("LOCAL_WHISPER", raising=False)
    p = _pipeline(order=order, **{k: FAKE[k] for k in keys})
    s = _get_settings(p, {"provider_order": order} if order else {})
    assert (s["transcription_backend"], s["styling_backend"]) == (speech, cleanup)


def test_get_settings_reports_the_engines_order_when_none_is_saved(monkeypatch):
    for k in ("OPENAI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    s = _get_settings(None)
    # It said groq, cerebras, openai while the engine ran groq, openai, cerebras.
    assert s["provider_order"] == style_openai._DEFAULT_PROVIDER_ORDER
    assert (s["transcription_backend"], s["styling_backend"]) == ("unknown", "unknown")
    # A saved order comes back cleaned, as the engine applies it.
    s = _get_settings(None, {"provider_order": ["Cerebras", "bogus"]})
    assert s["provider_order"] == ["cerebras", "groq", "openai"]


# ── Usage is counts first, and the money is labelled an estimate (QW10) ─────

USAGE_NOTE = "Estimated at each provider's published paid rates. Waffler can't see your bill."


@needs_node
def test_usage_shows_counts_and_a_labelled_estimate():
    v = js("L.usageView({transcription_count: 3291, today_cost_usd: 0.012, week_cost_usd: 0.0567, "
           "month_cost_usd: 0.06, total_cost_usd: 5.4321, avg_cost_per_transcription: 0.00165}, "
           "{total_words: 110457})")
    assert v["dictations"] == "3,291"
    assert v["words"] == "110,457"
    assert v["costs"] == {"today": "$0.01", "week": "$0.06", "month": "$0.06", "total": "$5.43",
                          "perDictation": "$0.002"}
    assert v["note"] == USAGE_NOTE
    # Nothing loaded yet: zeros, never "undefined" or "NaN".
    v = js("L.usageView(null, null)")
    assert v["dictations"] == "0" and v["words"] == "0" and v["costs"]["total"] == "$0.00"


def _usage_section():
    html = read("index.html")
    start = html.index('<div class="settings-section-title">📊 Usage</div>')
    return html[start:html.index('<div class="settings-section-title">', start + 10)]


def test_usage_puts_the_counts_before_the_money():
    usage = _usage_section()
    assert usage.index(">Dictations<") < usage.index(">Words<") < usage.index("Estimated cost") \
        < usage.index('id="usageTodayCost"')
    assert USAGE_NOTE in usage
    # decisions.md: the panel is never presented as what you spent.
    for word in ("spent", "spend", ">Transcriptions<", ">Avg Cost<"):
        assert word not in usage


def test_usage_fills_in_from_the_logic_helper():
    app = code_only(read("app.js"))
    body = app[app.index("async function loadUsageStats"):]
    body = body[:body.index("\n}\n") + 3]
    assert "WL.usageView(" in body
    assert "get_stats()" in body


# ── Journal search (QW11) ────────────────────────────────────────────────────

@needs_node
def test_a_search_with_no_matches_says_so_and_keeps_the_journal():
    assert js("L.feedView(0, '', 0)") == {"kind": "empty"}
    assert js("L.feedView(0, 'invoice', 0)") == {"kind": "empty"}
    assert js("L.feedView(60, '', 60)") == {"kind": "list"}
    assert js("L.feedView(60, 'invoice', 3)") == {"kind": "list"}
    # It used to show the first-run "Your journal is empty." here.
    assert js("L.feedView(60, '  quarterly budget ', 0)") == \
        {"kind": "no_match", "label": 'No entries match "quarterly budget"'}
    long = js("L.feedView(60, 'x'.repeat(80), 0)")["label"]
    assert long == 'No entries match "' + "x" * 40 + '…"'


@needs_node
def test_search_waits_for_a_pause_in_typing():
    assert 100 <= js("L.SEARCH_DEBOUNCE_MS") <= 200
    # A fake clock: five quick keystrokes make one render, with the last text.
    setup = """
      let now = 0, seq = 0; const timers = new Map();
      const T = { setTimeout: (f, ms) => { const id = ++seq; timers.set(id, [now + ms, f]); return id; },
                  clearTimeout: (id) => timers.delete(id) };
      const tick = (ms) => { now += ms; for (const [id, [at, f]] of [...timers]) if (at <= now) { timers.delete(id); f(); } };
      const seen = [];
      const run = L.debounce((q) => seen.push(q), 150, T);
    """
    assert js("(() => { ['i','in','inv','invo','invoice'].forEach((q) => { run(q); tick(40); }); "
              "const before = seen.length; tick(150); return [before, seen]; })()", setup) == [0, ["invoice"]]
    # cancel() drops a pending render (Clear search renders at once instead).
    assert js("(() => { run('a'); run.cancel(); tick(500); return seen; })()", setup) == []



@needs_node
def test_debounce_calls_the_browser_timers_the_way_a_browser_allows():
    # A browser's setTimeout throws "Illegal invocation" when called as a
    # method of another object, which Node allows; make Node strict too.
    setup = """
      const realSet = setTimeout;
      globalThis.setTimeout = function (f, d) {
        if (this !== undefined && this !== globalThis) throw new TypeError('Illegal invocation');
        return realSet(f, d);
      };
    """
    assert js("new Promise((done) => { const run = L.debounce((q) => done(q), 5); run('a'); run('b'); })",
              setup) == "b"

def test_search_input_is_debounced_and_clear_search_exists():
    app = code_only(read("app.js"))
    body = app[app.index("function onSearchInput"):]
    body = body[:body.index("\n}\n") + 3]
    assert "_renderFeedSoon()" in body and "renderFeed()" not in body
    assert "WL.debounce(() => renderFeed(), WL.SEARCH_DEBOUNCE_MS)" in app
    assert "WL.feedView(history.length, _searchText, filtered.length)" in app
    assert "function clearSearch()" in app
    html = read("index.html")
    no_match = html[html.index('id="noMatchState"'):]
    no_match = no_match[:no_match.index("</button>")]
    assert 'onclick="clearSearch()"' in no_match and ">Clear search" in no_match
