"""Transforms in the app (S7): the pipeline's side of it, run with the real
methods of app.py and the fakes of tests/_pipeline_harness.py.

What is checked is the wiring: that a transform started from the shortcut or
the menu goes through the styler's shared call, the safe paste, the Journal and
the usage record; that Esc, the pill's X and a dictation's hotkey treat a
running transform as they should; and that the settings and the menu match.
Nothing real is touched: no Accessibility, no clipboard, no model.
"""
import ast
import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _pipeline_harness as H  # noqa: E402
import pasteboard  # noqa: E402
import text_context as tc  # noqa: E402
import transform_action as ta  # noqa: E402
from style_openai import ChatError, ChatReply  # noqa: E402

SOURCE = ("so yesterday i went to the shop and bought some milk and eggs and then i came home "
          "and made a cake for the party")
GOOD = ("Yesterday I went to the shop and bought some milk and eggs. Then I came home "
        "and made a cake for the party.")
APP_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))


def settings_file(p, **transforms):
    body = {"transforms": {"enabled": True, **transforms}}
    (p.ns["DATA_DIR"] / "settings.json").write_text(json.dumps(body), encoding="utf-8")


class Model:
    """Stands in for the styler's shared call (``_chat``) and its helpers."""

    def __init__(self, text=GOOD):
        self.calls = []
        self.block = None
        self.error = None
        self.text = text

    def _chat(self, system, user, **kw):
        self.calls.append({"system": system, "user": user, **kw})
        if self.block is not None:
            self.block.wait(10)
        if self.error is not None:
            raise self.error
        return ChatReply(text=self.text, provider="groq", model="openai/gpt-oss-120b",
                         input_tokens=130, output_tokens=45, finish_reason="stop")

    def _strip_em_dashes(self, text):
        return text

    def _restore_censored_profanity(self, styled, raw):
        return styled


@pytest.fixture
def rig(tmp_path, monkeypatch):
    p = H.make_pipeline(tmp_path)
    model = Model()
    p.styler = model
    p.usage = []
    p.ns["record_usage_safely"] = lambda *a, **k: p.usage.append((a, k))
    monkeypatch.setattr(tc, "read_selection",
                        lambda allow_copy_fallback=False, timeout=1.5: tc.SelectionResult(SOURCE, "ax", tc.OK))
    monkeypatch.setattr(tc, "frontmost_app", lambda read_title=None: tc.make_context("com.apple.Notes", "Notes"))
    p.model = model
    settings_file(p)
    return p


def wait_idle(p):
    assert H.wait_for(lambda: not p._transforms.busy(), 5)
    time.sleep(0.05)


# ── a run, from the pipeline's side ──────────────────────────────────────────

def test_a_transform_goes_through_the_shared_call_the_safe_paste_and_the_journal(rig):
    p = rig
    assert p.run_transform() == ta.STARTED
    wait_idle(p)

    (call,) = p.model.calls
    assert call["purpose"] == "transform" and call["temperature"] == 0.3
    assert SOURCE in call["user"] and SOURCE not in call["system"]

    assert p.clipboard.pastes == [GOOD]
    paste = p.clipboard.paste_calls[0]
    assert paste["restore"] is True and paste["ticket"] is not None

    (entry,) = H.history(p)
    assert entry["kind"] == "transform" and entry["before"] == SOURCE and entry["after"] == GOOD
    assert entry["id"] >= 1 and entry["paste"] == "pasted" and entry["app"] == "Notes"
    assert "text" not in entry and "styled" not in entry
    assert p.page.items == [entry]

    ((args, kw),) = p.usage
    assert args == ("gpt",) and kw["purpose"] == "transform" and kw["provider"] == "groq"
    assert kw["input_tokens"] == 130 and kw["output_tokens"] == 45

    (toast,) = p.overlay.toasts()
    assert toast["heading"] == "Polish done" and "Cmd+Z" not in toast["body"] and "Ctrl+Z" in toast["body"]
    assert toast["buttons"][0]["action"] == f"show_transform:{entry['id']}"
    assert toast["reposition"] is True and toast["timeout"]

    names = p.overlay.names()
    assert names.index("show") < names.index("show_working") < names.index("end_working")
    assert p.overlay.calls[names.index("end_working")][1] == ("done",)
    assert p.page.statuses == ["processing", "done"]
    assert p.hotkey_listener.processing == [True, False], "Esc is armed while it runs"
    assert p._last_result.get() == "", "'Paste last' is still the last dictation"


def test_a_failed_paste_leaves_the_rewrite_for_copy_again_and_uses_the_failure_message(rig):
    p = rig
    p.clipboard.outcome = pasteboard.PasteOutcome(pasteboard.FAILED, "keystroke_error", on_clipboard=True)
    p.run_transform()
    wait_idle(p)
    assert p._last_result.get() == GOOD, "the failed-paste toast's Copy again copies the rewrite"
    toasts = p.overlay.toasts()
    assert [t["heading"] for t in toasts] == ["Couldn't paste"]
    assert toasts[0]["buttons"][0]["action"] == "copy_last"
    (entry,) = H.history(p)
    assert entry["paste"] == "failed" and entry["after"] == GOOD


def test_a_provider_failure_shows_the_message_and_changes_nothing(rig):
    p = rig
    p.model.error = ChatError("RATE_LIMIT|tokens per minute (TPM)|9s|Groq: busy")
    p.run_transform()
    wait_idle(p)
    (toast,) = p.overlay.toasts()
    assert toast["heading"] == "Limit reached" and toast["style"] == "warn"
    assert p.clipboard.pastes == [] and H.history(p) == [] and p.usage == []


def test_the_toast_asks_the_governor_first(rig):
    p = rig
    p._toast_allowed = lambda kind: False
    p.run_transform()
    wait_idle(p)
    assert p.overlay.toasts() == []
    assert p.clipboard.pastes == [GOOD], "a message that is held back does not hold back the work"


def test_the_clipboard_setting_reaches_the_paste(rig):
    p = rig
    (p.ns["DATA_DIR"] / "settings.json").write_text(json.dumps(
        {"transforms": {"enabled": True}, "clipboard_after_paste": "keep"}), encoding="utf-8")
    p.run_transform()
    wait_idle(p)
    assert p.clipboard.paste_calls[0]["restore"] is False


def test_off_means_off(rig):
    p = rig
    settings_file(p, enabled=False)
    assert p.run_transform() == ta.OFF
    assert p.model.calls == [] and p.overlay.calls == []
    assert p.transforms_enabled() is False


def test_the_menu_start_is_the_same_run_from_another_door(rig):
    p = rig
    assert p.run_transform("menu") == ta.STARTED
    wait_idle(p)
    assert p.clipboard.pastes == [GOOD]


# ── a dictation and a transform never overlap ───────────────────────────────

def test_a_transform_will_not_start_while_recording_or_while_a_dictation_is_processed(rig):
    p = rig
    p.is_recording = True
    assert p.run_transform() == ta.DICTATING
    p.is_recording = False
    run = p._watchdog.begin(5)
    assert p._dictation_busy() is True
    assert p.run_transform() == ta.DICTATING
    p._watchdog.end(run, pw_done())
    assert p._dictation_busy() is False
    assert p.model.calls == []
    assert [t["heading"] for t in p.overlay.toasts()][-1] == "Waffler is busy"


def pw_done():
    import pipeline_watchdog
    return pipeline_watchdog.DONE


def test_the_dictation_hotkey_is_refused_while_a_transform_runs(rig):
    p = rig
    p.model.block = threading.Event()
    reset = []
    p.hotkey_listener.reset_state = lambda: reset.append(1)
    p.run_transform()
    assert H.wait_for(lambda: p.model.calls)
    p.on_hotkey_press()
    assert p.is_recording is False and getattr(p.audio, "starts", 0) == 0
    assert reset == [1], "the listener is told nothing started"
    assert p.overlay.toasts()[-1]["heading"] == "A transform is running"
    p.model.block.set()
    wait_idle(p)
    p.on_hotkey_press()
    assert p.is_recording is True, "and it works again once the transform is over"


def test_esc_stops_a_running_transform_and_is_not_a_dictation_cancel(rig):
    p = rig
    p.model.block = threading.Event()
    p.run_transform()
    assert H.wait_for(lambda: p.model.calls)
    p._on_hotkey_cancel()
    wait_idle(p)
    assert p.clipboard.pastes == [] and H.history(p) == []
    assert p.overlay.toasts()[-1]["heading"] == "Transform cancelled"
    assert p.page.statuses[-1] == "idle"
    p.model.block.set()
    assert H.wait_for(lambda: p.usage), "the answer that came late is still counted"
    assert p.clipboard.pastes == []


def test_the_pills_x_stops_it_too_without_touching_the_dictation_state(rig):
    p = rig
    p.model.block = threading.Event()
    p._processing_cancelled.clear()
    p.run_transform()
    assert H.wait_for(lambda: p.model.calls)
    p._on_overlay_cancel()
    wait_idle(p)
    assert p.clipboard.pastes == []
    assert not p._processing_cancelled.is_set(), "a dictation's cancel flag is not set by a transform's"
    p.model.block.set()


def test_esc_with_no_transform_is_what_it_always_was(rig):
    p = rig
    p._on_hotkey_cancel()
    assert p.overlay.toasts() == [] and p.page.statuses == []


# ── "Show in Journal" ────────────────────────────────────────────────────────

def test_show_in_journal_opens_the_journal_on_that_entry(rig):
    p = rig
    opened = []
    p._show_journal = lambda: opened.append(1)
    p._on_toast_action("show_transform:41")
    assert opened == [1]
    assert p.page.scripts == ["window.waffler_show_transform && window.waffler_show_transform(41)"]


@pytest.mark.parametrize("action", ["show_transform", "show_transform:", "show_transform:abc", "show_transform:-3"])
def test_a_show_in_journal_without_a_real_entry_still_opens_the_journal(rig, action):
    p = rig
    opened = []
    p._show_journal = lambda: opened.append(1)
    p._on_toast_action(action)
    assert opened == [1] and p.page.scripts == []


def test_clicking_it_frees_the_notifier_slot(rig):
    p = rig
    p._show_journal = lambda: None
    assert p._toast_allowed("transform_result")
    assert p._notifier.request("hold_hint").reason == "slot_busy"
    p._on_toast_action("show_transform:7")
    assert p._notifier.request("hold_hint").reason != "slot_busy"


# ── the shortcut ─────────────────────────────────────────────────────────────

def test_the_shortcut_is_registered_when_on_and_dropped_when_off(rig):
    p = rig
    refreshed = []
    p.ns["_refresh_tray_menu"] = lambda: refreshed.append(1)
    p._action_hotkeys.platform = "Windows"
    settings_file(p, hotkey=["ctrl", "shift", "y"])     # (Ctrl and Alt are AltGr on Windows: refused since S11a)
    p.apply_transform_settings()
    assert p._action_hotkeys.registered() == {"transform": ["ctrl", "shift", "y"]}
    settings_file(p, enabled=False, hotkey=["ctrl", "shift", "y"])
    p.apply_transform_settings()
    assert p._action_hotkeys.registered() == {}
    settings_file(p, hotkey=[])
    p.apply_transform_settings()
    assert p._action_hotkeys.registered() == {}
    assert len(refreshed) == 3, "the menu is refreshed each time"


def test_pressing_the_shortcut_runs_a_transform(rig):
    p = rig
    p._action_hotkeys.platform = "Windows"
    settings_file(p, hotkey=["ctrl", "shift", "y"])
    p.apply_transform_settings()
    assert p._action_hotkeys.key_down("y", ["ctrl", "shift"]) == ["transform"]
    wait_idle(p)
    assert p.clipboard.pastes == [GOOD]


def test_a_saved_shortcut_that_overlaps_the_dictation_hotkey_is_dropped_and_said_in_the_log(rig):
    p = rig
    p._action_hotkeys.platform = "Windows"
    (p.ns["DATA_DIR"] / "settings.json").write_text(json.dumps(
        {"transforms": {"enabled": True, "hotkey": ["win", "ctrl", "shift", "y"]},
         "hotkey_keys": ["ctrl", "shift"]}), encoding="utf-8")
    p.apply_transform_settings()
    assert p._action_hotkeys.registered() == {}
    assert any("not usable" in line and "overlaps" in line for line in p.logged)


def test_the_shortcut_joins_the_dictation_listener(rig):
    p = rig
    joined = []
    p.hotkey_listener.attach_observer = joined.append
    p._action_hotkeys.platform = "Darwin"
    settings_file(p, hotkey=["control", "option", "t"])
    p.apply_transform_settings()
    p.apply_transform_settings()
    assert len(joined) == 1, "once per listener, however often the settings are applied"


def test_applying_settings_never_raises(rig):
    p = rig
    p._read_settings = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    p.apply_transform_settings()
    assert any("not applied" in line for line in p.logged)


# ── what the hooks hand the runner ───────────────────────────────────────────

def test_the_hooks_are_the_pipelines_own_calls(rig):
    p = rig
    hooks = p._transform_hooks()
    assert hooks.settings == p._read_settings and hooks.dictation_busy == p._dictation_busy
    assert hooks.settle_paste == p._settle_paste and hooks.remember == p._remember
    assert hooks.say == p._transform_say
    assert hooks.platform == "win32"
    # Late-bound: a styler swapped in after start is the one asked.
    other = Model("Other.")
    p.styler = other
    reply = hooks.chat("s", "u", max_tokens=1, temperature=0.3, deadline=None, purpose="transform")
    assert reply.text == "Other." and other.calls
    assert hooks.dashes("a") == "a" and hooks.profanity("a", "b") == "a"


def test_the_hooks_ask_for_the_copy_fallback_and_the_dictation_path_never_does():
    klass = next(n for n in APP_TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    src = ast.unparse(next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "_transform_hooks"))
    assert "read_selection(allow_copy_fallback=True)" in src
    # The check before the paste reads the selection the way it was first read:
    # by copying only where the first read had to copy.
    assert "allow_copy_fallback=method == _text_context.METHOD_COPY" in src


# ── the menu and the update check ────────────────────────────────────────────

def test_an_update_waits_for_a_running_transform():
    helper = next(n for n in APP_TREE.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_update_would_interrupt")
    ns = {}
    exec(compile(ast.Module([helper], []), "<app.py>", "exec"), ns)
    idle = types.SimpleNamespace(is_recording=False, _watchdog=types.SimpleNamespace(active=lambda: []),
                                 _transforms=types.SimpleNamespace(busy=lambda: False))
    busy = types.SimpleNamespace(is_recording=False, _watchdog=types.SimpleNamespace(active=lambda: []),
                                 _transforms=types.SimpleNamespace(busy=lambda: True))
    assert ns["_update_would_interrupt"](idle) == ""
    assert ns["_update_would_interrupt"](busy) == "a transform is running"


def test_the_mac_menu_item_is_there_and_starts_hidden():
    src = ast.unparse(next(n for n in APP_TREE.body if isinstance(n, ast.FunctionDef)
                           and n.name == "_create_mac_menubar_icon"))
    assert "Transform selected text" in src and "transformSelected:" in src
    assert "item_transform.setHidden_(True)" in src
    assert "_mac_transform_item = item_transform" in src
    refresh = ast.unparse(next(n for n in APP_TREE.body if isinstance(n, ast.FunctionDef)
                               and n.name == "_refresh_transform_item"))
    assert "setHidden_(not _transforms_enabled())" in refresh


def test_the_windows_tray_item_is_there_and_visible_only_while_on():
    src = ast.unparse(next(n for n in APP_TREE.body if isinstance(n, ast.FunctionDef)
                           and n.name == "_create_windows_tray_icon"))
    assert "'Transform selected text'" in src and "_transform_async()" in src
    assert "visible=lambda item=None: _transforms_enabled()" in src


def test_the_menu_starts_a_transform_off_the_menus_thread():
    fn = next(n for n in APP_TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_transform_async")
    started = []

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            started.append((target, args, name))
            self.target = target

        def start(self):
            pass

    class Pipe:
        def run_transform(self, origin="shortcut"):
            pass
    ns = {"threading": types.SimpleNamespace(Thread=FakeThread), "_pipeline": Pipe()}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    ns["_transform_async"]()
    assert started and started[0][1] == ("menu",) and started[0][2] == "TransformMenu"
    ns["_pipeline"] = None
    ns["_transform_async"]()                 # not ready yet: nothing, no error
    assert len(started) == 1


# ── the shortcut is in place before the dictation listener starts ───────────

def test_the_shortcut_is_applied_before_the_listener_starts_because_windows_start_does_not_return():
    klass = next(n for n in APP_TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    start = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "start_hotkey")
    calls = sorted((n.lineno, n.func.attr) for n in ast.walk(start)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr in ("apply_transform_settings", "start"))
    names = [c[1] for c in calls]
    assert names.index("apply_transform_settings") < names.index("start")

    api = next(n for n in APP_TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    save = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == "save_hotkey_config")
    # Since S11a the restart is one helper, which a change of the cancel key shares.
    assert any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "_restart_hotkey_listener"
               for n in ast.walk(save))
    helper = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == "_restart_hotkey_listener")
    restart = next(n for n in ast.walk(helper) if isinstance(n, ast.FunctionDef) and n.name == "_restart")
    calls = sorted((n.lineno, n.func.attr) for n in ast.walk(restart)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr in ("apply_transform_settings", "start"))
    names = [c[1] for c in calls]
    assert names.index("apply_transform_settings") < names.index("start")


def test_every_transform_helper_the_harness_lifts_is_a_method_of_the_pipeline():
    klass = next(n for n in APP_TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    have = {n.name for n in klass.body if isinstance(n, ast.FunctionDef)}
    for name in ("_transform_hooks", "_dictation_busy", "transforms_enabled", "run_transform",
                 "apply_transform_settings", "_on_transform_shortcut", "_transform_say",
                 "_transform_ui_begin", "_transform_ui_tick", "_transform_ui_end"):
        assert name in have and name in H.PIPELINE_METHODS, name
