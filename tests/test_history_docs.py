"""What the docs say about history details (S4): the policy, the README and the
CHANGELOG describe the same things the window and the code do."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_policy_readme_and_changelog_say_the_same_thing():
    policy = " ".join((ROOT / "docs" / "CODE_SIGNING_POLICY.md").read_text(encoding="utf-8").split())
    for part in ("usage.jsonl", "inflight.json", "notifications.json", "delete any single entry",
                 "export your history", 'is not removed by "Delete all my data"', "the app you were in",
                 "redo a take from the Journal"):
        assert part in policy, part
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Delete any entry (with an undo), redo a take" in readme and "JSON, CSV or plain text" in readme
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = log[log.index("## [3.16.0]"):log.index("## [3.15.1]")]
    for headline in ("**Delete one entry, and undo it.**", "**Redo this take.**", "**Export history.**",
                     "**A take Waffler was stopped in the middle of comes back.**", "**Entries know more.**",
                     "**The usage record rewrote itself twice per dictation.**",
                     "**Two takes ending in the same second shared one recording.**"):
        assert headline in unreleased, headline
    # No dash of either kind in what was written for this package.
    for text in (unreleased[unreleased.index("**Delete one entry"):unreleased.index("### Changed")],):
        assert "\u2014" not in text and "\u2013" not in text
