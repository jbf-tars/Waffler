"""Mac: the pill's process stops ticking 20 times a second while nothing shows.

The pill runs as a second process with one timer: every 50 ms it reads the
commands the app sent and moves the level bars on. It ran at that rate all day,
pill or no pill. On one Mac, in 5.9 days, the pill's process used 57 minutes of
processor time and Waffler itself 16.

While the recording pill is hidden, its bars are at rest, no command is
waiting and the idle pill is off, each tick now puts the next one a second
away. A command that arrives (show, a message, a Space change, a retry) wakes
the timer at once, on the main thread, so it is read no later than before; and
if a wake were ever lost, the command is read within the second. The tick
itself, and everything that shows, hides, raises or moves the pill, is the
code 3.16.5 shipped: tests/test_idle_pill.py pins it by hash, and this adds a
decorator around the tick and a queue that wakes it, touching neither.

The pieces are lifted out of overlay_process.py and run with stand-ins for
AppKit: no window is made.
"""
import ast
import queue
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

MAC = ROOT / "src" / "overlay_process.py"
SRC = MAC.read_text(encoding="utf-8")
TREE = ast.parse(SRC)
NAMES = ("_slow_when_idle", "_nothing_to_animate", "_wake_tick", "_WakingQueue", "_resume_tick")


class Timer:
    def __init__(self):
        self.fire_dates = []

    def setFireDate_(self, when):
        self.fire_dates.append(when)


class Dates:
    @staticmethod
    def dateWithTimeIntervalSinceNow_(seconds):
        return ("in", seconds)

    @staticmethod
    def date():
        return ("now",)


class View:
    def __init__(self):
        self._bars = [0.0] * 9
        self._targets = [0.0] * 9
        self.woken = []

    def performSelectorOnMainThread_withObject_waitUntilDone_(self, sel, obj, wait):
        self.woken.append((sel, obj, wait))


def rig():
    nodes = [n for n in TREE.body
             if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in NAMES
             or isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "IDLE_TICK_S"]
    assert {n.name for n in nodes if not isinstance(n, ast.Assign)} == set(NAMES)
    ns = {"queue": queue, "NSDate": Dates, "_visible": False, "_idle_win": None,
          "_tick_slowed": False, "_tick_timer": None, "_g_view": View()}
    exec(compile(ast.Module(nodes, []), str(MAC), "exec"), ns)
    ns["_cmd_queue"] = ns["_WakingQueue"]()
    ticks = []

    def tick(self, timer):                   # the real animTick_ drains the queue first
        while not ns["_cmd_queue"].empty():
            ticks.append(ns["_cmd_queue"].get_nowait())
        ticks.append("tick")
    ns["ticks"] = ticks
    ns["tick"] = ns["_slow_when_idle"](tick)
    return ns


def test_with_nothing_showing_each_tick_puts_the_next_a_second_away():
    ns = rig()
    t = Timer()
    ns["tick"](ns["_g_view"], t)
    assert ns["ticks"] == ["tick"], "the tick itself still ran"
    assert t.fire_dates == [("in", ns["IDLE_TICK_S"])] and ns["IDLE_TICK_S"] == 1.0
    assert ns["_tick_slowed"] is True


def test_while_the_pill_is_up_the_timer_is_left_alone():
    ns = rig()
    ns["_visible"] = True
    t = Timer()
    for _ in range(5):
        ns["tick"](ns["_g_view"], t)
    assert t.fire_dates == [] and ns["_tick_slowed"] is False


def test_bars_still_settling_keep_the_rate():
    ns = rig()
    ns["_g_view"]._bars[3] = 0.4            # hidden a moment ago: still easing to rest
    t = Timer()
    ns["tick"](ns["_g_view"], t)
    assert t.fire_dates == []
    ns["_g_view"]._bars[3] = 0.0
    ns["tick"](ns["_g_view"], t)
    assert len(t.fire_dates) == 1


def test_the_idle_pill_keeps_the_rate():
    """It is looked after on every tick (the pointer resting on it): with it
    on, nothing changes."""
    ns = rig()
    ns["_idle_win"] = object()
    t = Timer()
    ns["tick"](ns["_g_view"], t)
    assert t.fire_dates == [] and ns["_tick_slowed"] is False


def test_a_command_that_arrives_wakes_the_timer_on_the_main_thread():
    ns = rig()
    t = Timer()
    ns["tick"](ns["_g_view"], t)
    ns["_cmd_queue"].put({"type": "show"})
    view = ns["_g_view"]
    assert view.woken == [(b"resumeTick:", None, False)], "asked of the main thread, not done from this one"
    ns["_resume_tick"]()                      # what resumeTick: runs there
    assert t.fire_dates[-1] == ("now",) and ns["_tick_slowed"] is False
    ns["_visible"] = True                     # the show was dispatched by the tick that followed
    ns["tick"](view, t)
    assert ns["ticks"][-2:] == [{"type": "show"}, "tick"]
    assert len(t.fire_dates) == 2, "and the pill being up, nothing slows it again"


def test_at_full_rate_a_command_costs_no_wake():
    """30 level updates a second while recording must not each cross threads."""
    ns = rig()
    ns["_visible"] = True
    ns["tick"](ns["_g_view"], Timer())
    for _ in range(30):
        ns["_cmd_queue"].put({"type": "level"})
    assert ns["_g_view"].woken == []


def test_a_command_that_lands_as_the_tick_decides_is_not_left_waiting():
    """The flag goes up before the queue is looked at again: a command put in
    between either sees the flag (and wakes the timer) or is seen by that look
    (and the timer is not slowed)."""
    ns = rig()
    t = Timer()
    real_empty = ns["_cmd_queue"].empty
    state = {"n": 0}

    def empty():
        state["n"] += 1
        if state["n"] == 2:                  # the tick drained (1), now decides (2): a command lands first
            queue.Queue.put(ns["_cmd_queue"], {"type": "show"})
        return real_empty()
    ns["_cmd_queue"].empty = empty
    ns["tick"](ns["_g_view"], t)
    assert t.fire_dates == [] and ns["_tick_slowed"] is False


def test_a_fault_in_any_of_it_leaves_the_tick_at_full_rate():
    ns = rig()

    class Broken(Timer):
        def setFireDate_(self, when):
            raise RuntimeError("no")
    ns["tick"](ns["_g_view"], Broken())      # must not raise
    assert ns["_tick_slowed"] is False
    ns["_g_view"] = None
    ns["_tick_slowed"] = True
    ns["_cmd_queue"].put({"type": "show"})   # no view yet: must not raise
    assert ns["_cmd_queue"].qsize() == 1


def test_it_is_wired_around_the_tick_without_touching_it():
    view = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "WaffleView")
    tick = next(n for n in view.body if isinstance(n, ast.FunctionDef) and n.name == "animTick_")
    assert [d.id for d in tick.decorator_list] == ["_slow_when_idle"]
    assert any(isinstance(n, ast.FunctionDef) and n.name == "resumeTick_" for n in view.body)
    assert "_cmd_queue: queue.Queue = _WakingQueue()" in SRC
    # The wrapper keeps the name and the two arguments PyObjC turns into "animTick:".
    wrap = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_slow_when_idle")
    inner = next(n for n in wrap.body if isinstance(n, ast.FunctionDef))
    assert inner.name == "animTick_" and [a.arg for a in inner.args.args] == ["self", "timer"]


def test_the_code_that_shows_hides_and_moves_the_pill_is_still_3_16_5s():
    import test_idle_pill as pinned
    assert pinned._pinned() == pinned._PINNED_ALL
