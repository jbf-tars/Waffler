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
