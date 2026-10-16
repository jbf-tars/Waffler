"""What the docs say about Command mode (S8): the changelog, the README, the
privacy policy, the Privacy page and the roadmap's settings appendix describe
what the code does."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import action_hotkeys  # noqa: E402
import command_action  # noqa: E402
import command_mode as cm  # noqa: E402
import transforms  # noqa: E402


def _flat(path):
    return " ".join((ROOT / path).read_text(encoding="utf-8").split())


def _mine():
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = log[log.index("## [Unreleased]"):log.index("## [3.15.1]")]
    start = unreleased.index("- **Command mode (beta): hold a shortcut and say what you want done.**")
    return unreleased[start:unreleased.index("### Changed")]


def test_the_changelog_says_what_the_user_gets():
    mine = " ".join(_mine().split())
    for part in ("Settings has a new Command mode section, off until you turn it on",
                 "hold them, say an instruction and let go",
                 "\"Make this shorter\" or \"Translate to French\" edits it in place",
                 "your app's own Undo takes it back",
                 "\"Reply saying I'll be 10 minutes late\" writes new text where your cursor is",
                 "up to 500 characters before the cursor as context",
                 "Perplexity, Google, DuckDuckGo, ChatGPT or Claude",
                 "with only your spoken question in the address, never the text you selected",
                 "not cleaned up, turned into a snippet or given a style",
                 "A draft never replaces a selection",
                 "the draft is put on your clipboard and your text is left alone",
                 "nothing is pasted: the result goes on your clipboard instead",
                 "Up to 1,000 words at a time", "Esc or the pill's X stops it until the paste begins",
                 "gives up after 30 seconds from letting go",
                 "never starts during a dictation", "Holding for under 0.3 seconds does nothing",
                 "We couldn't hear you", "\"Edited\", \"Drafted\" or \"Searched\" with the engine",
                 "not counted in your words, dictations, streak or cost per dictation",
                 "no Redo because no recording is kept",
                 "cannot overlap your dictation hotkey or your Transform shortcut",
                 "sent only while you hold the shortcut", "on your own key", "Beta"):
        assert part in mine, part


def test_the_changelog_entry_is_plain_words():
    mine = _mine()
    assert "—" not in mine and "–" not in mine
    for jargon in ("AppContext", "bundle_id", "AX ", "NSWorkspace", "provider tag", "settings.json",
                   "_chat", "kind:", "usage.jsonl", "watchdog", "event tap", "CGEvent", "JSON", "ACTION:"):
        assert jargon not in mine, jargon


def test_the_changelog_numbers_are_the_codes():
    mine = " ".join(_mine().split())
    assert cm.MAX_SELECTION_WORDS == 1000 and "1,000 words" in mine
    assert cm.CONTEXT_CHARS == 500 and "500 characters" in mine
    assert command_action.TOTAL_DEADLINE_S == 30.0 and "30 seconds" in mine
    assert command_action.TAP_S == 0.3 and "0.3 seconds" in mine
    assert transforms.MIN_SHARE_CUSTOM == 0.10 and "a tenth of your words" in mine
    assert [e.label for e in cm.ENGINES] == ["Perplexity", "Google", "DuckDuckGo", "ChatGPT", "Claude"]


def test_the_readme_has_the_feature_and_the_privacy_line():
    readme = _flat("README.md")
    for part in ("**Command mode (beta).**", "Hold a shortcut and say what you want",
                 "edit the selected text", "write new text where your cursor is",
                 "ask a question and Waffler opens it in your search engine",
                 "Perplexity, Google, DuckDuckGo, ChatGPT or Claude",
                 "Command mode is off until you turn it on",
                 "Waffler records what you say, your speech provider turns it into text",
                 "with the text you selected (or up to 500 characters before your cursor when nothing is selected)",
                 "goes to your clean-up provider on your own key",
                 "A question opens your browser with only your spoken question in the address",
                 "The recording is not kept"):
        assert part in readme, part


def test_the_policy_says_what_is_read_and_where_it_goes():
    policy = _flat("docs/CODE_SIGNING_POLICY.md")
    for part in ("**What you say, and the text around it (Command mode, only while you hold its shortcut).**",
                 "is off until you turn it on", "records what you say",
                 "to the speech provider you configured", "the recording is not kept",
                 "reads the text selected in the app in front", "Accessibility permission",
                 "by pressing Copy and putting your clipboard back",
                 "up to 500 characters before your cursor",
                 "It never reads a password field and does nothing at any other time",
                 "sent to the clean-up provider you configured",
                 "opens your browser at the search engine you chose with only your spoken question in the address",
                 "the selected text and the text before the cursor are never put there",
                 "kept in your history and deleted with it",
                 "only watches for that one combination"):
        assert part in policy, part
    block = policy[policy.index("**What you say, and the text around it"):]
    block = block[:block.index("- **The app you are in.**")]
    assert "—" not in block and "–" not in block


def test_the_roadmap_documents_every_key_it_added():
    text = (ROOT / "docs" / "dev-notes" / "specs" / "2026-10-16-polished-dictation-roadmap.md").read_text(encoding="utf-8")
    appendix = text[text.index("### S8 Command mode (beta)"):]
    keys = set(re.findall(r"^\| `([\w.]+)` \|", appendix, flags=re.M))
    assert keys == {"enabled", "hotkey", "web_engine"}
    view = cm.settings({})
    for key in keys:
        assert key in view, key
    # What it says about the defaults and the engines is true.
    assert view == {"enabled": False, "hotkey": [], "web_engine": "perplexity"}
    for engine in cm.ENGINES:
        assert f"`{engine.id}`" in appendix, engine.id
    for name in ("command_result", "command_busy", "command_failed", "show_command:<entry id>",
                 "save_command_hotkey", "register_hold", "modifiers_changed", "modifier_up", "saved_shortcuts",
                 "purpose: \"command\"", "kind: \"command\"", "NON_DICTATION_KINDS", "command_count",
                 "ACTION: edit", "parse_reply", "search_url", "ask_query"):
        assert name in appendix, name
    assert action_hotkeys.ActionHotkeys.register_hold
    assert "register_hold(action_id, keys, on_down, on_up, dictation_hotkey)" in appendix
    assert "30 seconds in all from letting go" in appendix and "capped at 60 seconds" in appendix
    assert command_action.MAX_HOLD_S == 60.0 and command_action.TOTAL_DEADLINE_S == 30.0


def test_the_s7_part_of_the_appendix_still_stands_alone():
    text = (ROOT / "docs" / "dev-notes" / "specs" / "2026-10-16-polished-dictation-roadmap.md").read_text(encoding="utf-8")
    assert text.index("### S7 Transforms (beta)") < text.index("### S8 Command mode (beta)")
    s7 = text[text.index("### S7 Transforms (beta)"):text.index("### S8 Command mode (beta)")]
    assert set(re.findall(r"^\| `([\w.]+)` \|", s7, flags=re.M)).isdisjoint({"web_engine"})
