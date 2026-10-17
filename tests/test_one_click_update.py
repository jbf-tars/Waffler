"""Check for updates, and one click does the whole update.

Before: Settings, About had a quiet text link, the menu bar and tray had nothing, and an
update took three clicks (check, "Download and install", "Install now"). Now the About page
has a button, the menu bar (Mac) and the tray (Windows) have "Check for updates", and a
click on Check for updates, or on Update in the Journal, downloads the update, installs it
and opens Waffler again.
"""
import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")
APP_JS = (UI / "app.js").read_text(encoding="utf-8")
INDEX = (UI / "index.html").read_text(encoding="utf-8")
APP_PY = (ROOT / "app.py").read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_PY)


def js(expr):
    code = (f"const L = require({json.dumps(str(UI / 'logic.js'))});\n"
            f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def js_function(name):
    """The source of one top-level function in ui/app.js."""
    m = re.search(r"^(?:async )?function %s\b.*?^}\n" % re.escape(name), APP_JS, re.S | re.M)
    assert m, name
    return m.group(0)


# ── what the dialog says ─────────────────────────────────────────────────────

@needs_node
def test_while_it_downloads_the_dialog_names_the_version_and_says_it_opens_again():
    v = js("L.updatingView({latest_version: '3.16.1'}, 'downloading')")
    assert v["icon"] == "download" and v["title"] == "Updating Waffler to 3.16.1…"
    assert "opens again by itself" in v["subtitle"]


@needs_node
def test_without_a_version_it_still_reads_well():
    assert js("L.updatingView(null, 'downloading')")["title"] == "Updating Waffler…"
    assert js("L.updatingView({}, 'downloading')")["title"] == "Updating Waffler…"


@needs_node
def test_while_it_installs_it_says_waffler_is_closing_and_opens_again():
    v = js("L.updatingView({latest_version: '3.16.1'}, 'installing')")
    assert v["title"] == "Installing…" and "closing" in v["subtitle"] and "opens again" in v["subtitle"]


@needs_node
def test_the_words_have_no_em_dash_and_no_raw_error():
    for stage in ("downloading", "installing"):
        v = js(f"L.updatingView({{latest_version: '3.16.1'}}, {json.dumps(stage)})")
        assert "\u2014" not in v["title"] + v["subtitle"]


# ── one click does the whole update ──────────────────────────────────────────

def test_an_update_found_goes_straight_on_to_the_download():
    fn = js_function("openUpdateModalFromCheck")
    assert "v.kind === 'available'" in fn and "startDownloadFlow(v.primary.download)" in fn
    # ... and a release with no installer for this computer still opens its page.
    assert "primaryHandler" in fn and "open_url" in fn


def test_the_download_dialog_is_the_updating_one_with_a_cancel():
    fn = js_function("startDownloadFlow")
    assert "WL.updatingView(_lastUpdateInfo, 'downloading')" in fn
    assert "cancelLabel: 'Cancel'" in fn and "showProgress: true" in fn


def test_a_finished_download_installs_without_another_click():
    fn = js_function("pollUpdateProgress")
    done = fn[fn.index("if (p.done)"):]
    assert "installDownloadedUpdate();" in done
    assert "Install now" not in APP_JS and "Ready to install" not in APP_JS


def test_the_install_still_waits_for_a_dictation_in_progress():
    fn = js_function("installDownloadedUpdate")
    assert "r.busy" in fn and "_installRetryTimer" in fn and "WL.INSTALL_RETRY_MS" in fn
    assert "WL.updatingView(_lastUpdateInfo, 'installing')" in fn


def test_the_journal_notice_update_button_uses_the_same_one_click_flow():
    fn = js_function("checkForUpdates")
    assert "openUpdateModalFromCheck(r)" in fn


# ── where to find it ─────────────────────────────────────────────────────────

def test_the_about_page_has_a_button_not_a_quiet_link():
    m = re.search(r'<button[^>]*id="checkUpdateBtn"[^>]*>', INDEX)
    assert m and 'class="btn btn-sec btn-sm"' in m.group(0) and "checkForUpdatesManual()" in m.group(0)


def lifted(name, **names):
    node = next(n for n in APP_TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = dict(names)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), ns)
    return ns[name]


def test_the_menu_item_shows_the_window_and_starts_the_check_there():
    calls = []
    fn = lifted("_tray_check_for_updates",
                _tray_show_window=lambda *a: calls.append("show"),
                _post_js=lambda s: calls.append(("js", s)) or True,
                _log_to_file=lambda m: calls.append(("log", m)))
    fn()
    assert calls[0] == "show"
    assert calls[1][0] == "js" and "checkForUpdatesManual()" in calls[1][1]
    assert len(calls) == 2


def test_if_the_window_does_not_take_it_the_menu_says_so_in_the_log_and_does_not_raise():
    calls = []
    fn = lifted("_tray_check_for_updates", _tray_show_window=lambda *a: None,
                _post_js=lambda s: False, _log_to_file=calls.append)
    fn()
    assert calls and "[update]" in calls[0]


def test_the_page_script_is_safe_when_the_page_has_not_loaded():
    fn = lifted("_tray_check_for_updates", _tray_show_window=lambda *a: None,
                _post_js=lambda s: calls.append(s) or True, _log_to_file=lambda m: None)
    calls = []
    fn()
    # Guarded, so an early click is a no-op in the page, not a script error.
    assert calls[0].startswith("if (window.checkForUpdatesManual)")


def test_the_mac_menu_bar_has_check_for_updates():
    assert 'item_update = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(\n            "Check for Updates...", b"checkForUpdates:"' in APP_PY
    assert "def checkForUpdates_(self, _sender):" in APP_PY and "_tray_check_for_updates()" in APP_PY
    assert "menu.addItem_(item_update)" in APP_PY


def test_the_windows_tray_has_check_for_updates():
    assert 'pystray.MenuItem("Check for updates", _tray_check_for_updates)' in APP_PY


def test_the_function_the_menu_calls_exists_in_the_page():
    assert re.search(r"^async function checkForUpdatesManual\(", APP_JS, re.M)
