"""The real pipeline is built once, however many callers ask at the same time.

Setup's last screen ("Open Notepad and try it") and its Done button both
start the pipeline on their own threads, and WafflerPipeline takes a second
or more to build. With only an ``if _pipeline:`` check, a Done click during
that second built a second pipeline and a second hotkey listener, so every
dictation pasted, billed and landed in the Journal twice.

app.py imports pywebview and audio hardware at module scope, so these tests
run app.py's own _initialize_pipeline code, lifted out with ast, against a
slow fake pipeline. Nothing is launched and nothing touches the network.
"""
import ast
import sys
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

NAMES = ("_pipeline", "_pipeline_init_lock", "_pipeline_initialising", "_device_monitor",
         "_pipeline_running_or_starting", "_initialize_pipeline", "_build_pipeline")


def _load(fake_pipeline_cls, monkeypatch):
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in NAMES:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in NAMES for t in node.targets):
            nodes.append(node)
    log = []

    class FakeConfig:
        has_api_key = True
        hotkey = "ctrl+shift"

        def reload_env(self):
            pass

    class FakeMonitor:
        def __init__(self, on_change=None, log_fn=None):
            pass

        def start(self):
            pass

    fake_mod = types.ModuleType("src.audio_device_monitor")
    fake_mod.AudioDeviceMonitor = FakeMonitor
    monkeypatch.setitem(sys.modules, "src.audio_device_monitor", fake_mod)

    ns = {"threading": threading, "WafflerPipeline": fake_pipeline_cls,
          "_config": FakeConfig(), "_log_to_file": log.append}
    exec(compile(ast.Module(nodes, []), "<app.py>", "exec"), ns)
    for n in NAMES:
        assert n in ns, f"{n} not found in app.py"
    return ns


def _slow_pipeline():
    built, listeners = [], []

    class SlowPipeline:
        def __init__(self, config):
            time.sleep(0.3)   # the real one builds HTTP clients and audio streams
            built.append(self)

        def start_hotkey(self):
            listeners.append(self)

    return SlowPipeline, built, listeners


def test_two_callers_at_once_build_one_pipeline_and_one_listener(monkeypatch):
    cls, built, listeners = _slow_pipeline()
    ns = _load(cls, monkeypatch)
    threads = [threading.Thread(target=ns["_initialize_pipeline"]) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    time.sleep(0.05)   # the hotkey thread is started by the winner
    assert len(built) == 1
    assert len(listeners) == 1
    assert ns["_pipeline"] is built[0]
    assert ns["_pipeline_initialising"] is False


def test_the_practice_guard_sees_a_pipeline_that_is_still_building(monkeypatch):
    """wizard_start_hotkey_test and start_dictation_for_setup ask this before
    starting a listener; it must be true while the first build is running."""
    cls, built, _ = _slow_pipeline()
    ns = _load(cls, monkeypatch)
    assert ns["_pipeline_running_or_starting"]() is False
    t = threading.Thread(target=ns["_initialize_pipeline"])
    t.start()
    time.sleep(0.1)
    assert ns["_pipeline"] is None and ns["_pipeline_running_or_starting"]() is True
    t.join(5)
    assert ns["_pipeline_running_or_starting"]() is True and len(built) == 1


def test_a_failed_build_can_be_tried_again(monkeypatch):
    attempts = []

    class Flaky:
        def __init__(self, config):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("no network")

        def start_hotkey(self):
            pass

    ns = _load(Flaky, monkeypatch)
    ns["_initialize_pipeline"]()
    assert ns["_pipeline"] is None and ns["_pipeline_initialising"] is False
    ns["_initialize_pipeline"]()
    assert ns["_pipeline"] is not None and len(attempts) == 2


def test_setup_callers_use_the_guard():
    py = (ROOT / "app.py").read_text(encoding="utf-8")
    for start, end in (("def start_dictation_for_setup", "def open_practice_editor"),
                       ("def wizard_start_hotkey_test", "def wizard_stop_hotkey_test")):
        body = py[py.index(start):py.index(end)]
        assert "_pipeline_running_or_starting()" in body, start
