"""Paste last and Copy last as shortcuts (S11a).

The real pipeline methods from app.py run against the fakes in
tests/_pipeline_harness.py: no keyboard, no clipboard, no overlay. The key
presses are fed to the registry by hand, as a listener would feed them.
"""
import ast
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _pipeline_harness as H  # noqa: E402
import action_hotkeys as ah  # noqa: E402
import notifier  # noqa: E402
import user_messages as um  # noqa: E402
from _pipeline_harness import FakeClipboard, make_pipeline  # noqa: E402

APP_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
PASTE = ["ctrl", "shift", "v"]            # the harness is a Windows machine
COPY = ["ctrl", "shift", "u"]             # (Ctrl and Alt together would be AltGr there)


def settings_file(p, **settings):
    (p.ns["DATA_DIR"] / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


@pytest.fixture
def rig(tmp_path):
    p = make_pipeline(tmp_path)
    p._last_result.set("one two three four five")
    return p


def press(p, keys, key, hold_s=0.0):
    """The person presses ``keys`` (modifiers plus ``key``) and lets go."""
    mods = [k for k in keys if k != key]
    p._action_hotkeys.key_down(key, mods)
    p._action_hotkeys.key_up(key)


def headings(p):
    return [t["heading"] for t in p.overlay.toasts()]


# ── registering ──────────────────────────────────────────────────────────────

def test_nothing_is_registered_until_a_shortcut_is_set(rig):
    rig.apply_last_result_shortcuts()
    assert rig._action_hotkeys.registered() == {}
    settings_file(rig, paste_last_hotkey=PASTE)
    rig.apply_last_result_shortcuts()
    assert rig._action_hotkeys.registered() == {"paste_last": PASTE}
    settings_file(rig, paste_last_hotkey=PASTE, copy_last_hotkey=COPY)
    rig.apply_last_result_shortcuts()
    assert rig._action_hotkeys.registered() == {"paste_last": PASTE, "copy_last": COPY}
    settings_file(rig, copy_last_hotkey=COPY)
    rig.apply_last_result_shortcuts()
    assert rig._action_hotkeys.registered() == {"copy_last": COPY}, "clearing one drops it"
    settings_file(rig)
    rig.apply_last_result_shortcuts()
    assert rig._action_hotkeys.registered() == {}


def test_a_saved_shortcut_that_no_longer_passes_is_dropped_and_said_in_the_log(rig):
    settings_file(rig, copy_last_hotkey=["ctrl", "alt", "k"], paste_last_hotkey=PASTE)
    rig.apply_last_result_shortcuts()
    assert rig._action_hotkeys.registered() == {"paste_last": PASTE}
    assert any("[copy-last]" in line and "not usable" in line and "AltGr" in line for line in rig.logged)


def test_the_two_shortcuts_cannot_share_a_combination_even_from_a_hand_edited_file(rig):
    settings_file(rig, paste_last_hotkey=PASTE, copy_last_hotkey=PASTE)
    rig.apply_last_result_shortcuts()
    assert list(rig._action_hotkeys.registered()) == ["paste_last"]
    assert any("[copy-last]" in line and "Another shortcut (Paste last)" in line for line in rig.logged)


def test_the_cancel_key_in_settings_reaches_the_registry(rig):
    settings_file(rig, cancel_key="f16", copy_last_hotkey=["f16"])
    rig.apply_last_result_shortcuts()
    assert rig._action_hotkeys.registered() == {}, "the cancel key is not a shortcut's"
    assert any("F16 is your cancel key" in line for line in rig.logged)
    settings_file(rig, cancel_key="f17", copy_last_hotkey=["f16"])
    rig.apply_last_result_shortcuts()
    assert rig._action_hotkeys.registered() == {"copy_last": ["f16"]}


def test_transform_and_command_also_tell_the_registry_the_cancel_key(rig):
    settings_file(rig, cancel_key="f9")
    rig.apply_transform_settings()
    assert rig._action_hotkeys._cancel_key == "f9"
    settings_file(rig, cancel_key="f10")
    rig.apply_command_settings()
    assert rig._action_hotkeys._cancel_key == "f10"


def test_the_shortcuts_join_the_dictation_listener(rig):
    joined = []
    rig.hotkey_listener.attach_observer = joined.append
    rig._action_hotkeys.platform = "Darwin"
    settings_file(rig, paste_last_hotkey=["control", "option", "v"])
    rig.apply_last_result_shortcuts()
    rig.apply_last_result_shortcuts()
    assert len(joined) == 1, "once per listener, however often the settings are applied"


def test_applying_never_raises(rig):
    rig._read_settings = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    rig.apply_last_result_shortcuts()
    assert any("not applied" in line for line in rig.logged)


# ── Paste last ───────────────────────────────────────────────────────────────

def test_paste_last_pastes_when_the_keys_come_up(rig):
    settings_file(rig, paste_last_hotkey=PASTE)
    rig.apply_last_result_shortcuts()
    rig._action_hotkeys.key_down("v", ["ctrl", "shift"])
    assert rig.clipboard.paste_calls == [], "the modifiers are still down"
    rig._action_hotkeys.key_up("v")
    [call] = rig.clipboard.paste_calls
    assert call["text"] == "one two three four five" and call["restore"] is True
    assert rig.overlay.toasts() == [], "a paste that went in says nothing"


def test_paste_last_uses_the_same_safe_paste_as_the_menu(rig):
    settings_file(rig, paste_last_hotkey=PASTE, clipboard_after_paste="keep")
    rig.apply_last_result_shortcuts()
    press(rig, PASTE, "v")
    assert rig.clipboard.paste_calls[0]["restore"] is False
    assert any("[paste-last] outcome=pasted words=5" in m for m in rig.logged)


def test_paste_last_pastes_where_the_person_is_not_into_the_app_last_dictated_into(rig):
    """The tray menu on Windows leaves a window of ours in front, so the menu aims at the
    last app; a shortcut leaves nothing of ours in front."""
    settings_file(rig, paste_last_hotkey=PASTE)
    rig.apply_last_result_shortcuts()
    rig._prev_window = 4242                                  # the harness is a Windows machine
    press(rig, PASTE, "v")
    assert rig.clipboard.paste_calls[0]["window"] is None
    rig.paste_last()
    assert rig.clipboard.paste_calls[1]["window"] == 4242, "the menu is as it was"
    assert rig.paste_last(aim_at_last_app=False) and rig.clipboard.paste_calls[2]["window"] is None


def test_on_windows_paste_last_waits_for_the_modifier_keys_to_come_up_before_it_sends_ctrl_v(rig, monkeypatch):
    """The first key to come up starts it; Shift may still be down, and Ctrl+V would be Ctrl+Shift+V."""
    settings_file(rig, paste_last_hotkey=PASTE)
    rig.apply_last_result_shortcuts()
    polls = {"n": 0}

    def down():
        polls["n"] += 1
        return polls["n"] < 5
    monkeypatch.setattr(ah, "windows_modifiers_down", down)
    seen_when_pasted = []
    original = rig.clipboard.paste_text

    def paste_text(*a, **k):
        seen_when_pasted.append(polls["n"])
        return original(*a, **k)
    rig.clipboard.paste_text = paste_text
    press(rig, PASTE, "v")
    assert seen_when_pasted == [5], "it pasted only once the modifiers were up"
    assert len(rig.clipboard.paste_calls) == 1


def test_on_a_mac_paste_last_does_not_wait_itself_because_the_safe_paste_does(rig, monkeypatch):
    rig.ns["_platform"] = types.SimpleNamespace(system=lambda: "Darwin")
    rig._action_hotkeys.platform = "Darwin"
    settings_file(rig, paste_last_hotkey=["control", "option", "v"])
    rig.apply_last_result_shortcuts()
    monkeypatch.setattr(ah, "windows_modifiers_down", lambda: (_ for _ in ()).throw(AssertionError("asked")))
    press(rig, ["control", "option", "v"], "v")
    assert len(rig.clipboard.paste_calls) == 1


def test_a_modifier_that_never_comes_up_does_not_stop_the_paste(rig, monkeypatch):
    settings_file(rig, paste_last_hotkey=PASTE)
    rig.apply_last_result_shortcuts()
    monkeypatch.setattr(ah, "windows_modifiers_down", lambda: True)
    monkeypatch.setattr(ah, "wait_for_modifiers_up",
                        lambda is_down, timeout, **k: (is_down(), timeout)[0] and False)
    press(rig, PASTE, "v")
    assert len(rig.clipboard.paste_calls) == 1


def test_paste_last_waits_for_a_dictation_a_command_or_a_transform_and_says_so(rig):
    settings_file(rig, paste_last_hotkey=PASTE)
    rig.apply_last_result_shortcuts()
    rig.is_recording = True
    press(rig, PASTE, "v")
    assert rig.clipboard.paste_calls == [] and headings(rig) == ["Waffler is busy"]
    assert any("refused: busy" in m for m in rig.logged)
    rig.is_recording = False
    rig._transforms = types.SimpleNamespace(busy=lambda: True)
    rig._action_hotkeys._last_fired.clear()
    rig._notifier = notifier.Notifier(store=notifier.MemoryStore(), context=rig._notifier_context, log=rig.logged.append)
    press(rig, PASTE, "v")
    assert rig.clipboard.paste_calls == [] and headings(rig).count("Waffler is busy") == 2
    rig._transforms = types.SimpleNamespace(busy=lambda: False)
    rig._commands = types.SimpleNamespace(busy=lambda: True)
    rig._action_hotkeys._last_fired.clear()
    rig._notifier = notifier.Notifier(store=notifier.MemoryStore(), context=rig._notifier_context, log=rig.logged.append)
    press(rig, PASTE, "v")
    assert rig.clipboard.paste_calls == [] and headings(rig).count("Waffler is busy") == 3
    rig._commands = types.SimpleNamespace(busy=lambda: False)
    rig._action_hotkeys._last_fired.clear()
    press(rig, PASTE, "v")
    assert len(rig.clipboard.paste_calls) == 1, "and it works again once they are done"


def test_paste_last_while_a_dictation_is_being_processed_is_refused_too(rig):
    settings_file(rig, paste_last_hotkey=PASTE)
    rig.apply_last_result_shortcuts()
    rig._watchdog = types.SimpleNamespace(active=lambda: [object()])
    press(rig, PASTE, "v")
    assert rig.clipboard.paste_calls == [] and headings(rig) == ["Waffler is busy"]


def test_a_refusal_is_said_once_not_for_every_press(rig):
    settings_file(rig, paste_last_hotkey=PASTE)
    rig.apply_last_result_shortcuts()
    rig.is_recording = True
    for _ in range(5):
        rig._action_hotkeys._last_fired.clear()
        press(rig, PASTE, "v")
    assert headings(rig) == ["Waffler is busy"], "the notifier keeps a gap"


def test_paste_last_with_nothing_to_paste_says_so(tmp_path):
    p = make_pipeline(tmp_path)
    settings_file(p, paste_last_hotkey=PASTE)
    p.apply_last_result_shortcuts()
    press(p, PASTE, "v")
    assert p.clipboard.paste_calls == [] and headings(p) == ["Nothing to paste yet"]


def test_a_failed_paste_last_says_why_the_way_the_menu_does(tmp_path):
    out = H.pasteboard.PasteOutcome(H.pasteboard.FAILED, "no_focus", on_clipboard=True)
    p = make_pipeline(tmp_path, clipboard=FakeClipboard(outcome=out))
    p._last_result.set("one two three")
    settings_file(p, paste_last_hotkey=PASTE)
    p.apply_last_result_shortcuts()
    press(p, PASTE, "v")
    assert headings(p) == ["Couldn't paste"]


def test_the_menu_items_are_as_they_were_and_do_not_refuse(rig):
    """Only the shortcuts wait for a dictation: the menu bar items keep their behaviour."""
    rig.is_recording = True
    assert rig.paste_last() == {"ok": True, "outcome": "pasted"}
    assert rig.copy_last() == {"ok": True}
    assert rig.overlay.toasts() == []


# ── Copy last ────────────────────────────────────────────────────────────────

def test_copy_last_copies_when_the_keys_go_down_and_says_it_did(rig):
    settings_file(rig, copy_last_hotkey=COPY)
    rig.apply_last_result_shortcuts()
    rig._action_hotkeys.key_down("u", ["ctrl", "shift"])
    assert rig.clipboard.copies == ["one two three four five"], "nothing here types, so it need not wait"
    assert headings(rig) == ["Copied"]
    assert rig.clipboard.paste_calls == []


def test_copy_last_says_when_it_could_not_or_when_busy_or_empty(tmp_path):
    p = make_pipeline(tmp_path, clipboard=FakeClipboard(copy_ok=False))
    p._last_result.set("one two three")
    settings_file(p, copy_last_hotkey=COPY)
    p.apply_last_result_shortcuts()
    press(p, COPY, "u")
    assert headings(p) == ["Couldn't copy"]

    (tmp_path / "q").mkdir()
    q = make_pipeline(tmp_path / "q")
    settings_file(q, copy_last_hotkey=COPY)
    q.apply_last_result_shortcuts()
    press(q, COPY, "u")
    assert headings(q) == ["Nothing to copy yet"]

    (tmp_path / "r").mkdir()
    r = make_pipeline(tmp_path / "r")
    r._last_result.set("one two three")
    r.is_recording = True
    settings_file(r, copy_last_hotkey=COPY)
    r.apply_last_result_shortcuts()
    press(r, COPY, "u")
    assert r.clipboard.copies == [] and headings(r) == ["Waffler is busy"]


# ── what is said, and what is never logged ───────────────────────────────────

def test_the_messages_are_plain_sentences():
    for message in (um.LAST_BUSY, um.LAST_NOTHING_TO_PASTE, um.LAST_NOTHING_TO_COPY, um.LAST_COPIED,
                    um.LAST_COPY_FAILED):
        heading, body = message
        assert heading and body.endswith(".") and "—" not in heading + body and "–" not in heading + body
        assert not heading.endswith(".")


def test_the_log_never_carries_the_words(tmp_path):
    p = make_pipeline(tmp_path)
    p._last_result.set("the password is hunter two")
    settings_file(p, paste_last_hotkey=PASTE, copy_last_hotkey=COPY)
    p.apply_last_result_shortcuts()
    press(p, PASTE, "v")
    press(p, COPY, "u")
    p.is_recording = True
    p._action_hotkeys._last_fired.clear()
    press(p, PASTE, "v")
    assert not any("hunter" in line for line in p.logged)


def test_the_notice_is_a_registered_kind_with_a_gap_and_the_app_asks_for_it():
    kind = notifier.KINDS["last_result_notice"]
    assert kind.severity == notifier.INFO and kind.min_gap_s == 3 and kind.max_times is None
    src = ast.unparse(next(n for n in ast.walk(APP_TREE) if isinstance(n, ast.FunctionDef)
                           and n.name == "_last_result_say"))
    assert "self._toast_allowed('last_result_notice')" in src


# ── the harness and the app agree ────────────────────────────────────────────

def test_every_helper_the_harness_lifts_is_a_method_of_the_pipeline():
    klass = next(n for n in APP_TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    have = {n.name for n in klass.body if isinstance(n, ast.FunctionDef)}
    for name in ("_last_result_blocked", "_last_result_say", "_on_paste_last_shortcut",
                 "_on_copy_last_shortcut", "apply_last_result_shortcuts"):
        assert name in have, name
        assert name in H.PIPELINE_METHODS, f"{name} must be in tests/_pipeline_harness.py PIPELINE_METHODS"


def test_the_pipeline_never_pastes_from_a_timer_or_reads_the_clipboard_for_the_shortcut():
    """The one ClipboardManager.paste() rule: Paste last goes through paste_last(), which uses paste_text."""
    src = ast.unparse(next(n for n in ast.walk(APP_TREE) if isinstance(n, ast.FunctionDef)
                           and n.name == "_on_paste_last_shortcut"))
    assert "self.paste_last(aim_at_last_app=False)" in src and ".paste(" not in src and "pyperclip" not in src
