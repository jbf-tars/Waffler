"""What the docs say about Transforms (S7): the changelog, the README, the
privacy policy, the Privacy page and the roadmap's settings appendix describe
what the code does."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import action_hotkeys  # noqa: E402
import transforms as tf  # noqa: E402


def _flat(path):
    return " ".join((ROOT / path).read_text(encoding="utf-8").split())


def _mine():
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = log[log.index("## [3.16.0]"):log.index("## [3.15.1]")]
    start = unreleased.index("- **Transforms (beta): rewrite the text you selected")
    return unreleased[start:unreleased.index("### Changed")]


def test_the_changelog_says_what_the_user_gets():
    mine = " ".join(_mine().split())
    for part in ("Settings has a new Transforms section, off until you turn it on",
                 "Transform selected text", "Polish", "Prompt Engineer",
                 "up to five of your own", "Not specified", "never invents details",
                 "Your text already looks good", "under 30 percent of the words",
                 "Your app's own Undo takes a rewrite back",
                 "cannot overlap your dictation hotkey",
                 "never starts during a dictation", "Esc or the pill's X",
                 "gives up after 30 seconds", "nothing is pasted: the rewrite is put on your clipboard", "or Waffler cannot check in time",
                 "sharing almost none of your wording",
                 "Up to 1,000 words at a time", "Copy result and Copy original", "Show in Journal",
                 "not counted in your words, dictations, streak or cost per dictation",
                 "only when you use a transform", "sent to the clean-up provider you chose",
                 "Beta"):
        assert part in mine, part


def test_the_changelog_entry_is_plain_words():
    mine = _mine()
    assert "—" not in mine.replace("em dashes", "") and "–" not in mine
    for jargon in ("AppContext", "bundle_id", "AX ", "NSWorkspace", "provider tag", "settings.json",
                   "_chat", "kind:", "usage.jsonl", "watchdog", "event tap", "CGEvent"):
        assert jargon not in mine, jargon


def test_the_changelog_numbers_are_the_codes():
    mine = " ".join(_mine().split())
    assert tf.MAX_WORDS == 1000 and "1,000 words" in mine
    assert tf.MAX_CUSTOM == 5 and tf.MAX_CUSTOM_RULES == 5 and "five" in mine
    assert tf.MIN_SHARE == 0.30 and "30 percent" in mine
    import transform_action
    assert transform_action.ASK_DEADLINE_S == 30.0 and "30 seconds" in mine


def test_the_readme_has_the_feature_and_the_privacy_line():
    readme = _flat("README.md")
    for part in ("**Transforms (beta).**", "Polish with rules you choose",
                 "Prompt Engineer for turning a rough request into a structured prompt",
                 "up to five transforms of your own", "Your app's Undo takes it back",
                 "Transforms is off until you turn it on",
                 "Waffler reads the text you selected", "sends it to your clean-up provider on your own key",
                 "left out of the logs zip"):
        assert part in readme, part


def test_the_policy_says_what_is_read_and_where_it_goes():
    policy = _flat("docs/CODE_SIGNING_POLICY.md")
    for part in ("**Selected text (Transforms, only when you use it).**",
                 "is off until you turn it on", "reads the text selected in the app in front",
                 "Accessibility permission", "pressing Copy and putting your clipboard back",
                 "It never reads a password field and does nothing at any other time",
                 "sent to the clean-up provider you configured", "Recordings and keys are not sent",
                 "kept in your history, and deleted with it", "their wording is left out of the logs bundle",
                 "only watches for that one combination"):
        assert part in policy, part


def test_the_privacy_page_says_it_in_both_places():
    html = " ".join((ROOT / "ui" / "index.html").read_text(encoding="utf-8").split())
    assert ("Only when you use a transform: Waffler reads your selection, through Accessibility or by copying "
            "it and putting your clipboard back, and sends it to your provider on your own key. "
            "It never reads a password field.") in html
    assert ("Only when you use a transform, Waffler reads the text you selected, with Accessibility or by "
            "copying it, and sends it to your provider on your own key.") in html
    block = html[html.index('id="transformsPrivacyRow"'):html.index('id="tipsMemoryRow"')]
    assert "—" not in block and "–" not in block


def test_the_roadmap_documents_every_key_it_added():
    text = (ROOT / "docs" / "dev-notes" / "specs" / "2026-10-16-polished-dictation-roadmap.md").read_text(encoding="utf-8")
    appendix = text[text.index("## Settings added"):]
    appendix = appendix[:appendix.index("### S8")] if "### S8" in appendix else appendix      # each package's own part
    assert "### S7 Transforms (beta)" in appendix
    keys = set(re.findall(r"^\| `([\w.]+)` \|", appendix, flags=re.M))
    view = tf.settings({})
    expected = {"enabled", "hotkey", "default", "polish.rules", "polish.custom_rules",
                "prompt_engineer.skeleton", "custom"}
    assert keys == expected
    for key in expected:
        node = view
        for part in key.split("."):
            assert part in node, key
            node = node[part]
    # What it says about the defaults is true.
    assert "`grammar`, `clarity`, `tone`, `sentences`, `paragraphs` on; `structure`, `concise` off" in appendix
    assert [r.key for r in tf.POLISH_RULES if r.default] == ["grammar", "clarity", "tone", "sentences", "paragraphs"]
    for name in ("transform_result", "transform_busy", "transform_failed", "show_transform:<entry id>",
                 "save_transform_hotkey", "attach_observer", "purpose: \"transform\"", "kind: \"transform\""):
        assert name in appendix, name
    assert "Up to 5" in appendix and "160 characters" in appendix and "1,200 characters" in appendix
    assert action_hotkeys.ActionHotkeys.register and "register(action_id, keys, callback, dictation_hotkey)" in appendix
