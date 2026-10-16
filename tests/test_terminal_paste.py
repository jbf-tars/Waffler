"""A result of more than one line is not pasted into a terminal (audit L2).

The selection and the text before the cursor are other people's words, and can
steer what a model writes. A shell without bracketed paste (macOS's own bash
3.2, Windows' cmd) runs each line of a paste as it arrives, so a multi-line
result is copied instead, and the person is told. One line is pasted as ever
(it waits for Return), and so is anything pasted into any other app.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import app_categories as ac  # noqa: E402
import text_context as tc  # noqa: E402
import transform_action as ta  # noqa: E402
import user_messages as um  # noqa: E402
import test_command_action as tca  # noqa: E402
import test_transform_action as tta  # noqa: E402

TERMINALS = [("com.apple.Terminal", "Terminal"), ("com.googlecode.iterm2", "iTerm2"),
             ("dev.warp.Warp-Stable", "Warp"), ("net.kovidgoyal.kitty", "kitty"),
             ("com.mitchellh.ghostty", "Ghostty"), ("", "cmd"), ("", "powershell"), ("", "WindowsTerminal"),
             ("", "pwsh.exe"), ("", "mintty")]
NOT_TERMINALS = [("com.apple.Notes", "Notes"), ("com.apple.mail", "Mail"), ("com.microsoft.VSCode", "Code"),
                 ("com.google.Chrome", "Google Chrome"), ("", "notepad"), ("", "WINWORD"), ("", ""),
                 ("com.apple.dt.Xcode", "Xcode"), ("com.apple.TerminalApp", "Terminal Emulator Pro")]
MULTI = "git status\ngit push --force origin main"


@pytest.mark.parametrize("bundle,name", TERMINALS)
def test_terminals_are_known_by_bundle_or_name(bundle, name):
    assert ac.is_terminal(bundle, name) is True


@pytest.mark.parametrize("bundle,name", NOT_TERMINALS)
def test_other_apps_are_not_terminals(bundle, name):
    assert ac.is_terminal(bundle, name) is False


def test_a_terminal_message_is_a_plain_sentence_without_dashes():
    heading, body = um.TERMINAL_COPIED
    assert body.endswith(".") and "—" not in heading + body and "–" not in heading + body
    assert heading[0].isupper() and not heading.endswith(".")


# ── Command mode ─────────────────────────────────────────────────────────────

def command(front, text, selection=False):
    rig = tca.Rig()
    rig.front = front
    rig.chat_text = f"ACTION: draft\n{text}"
    rig.spoken = "write the commands"
    if not selection:
        rig.ax_result = tc.SelectionResult("", tc.METHOD_NONE, tc.NO_SELECTION)
    rig.go()
    return rig


def test_a_multi_line_draft_is_copied_not_pasted_into_a_terminal():
    rig = command(tc.make_context("com.apple.Terminal", "Terminal"), MULTI)
    assert rig.pastes == [], "each line would run as it arrived"
    assert rig.copies == [MULTI] and rig.remembered == []
    assert rig.last()[2:4] == um.TERMINAL_COPIED
    (entry,) = rig.saved
    assert entry["paste"] == "copied" and entry["after"] == MULTI


def test_a_one_line_draft_is_pasted_into_a_terminal_as_before():
    rig = command(tc.make_context("com.apple.Terminal", "Terminal"), "ls -la")
    assert [p["text"] for p in rig.pastes] == ["ls -la"] and rig.copies == []


def test_a_multi_line_draft_is_pasted_into_any_other_app():
    rig = command(tca.NOTES, "Hi Sam,\n\nSee you at one.\n\nBest")
    assert len(rig.pastes) == 1 and rig.copies == []


def test_a_multi_line_edit_of_a_selection_in_a_terminal_is_copied_too():
    rig = tca.Rig()
    rig.front = tc.make_context("com.googlecode.iterm2", "iTerm2")
    rig.chat_text = f"ACTION: edit\n{tca.SHORT}\nand a second line of it"
    rig.go()
    assert rig.pastes == [] and rig.copies and rig.last()[2:4] == um.TERMINAL_COPIED


# ── Transforms ───────────────────────────────────────────────────────────────

def test_a_multi_line_rewrite_is_copied_not_pasted_into_a_terminal():
    rig = tta.Rig()
    rig.front = tc.make_context("com.apple.Terminal", "Terminal")
    rig.chat_result = tta.reply(tta.GOOD + "\nA second line.")
    rig.go()
    assert rig.pastes == [] and rig.copies == [tta.GOOD + "\nA second line."]
    assert rig.last()[2:4] == um.TERMINAL_COPIED
    assert rig.saved[0]["paste"] == "copied"


def test_a_one_line_rewrite_is_pasted_into_a_terminal_as_before():
    rig = tta.Rig()
    rig.front = tc.make_context("com.apple.Terminal", "Terminal")
    rig.go()
    assert [p["text"] for p in rig.pastes] == [tta.GOOD]


def test_a_multi_line_rewrite_is_pasted_into_any_other_app():
    rig = tta.Rig()
    rig.chat_result = tta.reply(tta.GOOD + "\nA second line.")
    rig.go()
    assert len(rig.pastes) == 1 and rig.copies == []
