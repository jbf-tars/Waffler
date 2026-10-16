"""Making the dictation hotkey's listener again, one at a time (audit M3).

Changing a shortcut twice quickly (a double-click on a preset, or a cancel-key
save right after a hotkey save) used to stop the same, already stopped,
listener twice and build two new ones, and on a Mac each listener owns an event
tap: two taps in one process is the old v3.14.13 segfault, and on Windows two
hooks fire every key twice.

Nothing real is made: the listeners are fakes that count how many are running
at once, and the restart is the real ``Api._restart_hotkey_listener`` lifted
out of app.py.
"""
import ast
import json
import shutil
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import hotkey_restart as hr  # noqa: E402

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")
_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
WAIT = 5.0


class Fleet:
    """Every listener ever made, and how many were running at once."""

    def __init__(self):
        self.lock = threading.Lock()
        self.made = []
        self.running = 0
        self.most_running = 0

    def listener(self, **kw):
        fleet = self

        class Listener:
            def __init__(self):
                self.kw = kw
                self.started = self.stopped = False
                fleet.made.append(self)

            def start(self):
                with fleet.lock:
                    if self.stopped:                 # a listener that was stopped before it began stays stopped
                        return
                    self.started = True
                    fleet.running += 1
                    fleet.most_running = max(fleet.most_running, fleet.running)

            def stop(self):
                with fleet.lock:
                    if self.started and not self.stopped:
                        fleet.running -= 1
                    self.stopped = True

        return Listener()


# ── one turn at a time ───────────────────────────────────────────────────────

def test_a_request_is_numbered_and_only_the_newest_gets_to_act():
    swap = hr.ListenerSwap()
    first, second = swap.ask(), swap.ask()
    assert second > first
    with swap.turn(first) as mine:
        assert mine is False, "an older request gives way to a newer one"
    with swap.turn(second) as mine:
        assert mine is True


def test_turns_never_overlap():
    swap = hr.ListenerSwap()
    inside, most, order = [0], [0], []
    lock = threading.Lock()

    def one():
        mine = swap.ask()
        with swap.turn(mine) as newest:
            with lock:
                inside[0] += 1
                most[0] = max(most[0], inside[0])
            time.sleep(0.02)
            order.append(newest)
            with lock:
                inside[0] -= 1

    threads = [threading.Thread(target=one) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(WAIT)
    assert most[0] == 1, "two restarts built a listener at the same time"
    assert order[-1] is True, "the last request to ask is the last to act, and acts"


def test_a_turn_that_fails_does_not_keep_the_next_one_waiting():
    swap = hr.ListenerSwap()
    with pytest.raises(RuntimeError):
        with swap.turn(swap.ask()):
            raise RuntimeError("no hook")
    got = []

    def next_turn():
        with swap.turn(swap.ask()) as newest:
            got.append(newest)
    t = threading.Thread(target=next_turn)
    t.start()
    t.join(WAIT)
    assert got == [True]


def test_the_module_has_one_shared_swap():
    assert isinstance(hr.SWAP, hr.ListenerSwap)


# ── the real restart in app.py ───────────────────────────────────────────────

def lifted(fleet, system="Darwin", settings=None):
    """``Api._restart_hotkey_listener`` as app.py has it, run against fake
    listeners and a fake pipeline; returns (call it, the pipeline, the log)."""
    node = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == "_restart_hotkey_listener")
    logged, store = [], dict(settings or {})
    applied = []
    pipeline = types.SimpleNamespace(
        hotkey_listener=fleet.listener(), on_hotkey_press=lambda: None, on_hotkey_release=lambda: None,
        _on_hotkey_cancel=lambda: None, _on_hands_free_lock=lambda: None,
        apply_transform_settings=lambda: applied.append("transform"),
        apply_command_settings=lambda: applied.append("command"),
        apply_last_result_shortcuts=lambda: applied.append("last"))
    pipeline.hotkey_listener.start()
    ns = {"_pipeline": pipeline, "_log_to_file": logged.append, "threading": threading,
          "time": types.SimpleNamespace(sleep=lambda s: time.sleep(0.02)),
          "_platform": types.SimpleNamespace(system=lambda: system),
          "_double_tap": types.SimpleNamespace(enabled=lambda stored: bool(stored.get("double_tap")))}
    exec(compile(ast.Module([node], []), "<app.py Api>", "exec"), ns)

    class Api:
        def _load_settings_file(self):
            return dict(store)
    Api._restart_hotkey_listener = ns["_restart_hotkey_listener"]
    return Api(), pipeline, logged, store, applied


@pytest.fixture
def fake_listener_modules(monkeypatch):
    fleet = Fleet()

    def factory(**kw):
        return fleet.listener(**kw)
    monkeypatch.setitem(sys.modules, "smart_hotkey", types.SimpleNamespace(SmartHotkeyListener=factory))
    monkeypatch.setitem(sys.modules, "windows_hotkey", types.SimpleNamespace(WindowsHotkeyListener=factory))
    monkeypatch.setattr(hr, "SWAP", hr.ListenerSwap())
    return fleet


def settle(pipeline, fleet, expected_made):
    deadline = time.time() + WAIT
    while time.time() < deadline:
        if len(fleet.made) >= expected_made and pipeline.hotkey_listener.started:
            return True
        time.sleep(0.01)
    return False


@pytest.mark.parametrize("system", ["Darwin", "Windows"])
def test_one_change_replaces_the_listener_and_applies_the_action_shortcuts(fake_listener_modules, system):
    fleet = fake_listener_modules
    api, pipeline, logged, store, applied = lifted(fleet, system, {"hotkey_keys": ["control", "option"]})
    first = pipeline.hotkey_listener
    api._restart_hotkey_listener()
    assert settle(pipeline, fleet, 2)
    assert pipeline.hotkey_listener is not first and first.stopped
    assert pipeline.hotkey_listener.kw["keys"] == ["control", "option"]
    assert applied == ["transform", "command", "last"]
    assert fleet.running == 1


@pytest.mark.parametrize("system", ["Darwin", "Windows"])
def test_two_quick_changes_leave_one_listener_not_two(fake_listener_modules, system):
    """A double-click on a preset: the second request used to stop the listener
    the first had already stopped, and both built a new one."""
    fleet = fake_listener_modules
    api, pipeline, logged, store, applied = lifted(fleet, system, {"hotkey_keys": ["fn"]})
    api._restart_hotkey_listener()
    store["hotkey_keys"] = ["control", "option"]
    api._restart_hotkey_listener()
    deadline = time.time() + WAIT
    while time.time() < deadline and not (
            pipeline.hotkey_listener.started and pipeline.hotkey_listener.kw.get("keys") == ["control", "option"]):
        time.sleep(0.01)
    time.sleep(0.2)                                  # anything still to come would have come
    assert fleet.running == 1 and fleet.most_running == 1
    assert pipeline.hotkey_listener.kw["keys"] == ["control", "option"], "the newest save wins"
    assert pipeline.hotkey_listener.started and not pipeline.hotkey_listener.stopped


def test_many_changes_at_once_leave_the_newest_listener_alone(fake_listener_modules):
    fleet = fake_listener_modules
    api, pipeline, logged, store, applied = lifted(fleet, "Darwin", {"hotkey_keys": ["fn"]})
    for keys in (["fn"], ["control", "option"], ["cmd", "shift", "k"], ["f13"], ["control", "option"]):
        store["hotkey_keys"] = keys
        api._restart_hotkey_listener()
    deadline = time.time() + WAIT
    while time.time() < deadline and fleet.running != 1:
        time.sleep(0.01)
    time.sleep(0.3)
    assert fleet.running == 1 and fleet.most_running == 1
    assert pipeline.hotkey_listener.kw["keys"] == ["control", "option"]
    assert sum(1 for l in fleet.made if l.started and not l.stopped) == 1


def test_nothing_is_done_before_the_pipeline_is_up(fake_listener_modules):
    fleet = fake_listener_modules
    api, pipeline, logged, store, applied = lifted(fleet)
    node = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == "_restart_hotkey_listener")
    ns = {"_pipeline": None, "_log_to_file": logged.append, "threading": threading, "time": time,
          "_platform": types.SimpleNamespace(system=lambda: "Darwin"),
          "_double_tap": types.SimpleNamespace(enabled=lambda s: False)}
    exec(compile(ast.Module([node], []), "<app.py Api>", "exec"), ns)
    ns["_restart_hotkey_listener"](api)
    assert len(fleet.made) == 1


def test_the_windows_hook_loop_does_not_hold_the_swap_up(fake_listener_modules):
    """On Windows start() runs the hook's message loop and never returns: it is
    given a thread of its own, so the next change is not kept waiting for it."""
    fleet = fake_listener_modules
    api, pipeline, logged, store, applied = lifted(fleet, "Windows", {"hotkey_keys": ["win", "ctrl"]})
    blocked = threading.Event()
    real_factory = sys.modules["windows_hotkey"].WindowsHotkeyListener

    def blocking_factory(**kw):
        listener = real_factory(**kw)
        original = listener.start

        def start():
            original()
            blocked.wait(WAIT)                       # the message loop
        listener.start = start
        return listener
    sys.modules["windows_hotkey"].WindowsHotkeyListener = blocking_factory
    api._restart_hotkey_listener()
    assert settle(pipeline, fleet, 2)
    api._restart_hotkey_listener()                   # must not wait for the loop above to end
    deadline = time.time() + WAIT
    while time.time() < deadline and len(fleet.made) < 3:
        time.sleep(0.01)
    assert len(fleet.made) == 3, "the second change waited for the first listener's message loop"
    blocked.set()


def test_the_restart_still_applies_the_shortcuts_before_the_new_listener_starts():
    node = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == "_restart_hotkey_listener")
    src = ast.unparse(node)
    assert src.index("apply_last_result_shortcuts") < src.index("hotkey_listener.start()")
    assert "SWAP" in src, "the restart must go through the one-at-a-time swap"


# ── the window's preset buttons ──────────────────────────────────────────────

def _node(expr):
    code = (f"const L = require({json.dumps(str(ROOT / 'ui' / 'logic.js'))});\n"
            f"Promise.resolve({expr}).then((v) => process.stdout.write(JSON.stringify(v === undefined ? null : v)));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_a_save_in_flight_turns_a_second_click_away_until_it_ends():
    got = _node("""(() => {
      const g = L.savingGate(); const seen = [];
      seen.push(g.busy());
      seen.push(g.enter());     // the first click is let in
      seen.push(g.busy());
      seen.push(g.enter());     // a double-click's second one is not
      g.leave();
      seen.push(g.busy());
      seen.push(g.enter());     // and the next, after the save, is
      return seen;
    })()""")
    assert got == [False, True, True, False, False, True]


@needs_node
def test_a_gate_is_let_go_even_when_the_save_throws():
    got = _node("""(async () => {
      const g = L.savingGate();
      try { await g.run(async () => { throw new Error('bridge gone'); }); } catch (e) { /* the caller sees it */ }
      const ran = []; await g.run(async () => { ran.push(1); }); const second = await g.run(async () => { ran.push(2); });
      return [g.busy(), ran, second];
    })()""")
    assert got == [False, [1, 2], True]


@needs_node
def test_run_does_nothing_for_a_click_that_arrives_during_a_save():
    got = _node("""(async () => {
      const g = L.savingGate(); const ran = []; let release;
      const slow = g.run(() => new Promise((r) => { release = r; ran.push('first'); }));
      const second = await g.run(async () => { ran.push('second'); });
      release(); await slow;
      return [ran, second];
    })()""")
    assert got == [["first"], False]


def test_the_three_preset_paths_all_go_through_the_gate():
    js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    for name in ("changeSettingsHotkey", "selectHotkeyPreset"):
        body = js[js.index(f"async function {name}("):]
        body = body[:body.index("\n}\n")]
        assert "_hotkeySaving" in body, f"{name} must not run twice at once"
    capture = js[js.index("async function saveHotkeyCapture("):]
    capture = capture[:capture.index("\n}\n")]
    assert "_hotkeySaving" in capture
