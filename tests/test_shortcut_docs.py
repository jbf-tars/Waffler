"""The roadmap's S11a appendix, the CHANGELOG and the README say what the
shortcuts code does; this keeps them true to it (src/action_hotkeys.py,
src/shortcut_settings.py)."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import action_hotkeys as ah  # noqa: E402
import notifier  # noqa: E402
import shortcut_settings as ss  # noqa: E402

DOC = ROOT / "docs" / "dev-notes" / "specs" / "2026-10-16-polished-dictation-roadmap.md"


def section():
    text = DOC.read_text(encoding="utf-8")
    return text[text.index("### S11a Shortcuts"):]


def test_the_appendix_documents_every_key_it_added_with_the_defaults_the_code_has():
    doc = section()
    keys = set(re.findall(r"^\| `([\w.]+)` \|", doc, flags=re.M))
    assert keys == set(ss.SETTING_KEYS)
    view = ss.settings({})
    assert view == {"paste_last_hotkey": [], "copy_last_hotkey": [], "cancel_key": "esc"}
    assert "`[]` (unset)" in doc and '`"esc"` (key absent)' in doc
    assert ah.CANCEL_KEYS[0] == "esc" and ah.CANCEL_KEYS[-1] == "f20"
    assert '`"f1"` to `"f20"`' in doc


def test_the_appendix_names_the_things_the_code_has():
    doc = section()
    for name in ("ACTIONS", "ActionSpec", "saved_shortcuts", "stored_keys", "feature_on", "cancel_key_of",
                 "validate", "cancel_problem", "dictation_clash_message", "swallow_down", "swallow_up",
                 "set_ptt_probe", "set_recording", "RECORDING_MAX_S", "SWALLOW_STALE_S", "MODIFIER_HOLD_S",
                 "register_on_release", "modifiers_changed", "other_key_down", "is_held", "is_swallowing",
                 "get_shortcuts", "check_shortcut", "save_shortcut", "reset_shortcuts", "shortcut_recording",
                 "_restart_hotkey_listener", "apply_last_result_shortcuts", "last_result_notice",
                 "captureKeyId", "shortcutRows", "wait_for_modifiers_up", "windows_event", "mask"):
        assert name in doc, name
    for name in ("validate", "cancel_problem", "dictation_clash_message", "saved_shortcuts", "stored_keys",
                 "feature_on", "cancel_key_of", "wait_for_modifiers_up", "windows_event", "windows_modifiers_down"):
        assert callable(getattr(ah, name)), name
    for name in ("SWALLOW_STALE_S", "RECORDING_MAX_S", "MODIFIER_HOLD_S", "ACTIONS", "CANCEL_KEYS"):
        assert hasattr(ah, name), name
    for method in ("swallow_down", "swallow_up", "set_ptt_probe", "set_recording", "register_on_release",
                   "modifiers_changed", "other_key_down", "is_held", "is_swallowing"):
        assert callable(getattr(ah.ActionHotkeys, method)), method
    assert notifier.KINDS["last_result_notice"].min_gap_s == 3 and "a gap of 3 s" in doc


def test_the_four_actions_are_the_ones_the_appendix_lists():
    doc = section()
    for spec in ah.ACTIONS:
        assert f"`{spec.id}` ({spec.kind})" in doc, spec.id
    assert [a.id for a in ah.ACTIONS] == ["transform", "command", "paste_last", "copy_last"]


def test_the_changelog_says_what_changed_in_the_users_words():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = text[text.index("## [3.16.0]"):text.index("## [3.15.1]")]
    for phrase in ("**Shortcuts: every shortcut in one place.**", "**Paste last and Copy last have shortcuts.**",
                   "**Choose your cancel key.**", "**Command mode can be two modifier keys held together (Mac).**",
                   "**A shortcut's key no longer reaches the app in front.**",
                   "**More combinations are refused, and each says why.**",
                   "**Windows: the keys you choose are the keys you press.**"):
        assert phrase in unreleased, phrase
    for stale in ("it only\n  listens, so it also reaches the app in front", "The shortcut only listens"):
        assert stale not in unreleased
    mine = unreleased[unreleased.index("**Shortcuts: every shortcut in one place.**"):]
    mine = mine[:mine.index("\n### Fixed")]
    assert "—" not in mine and "–" not in mine, "no dashes in what was written for this change"


def test_the_readme_lists_the_page_the_new_shortcuts_and_the_cancel_key():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "**Every shortcut in one place.**" in readme and "Settings, Shortcuts" in readme
    assert "Paste last pastes when you let go of the keys" in readme
    assert "Settings, Shortcuts can change Esc to an F key" in readme
