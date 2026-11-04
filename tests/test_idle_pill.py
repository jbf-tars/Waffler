"""S12, the Flow Bar half: the pill between dictations, its hover menu, and
the remembered position (src/idle_pill.py).

Off by default, and the promise that matters most is pinned first: with the
setting off and nothing saved, the overlay subprocess gets exactly the commands
it got before for a dictation, and the code that shows, hides, raises and keeps
the recording pill on the active Space is the code 3.16.5 shipped.

src/overlay_process.py imports AppKit, so its pieces are lifted out of the
source and run against fakes, as tests/test_working_pill.py does. app.py's
methods run in the pipeline harness and against temporary folders. No overlay
process is launched and no window is shown.
"""
import ast
import hashlib
import json
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import idle_pill as ip  # noqa: E402
import privacy_data  # noqa: E402
import user_messages as um  # noqa: E402
from overlay import RecordingOverlay  # noqa: E402
from _pipeline_harness import make_pipeline  # noqa: E402
from _settings_fake import settings_api  # noqa: E402

APP_SRC = (ROOT / "app.py").read_text(encoding="utf-8")
APP_TREE = ast.parse(APP_SRC)
MAC = ROOT / "src" / "overlay_process.py"
MAC_SRC = MAC.read_text(encoding="utf-8")
MAC_TREE = ast.parse(MAC_SRC)


# ═══ 1. Off, nothing saved: the overlay hears what it always did ═════════════

class _Stdin:
    def __init__(self):
        self.lines = []

    def write(self, data):
        self.lines.extend(json.loads(x) for x in data.splitlines() if x.strip())

    def flush(self):
        pass


class _Proc:
    pid = 4242

    def __init__(self):
        self.stdin = _Stdin()
        self.stdout = []
        self.stderr = []

    def poll(self):
        return None


def _overlay(**kw):
    o = RecordingOverlay(**kw)
    o._log = lambda *a, **k: None
    o._process = _Proc()
    return o


def _a_dictation(o):
    """What the pipeline sends for a dictation that is paused, processed
    slowly (the offer comes and goes), pasted, and then a toast with no
    recording behind it."""
    o.show()
    o.update_level(0.3)
    o.update_level(1.7)
    o.update_state("paused")
    o.update_state("recording")
    o.show_working(0.0, reliable=True)
    o.set_progress("Transcribing", 1.2)
    o.show_working(1.0)
    o.show_toast("info", "Still working on it", "Slow.",
                 buttons=[{"label": "Keep waiting", "action": "keep_waiting", "kind": "primary"}],
                 timeout=4)
    o.hide_toast(style="info")
    o.end_working("done")
    o.hide()
    o.show_toast("warn", "Not pasted", "b", reposition=True)
    o.hide_toast()


# Recorded from 3.16.5's src/overlay.py (origin/main) with the same calls.
_DICTATION_3_16_5 = [
    {"type": "show", "gen": 1}, {"type": "level", "value": 0.3}, {"type": "level", "value": 1.0},
    {"type": "state", "value": "paused"}, {"type": "state", "value": "recording"},
    {"type": "working", "elapsed_seconds": 0.0},
    {"type": "progress", "label": "Transcribing", "elapsed_seconds": 1.2},
    {"type": "working", "elapsed_seconds": 1.0},
    {"type": "show_toast", "style": "info", "heading": "Still working on it", "body": "Slow.",
     "buttons": [{"label": "Keep waiting", "action": "keep_waiting", "kind": "primary"}],
     "timeout": 4.0},
    {"type": "hide_toast", "style": "info"}, {"type": "working_end", "result": "done"},
    {"type": "hide"},
    {"type": "show_toast", "style": "warn", "heading": "Not pasted", "body": "b", "reposition": True},
    {"type": "hide_toast"},
]


def test_with_the_setting_off_and_nothing_saved_a_dictation_sends_what_3_16_5_sent():
    o = _overlay()
    o.set_pill_config(idle=False, positions={})
    _a_dictation(o)
    assert o._process.stdin.lines == _DICTATION_3_16_5


def test_never_told_about_the_pill_is_the_same():
    o = _overlay()
    _a_dictation(o)
    assert o._process.stdin.lines == _DICTATION_3_16_5


def test_a_new_overlay_process_is_told_nothing_while_off_and_nothing_is_saved(monkeypatch):
    import overlay as ov
    made = []

    def popen(cmd, **kw):
        made.append(_Proc())
        return made[-1]

    monkeypatch.setattr(ov.subprocess, "Popen", popen)
    o = RecordingOverlay()
    o._log = lambda *a, **k: None
    o.set_pill_config(idle=False, positions={})
    o._start_process()
    assert made and made[0].stdin.lines == []


def test_the_pipeline_with_the_setting_off_and_no_file_tells_the_overlay_nothing(tmp_path):
    p = _darwin_pipeline(tmp_path)
    p.overlay = _overlay()
    p.apply_pill_settings()
    assert p.overlay._process.stdin.lines == []
    assert not (tmp_path / ip.POSITIONS_FILE).exists()


def _strip(segment: str) -> str:
    """Source without comments, blank lines or indentation: the same on every
    Python version, unlike ast.dump."""
    out = []
    for line in segment.splitlines():
        line = re.sub(r"\s+#.*$", "", line).strip()
        if line and not line.startswith("#"):
            out.append(line)
    return "\n".join(out)


def _fn(name, tree=None):
    tree = MAC_TREE if tree is None else tree
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _dispatch_branches():
    node = _fn("_dispatch_cmd").body[-1]
    out = {}
    while isinstance(node, ast.If):
        t = node.test
        if isinstance(t, ast.Compare) and getattr(t.left, "id", "") == "ctype":
            out[t.comparators[0].value] = node
        node = node.orelse[0] if node.orelse and isinstance(node.orelse[0], ast.If) else None
    return out


def _src(node_or_nodes):
    nodes = node_or_nodes if isinstance(node_or_nodes, list) else [node_or_nodes]
    return _strip("\n".join(ast.get_source_segment(MAC_SRC, n) for n in nodes))


def _body_src(branch):
    return _src(branch.body)



def _h(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# The code that decides how and when the recording pill is shown, hidden,
# raised and moved across Spaces during a dictation, as 3.16.5 shipped it
# (computed from origin/main). S12 must not change any of it. If one of these
# is changed on purpose, update the hash here, and test the pill by hand on
# full-screen Spaces (swipe through three or more while dictating, Fn + Space,
# a silent take's toast) before shipping: that is what 3.14.68 and 3.14.69 fixed.
def _pinned():
    out = {name: _h(_src(_fn(name))) for name in (
        "_refresh_overlay_space_membership", "_reassert_overlay_window", "_schedule_show_retry",
        "_enter_working", "_end_working", "_show_toast", "_hide_toast", "_stdin_reader", "main")}
    branches = _dispatch_branches()
    for ctype in ("show", "_retry_show", "hide", "working", "working_end", "_done_expire",
                  "level", "show_toast", "hide_toast", "progress"):
        out["branch:" + ctype] = _h(_body_src(branches[ctype]))
    # Space changes: what happens while the pill is up. (While it is not,
    # the idle pill is brought along: that is new.)
    out["branch:_space_changed:visible"] = _h(_src(branches["_space_changed"].body[0].body))
    view = next(n for n in MAC_TREE.body if isinstance(n, ast.ClassDef) and n.name == "WaffleView")
    tick = next(n for n in view.body if isinstance(n, ast.FunctionDef) and n.name == "animTick_")
    out["animTick_:first7"] = _h(_src(tick.body[:7]))
    out["WaffleView.mouseDown_"] = _h(_src(next(
        n for n in view.body if isinstance(n, ast.FunctionDef) and n.name == "mouseDown_")))
    return out


_PINNED_ALL = {
    "_refresh_overlay_space_membership": "09814e34a7d5bdff",
    "_reassert_overlay_window": "e8048dc954b9e0a5",
    "_schedule_show_retry": "434860787c12aadf",
    "_enter_working": "b35664a5679cdda4",
    "_end_working": "6f6456037b7416cc",
    "_show_toast": "7cbece8d27ab4a36",
    "_hide_toast": "aacea7c55d7b13cc",
    "_stdin_reader": "bef313db64d0e162",
    "main": "4d024bfd03f120ed",
    "branch:show": "983e75fa71656d6f",
    "branch:_retry_show": "740afd2d7f9f9f57",
    "branch:hide": "c364151869d7862b",
    "branch:working": "1f2eb78babb0ecc2",
    "branch:working_end": "2141d5d27b16df42",
    "branch:_done_expire": "61fafa6a456d3ad7",
    "branch:level": "cf29fd6daace6e32",
    "branch:show_toast": "e66251d9e064f7c9",
    "branch:hide_toast": "e7ed868605522736",
    "branch:progress": "55b50d5ada5a1ef9",
    "branch:_space_changed:visible": "40668b9fd8a0c078",
    "animTick_:first7": "2588c152f7614138",
    "WaffleView.mouseDown_": "da8e28861ceec088",
}


def test_the_recording_pill_is_shown_hidden_and_kept_on_the_space_by_3_16_5s_code():
    assert _pinned() == _PINNED_ALL


def test_the_idle_pill_is_only_looked_after_once_it_exists():
    view = next(n for n in MAC_TREE.body if isinstance(n, ast.ClassDef) and n.name == "WaffleView")
    tick = next(n for n in view.body if isinstance(n, ast.FunctionDef) and n.name == "animTick_")
    assert len(tick.body) == 8, "one statement added to the animation tick, at the end"
    assert _src(tick.body[7]) == "if _idle_win is not None:\n_idle_guarded(_idle_tick)"
    # And no window is made for it until the setting is on.
    cfg = ast.get_source_segment(MAC_SRC, _fn("_apply_pill_config"))
    assert "if _idle_enabled:\n        _ensure_idle_window()" in cfg
    assert "_ensure_idle_window()" not in ast.get_source_segment(MAC_SRC, _fn("main"))


# ═══ 2. The setting ══════════════════════════════════════════════════════════

_API = next(n for n in APP_TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


def _lift_api(name, **ns):
    node = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module([node], []), "<app.py Api>", "exec"), ns)
    return ns[name]


class FakePipeline:
    def __init__(self, fail=False):
        self.applied = 0
        self.fail = fail

    def apply_pill_settings(self):
        self.applied += 1
        if self.fail:
            raise RuntimeError("no overlay")


def _settings(tmp_path, initial=None, pipeline=None):
    api = settings_api(tmp_path, initial)
    ns = {"os": __import__("os"), "_pipeline": pipeline, "_log_to_file": lambda m: None,
          "CLIPBOARD_CHOICE_UNKNOWN": um.CLIPBOARD_CHOICE_UNKNOWN,
          "SOUNDS_UNKNOWN": um.SOUNDS_UNKNOWN, "TIPS_UNKNOWN": um.TIPS_UNKNOWN,
          "_languages": __import__("languages")}
    return api, _lift_api("save_settings", **ns), _lift_api("get_settings", **ns)


def test_it_is_off_until_set(tmp_path):
    api, _save, get = _settings(tmp_path)
    assert get(api)["idle_pill"] is False


@pytest.mark.parametrize("stored, want", [
    ({"idle_pill": True}, True), ({"idle_pill": False}, False), ({"idle_pill": "true"}, False),
    ({"idle_pill": 1}, False), ({"idle_pill": None}, False), ({"idle_pill": [True]}, False),
])
def test_only_a_real_true_turns_it_on(tmp_path, stored, want):
    api, _save, get = _settings(tmp_path, stored)
    assert get(api)["idle_pill"] is want is ip.enabled(stored)


@pytest.mark.parametrize("value", [True, False])
def test_save_stores_the_switch_keeps_the_rest_and_applies_it_at_once(tmp_path, value):
    pipeline = FakePipeline()
    api, save, _get = _settings(tmp_path, {"hotkey_keys": ["fn"]}, pipeline)
    r = save(api, {"idle_pill": value})
    assert r["ok"] is True, r
    assert api.data == {"hotkey_keys": ["fn"], "idle_pill": value}
    _api2, _save2, get = _settings(tmp_path)
    assert get(api)["idle_pill"] is value
    assert pipeline.applied == 1
    assert any("pill" in n for n in r["notes"])


@pytest.mark.parametrize("bad", ["false", "true", "", 0, 1, None, [True], {"a": 1}])
def test_save_refuses_anything_else_and_saves_nothing(tmp_path, bad):
    pipeline = FakePipeline()
    api, save, _get = _settings(tmp_path, {"dialect": "en-GB", "idle_pill": True}, pipeline)
    r = save(api, {"idle_pill": bad, "dialect": "en-US"})
    assert r == {"ok": False, "error": um.IDLE_PILL_UNKNOWN}
    assert api.data == {"dialect": "en-GB", "idle_pill": True}
    assert api.saves == 0 and pipeline.applied == 0


def test_a_save_of_something_else_leaves_the_pill_alone(tmp_path):
    pipeline = FakePipeline()
    api, save, _get = _settings(tmp_path, {"idle_pill": True}, pipeline)
    assert save(api, {"auto_paste": False})["ok"] is True
    assert api.data == {"idle_pill": True, "auto_paste": False}
    assert pipeline.applied == 0


def test_a_failure_to_apply_never_fails_the_save(tmp_path):
    api, save, _get = _settings(tmp_path, None, FakePipeline(fail=True))
    assert save(api, {"idle_pill": True})["ok"] is True
    assert api.data == {"idle_pill": True}


def test_the_refusal_is_a_plain_sentence():
    assert um.IDLE_PILL_UNKNOWN.endswith(".") and "—" not in um.IDLE_PILL_UNKNOWN
    for line in (um.PILL_MENU_PASTE, um.PILL_MENU_COPY, um.PILL_MENU_OPEN, um.PILL_MENU_HIDE,
                 um.PILL_MENU_MIC_CAPTION, um.PILL_MENU_MIC_SYSTEM, um.PILL_POSITION_RESET,
                 um.PILL_POSITION_NOT_RESET):
        assert "—" not in line and "–" not in line


def test_reset_pill_position_forgets_every_screen_and_tells_the_overlay(tmp_path):
    store = ip.PillPositions(tmp_path / ip.POSITIONS_FILE)
    store.save("1@0,0,1512x982", 300, 40)
    pipeline = FakePipeline()
    reset = _lift_api("reset_pill_position", DATA_DIR=tmp_path, _pipeline=pipeline,
                      _log_to_file=lambda m: None, _user_messages=um)
    assert reset(None) == {"ok": True, "message": um.PILL_POSITION_RESET}
    assert store.load() == {} and not store.path().exists()
    assert pipeline.applied == 1
    assert reset(None)["ok"] is True          # nothing to forget is fine too


# ═══ 3. Where the pill sits ══════════════════════════════════════════════════

VIS = (0.0, 0.0, 1512.0, 945.0)        # a 14-inch MacBook Pro, less the menu bar


def test_a_screen_is_known_by_its_display_and_its_frame():
    assert ip.screen_key(1, (0, 0, 1512, 982)) == "1@0,0,1512x982"
    assert ip.screen_key(2, (-1920.0, 0.4, 1920, 1080)) == "2@-1920,0,1920x1080"
    assert ip.screen_key(1, (0, 0, 1512, 982)) != ip.screen_key(1, (1512, 0, 1512, 982))
    assert ip.screen_key("x", (0, 0, 1, 1)) == "0@0,0,1x1"


@pytest.mark.parametrize("x, y, want", [
    (100, 100, (100, 100)),          # inside: untouched
    (-50, 20, (0, 20)),              # off the left
    (1500, 20, (1512 - 46, 20)),     # off the right
    (100, -30, (100, 0)),            # under the Dock
    (100, 2000, (100, 945 - 18)),    # above the menu bar
])
def test_a_spot_is_kept_inside_the_visible_frame(x, y, want):
    assert ip.clamp_origin(x, y, ip.IDLE_W, ip.IDLE_H, VIS) == want


def test_both_pills_share_the_bottom_centre_that_is_saved():
    anchor = ip.anchor_of(700, 300, ip.IDLE_W)
    assert anchor == {"cx": 723.0, "y": 300.0}
    assert ip.origin_at(anchor, ip.IDLE_W, ip.IDLE_H, VIS) == (700.0, 300.0)
    big = ip.origin_at(anchor, 69, 97, VIS)
    assert big == (723.0 - 34.5, 300.0)
    # Saved near the top: the taller recording pill is kept on the screen.
    high = ip.origin_at({"cx": 723.0, "y": 930.0}, 69, 97, VIS)
    assert high == (723.0 - 34.5, 945.0 - 97)


def test_the_menu_opens_above_the_pill_or_below_when_there_is_no_room():
    pill = (700.0, 20.0, ip.IDLE_W, ip.IDLE_H)
    x, y = ip.menu_origin(pill, 200, 150, VIS)
    assert y == 20 + ip.IDLE_H + 6 and x == 700 + (ip.IDLE_W - 200) / 2
    top = (700.0, 920.0, ip.IDLE_W, ip.IDLE_H)
    assert ip.menu_origin(top, 200, 150, VIS)[1] == 920 - 6 - 150
    corner = (0.0, 20.0, ip.IDLE_W, ip.IDLE_H)
    assert ip.menu_origin(corner, 200, 150, VIS)[0] == 0


def test_a_toast_over_a_pill_moved_to_the_top_goes_under_it_and_stays_on_screen():
    assert ip.toast_origin_inside(100, 200, 380, 210, 100, VIS, 12) == (100, 200)
    assert ip.toast_origin_inside(100, 900, 380, 210, 800, VIS, 12) == (100, 800 - 12 - 210)
    assert ip.toast_origin_inside(1400, 200, 380, 210, 100, VIS, 12) == (1512 - 380, 200)


def test_positions_are_kept_per_screen(tmp_path):
    clock = iter(range(100, 200))
    store = ip.PillPositions(tmp_path / ip.POSITIONS_FILE, clock=lambda: next(clock))
    assert store.load() == {}
    assert store.save("1@0,0,1512x982", 723.04, 40)
    assert store.save("2@1512,0,2560x1440", 1900, 600)
    assert store.save("1@0,0,1512x982", 500, 41)          # moved again on the first
    assert store.load() == {"1@0,0,1512x982": {"cx": 500.0, "y": 41.0},
                            "2@1512,0,2560x1440": {"cx": 1900.0, "y": 600.0}}
    raw = json.loads(store.path().read_text(encoding="utf-8"))
    assert raw["version"] == ip.POSITIONS_VERSION and set(raw["screens"]) == set(store.load())


def test_only_the_most_recent_screens_are_kept(tmp_path):
    clock = iter(range(1000))
    store = ip.PillPositions(tmp_path / ip.POSITIONS_FILE, clock=lambda: next(clock))
    for i in range(ip.MAX_SCREENS + 3):
        store.save(f"{i}@0,0,100x100", i, i)
    kept = store.load()
    assert len(kept) == ip.MAX_SCREENS
    assert "0@0,0,100x100" not in kept and f"{ip.MAX_SCREENS + 2}@0,0,100x100" in kept


@pytest.mark.parametrize("bad", [
    "not json", "[]", '{"screens": []}', '{"screens": {"k": "x"}}', "", '{"screens": null}',
    "\x00\x01",
])
def test_a_damaged_file_reads_as_nothing_saved(tmp_path, bad):
    p = tmp_path / ip.POSITIONS_FILE
    p.write_text(bad, encoding="utf-8")
    store = ip.PillPositions(p)
    assert store.load() == {}
    assert store.save("1@0,0,10x10", 5, 5)                 # and a drop writes a good one
    assert store.load() == {"1@0,0,10x10": {"cx": 5.0, "y": 5.0}}


def test_entries_that_cannot_be_read_are_left_out_and_never_written_back(tmp_path):
    p = tmp_path / ip.POSITIONS_FILE
    p.write_text(json.dumps({"screens": {
        "good": {"cx": 10, "y": 20},
        "nan": {"cx": float("nan"), "y": 1}, "bool": {"cx": True, "y": 1},
        "far": {"cx": 1e9, "y": 1}, "missing": {"cx": 3}, "": {"cx": 1, "y": 1},
        "k" * 81: {"cx": 1, "y": 1},
    }}), encoding="utf-8")
    store = ip.PillPositions(p)
    assert store.load() == {"good": {"cx": 10.0, "y": 20.0}}
    store.save("new", 1, 2)
    assert set(json.loads(p.read_text(encoding="utf-8"))["screens"]) == {"good", "new"}


@pytest.mark.parametrize("key, cx, y", [
    (None, 1, 1), ("", 1, 1), ("k" * 81, 1, 1), ("k", "1", 1), ("k", 1, None),
    ("k", float("inf"), 1), ("k", True, 1),
])
def test_a_drop_that_cannot_be_kept_writes_nothing(tmp_path, key, cx, y):
    store = ip.PillPositions(tmp_path / ip.POSITIONS_FILE)
    assert store.save(key, cx, y) is False
    assert not store.path().exists()


def test_the_file_is_registered_with_privacy_and_data():
    assert ip.POSITIONS_FILE in privacy_data.KEPT_FILES
    assert ip.POSITIONS_FILE not in privacy_data.DATA_FILES
    assert not set(privacy_data.KEPT_FILES) & set(privacy_data.DATA_FILES)


def test_the_file_is_written_with_the_atomic_helper():
    src = (ROOT / "src" / "idle_pill.py").read_text(encoding="utf-8")
    assert "write_json_atomic(p," in src
    assert not re.search(r"(?<![\w.])open\(", src), "no bare open(): the store writes atomically"


# ═══ 4. The menu ═════════════════════════════════════════════════════════════

def test_the_menu_has_the_five_lines_in_order():
    items = ip.menu_items(has_last=True, mic_name="MacBook Pro Microphone")
    assert [i["action"] for i in items] == ["paste_last", "copy_last", "open", "microphone", "hide"]
    assert [i["label"] for i in items] == ["Paste last", "Copy last", "Open Waffler",
                                           "MacBook Pro Microphone", "Hide the pill"]
    assert all(i["enabled"] for i in items)
    assert items[3]["caption"] == "Microphone"


def test_paste_and_copy_are_greyed_while_there_is_nothing_to_paste():
    items = {i["action"]: i["enabled"] for i in ip.menu_items(has_last=False, mic_name=None)}
    assert items == {"paste_last": False, "copy_last": False, "open": True, "microphone": True,
                     "hide": True}


def test_the_microphone_line_names_the_one_in_use_cut_to_fit():
    assert ip.mic_label(None) == um.PILL_MENU_MIC_SYSTEM
    assert ip.mic_label("  ") == um.PILL_MENU_MIC_SYSTEM
    long = ip.mic_label("Siobhan's AirPods Pro (2nd generation) Hands-Free")
    assert len(long) <= ip.MIC_NAME_CHARS and long.endswith("…")
    assert long.startswith("Siobhan's AirPods Pro")
    assert ip.mic_label("Yeti\n Nano") == "Yeti Nano"


def _router(log):
    return dict(paste_last=lambda: log.append("paste"), copy_last=lambda: log.append("copy"),
                open_window=lambda: log.append("window"),
                open_microphones=lambda: log.append("mics"), hide_pill=lambda: log.append("hide"))


@pytest.mark.parametrize("action, called", [
    ("paste_last", "paste"), ("copy_last", "copy"), ("open", "window"),
    ("microphone", "mics"), ("hide", "hide"),
])
def test_each_line_runs_exactly_its_own_action(action, called):
    log = []
    assert ip.route(action, **_router(log)) == action
    assert log == [called]


@pytest.mark.parametrize("action", ["", "quit", None, 3, "paste_last ", "PASTE_LAST"])
def test_anything_else_runs_nothing(action):
    log = []
    assert ip.route(action, **_router(log)) is None
    assert log == []


def test_only_open_and_the_microphone_bring_the_window_forward():
    assert ip.OPENS_WINDOW == {"open", "microphone"}
    assert set(ip.ACTIONS) == {i["action"] for i in ip.menu_items(has_last=True, mic_name="x")}


class TestHover:
    def test_resting_on_the_pill_asks_once(self):
        h = ip.HoverMenu()
        assert h.tick(0.0, on_pill=True, on_menu=False) is None
        assert h.tick(0.1, on_pill=True, on_menu=False) is None
        assert h.tick(0.2, on_pill=True, on_menu=False) == "ask"
        assert h.tick(0.3, on_pill=True, on_menu=False) is None
        assert h.should_open(on_pill=True, on_menu=False)

    def test_passing_over_it_asks_nothing(self):
        h = ip.HoverMenu()
        h.tick(0.0, on_pill=True, on_menu=False)
        h.tick(0.1, on_pill=False, on_menu=False)
        assert h.tick(0.25, on_pill=True, on_menu=False) is None

    def test_an_answer_after_the_pointer_left_opens_nothing(self):
        h = ip.HoverMenu()
        for t in (0.0, 0.2):
            h.tick(t, on_pill=True, on_menu=False)
        h.tick(0.3, on_pill=False, on_menu=False)
        assert not h.should_open(on_pill=False, on_menu=False)

    def test_the_menu_stays_while_crossing_to_it_and_closes_after_leaving_both(self):
        h = ip.HoverMenu()
        for t in (0.0, 0.2):
            h.tick(t, on_pill=True, on_menu=False)
        h.opened()
        assert h.tick(0.3, on_pill=False, on_menu=False) is None       # in the gap
        assert h.tick(0.4, on_pill=False, on_menu=True) is None        # on the menu
        assert h.tick(0.9, on_pill=False, on_menu=True) is None
        assert h.tick(1.0, on_pill=False, on_menu=False) is None
        assert h.tick(1.0 + ip.LEAVE_GRACE_S / 2, on_pill=False, on_menu=False) is None
        assert h.tick(1.0 + ip.LEAVE_GRACE_S + 0.01, on_pill=False, on_menu=False) == "close"
        assert not h.open

    def test_an_answer_that_never_comes_is_asked_for_again(self):
        h = ip.HoverMenu()
        h.tick(0.0, on_pill=True, on_menu=False)
        assert h.tick(0.2, on_pill=True, on_menu=False) == "ask"
        assert h.tick(1.0, on_pill=True, on_menu=False) is None
        assert h.tick(0.2 + ip.ASK_TIMEOUT_S, on_pill=True, on_menu=False) == "ask"

    def test_no_menu_right_after_a_drag_until_the_pointer_has_left(self):
        h = ip.HoverMenu()
        h.dropped()
        for t in (0.0, 0.2, 0.5, 2.0):
            assert h.tick(t, on_pill=True, on_menu=False) is None
        h.tick(2.1, on_pill=False, on_menu=False)
        h.tick(2.2, on_pill=True, on_menu=False)
        assert h.tick(2.2 + ip.HOVER_DWELL_S + 0.01, on_pill=True, on_menu=False) == "ask"

    def test_a_click_asks_at_once_but_not_twice(self):
        h = ip.HoverMenu()
        assert h.ask_now(0.0) is True
        assert h.ask_now(0.1) is False
        h.opened()
        assert h.ask_now(5.0) is False


# ═══ 5. The app's side: what each line does ══════════════════════════════════

def _darwin_pipeline(tmp_path, stored=None):
    p = make_pipeline(tmp_path)
    p.ns["_platform"] = types.SimpleNamespace(system=lambda: "Darwin")
    if stored is not None:
        (tmp_path / "settings.json").write_text(json.dumps(stored), encoding="utf-8")
    p._pill_positions = ip.PillPositions(tmp_path / ip.POSITIONS_FILE)
    return p


def _wired(tmp_path, busy=False):
    p = _darwin_pipeline(tmp_path, {"idle_pill": True})
    did = []
    p.paste_last = lambda aim_at_last_app=True: did.append(("paste", aim_at_last_app))
    p.copy_last = lambda: did.append(("copy",))
    p._last_result_blocked = lambda: busy
    p.ns["_tray_show_window"] = lambda *a, **k: did.append(("window",))
    p.ns["_store_setting"] = lambda key, value: did.append(("setting", key, value))
    return p, did


@pytest.mark.parametrize("action, want", [
    ("paste_last", [("paste", False)]),
    ("copy_last", [("copy",)]),
    ("hide", [("setting", "idle_pill", False)]),
    ("open", [("window",)]),
    ("microphone", [("window",)]),
])
def test_each_line_calls_the_right_thing(tmp_path, action, want):
    p, did = _wired(tmp_path)
    p._run_pill_action(action)
    assert did == want


def test_paste_copy_and_hide_never_bring_waffler_forward(tmp_path):
    for action in ("paste_last", "copy_last", "hide"):
        p, did = _wired(tmp_path)
        p._run_pill_action(action)
        assert ("window",) not in did, action
        assert not any("waffler_show_microphone" in s for s in p.page.scripts)


def test_paste_last_pastes_where_the_person_is_typing():
    """The menu never takes the focus, so the app in front is the one to paste
    into: aim_at_last_app=False, as for the Paste last shortcut."""
    body = ast.get_source_segment(APP_SRC, _pipeline_fn("_pill_paste_last"))
    assert "self.paste_last(aim_at_last_app=False)" in body


def test_paste_last_waits_for_a_dictation_in_progress(tmp_path):
    p, did = _wired(tmp_path, busy=True)
    p._run_pill_action("paste_last")
    assert did == []
    assert any("refused: busy" in line for line in p.logged)


def test_the_microphone_line_opens_settings_at_microphones(tmp_path):
    p, did = _wired(tmp_path)
    p._run_pill_action("microphone")
    assert did == [("window",)]
    assert any("waffler_show_microphone" in s for s in p.page.scripts)


def test_hide_turns_the_setting_off_tells_the_overlay_and_the_window(tmp_path):
    p, did = _wired(tmp_path)
    p.ns["_store_setting"] = lambda key, value: (tmp_path / "settings.json").write_text(
        json.dumps({key: value}), encoding="utf-8")
    p._run_pill_action("hide")
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8")) == {"idle_pill": False}
    assert ("set_pill_config", (False, {}), {}) in p.overlay.calls
    assert any("waffler_pill_idle_changed(false)" in s for s in p.page.scripts)


def test_an_unknown_or_failing_action_is_logged_not_raised(tmp_path):
    p, did = _wired(tmp_path)
    p._run_pill_action("format_disk")
    assert did == [] and any("unknown menu action" in line for line in p.logged)

    def boom():
        raise RuntimeError("clipboard locked")
    p.copy_last = boom
    p._run_pill_action("copy_last")
    assert any("menu action failed" in line for line in p.logged)


def test_hover_answers_with_the_menu_now(tmp_path):
    p, _ = _wired(tmp_path)
    p.audio.opened_device = lambda: "Yeti Nano"
    p._on_pill_event("hover", {})
    (name, (items,), _k), = [c for c in p.overlay.calls if c[0] == "show_pill_menu"]
    assert [i["label"] for i in items][3] == "Yeti Nano"
    assert items[0]["enabled"] is False                   # nothing dictated yet
    p._last_result.set("hello there")
    p._on_pill_event("hover", {})
    assert [c for c in p.overlay.calls if c[0] == "show_pill_menu"][-1][1][0][0]["enabled"] is True


def test_a_drop_is_saved_for_its_screen(tmp_path):
    p, _ = _wired(tmp_path)
    p._save_pill_position("1@0,0,1512x982", 600.0, 33.0)
    assert p._pill_positions.load() == {"1@0,0,1512x982": {"cx": 600.0, "y": 33.0}}
    p._save_pill_position(None, "x", 1)                     # nonsense from the pipe: ignored
    assert len(p._pill_positions.load()) == 1


def test_menu_events_are_handled_off_the_overlays_reader_thread():
    body = ast.get_source_segment(APP_SRC, _pipeline_fn("_on_pill_event"))
    assert body.count("threading.Thread(") == 2 and "daemon=True" in body


def test_the_pipeline_sends_the_setting_and_the_spots_on_a_mac_only(tmp_path):
    p = _darwin_pipeline(tmp_path, {"idle_pill": True})
    p._pill_positions.save("1@0,0,10x10", 5, 5)
    p.apply_pill_settings()
    assert p.overlay.calls[-1] == ("set_pill_config", (True, {"1@0,0,10x10": {"cx": 5.0, "y": 5.0}}), {})
    p.ns["_platform"] = types.SimpleNamespace(system=lambda: "Windows")
    p.overlay.calls.clear()
    p.apply_pill_settings()
    assert p.overlay.calls == []


def test_the_pipeline_hands_the_overlay_its_settings_before_the_overlay_starts():
    init = ast.get_source_segment(APP_SRC, _pipeline_fn("__init__"))
    assert "on_pill_event=self._on_pill_event" in init
    assert init.index("self.apply_pill_settings()") < init.index("target=self.overlay.prestart")


def _pipeline_fn(name):
    cls = next(n for n in APP_TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


# ═══ 6. The overlay protocol, parent side (src/overlay.py) ═══════════════════

def test_the_config_is_sent_when_on_or_saved_and_once_more_to_turn_it_off():
    o = _overlay()
    o.set_pill_config(idle=True, positions={})
    o.set_pill_config(idle=False, positions={"k": {"cx": 1, "y": 2}})
    o.set_pill_config(idle=False, positions={})               # back to the default: said once
    o.set_pill_config(idle=False, positions={})               # and then nothing more
    assert o._process.stdin.lines == [
        {"type": "pill_config", "idle": True, "positions": {}},
        {"type": "pill_config", "idle": False, "positions": {"k": {"cx": 1, "y": 2}}},
        {"type": "pill_config", "idle": False, "positions": {}},
    ]


def test_a_new_overlay_process_is_given_the_config_when_it_is_not_the_default(monkeypatch):
    import overlay as ov
    made = []
    monkeypatch.setattr(ov.subprocess, "Popen", lambda cmd, **kw: made.append(_Proc()) or made[-1])
    o = RecordingOverlay()
    o._log = lambda *a, **k: None
    o.set_pill_config(idle=True, positions={"k": {"cx": 1, "y": 2}})   # not up yet: kept
    assert made == []
    o._start_process()
    assert made[0].stdin.lines == [{"type": "pill_config", "idle": True,
                                    "positions": {"k": {"cx": 1, "y": 2}}}]


def test_the_menu_is_sent_as_given():
    o = _overlay()
    items = ip.menu_items(has_last=True, mic_name="Yeti")
    o.show_pill_menu(items)
    assert o._process.stdin.lines == [{"type": "pill_menu", "items": items}]


def test_pill_events_reach_the_app_and_a_failing_handler_does_not_stop_the_reader():
    got = []

    def handler(name, data):
        got.append((name, data))
        if name == "hover":
            raise RuntimeError("busy")

    class Child:
        stdout = ['{"event": "pill_hover"}\n',
                  '{"event": "pill_action", "action": "copy_last"}\n',
                  '{"event": "pill_moved", "screen": "1@0,0,10x10", "cx": 5, "y": 6}\n',
                  '{"event": "stop"}\n']

    stops = []
    o = RecordingOverlay(on_pill_event=handler, on_stop=lambda: stops.append(1))
    o._log = lambda *a, **k: None
    o._read_stdout(Child())
    assert [n for n, _ in got] == ["hover", "action", "moved"]
    assert got[1][1]["action"] == "copy_last" and got[2][1]["screen"] == "1@0,0,10x10"
    assert stops == [1]
    # An older caller without the handler: the events are ignored.
    o2 = RecordingOverlay()
    o2._log = lambda *a, **k: None
    o2._read_stdout(Child())


# ═══ 7. The overlay protocol, Mac side, with fakes ═══════════════════════════

def _handled_types(func):
    out = set()
    for node in ast.walk(_fn(func)):
        if isinstance(node, ast.Compare) and getattr(node.left, "id", "") == "ctype":
            out |= {c.value for c in node.comparators if isinstance(c, ast.Constant)}
    return out


def test_the_mac_overlay_handles_the_new_commands():
    assert {"pill_config", "pill_menu", "_quit_patience"} <= _handled_types("_dispatch_cmd")


def _pt(x, y):
    return types.SimpleNamespace(x=float(x), y=float(y))


def _r(x, y, w, h):
    return types.SimpleNamespace(origin=_pt(x, y), size=types.SimpleNamespace(width=float(w), height=float(h)))


class FakeScreen:
    def __init__(self, did, frame, visible):
        self.did, self._f, self._v = did, frame, visible

    def frame(self):
        return _r(*self._f)

    def visibleFrame(self):
        return _r(*self._v)

    def deviceDescription(self):
        return {"NSScreenNumber": self.did}


LAPTOP = FakeScreen(1, (0, 0, 1512, 982), (0, 0, 1512, 945))
MONITOR = FakeScreen(2, (1512, 0, 2560, 1440), (1512, 70, 2560, 1345))   # Dock at its bottom


class FakeWindow:
    def __init__(self, frame=(0, 0, 46, 18)):
        self.f = list(frame)
        self.calls = []

    def frame(self):
        return _r(*self.f)

    def setFrameOrigin_(self, p):
        self.f[0], self.f[1] = p.x, p.y
        self.calls.append(("origin", p.x, p.y))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a: self.calls.append((name,) + a)


class FakeApp:
    def __init__(self):
        self.calls = []

    def replyToApplicationShouldTerminate_(self, yes):
        self.calls.append(("reply", yes))

    def terminate_(self, sender):
        self.calls.append(("terminate", sender))


_MAC_CONSTS = ("OUTER_RIM", "INNER_PAD", "GRID_ROWS", "GRID_COLS", "CELL_SIZE", "GRID_GAP",
               "WAFFLE_W", "WAFFLE_H", "BTN_R", "BTN_GAP", "WIN_W", "WIN_H", "TOAST_W", "TOAST_H",
               "TOAST_PAD", "IDLE_CAPSULE_W", "IDLE_CAPSULE_H", "MENU_W", "MENU_PAD", "MENU_ITEM_H",
               "MENU_CAPTION_ITEM_H", "_NS_TERMINATE_CANCEL", "_NS_TERMINATE_NOW",
               "_NS_TERMINATE_LATER")
_MAC_FUNCS = ("_idle_guarded", "_compute_overlay_position", "_rect", "_point_in", "_screen_key_of", "_screen_at",
              "_cursor_screen", "_saved_origin_on", "_idle_target_origin", "_toast_origin",
              "_apply_pill_config", "_idle_place", "_idle_show", "_idle_hide", "_idle_tick",
              "_idle_on_space_changed", "_open_pill_menu", "_close_pill_menu", "_idle_press",
              "_idle_drag_to", "_idle_release", "_answer_pending_quit", "_dispatch_cmd")


class _Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def time(self):
        return self.now


class _FakeTimer:
    made = []

    def __init__(self, delay, fn):
        self.delay, self.fn, self.daemon, self.started = delay, fn, False, False
        _FakeTimer.made.append(self)

    def start(self):
        self.started = True


def mac(screens=(LAPTOP, MONITOR), mouse=(700, 500)):
    """overlay_process.py's idle-pill pieces in a namespace of fakes."""
    import queue
    clock = _Clock()
    ns = {"_ip": ip, "sys": sys, "queue": queue, "time": clock, "clock": clock}
    nodes = [n for n in MAC_TREE.body
             if (isinstance(n, ast.FunctionDef) and n.name in _MAC_FUNCS)
             or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in _MAC_CONSTS)]
    found = {n.name for n in nodes if isinstance(n, ast.FunctionDef)}
    assert found == set(_MAC_FUNCS), set(_MAC_FUNCS) - found
    exec(compile(ast.Module(nodes, []), str(MAC), "exec"), ns)
    emitted, made, dbg = [], [], []
    app = FakeApp()
    ns.update(
        NSEvent=types.SimpleNamespace(mouseLocation=lambda: _pt(*ns["mouse"])),
        NSScreen=types.SimpleNamespace(screens=lambda: list(screens),
                                       mainScreen=lambda: screens[0] if screens else None),
        NSMakePoint=_pt, NSMakeRect=_r,
        NSApplication=types.SimpleNamespace(sharedApplication=lambda: app),
        NSStatusWindowLevel=25, _OVERLAY_COLLECTION_BEHAVIOR=0x111,
        NSColor=types.SimpleNamespace(clearColor=lambda: "clear"),
        emit=lambda event, **k: emitted.append((event, k)),
        _dbg=lambda event, **k: dbg.append(event),
        _new_overlay_window=lambda rect: made.append(FakeWindow((rect.origin.x, rect.origin.y,
                                                                 rect.size.width, rect.size.height)))
        or made[-1],
        PillMenuView=types.SimpleNamespace(alloc=lambda: types.SimpleNamespace(
            initWithFrame_=lambda f: types.SimpleNamespace(set_items=lambda items: None,
                                                           track=lambda loc: None))),
        _ensure_idle_window=lambda: None,
        _hide_toast=lambda *a: None,
        mouse=mouse, emitted=emitted, made=made, dbg=dbg, app=app,
        _cmd_queue=queue.Queue(),
        _saved_positions={}, _idle_enabled=False, _idle_win=None, _idle_view=None,
        _idle_shown=False, _idle_drag=None, _idle_ticks=0, _menu_win=None, _menu_view=None,
        _hover=ip.HoverMenu(), _visible=False, _toast_win=None, _waffle_x=0.0, _waffle_y=0.0,
        _terminate_pending=False, _parent_asked_quit=False, _show_gen=0, _done_gen=0,
        _mode="recording", _g_window=None, _g_view=None,
    )
    return ns


def _old_position(screen):
    """3.16.5's placement: centred on the visible frame, 16 points up."""
    vf = screen.visibleFrame()
    return vf.origin.x + (vf.size.width - 69) / 2.0, vf.origin.y + 16


@pytest.mark.parametrize("mouse, screen", [((700, 500), LAPTOP), ((2000, 900), MONITOR),
                                           ((-5000, -5000), LAPTOP)])
def test_with_nothing_saved_the_recording_pill_goes_where_it_always_went(mouse, screen):
    ns = mac(mouse=mouse)
    assert ns["WIN_W"] == 69
    assert ns["_compute_overlay_position"]() == _old_position(screen)


def test_a_spot_saved_for_this_screen_places_the_recording_pill_there():
    ns = mac(mouse=(2000, 900))
    key = ip.screen_key(2, (1512, 0, 2560, 1440))
    ns["_saved_positions"].update({key: {"cx": 2000.0, "y": 400.0}})
    assert ns["_compute_overlay_position"]() == (2000.0 - 34.5, 400.0)
    # On the other screen, which has nothing saved, it is where it always was.
    ns["mouse"] = (700, 500)
    assert ns["_compute_overlay_position"]() == _old_position(LAPTOP)


def test_a_spot_saved_for_a_screen_that_is_gone_is_not_used():
    ns = mac(screens=(LAPTOP,), mouse=(700, 500))
    ns["_saved_positions"].update({ip.screen_key(2, (1512, 0, 2560, 1440)): {"cx": 2000.0, "y": 400.0},
                                   ip.screen_key(1, (0, 0, 1920, 1080)): {"cx": 900.0, "y": 400.0}})
    assert ns["_compute_overlay_position"]() == _old_position(LAPTOP)


def test_a_saved_spot_is_kept_inside_the_screen_it_is_used_on():
    ns = mac(mouse=(700, 500))
    ns["_saved_positions"].update({ip.screen_key(1, (0, 0, 1512, 982)): {"cx": 1510.0, "y": 940.0}})
    assert ns["_compute_overlay_position"]() == (1512 - 69, 945 - 97)


def test_toasts_are_where_they_were_unless_a_spot_is_saved():
    ns = mac()
    assert ns["_toast_origin"]((700.0, 16.0)) == (700 + (69 - 380) // 2, 16 + 69 + 12)
    ns["_saved_positions"].update({ip.screen_key(1, (0, 0, 1512, 982)): {"cx": 734.5, "y": 840.0}})
    tx, ty = ns["_toast_origin"]((700.0, 840.0))
    assert ty == 840 - 12 - 210 and tx == 700 + (69 - 380) // 2


def _idle_on(ns):
    ns["_idle_win"] = FakeWindow()
    ns["_dispatch_cmd"]({"type": "pill_config", "idle": True, "positions": {}})
    ns["_idle_tick"]()


def test_turned_on_the_idle_pill_shows_at_the_bottom_centre_without_taking_the_focus():
    ns = mac()
    _idle_on(ns)
    w = ns["_idle_win"]
    assert ns["_idle_shown"] is True
    assert ("origin", (1512 - 46) / 2.0, 16.0) in w.calls
    names = [c[0] for c in w.calls]
    assert "orderFrontRegardless" in names
    assert not any("Key" in n or "activate" in n.lower() for n in names)


def test_the_idle_pill_steps_aside_for_the_recording_pill_and_toasts_and_comes_back():
    ns = mac()
    _idle_on(ns)
    w = ns["_idle_win"]
    ns["_visible"] = True                          # a dictation's show
    ns["_idle_tick"]()
    assert ns["_idle_shown"] is False and ("orderOut_", None) in w.calls
    ns["_visible"] = False                         # its hide (or the tick ending)
    ns["_idle_tick"]()
    assert ns["_idle_shown"] is True
    ns["_toast_win"] = object()                    # a toast
    ns["_idle_tick"]()
    assert ns["_idle_shown"] is False
    ns["_toast_win"] = None
    ns["_idle_tick"]()
    assert ns["_idle_shown"] is True


def test_the_idle_pill_never_touches_the_recording_pills_window_or_its_position():
    ns = mac()
    g = FakeWindow((0, 0, 69, 97))
    ns["_g_window"], ns["_waffle_x"], ns["_waffle_y"] = g, 721.5, 16.0
    _idle_on(ns)
    ns["mouse"] = (ns["_idle_win"].f[0] + 5, 20)
    for _ in range(10):
        ns["clock"].now += 0.05
        ns["_idle_tick"]()
    ns["_idle_press"]()
    ns["mouse"] = (100, 300)
    ns["_idle_drag_to"]()
    ns["_idle_release"]()
    ns["_dispatch_cmd"]({"type": "pill_config", "idle": False, "positions": {}})
    ns["_idle_tick"]()
    assert g.calls == []
    assert (ns["_waffle_x"], ns["_waffle_y"]) == (721.5, 16.0)


def test_turning_it_off_hides_it():
    ns = mac()
    _idle_on(ns)
    ns["_dispatch_cmd"]({"type": "pill_config", "idle": False, "positions": {}})
    assert ns["_idle_shown"] is False


def test_resting_on_it_asks_the_app_for_the_menu_and_the_answer_opens_it():
    ns = mac()
    _idle_on(ns)
    w = ns["_idle_win"]
    ns["mouse"] = (w.f[0] + 10, w.f[1] + 5)
    for _ in range(6):
        ns["clock"].now += 0.05
        ns["_idle_tick"]()
    assert ns["emitted"] == [("pill_hover", {})]
    ns["_dispatch_cmd"]({"type": "pill_menu", "items": ip.menu_items(has_last=True, mic_name="Yeti")})
    menu = ns["_menu_win"]
    assert menu is not None and menu in ns["made"]
    assert ("orderFrontRegardless",) in menu.calls
    assert not any("Key" in c[0] for c in menu.calls)
    assert menu.f[1] == w.f[1] + ip.IDLE_H + 6                # above the pill
    # Leaving both for a moment closes it.
    ns["mouse"] = (5, 600)
    for _ in range(12):
        ns["clock"].now += 0.05
        ns["_idle_tick"]()
    assert ns["_menu_win"] is None and ("orderOut_", None) in menu.calls


def test_a_menu_that_arrives_after_the_pointer_left_is_not_opened():
    ns = mac()
    _idle_on(ns)
    w = ns["_idle_win"]
    ns["mouse"] = (w.f[0] + 10, w.f[1] + 5)
    for _ in range(6):
        ns["clock"].now += 0.05
        ns["_idle_tick"]()
    ns["mouse"] = (5, 600)
    ns["_dispatch_cmd"]({"type": "pill_menu", "items": ip.menu_items(has_last=True, mic_name="Yeti")})
    assert ns["_menu_win"] is None


def test_a_menu_nobody_asked_for_is_not_opened():
    ns = mac()
    _idle_on(ns)
    w = ns["_idle_win"]
    ns["mouse"] = (w.f[0] + 10, w.f[1] + 5)
    ns["_dispatch_cmd"]({"type": "pill_menu", "items": ip.menu_items(has_last=True, mic_name="Yeti")})
    assert ns["_menu_win"] is None


def test_a_drag_moves_it_keeps_it_on_screen_and_saves_it_for_that_screen():
    ns = mac()
    _idle_on(ns)
    w = ns["_idle_win"]
    start = (w.f[0] + 10, w.f[1] + 5)
    ns["mouse"] = start
    ns["_idle_press"]()
    ns["mouse"] = (start[0] - 2000, start[1] + 300)     # far off the left edge
    ns["_idle_drag_to"]()
    ns["_idle_release"]()
    assert w.f[0] == 0.0 and w.f[1] == 16.0 + 300
    key = ip.screen_key(1, (0, 0, 1512, 982))
    want = {"cx": 23.0, "y": 316.0}
    assert ns["emitted"] == [("pill_moved", {"screen": key, **want})]
    assert ns["_saved_positions"] == {key: want}
    assert ns["_hover"].quiet, "no menu pops up under the pointer that just dropped it"
    # The next recording pill on that screen sits over it.
    assert ns["_compute_overlay_position"]() == (0.0, 316.0)


def test_a_drag_onto_the_other_screen_is_saved_for_that_screen():
    ns = mac()
    _idle_on(ns)
    w = ns["_idle_win"]
    ns["mouse"] = (w.f[0] + 10, w.f[1] + 5)
    ns["_idle_press"]()
    ns["mouse"] = (2500, 500)
    ns["_idle_drag_to"]()
    ns["_idle_release"]()
    (event, data), = ns["emitted"]
    assert data["screen"] == ip.screen_key(2, (1512, 0, 2560, 1440))


def test_a_click_without_a_drag_asks_for_the_menu_at_once():
    ns = mac()
    _idle_on(ns)
    w = ns["_idle_win"]
    ns["mouse"] = (w.f[0] + 10, w.f[1] + 5)
    ns["_idle_press"]()
    ns["mouse"] = (w.f[0] + 11, w.f[1] + 6)              # a twitch, not a drag
    ns["_idle_drag_to"]()
    ns["_idle_release"]()
    assert ns["emitted"] == [("pill_hover", {})]
    assert ("origin", w.f[0] + 1, w.f[1] + 1) not in w.calls


def test_reset_puts_the_idle_pill_back_and_the_recording_pill_follows():
    ns = mac()
    key = ip.screen_key(1, (0, 0, 1512, 982))
    ns["_idle_win"] = FakeWindow()
    ns["_dispatch_cmd"]({"type": "pill_config", "idle": True, "positions": {key: {"cx": 100, "y": 300}}})
    ns["_idle_tick"]()
    assert (ns["_idle_win"].f[0], ns["_idle_win"].f[1]) == (77.0, 300.0)
    ns["_dispatch_cmd"]({"type": "pill_config", "idle": True, "positions": {}})
    assert (ns["_idle_win"].f[0], ns["_idle_win"].f[1]) == ((1512 - 46) / 2.0, 16.0)
    assert ns["_compute_overlay_position"]() == _old_position(LAPTOP)


def test_a_space_change_brings_the_idle_pill_along():
    ns = mac()
    _idle_on(ns)
    w = ns["_idle_win"]
    w.calls.clear()
    ns["_dispatch_cmd"]({"type": "_space_changed"})
    assert ("orderFrontRegardless",) in w.calls
    assert ("setCollectionBehavior_", 0x111) in w.calls


def test_a_fault_in_the_idle_pill_never_reaches_the_dispatcher():
    ns = mac()
    ns["_idle_win"] = FakeWindow()
    ns["_ensure_idle_window"] = lambda: 1 / 0
    ns["_dispatch_cmd"]({"type": "pill_config", "idle": True, "positions": {}})
    ns["NSEvent"] = None                           # mouseLocation now raises
    ns["_hover"].asked_at = 0.0
    ns["_dispatch_cmd"]({"type": "pill_menu", "items": [{"action": "open", "label": "Open"}]})
    ns["_idle_guarded"](ns["_idle_tick"])
    assert "idle.error" in ns["dbg"]
    for name in ("_apply_pill_config", "_open_pill_menu", "_idle_on_space_changed"):
        assert f"_idle_guarded({name}" in MAC_SRC, name


def test_a_damaged_config_is_read_as_nothing_saved():
    ns = mac()
    ns["_idle_win"] = FakeWindow()
    ns["_dispatch_cmd"]({"type": "pill_config", "idle": "yes", "positions": "everywhere"})
    assert ns["_saved_positions"] == {} and ns["_idle_enabled"] is True


def test_the_idle_and_menu_windows_are_non_activating_panels():
    code = "\n".join(line.split("#", 1)[0] for line in MAC_SRC.splitlines())
    assert "_idle_win = _new_overlay_window(" in code
    assert "_menu_win = _new_overlay_window(" in code
    for bad in ("makeKeyWindow", "makeKeyAndOrderFront_", "activateIgnoringOtherApps",
                "NSRunningApplication", ".activate("):
        assert bad not in code, bad
    # Their views take the first click, as the pill's and the toasts' do.
    for cls in ("IdlePillView", "PillMenuView"):
        node = next(n for n in MAC_TREE.body if isinstance(n, ast.ClassDef) and n.name == cls)
        fn = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "acceptsFirstMouse_")
        assert ast.unparse(fn.body[-1]) == "return True"


def test_a_menu_click_sends_the_action_and_closes_the_menu():
    node = next(n for n in MAC_TREE.body if isinstance(n, ast.ClassDef) and n.name == "PillMenuView")
    fn = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "mouseDown_")
    body = ast.get_source_segment(MAC_SRC, fn)
    assert 'emit("pill_action", action=str(it["action"]))' in body
    assert body.index("emit(") < body.index("_close_pill_menu()")
    assert 'if not it.get("enabled", True):\n            return' in body


# ═══ 8. The 3.16.5 quit leftovers ════════════════════════════════════════════

def _delegate(ns):
    node = next(n for n in MAC_TREE.body if isinstance(n, ast.ClassDef) and n.name == "OverlayAppDelegate")
    import quit_flow
    ns.update(NSObject=object, overlay_terminate_decision=quit_flow.overlay_terminate_decision,
              OVERLAY_FORWARD_PATIENCE_S=quit_flow.OVERLAY_FORWARD_PATIENCE_S,
              threading=types.SimpleNamespace(Timer=_FakeTimer), _last_quit_forward=None)
    exec(compile(ast.Module([node], []), str(MAC), "exec"), ns)
    return ns["OverlayAppDelegate"]()


def test_a_quit_from_outside_waits_for_the_app_then_answers_yes():
    """`tell application "Waffler" to quit` landing on the overlay: it tells
    the app and answers later (the script waits); when the app tells it to
    quit, the answer is yes, so AppleScript reports success instead of
    "User canceled (-128)". Checked live on this Mac with a PyObjC process:
    NSTerminateCancel gives the sender error -128, NSTerminateLater followed
    by replyToApplicationShouldTerminate_(True) gives no error."""
    ns = mac()
    _FakeTimer.made = []
    d = _delegate(ns)
    assert d.applicationShouldTerminate_(None) == ns["_NS_TERMINATE_LATER"] == 2
    assert ns["emitted"] == [("quit_request", {})]
    assert ns["_terminate_pending"] is True
    (timer,) = _FakeTimer.made
    assert timer.daemon and timer.started and timer.delay == ns["OVERLAY_FORWARD_PATIENCE_S"]
    ns["_dispatch_cmd"]({"type": "quit"})                 # the app's quit (or its stdin closing)
    assert ns["app"].calls == [("reply", True)]
    assert ns["_parent_asked_quit"] is True and ns["_terminate_pending"] is False
    timer.fn()
    ns["_dispatch_cmd"](ns["_cmd_queue"].get_nowait())    # the patience timer, too late: nothing
    assert ns["app"].calls == [("reply", True)]


def test_if_the_app_never_answers_the_overlay_says_no_and_stays_up_as_before():
    ns = mac()
    _FakeTimer.made = []
    d = _delegate(ns)
    d.applicationShouldTerminate_(None)
    _FakeTimer.made[0].fn()
    ns["_dispatch_cmd"](ns["_cmd_queue"].get_nowait())
    assert ns["app"].calls == [("reply", False)]
    # A later quit from the app itself still ends it the usual way.
    ns["_dispatch_cmd"]({"type": "quit"})
    assert ns["app"].calls == [("reply", False), ("terminate", None)]


def test_the_apps_own_quit_still_terminates_at_once():
    ns = mac()
    d = _delegate(ns)
    ns["_dispatch_cmd"]({"type": "quit"})
    assert ns["app"].calls == [("terminate", None)]
    ns["app"].calls.clear()
    assert d.applicationShouldTerminate_(None) == ns["_NS_TERMINATE_NOW"]
    assert ns["emitted"] == []


def _lift_app(name):
    node = next(n for n in APP_TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(APP_SRC, node)


def _quit_tray_step(system):
    did = []

    class Item:
        def stop(self):
            did.append("stop")

    item = Item()
    ns = {"_platform": types.SimpleNamespace(system=lambda: system), "_tray_icon": item,
          "_window_ref": None, "_should_quit": False,
          "_remove_mac_status_item": lambda it: did.append(("removed", it))}
    exec(_lift_app("_quit_steps"), ns)
    dict(ns["_quit_steps"]())["tray"]()
    return did, item


def test_on_a_mac_quitting_takes_the_status_item_out_instead_of_calling_stop():
    did, item = _quit_tray_step("Darwin")
    assert did == [("removed", item)]
    body = _lift_app("_remove_mac_status_item")
    assert "NSStatusBar.systemStatusBar().removeStatusItem_" in body
    assert "AppHelper.callAfter(" in body             # on the main thread


def test_on_windows_quitting_still_stops_the_tray_icon():
    did, _item = _quit_tray_step("Windows")
    assert did == ["stop"]


def test_the_tray_step_comes_before_the_terminate():
    names = [n for n, _ in _quit_steps_on_darwin()]
    assert names.index("tray") < names.index("end app")


def _quit_steps_on_darwin():
    ns = {"_platform": types.SimpleNamespace(system=lambda: "Darwin"), "_tray_icon": None,
          "_window_ref": None, "_should_quit": False}
    exec(_lift_app("_quit_steps"), ns)
    return ns["_quit_steps"]()


# ═══ 9. The window's side ════════════════════════════════════════════════════

def test_settings_has_the_switch_and_the_reset_button_mac_only():
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    general = html[html.index('id="secGeneral"'):html.index('id="secKeys"')]
    group = general[general.index('id="pillGroup"'):general.index('<h2 class="sh2">Startup</h2>')]
    assert '<div id="pillGroup" hidden>' in general
    assert '<label for="pillIdleToggle">Show the pill when idle</label>' in group
    assert re.search(r'<input type="checkbox" class="tog" role="switch" id="pillIdleToggle" '
                     r'aria-describedby="pillIdleDesc" onchange="setPillIdle\(this.checked\)">', group)
    assert 'onclick="resetPillPosition(this)">Reset pill position</button>' in group
    assert "—" not in group and "–" not in group
    js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    assert "saveSetting('idle_pill', !!on)" in js
    assert "pywebview.api.reset_pill_position()" in js
    assert "window.waffler_pill_idle_changed = function" in js
    assert "renderPillRows();" in js[js.index("async function loadSettings"):]
    logic = (ROOT / "ui" / "logic.js").read_text(encoding="utf-8")
    block = logic[logic.index("const PILL_TEXT"):logic.index("function pillSaved")]
    assert "—" not in block and "–" not in block


def test_the_bridge_has_the_reset_call():
    assert "def reset_pill_position(self)" in APP_SRC
