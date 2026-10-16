"""Permissions checked when the hotkey goes down, and the key listener's health.

The decision table (what is KNOWN to be missing), the 60 s gap per permission,
"never block on a check that failed", the cached microphone read (the real one
costs about 7 ms), the Privacy pane each button opens, and how app.py acts on
the answer. Nothing here needs, or changes, any permission, and no event tap
is installed.
"""
import ast
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import mac_permissions as mp  # noqa: E402
import user_messages  # noqa: E402
from mac_permissions import PermissionPrecheck, evaluate_permissions  # noqa: E402

IS_MAC = sys.platform == "darwin"


class Clock:
    def __init__(self):
        self.now = 5000.0

    def __call__(self):
        return self.now


def make_precheck(mic="granted", ax=True, clock=None, mic_ttl=30.0, spawn=None, listener_gap=600.0):
    clock = clock or Clock()
    state = {"mic": mic, "ax": ax, "mic_calls": 0, "ax_calls": 0}

    def read_mic():
        state["mic_calls"] += 1
        if isinstance(state["mic"], Exception):
            raise state["mic"]
        return state["mic"]

    def read_ax():
        state["ax_calls"] += 1
        if isinstance(state["ax"], Exception):
            raise state["ax"]
        return state["ax"]

    pc = PermissionPrecheck(read_mic=read_mic, read_ax=read_ax, clock=clock, mic_ttl=mic_ttl,
                            listener_gap=listener_gap, spawn=spawn or (lambda fn: fn()))
    pc.state, pc.clock = state, clock
    return pc


def names(problems):
    return [p.permission for p in problems]


# ── the decision table ───────────────────────────────────────────────────

@pytest.mark.parametrize("mic,ax,alive,need_ax,expected", [
    # everything fine, or not known to be wrong: nothing
    ("granted", True, True, True, []),
    ("granted", True, None, True, []),               # the listener's state is not known yet
    ("not_asked", True, True, True, []),             # the system prompt comes on first use
    ("unknown", None, None, True, []),               # every check failed: carry on
    ("not_applicable", None, True, True, []),
    # the microphone
    ("denied", True, True, True, ["microphone"]),
    ("restricted", True, True, True, ["microphone"]),
    # Accessibility, only when it is needed (paste on)
    ("granted", False, True, True, ["accessibility"]),
    ("granted", False, True, False, []),
    ("granted", None, True, True, []),
    # the listener
    ("granted", True, False, True, ["keyboard"]),
    ("granted", None, False, True, ["keyboard"]),    # Accessibility not known to be off
    ("granted", False, False, True, ["accessibility"]),   # the likelier cause, said once
    ("granted", True, False, False, ["keyboard"]),
    # several at once: microphone first
    ("denied", False, True, True, ["microphone", "accessibility"]),
    ("denied", True, False, True, ["microphone", "keyboard"]),
])
def test_the_decision_table(mic, ax, alive, need_ax, expected):
    assert names(evaluate_permissions(mic, ax, alive, need_ax)) == expected


def test_a_dead_listener_with_accessibility_off_names_accessibility_not_the_keyboard():
    (p,) = evaluate_permissions("granted", False, False)
    assert p.permission == "accessibility" and p.action == "open_ax_settings"


def test_the_dead_listener_problem_points_at_input_monitoring():
    (p,) = evaluate_permissions("granted", True, False)
    assert p.permission == "keyboard" and p.action == "open_input_settings"
    assert p.pane == "input_monitoring"
    assert "Input Monitoring" in p.body and "restart" in p.body.lower()


def test_every_problem_has_a_button_for_the_pane_that_fixes_it():
    expected_pane = {"microphone": "microphone", "accessibility": "accessibility",
                     "keyboard": "input_monitoring"}
    seen = set()
    for args in (("denied", True, True), ("restricted", True, True), ("granted", False, True),
                 ("granted", True, False)):
        for p in evaluate_permissions(*args):
            seen.add(p.permission)
            assert p.button_label == "Open Settings"
            assert p.pane == expected_pane[p.permission]
            assert p.pane in mp.PANES, "the pane has a deep link"
            assert mp.PANE_ACTIONS[p.action] == p.pane
    assert seen == set(expected_pane)


def test_the_messages_are_plain_short_and_name_the_pane():
    for key, (heading, body) in user_messages.PERMISSION_MESSAGES.items():
        assert len(body) <= 140, (key, len(body))          # three lines of the pill
        assert len(heading) <= 40
        for text in (heading, body):
            assert "—" not in text and "–" not in text
    assert "Microphone" in user_messages.PERMISSION_MESSAGES["microphone"][1]
    assert "Accessibility" in user_messages.PERMISSION_MESSAGES["accessibility"][1]
    assert "restrict" in " ".join(user_messages.PERMISSION_MESSAGES["microphone_restricted"]).lower()


def test_the_pane_actions_open_the_exact_privacy_panes():
    assert mp.PANES["microphone"].endswith("Privacy_Microphone")
    assert mp.PANES["accessibility"].endswith("Privacy_Accessibility")
    assert mp.PANES["input_monitoring"].endswith("Privacy_ListenEvent")
    opened = []
    for action, pane in mp.PANE_ACTIONS.items():
        mp.open_pane(pane, runner=lambda cmd, **kw: opened.append(cmd))
    assert [c[1] for c in opened] == [mp.PANES[p] for p in mp.PANE_ACTIONS.values()]
    assert all(c[0] == "/usr/bin/open" for c in opened)


# ── the check at key-down ────────────────────────────────────────────────

def test_all_granted_means_no_toast():
    assert make_precheck().check() is None


def test_not_asked_yet_never_opens_settings():
    pc = make_precheck(mic="not_asked")
    assert pc.check() is None
    assert pc.check() is None


def test_one_message_per_press_microphone_before_accessibility():
    pc = make_precheck(mic="denied", ax=False)
    assert pc.check().permission == "microphone"
    assert pc.check().permission == "accessibility"       # same moment: the mic is in its gap
    assert pc.check() is None


def test_the_gap_is_60_seconds_per_permission():
    pc = make_precheck(mic="denied")
    assert pc.check() is not None
    pc.clock.now += 59
    assert pc.check() is None, "still inside the gap: recording starts as before"
    pc.clock.now += 2
    assert pc.check() is not None


def test_readers_that_raise_count_as_unknown_and_never_block():
    pc = make_precheck(mic=RuntimeError("av gone"), ax=OSError("ctypes gone"))
    assert pc.check() is None


def test_accessibility_is_not_read_when_it_is_not_needed():
    pc = make_precheck(ax=False)
    assert pc.check(need_accessibility=False) is None
    assert pc.state["ax_calls"] == 0


def test_a_granted_microphone_is_cached_so_key_down_stays_fast():
    pc = make_precheck(mic="granted")
    pc.warm()
    calls = pc.state["mic_calls"]
    for _ in range(50):
        pc.clock.now += 0.2
        assert pc.check() is None
    assert pc.state["mic_calls"] == calls, "no slow AVFoundation call on the hot path"


def test_a_stale_cache_answers_now_and_refreshes_in_the_background():
    spawned = []
    pc = make_precheck(mic="granted", mic_ttl=30, spawn=spawned.append)
    pc.warm()
    pc.state["mic"] = "denied"                 # revoked while idle
    pc.clock.now += 31
    assert pc.check() is None, "answers from the cache at once"
    assert len(spawned) == 1, "and starts exactly one refresh"
    pc.check()
    assert len(spawned) == 1, "no pile-up"
    spawned[0]()
    pc.clock.now += 1
    assert pc.check().permission == "microphone", "the next press sees the revocation"


def test_anything_but_granted_is_read_again_every_press_so_a_new_grant_is_seen_at_once():
    pc = make_precheck(mic="not_asked")
    pc.check()
    pc.check()
    assert pc.state["mic_calls"] >= 2
    pc.state["mic"] = "granted"
    assert pc.check() is None
    assert pc.state["mic_calls"] >= 3


def test_denied_then_granted_stops_nagging():
    pc = make_precheck(mic="denied")
    assert pc.check() is not None
    pc.state["mic"] = "granted"
    pc.clock.now += 120
    assert pc.check() is None


def test_the_check_is_cheap_on_the_hot_path():
    """Under 5 ms a press, with the real readers' cost taken out (they are
    measured separately): this is the code around them."""
    pc = make_precheck()
    pc.warm()
    t0 = time.perf_counter()
    for _ in range(1000):
        pc.check()
    per_call_ms = (time.perf_counter() - t0) / 1000 * 1000
    assert per_call_ms < 1.0, per_call_ms


# ── the check while idle: is the key listener alive, is Accessibility on? ─

def test_a_listener_that_is_alive_or_not_known_says_nothing():
    pc = make_precheck()
    assert pc.check_idle(True) is None
    assert pc.check_idle(None) is None


def test_a_dead_listener_says_so_with_the_input_monitoring_button_then_every_ten_minutes():
    pc = make_precheck()
    p = pc.check_idle(False)
    assert p.permission == "keyboard" and p.action == "open_input_settings"
    assert pc.check_idle(False) is None, "not again on the next look"
    pc.clock.now += 599
    assert pc.check_idle(False) is None
    pc.clock.now += 2
    assert pc.check_idle(False) is not None, "a reminder after ten minutes: no key press will ever ask"


def test_a_dead_listener_with_accessibility_off_names_accessibility():
    pc = make_precheck(ax=False)
    assert pc.check_idle(False).permission == "accessibility"


def test_a_listener_that_comes_back_and_dies_again_speaks_up_at_once():
    pc = make_precheck()
    assert pc.check_idle(False) is not None
    assert pc.check_idle(True) is None
    pc.clock.now += 5
    assert pc.check_idle(False) is not None


def test_accessibility_switched_off_while_idle_is_said_once_even_if_the_listener_lives():
    pc = make_precheck(ax=False)
    p = pc.check_idle(True)
    assert p.permission == "accessibility" and p.action == "open_ax_settings"
    pc.clock.now += 86400
    assert pc.check_idle(True) is None, "a reminder would only nag: the next key press says it again"
    pc.state["ax"] = True
    assert pc.check_idle(True) is None
    pc.state["ax"] = False
    assert pc.check_idle(True) is not None, "off again after being on: said again"


def test_idle_accessibility_is_only_asked_for_when_paste_needs_it():
    pc = make_precheck(ax=False)
    assert pc.check_idle(True, need_accessibility=False) is None
    assert pc.state["ax_calls"] == 0
    assert pc.check_idle(False, need_accessibility=False).permission == "keyboard"


def test_the_idle_check_survives_a_broken_accessibility_reader():
    pc = make_precheck(ax=OSError("ctypes"))
    assert pc.check_idle(False).permission == "keyboard"
    assert pc.check_idle(True) is None


def test_the_idle_check_never_reads_the_microphone():
    pc = make_precheck(mic="denied")
    pc.check_idle(False)
    pc.check_idle(True)
    assert pc.state["mic_calls"] == 0


# ── Accessibility, read live ─────────────────────────────────────────────

class _Fn:
    def __init__(self, value):
        self.value, self.calls = value, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.value


def test_accessibility_trusted_asks_without_prompting():
    fn = _Fn(True)
    lib = types.SimpleNamespace(AXIsProcessTrustedWithOptions=fn)
    assert mp.accessibility_trusted(lib, platform="darwin") is True
    assert fn.calls == [(None,)], "a NULL options dictionary: never a prompt"
    lib = types.SimpleNamespace(AXIsProcessTrustedWithOptions=_Fn(False))
    assert mp.accessibility_trusted(lib, platform="darwin") is False


def test_accessibility_trusted_falls_back_to_the_older_call():
    lib = types.SimpleNamespace(AXIsProcessTrusted=_Fn(True))
    assert mp.accessibility_trusted(lib, platform="darwin") is True


def test_accessibility_trusted_is_none_when_it_cannot_be_asked():
    assert mp.accessibility_trusted(types.SimpleNamespace(), platform="darwin") is None
    assert mp.accessibility_trusted(platform="win32") is None

    class Boom:
        @property
        def AXIsProcessTrustedWithOptions(self):
            raise OSError("dlopen failed")
    assert mp.accessibility_trusted(Boom(), platform="darwin") is None


def test_the_real_readers_return_valid_values_and_never_raise():
    assert mp.microphone_status() in {"granted", "denied", "restricted", "not_asked",
                                      "unknown", "not_applicable"}
    assert mp.accessibility_trusted() in (True, False, None)
    if not IS_MAC:
        assert mp.accessibility_trusted() is None


_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
_PIPE = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")


def _api_method(name):
    return next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _pipe_method(name):
    return next(n for n in _PIPE.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _lift(node, **ns):
    ns.setdefault("sys", types.SimpleNamespace(platform="darwin"))
    exec(compile(ast.Module([node], []), "<app.py>", "exec"), ns)
    return ns[node.name]


def test_the_bridges_accessibility_check_uses_the_same_function_as_the_paste():
    check = _lift(_api_method("check_accessibility_permission"),
                  _mac_perms=types.SimpleNamespace(accessibility_trusted=lambda: False),
                  _log_to_file=lambda m: None)
    assert check(object()) is False
    check = _lift(_api_method("check_accessibility_permission"),
                  _mac_perms=types.SimpleNamespace(accessibility_trusted=lambda: True),
                  _log_to_file=lambda m: None)
    assert check(object()) is True
    src = (ROOT / "src" / "pasteboard.py").read_text(encoding="utf-8")
    assert "from mac_permissions import accessibility_trusted" in src
    assert "permissions_manager" not in src, "the old permissions manager is not used"


# ── the event tap's own answer ───────────────────────────────────────────

@pytest.fixture
def tap_class(monkeypatch):
    """MacEventTap, loaded fresh against a fake Quartz, whatever the platform
    and whatever other tests have put in sys.modules."""
    import importlib.util
    quartz = types.ModuleType("Quartz")
    state = {"enabled": True, "enable_calls": 0, "sticks_off": False}

    def is_enabled(tap):
        return state["enabled"]

    def enable(tap, on):
        state["enable_calls"] += 1
        if not state["sticks_off"]:
            state["enabled"] = bool(on)

    def anything(name):
        if name.startswith("__"):
            raise AttributeError(name)
        return 0 if name.startswith("k") else (lambda *a, **k: None)
    quartz.__getattr__ = anything
    quartz.CGEventTapIsEnabled, quartz.CGEventTapEnable = is_enabled, enable
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    spec = importlib.util.spec_from_file_location("mac_hotkey_monitor_probe",
                                                  ROOT / "src" / "mac_hotkey_monitor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MacEventTap, state


class _Thread:
    def __init__(self, alive):
        self.alive = alive

    def is_alive(self):
        return self.alive


def test_a_tap_that_was_never_started_has_no_answer(tap_class):
    cls, _ = tap_class
    assert cls().is_alive() is None


def test_a_tap_whose_thread_ended_is_dead(tap_class):
    """What a tap that could not be created looks like: the thread returns."""
    cls, _ = tap_class
    tap = cls()
    tap._thread = _Thread(False)
    assert tap.is_alive() is False


def test_a_tap_still_being_made_has_no_answer(tap_class):
    cls, _ = tap_class
    tap = cls()
    tap._thread = _Thread(True)
    assert tap.is_alive() is None


def test_a_running_tap_is_alive(tap_class):
    cls, state = tap_class
    tap = cls()
    tap._thread, tap._tap = _Thread(True), object()
    assert tap.is_alive() is True and state["enable_calls"] == 0


def test_a_tap_the_system_switched_off_is_switched_on_again(tap_class):
    cls, state = tap_class
    tap = cls()
    tap._thread, tap._tap = _Thread(True), object()
    state["enabled"] = False
    assert tap.is_alive() is True and state["enable_calls"] == 1


def test_a_tap_that_stays_off_is_dead_what_a_revoked_permission_looks_like(tap_class):
    cls, state = tap_class
    tap = cls()
    tap._thread, tap._tap = _Thread(True), object()
    state["enabled"], state["sticks_off"] = False, True
    assert tap.is_alive() is False
    assert tap.is_alive(repair=False) is False


def test_a_tap_stopped_on_purpose_is_not_reported_dead(tap_class):
    cls, _ = tap_class
    tap = cls()
    tap._thread = _Thread(False)
    tap.stop()
    assert tap.is_alive() is None, "changing the hotkey stops the old listener"


def test_a_quartz_failure_is_not_an_answer(tap_class, monkeypatch):
    cls, _ = tap_class
    tap = cls()
    tap._thread, tap._tap = _Thread(True), object()
    quartz = sys.modules["Quartz"]
    monkeypatch.setattr(quartz, "CGEventTapIsEnabled", lambda t: (_ for _ in ()).throw(OSError()))
    assert tap.is_alive() is None


@pytest.mark.skipif(not IS_MAC, reason="the listener is the Mac one")
def test_the_listener_asks_its_tap(monkeypatch):
    from src.smart_hotkey import SmartHotkeyListener
    listener = SmartHotkeyListener(lambda: None, lambda: None)
    assert listener.is_alive() is None, "not started"


# ── how app.py acts on it ────────────────────────────────────────────────

import _pipeline_harness as H  # noqa: E402


class _Listener:
    def __init__(self, alive=True):
        self.alive, self.resets = alive, 0

    def reset_state(self):
        self.resets += 1

    def is_alive(self):
        return self.alive


def _mac_pipeline(tmp_path, precheck):
    p = H.make_pipeline(tmp_path)
    p.ns["_platform"] = types.SimpleNamespace(system=lambda: "Darwin")
    p._precheck = precheck
    p.hotkey_listener = _Listener()
    return p


def test_a_blocked_press_shows_one_toast_with_the_pane_button_and_resets_the_listener(tmp_path):
    p = _mac_pipeline(tmp_path, make_precheck(mic="denied"))
    assert p._permissions_block_recording() is True
    assert H.wait_for(lambda: p.overlay.toasts(), timeout=2)
    [toast] = p.overlay.toasts()
    assert toast["style"] == "error" and toast["heading"] == "Microphone is off"
    assert [(b["label"], b["action"], b["kind"]) for b in toast["buttons"]] == [
        ("Open Settings", "open_mic_settings", "primary"), ("Dismiss", "dismiss", "secondary")]
    assert toast["reposition"] is True and toast["timeout"] == 12, "no recording, so no pill to follow"
    assert p.hotkey_listener.resets == 1
    assert any("[precheck] microphone is denied" in m for m in p.logged)
    # The second press inside the gap records as before.
    assert p._permissions_block_recording() is False
    assert p.hotkey_listener.resets == 1


def test_accessibility_is_only_asked_for_when_auto_paste_is_on(tmp_path):
    (tmp_path / "settings.json").write_text('{"auto_paste": false}', encoding="utf-8")
    p = _mac_pipeline(tmp_path, make_precheck(ax=False))
    assert p._permissions_block_recording() is False
    (tmp_path / "settings.json").write_text('{"auto_paste": true}', encoding="utf-8")
    assert p._permissions_block_recording() is True
    assert H.wait_for(lambda: p.overlay.toasts(), timeout=2)
    assert p.overlay.toasts()[-1]["buttons"][0]["action"] == "open_ax_settings"


def test_a_check_that_errors_leaves_recording_exactly_as_it_was(tmp_path):
    class Boom:
        def check(self, need_accessibility=True):
            raise RuntimeError("tcc exploded")
    p = _mac_pipeline(tmp_path, Boom())
    assert p._permissions_block_recording() is False
    assert p.overlay.toasts() == [] and p.hotkey_listener.resets == 0
    assert any("[precheck] skipped: RuntimeError" in m for m in p.logged)


def test_nothing_is_checked_off_a_mac(tmp_path):
    called = []
    p = H.make_pipeline(tmp_path)                    # the harness is a Windows machine
    p._precheck = types.SimpleNamespace(check=lambda **kw: called.append(1))
    assert p._permissions_block_recording() is False and called == []


def test_a_reset_that_fails_does_not_stop_the_toast(tmp_path):
    p = _mac_pipeline(tmp_path, make_precheck(mic="denied"))
    p.hotkey_listener.reset_state = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    assert p._permissions_block_recording() is True


def test_the_toast_buttons_open_the_right_pane(tmp_path):
    p = _mac_pipeline(tmp_path, make_precheck())
    opened = []
    fake = types.SimpleNamespace(PANE_ACTIONS=mp.PANE_ACTIONS, open_pane=opened.append)
    p.ns["_mac_perms"] = fake
    for action in ("open_mic_settings", "open_ax_settings", "open_input_settings"):
        p._on_toast_action(action)
    assert H.wait_for(lambda: len(opened) == 3, timeout=2)
    assert sorted(opened) == ["accessibility", "input_monitoring", "microphone"]


def test_a_not_asked_microphone_never_opens_settings_through_the_pipeline(tmp_path):
    p = _mac_pipeline(tmp_path, make_precheck(mic="not_asked"))
    assert p._permissions_block_recording() is False
    time.sleep(0.05)
    assert p.overlay.toasts() == []


def test_the_press_checks_before_it_claims_the_recording_slot_or_starts_audio():
    press = _pipe_method("on_hotkey_press")
    src = ast.unparse(press)
    assert src.index("self._permissions_block_recording()") < src.index("self._processing_lock")
    assert src.index("self._permissions_block_recording()") < src.index("self.audio.start()")
    assert "not self.is_recording" in src.split("self._permissions_block_recording()")[0]


def _watch_ns(precheck, listener, sleeps, settings=None):
    """_watch_listener lifted out, with a clock that ends the loop."""
    class Stop(Exception):
        pass

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 3:
            raise Stop

    announced, logged = [], []
    fake_self = types.SimpleNamespace(
        hotkey_listener=listener, _precheck=precheck, _read_settings=lambda: settings or {},
        _announce_permission_problem=announced.append)
    fn = _lift(_pipe_method("_watch_listener"), time=types.SimpleNamespace(sleep=sleep),
               _log_to_file=logged.append)
    return fn, fake_self, announced, logged, Stop


def test_the_listener_watch_announces_a_dead_listener_once():
    sleeps = []
    fn, me, announced, logged, Stop = _watch_ns(make_precheck(), _Listener(alive=False), sleeps)
    with pytest.raises(Stop):
        fn(me)
    assert sleeps[0] == 10 and len(announced) == 1, "one message across three looks"
    assert announced[0].permission == "keyboard"
    assert any("keyboard while idle (key listener alive: False)" in m for m in logged)
    assert not any("failed" in m for m in logged)


def test_the_listener_watch_keeps_the_microphone_answer_fresh_for_the_next_press():
    pc = make_precheck(mic="granted")
    sleeps = []
    fn, me, announced, logged, Stop = _watch_ns(pc, _Listener(alive=True), sleeps)
    with pytest.raises(Stop):
        fn(me)
    reads_while_idle = pc.state["mic_calls"]
    assert reads_while_idle >= 3, "read on every look, off the hot path"
    pc.state["mic"] = "denied"                     # switched off while Waffler sat idle
    with pytest.raises(Stop):
        sleeps.clear()
        fn(me)
    assert pc.check().permission == "microphone", "so the very next press says so"


def test_the_listener_watch_says_nothing_while_the_listener_lives():
    sleeps = []
    fn, me, announced, logged, Stop = _watch_ns(make_precheck(), _Listener(alive=True), sleeps)
    with pytest.raises(Stop):
        fn(me)
    assert announced == [] and not any("failed" in m for m in logged)


def test_the_listener_watch_reports_accessibility_taken_away_while_the_listener_lives():
    sleeps = []
    fn, me, announced, logged, Stop = _watch_ns(make_precheck(ax=False), _Listener(alive=True), sleeps)
    with pytest.raises(Stop):
        fn(me)
    assert [p.permission for p in announced] == ["accessibility"], "once across three looks"
    # With paste off Accessibility is not needed, so nothing is said.
    fn, me, announced, logged, Stop = _watch_ns(make_precheck(ax=False), _Listener(alive=True), [],
                                                settings={"auto_paste": False})
    with pytest.raises(Stop):
        fn(me)
    assert announced == []


def test_the_listener_watch_survives_a_listener_without_the_question():
    sleeps = []
    fn, me, announced, logged, Stop = _watch_ns(make_precheck(), object(), sleeps)
    with pytest.raises(Stop):
        fn(me)
    assert announced == []


def test_the_listener_watch_starts_with_the_mac_listener_and_nowhere_else():
    src = ast.unparse(_pipe_method("start_hotkey"))
    assert "_watch_listener" in src
    assert src.index("self.hotkey_listener.start()") < src.index("_watch_listener")
    assert "Darwin" in src.split("_watch_listener")[0].split("self.hotkey_listener.start()")[1]
