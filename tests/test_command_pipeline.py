"""Command mode in the app (S8): the pipeline's side of it, run with the real
methods of app.py and the fakes of tests/_pipeline_harness.py.

What is checked is the wiring: that a hold of the Command shortcut goes
through the recorder, the speech service, the styler's shared call, the safe
paste, the Journal and the usage record; that Esc, the pill's X and a
dictation's hotkey treat a running command as they should; and that the
shortcut is registered, applied and dropped with the settings. Nothing real is
touched: no microphone, no Accessibility, no clipboard, no model, no browser.
"""
import ast
import json
import sys
import threading
import time
import types
import webbrowser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _pipeline_harness as H  # noqa: E402
import command_action as ca  # noqa: E402
import pasteboard  # noqa: E402
import pipeline_watchdog as pw  # noqa: E402
import text_context as tc  # noqa: E402
from style_openai import ChatError, ChatReply  # noqa: E402

SEL = ("I just wanted to reach out to let you know that the meeting has now been moved "
       "to Thursday at 3pm because the room is not free on Wednesday.")
SHORT = "The meeting moved to Thursday at 3pm because the room is not free on Wednesday."
APP_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
KEYS = ["ctrl", "shift", "y"]         # the harness is a Windows machine (Ctrl and Alt together would be AltGr)


def settings_file(p, **command):
    body = {"command_mode": {"enabled": True, "hotkey": KEYS, **command}}
    (p.ns["DATA_DIR"] / "settings.json").write_text(json.dumps(body), encoding="utf-8")


class Model:
    """Stands in for the styler's shared call (``_chat``) and its helpers."""

    def __init__(self, text=f"ACTION: edit\n{SHORT}"):
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
                         input_tokens=140, output_tokens=40, finish_reason="stop")

    def _strip_em_dashes(self, text):
        return text

    def _restore_censored_profanity(self, styled, raw):
        return styled


@pytest.fixture
def rig(tmp_path, monkeypatch):
    p = H.make_pipeline(tmp_path, transcriber=H.FakeTranscriber("make this shorter"))
    model = Model()
    p.styler = model
    p.model = model
    p.usage = []
    p.opened = []
    p.ns["record_usage_safely"] = lambda *a, **k: p.usage.append((a, k))
    p.selection = tc.SelectionResult(SEL, "ax", tc.OK)
    p.context = ("", "")
    monkeypatch.setattr(tc, "read_selection", lambda allow_copy_fallback=False, timeout=1.5: p.selection)
    monkeypatch.setattr(tc, "get_text_around_cursor", lambda chars=300, timeout=1.5: p.context)
    monkeypatch.setattr(tc, "frontmost_app", lambda read_title=None: tc.make_context("com.apple.Notes", "Notes"))
    monkeypatch.setattr(webbrowser, "open", lambda url, new=0, autoraise=True: p.opened.append(url) or True)
    settings_file(p)
    p.apply_command_settings()
    return p


def hold(p, seconds=0.4, then=None):
    """Press the Command shortcut, hold it for ``seconds``, let go."""
    assert p._action_hotkeys.key_down("y", ["ctrl", "shift"]) == ["command"]
    assert H.wait_for(lambda: p.page.statuses and p.page.statuses[-1] == "listening", 3)
    if then:
        then()
    time.sleep(seconds)
    p._action_hotkeys.key_up("y")


def wait_idle(p):
    assert H.wait_for(lambda: not p._commands.busy(), 6)
    time.sleep(0.05)


# ── a command, from the pipeline's side ──────────────────────────────────────

def test_a_held_instruction_goes_through_the_recorder_the_speech_service_the_shared_call_and_the_safe_paste(rig):
    p = rig
    hold(p)
    wait_idle(p)

    assert p.audio.starts == 1 and p.audio.stops == 1
    assert p.transcriber.calls == 1
    (call,) = p.model.calls
    assert call["purpose"] == "command" and call["temperature"] == 0.3
    assert call["user"].startswith("Instruction: make this shorter") and SEL in call["user"]

    assert p.clipboard.pastes == [SHORT]
    assert p.clipboard.paste_calls[0]["restore"] is True and p.clipboard.paste_calls[0]["ticket"] is not None

    (entry,) = H.history(p)
    assert entry["kind"] == "command" and entry["action"] == "edit" and entry["instruction"] == "make this shorter"
    assert entry["before"] == SEL and entry["after"] == SHORT
    assert entry["id"] >= 1 and entry["paste"] == "pasted" and entry["app"] == "Notes"
    assert entry["provider"] == {"command": "groq"} and entry.get("took_s", 0.0) >= 0
    assert "text" not in entry and "styled" not in entry and "audio" not in entry, "no recording is kept"
    assert p.page.items == [entry]

    kinds = [(a[0], k.get("purpose")) for a, k in p.usage]
    assert ("gpt", "command") in kinds and ("whisper", "command") in kinds
    gpt = next(k for a, k in p.usage if a[0] == "gpt")
    assert gpt["provider"] == "groq" and gpt["input_tokens"] == 140 and gpt["output_tokens"] == 40

    (toast,) = p.overlay.toasts()
    assert toast["heading"] == "Text edited" and "Ctrl+Z" in toast["body"]
    assert toast["buttons"][0]["action"] == f"show_command:{entry['id']}"
    assert toast["reposition"] is True and toast["timeout"]

    names = p.overlay.names()
    assert names.index("show") < names.index("show_working") < names.index("end_working")
    assert "update_level" in names, "the meter moves while holding"
    assert p.overlay.calls[names.index("end_working")][1] == ("done",)
    assert p.page.statuses == ["listening", "processing", "done"]
    assert p.hotkey_listener.processing == [True, False], "Esc is armed from the moment the keys go down"
    assert p._last_result.get() == "", "'Paste last' is still the last dictation"
    assert p.cue_backend.played == [], "sounds are off unless the person turned them on"
    assert p.is_recording is False, "the dictation's state is untouched"


def test_the_instruction_is_not_cleaned_up_snippeted_or_styled(rig):
    p = rig
    p.transcriber.text = "um make this shorter you know"
    p.styler.style = lambda *a, **k: pytest.fail("the clean-up must not run on an instruction")
    hold(p)
    wait_idle(p)
    assert p.model.calls[0]["user"].startswith("Instruction: um make this shorter you know")


def test_a_question_opens_the_browser_with_the_spoken_question_and_pastes_nothing(rig):
    p = rig
    p.transcriber.text = "what is the capital of Australia"
    p.model.text = "ACTION: ask\ncapital of Australia"
    hold(p)
    wait_idle(p)
    assert p.opened == ["https://www.perplexity.ai/search?q=capital%20of%20Australia"]
    assert p.clipboard.pastes == [] and p.clipboard.copies == []
    (entry,) = H.history(p)
    assert entry["action"] == "ask" and entry["engine"] == "perplexity" and entry["query"] == "capital of Australia"
    assert p.overlay.toasts()[-1]["heading"] == "Searching Perplexity"


def test_the_chosen_engine_comes_from_the_settings(rig):
    p = rig
    settings_file(p, web_engine="duckduckgo")
    p.transcriber.text = "what is the capital of Australia"
    p.model.text = "ACTION: ask\ncapital of Australia"
    hold(p)
    wait_idle(p)
    assert p.opened == ["https://duckduckgo.com/?q=capital%20of%20Australia"]


def test_a_draft_uses_the_text_before_the_cursor_and_is_pasted_at_the_cursor(rig):
    p = rig
    p.selection = tc.SelectionResult("", tc.METHOD_NONE, tc.NO_SELECTION)
    p.context = ("Hi Sam, are we still on for lunch?", "")
    p.transcriber.text = "reply saying I'll be 10 minutes late"
    p.model.text = "ACTION: draft\nHi Sam, yes, but I'll be about ten minutes late."
    hold(p)
    wait_idle(p)
    assert "Hi Sam, are we still on for lunch?" in p.model.calls[0]["user"]
    assert p.clipboard.pastes == ["Hi Sam, yes, but I'll be about ten minutes late."]
    assert H.history(p)[0]["action"] == "draft"


def test_the_copy_fallback_is_only_ever_asked_for_once_the_keys_are_up(rig):
    p = rig
    reads = []

    def read(allow_copy_fallback=False, timeout=1.5):
        reads.append((allow_copy_fallback, p._action_hotkeys._holds != {}))
        return (tc.SelectionResult("", tc.METHOD_NONE, tc.NOT_READABLE) if not allow_copy_fallback
                else tc.SelectionResult(SEL, "copy", tc.OK))
    tc.read_selection = read
    hold(p)
    wait_idle(p)
    assert [r[0] for r in reads][:2] == [False, True]
    copy_reads = [holding for allow, holding in reads if allow]
    assert copy_reads and not any(copy_reads), "no Copy while the shortcut's keys were still down"
    assert p.clipboard.pastes == [SHORT]


def test_a_failed_paste_leaves_the_result_for_copy_again(rig):
    p = rig
    p.clipboard.outcome = pasteboard.PasteOutcome(pasteboard.FAILED, "keystroke_error", on_clipboard=True)
    hold(p)
    wait_idle(p)
    assert p._last_result.get() == SHORT
    assert [t["heading"] for t in p.overlay.toasts()] == ["Couldn't paste"]
    assert H.history(p)[0]["paste"] == "failed"


def test_a_provider_failure_shows_the_message_and_changes_nothing(rig):
    p = rig
    p.model.error = ChatError("RATE_LIMIT|tokens per minute (TPM)|9s|Groq: busy")
    hold(p)
    wait_idle(p)
    (toast,) = p.overlay.toasts()
    assert toast["heading"] == "Limit reached" and toast["style"] == "warn"
    assert p.clipboard.pastes == [] and H.history(p) == []


def test_silence_gets_the_existing_could_not_hear_you_message_through_the_governor(rig):
    p = rig
    p.audio.wav = H.silence_wav(2.0)
    hold(p)
    wait_idle(p)
    assert H.wait_for(lambda: p.overlay.toasts(), 3)
    (toast,) = p.overlay.toasts()
    assert toast["heading"] == "We couldn't hear you" and toast["style"] == "error"
    assert p.model.calls == [] and p.transcriber.calls == 0
    p._notifier.release("no_audio")


def test_the_toast_asks_the_governor_first(rig):
    p = rig
    p._toast_allowed = lambda kind: False
    hold(p)
    wait_idle(p)
    assert p.overlay.toasts() == []
    assert p.clipboard.pastes == [SHORT], "a message that is held back does not hold back the work"


def test_off_means_off(rig):
    p = rig
    settings_file(p, enabled=False)
    p.apply_command_settings()
    assert p._action_hotkeys.registered() == {}
    assert p._commands.down() == ca.OFF
    assert p.audio.starts == 0 and p.model.calls == [] and p.overlay.calls == []
    assert p.command_enabled() is False


# ── a dictation, a transform and a command never overlap ─────────────────────

def test_a_command_will_not_start_while_recording_or_while_a_dictation_is_processed(rig):
    p = rig
    p.is_recording = True
    assert p._commands.down() == ca.BUSY_ELSEWHERE
    p.is_recording = False
    run = p._watchdog.begin(5)
    assert p._commands.down() == ca.BUSY_ELSEWHERE
    p._watchdog.end(run, pw.DONE)
    assert p.audio.starts == 0 and p.model.calls == []
    assert p.overlay.toasts()[-1]["heading"] == "Waffler is busy"


def test_a_command_will_not_start_while_a_transform_runs(rig):
    p = rig
    p._transforms._active = True
    assert p._commands.down() == ca.BUSY_ELSEWHERE
    p._transforms._active = False


def test_a_transform_will_not_start_while_a_command_is_held(rig):
    p = rig
    (p.ns["DATA_DIR"] / "settings.json").write_text(json.dumps({
        "command_mode": {"enabled": True, "hotkey": KEYS}, "transforms": {"enabled": True}}), encoding="utf-8")
    assert p._action_hotkeys.key_down("y", ["ctrl", "shift"]) == ["command"]
    assert H.wait_for(lambda: p._commands.busy())
    assert p._dictation_busy() is True
    assert p.run_transform() == "dictating"
    assert p.overlay.toasts()[-1]["heading"] == "Waffler is busy"
    p._action_hotkeys.key_up("y")
    wait_idle(p)


def test_the_dictation_hotkey_is_refused_while_a_command_runs(rig):
    p = rig
    reset = []
    p.hotkey_listener.reset_state = lambda: reset.append(1)
    p.model.block = threading.Event()
    hold(p, 0.35)
    assert H.wait_for(lambda: p.model.calls)
    starts = p.audio.starts
    p.on_hotkey_press()
    assert p.is_recording is False and p.audio.starts == starts
    assert reset == [1], "the listener is told nothing started"
    assert p.overlay.toasts()[-1]["heading"] == "A command is running"
    p.model.block.set()
    wait_idle(p)
    p.on_hotkey_press()
    assert p.is_recording is True, "and it works again once the command is over"


def test_the_dictation_hotkey_is_refused_while_the_command_keys_are_held(rig):
    p = rig
    assert p._action_hotkeys.key_down("y", ["ctrl", "shift"]) == ["command"]
    assert H.wait_for(lambda: p._commands.busy())
    p.on_hotkey_press()
    assert p.is_recording is False
    p._action_hotkeys.key_up("y")
    wait_idle(p)


# ── Esc and the pill's X ─────────────────────────────────────────────────────

def test_esc_while_holding_discards_the_recording(rig):
    p = rig
    assert p._action_hotkeys.key_down("y", ["ctrl", "shift"]) == ["command"]
    assert H.wait_for(lambda: p.page.statuses and p.page.statuses[-1] == "listening")
    p._on_hotkey_cancel()
    wait_idle(p)
    assert p.audio.stops == 1 and p.transcriber.calls == 0 and p.model.calls == []
    assert p.overlay.toasts()[-1]["heading"] == "Command cancelled"
    assert p.overlay.names().count("hide") >= 1, "the recording pill goes"
    p._action_hotkeys.key_up("y")                  # letting go afterwards changes nothing
    time.sleep(0.15)
    assert p.model.calls == [] and p.page.statuses[-1] == "idle"


def test_esc_while_the_model_thinks_stops_it_and_is_not_a_dictation_cancel(rig):
    p = rig
    p.model.block = threading.Event()
    hold(p, 0.35)
    assert H.wait_for(lambda: p.model.calls)
    p._on_hotkey_cancel()
    wait_idle(p)
    assert p.clipboard.pastes == [] and H.history(p) == []
    assert p.overlay.toasts()[-1]["heading"] == "Command cancelled"
    p.model.block.set()
    assert H.wait_for(lambda: any(k.get("purpose") == "command" and a[0] == "gpt" for a, k in p.usage)), \
        "the answer that came late is still counted"
    assert p.clipboard.pastes == []


def test_the_pills_x_stops_it_too_without_touching_the_dictation_state(rig):
    p = rig
    p.model.block = threading.Event()
    p._processing_cancelled.clear()
    hold(p, 0.35)
    assert H.wait_for(lambda: p.model.calls)
    p._on_overlay_cancel()
    wait_idle(p)
    assert p.clipboard.pastes == []
    assert not p._processing_cancelled.is_set(), "a dictation's cancel flag is not set by a command's"
    p.model.block.set()


def test_esc_with_no_command_is_what_it_always_was(rig):
    p = rig
    p._on_hotkey_cancel()
    assert p.overlay.toasts() == [] and p.page.statuses == []


# ── "Show in Journal" ────────────────────────────────────────────────────────

def test_show_in_journal_opens_the_journal_on_that_entry(rig):
    p = rig
    opened = []
    p._show_journal = lambda: opened.append(1)
    p._on_toast_action("show_command:41")
    assert opened == [1]
    assert p.page.scripts == ["window.waffler_show_command && window.waffler_show_command(41)"]


@pytest.mark.parametrize("action", ["show_command", "show_command:", "show_command:abc", "show_command:-3"])
def test_a_show_in_journal_without_a_real_entry_still_opens_the_journal(rig, action):
    p = rig
    opened = []
    p._show_journal = lambda: opened.append(1)
    p._on_toast_action(action)
    assert opened == [1] and p.page.scripts == []


# ── the shortcut ─────────────────────────────────────────────────────────────

def test_the_shortcut_is_registered_when_on_and_dropped_when_off(rig):
    p = rig
    assert p._action_hotkeys.registered() == {"command": KEYS}
    settings_file(p, enabled=False)
    p.apply_command_settings()
    assert p._action_hotkeys.registered() == {}
    settings_file(p, hotkey=[])
    p.apply_command_settings()
    assert p._action_hotkeys.registered() == {}
    settings_file(p, hotkey=["ctrl", "shift", "k"])
    p.apply_command_settings()
    assert p._action_hotkeys.registered() == {"command": ["ctrl", "shift", "k"]}


def test_both_action_shortcuts_live_side_by_side(rig):
    p = rig
    (p.ns["DATA_DIR"] / "settings.json").write_text(json.dumps({
        "command_mode": {"enabled": True, "hotkey": KEYS},
        "transforms": {"enabled": True, "hotkey": ["ctrl", "shift", "u"]}}), encoding="utf-8")
    p.apply_transform_settings()
    p.apply_command_settings()
    assert p._action_hotkeys.registered() == {"command": KEYS, "transform": ["ctrl", "shift", "u"]}


def test_a_saved_shortcut_that_overlaps_the_dictation_hotkey_is_dropped_and_said_in_the_log(rig):
    p = rig
    (p.ns["DATA_DIR"] / "settings.json").write_text(json.dumps(
        {"command_mode": {"enabled": True, "hotkey": KEYS},
         "hotkey_keys": ["ctrl", "shift"]}), encoding="utf-8")
    p.apply_command_settings()
    assert p._action_hotkeys.registered() == {}
    assert any("not usable" in line and "overlaps" in line for line in p.logged)


def test_a_saved_shortcut_that_clashes_with_the_transform_shortcut_loses(rig):
    p = rig
    p._action_hotkeys.unregister("command")          # as at start-up: Transform is applied first
    (p.ns["DATA_DIR"] / "settings.json").write_text(json.dumps({
        "command_mode": {"enabled": True, "hotkey": KEYS},
        "transforms": {"enabled": True, "hotkey": KEYS}}), encoding="utf-8")
    p.apply_transform_settings()
    p.apply_command_settings()
    assert p._action_hotkeys.registered() == {"transform": KEYS}
    assert any("[command] the saved shortcut is not usable" in line for line in p.logged)


def test_the_shortcut_joins_the_dictation_listener_once(rig):
    p = rig
    joined = []
    p.hotkey_listener.attach_observer = joined.append
    p._action_hotkeys.platform = "Darwin"
    p.apply_command_settings()
    p.apply_command_settings()
    assert len(joined) == 1


def test_applying_settings_never_raises(rig):
    p = rig
    p._read_settings = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    p.apply_command_settings()
    assert any("not applied" in line for line in p.logged)


def test_changing_the_setting_while_a_hold_is_on_ends_it_once(rig):
    p = rig
    p._action_hotkeys.key_down("y", ["ctrl", "shift"])
    assert H.wait_for(lambda: p._commands.busy())
    settings_file(p, enabled=False)
    p.apply_command_settings()                       # the hold ends: the keys are treated as let go
    wait_idle(p)
    assert p.audio.stops == 1


# ── what the hooks hand the runner ───────────────────────────────────────────

def test_the_hooks_are_the_pipelines_own_calls(rig):
    p = rig
    hooks = p._command_hooks()
    assert hooks.settings == p._read_settings and hooks.busy_elsewhere == p._command_blocked
    assert hooks.settle_paste == p._settle_paste and hooks.remember == p._remember
    assert hooks.say == p._command_say and hooks.speech_provider == p._speech_provider_name
    assert hooks.platform == "win32"
    other = Model("Other.")
    p.styler = other
    reply = hooks.chat("s", "u", max_tokens=1, temperature=0.3, deadline=None, purpose="command")
    assert reply.text == "Other." and other.calls
    assert hooks.dashes("a") == "a" and hooks.profanity("a", "b") == "a"
    assert hooks.classify_speech_error(RuntimeError("Connection reset")) == "offline"


def test_the_copy_fallback_is_asked_for_by_the_runner_not_the_hooks_and_never_on_the_dictation_path():
    klass = next(n for n in APP_TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    src = ast.unparse(next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "_command_hooks"))
    assert "allow_copy_fallback=copy" in src and "allow_copy_fallback=True" not in src
    # The runner calls it with True in one place, after the keys are up.
    runner = (ROOT / "src" / "command_action.py").read_text(encoding="utf-8")
    assert runner.count("h.read_selection(True)") == 1 and runner.count("h.read_selection(False)") == 1
    # The dictation path never touches the selection.
    for name in ("_process", "on_hotkey_press", "on_hotkey_release", "_tidy_transcript", "redo_take"):
        body = ast.unparse(next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name))
        for forbidden in ("read_selection", "get_text_around_cursor", "allow_copy_fallback"):
            assert forbidden not in body, f"{name} uses {forbidden}"


def test_the_speech_usage_row_is_the_providers_and_tagged(rig):
    p = rig
    hooks = p._command_hooks()
    hooks.record_speech(4.0, {"backend": "mlx"})
    hooks.record_speech(2.0, {"backend": "api"})
    hooks.record_speech(0.0, {"backend": "groq"})
    rows = [(a, k) for a, k in p.usage]
    assert rows[0] == (("whisper",), {"duration_seconds": 4.0, "provider": "local", "purpose": "command"})
    assert rows[1][1]["provider"] == "openai" and len(rows) == 2


def test_the_browser_is_opened_in_a_new_tab_with_the_standard_library(rig):
    p = rig
    got = []
    import webbrowser as wb
    wb.open = lambda url, new=0, autoraise=True: got.append((url, new)) or True
    assert p._command_hooks().open_url("https://example.com/?q=x") is True
    assert got == [("https://example.com/?q=x", 2)]


# ── the app around it ────────────────────────────────────────────────────────

def test_an_update_waits_for_a_running_command():
    helper = next(n for n in APP_TREE.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_update_would_interrupt")
    ns = {}
    exec(compile(ast.Module([helper], []), "<app.py>", "exec"), ns)

    def pipeline(command):
        return types.SimpleNamespace(is_recording=False, _watchdog=types.SimpleNamespace(active=lambda: []),
                                     _transforms=types.SimpleNamespace(busy=lambda: False),
                                     _commands=types.SimpleNamespace(busy=lambda: command))
    assert ns["_update_would_interrupt"](pipeline(False)) == ""
    assert ns["_update_would_interrupt"](pipeline(True)) == "a command is running"


def test_the_shortcut_is_applied_before_the_listener_starts_because_windows_start_does_not_return():
    klass = next(n for n in APP_TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    start = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "start_hotkey")
    calls = sorted((n.lineno, n.func.attr) for n in ast.walk(start)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr in ("apply_command_settings", "start"))
    names = [c[1] for c in calls]
    assert names.index("apply_command_settings") < names.index("start")

    api = next(n for n in APP_TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    save = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == "save_hotkey_config")
    # Since S11a the restart is one helper, which a change of the cancel key shares.
    assert any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "_restart_hotkey_listener"
               for n in ast.walk(save))
    helper = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == "_restart_hotkey_listener")
    restart = next(n for n in ast.walk(helper) if isinstance(n, ast.FunctionDef) and n.name == "_restart")
    calls = sorted((n.lineno, n.func.attr) for n in ast.walk(restart)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr in ("apply_command_settings", "start"))
    names = [c[1] for c in calls]
    assert names.index("apply_command_settings") < names.index("start")


def test_every_command_helper_the_harness_lifts_is_a_method_of_the_pipeline():
    klass = next(n for n in APP_TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    have = {n.name for n in klass.body if isinstance(n, ast.FunctionDef)}
    for name in ("_command_hooks", "_command_blocked", "command_enabled", "apply_command_settings",
                 "_command_say", "_command_ui_begin", "_command_ui_level", "_command_ui_working",
                 "_command_ui_tick", "_command_ui_end"):
        assert name in have, name
        assert name in H.PIPELINE_METHODS, f"{name} must be in tests/_pipeline_harness.py PIPELINE_METHODS"


def test_the_pipeline_makes_its_runner_next_to_the_transform_runner():
    klass = next(n for n in APP_TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    init = ast.unparse(next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"))
    assert "self._commands = _command_action.CommandRunner(self._command_hooks())" in init
    assert init.index("self._transforms =") < init.index("self._commands =")


def test_there_is_no_menu_item_for_command_mode_because_it_is_hold_to_talk():
    for name in ("_create_mac_menubar_icon", "_create_windows_tray_icon"):
        src = ast.unparse(next(n for n in APP_TREE.body if isinstance(n, ast.FunctionDef) and n.name == name))
        assert "ommand mode" not in src and "_commands" not in src
