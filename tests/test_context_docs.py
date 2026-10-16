"""What the docs say about the app a dictation is for (S5): the policy, the
README and the CHANGELOG describe what the code and the Privacy page do."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _flat(path):
    return " ".join((ROOT / path).read_text(encoding="utf-8").split())


def test_the_policy_says_what_is_read_and_what_is_kept():
    policy = _flat("docs/CODE_SIGNING_POLICY.md")
    for part in ("the app you were in", "its kind", "title of the front window", "never stored", "address of the page",
                 "is not sent to your speech or clean-up provider"):
        assert part in policy, part


def test_the_changelog_says_what_changed_in_plain_words():
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = log[log.index("## [Unreleased]"):log.index("## [3.15.1]")]
    for headline in ("**Each entry notes what kind of app it was for.**",
                     "**Two clean-ups at once could use each other's words.**",
                     '**An error that only contained "rate" paused a provider.**'):
        assert headline in unreleased, headline
    start = unreleased.index("**Each entry notes what kind of app it was for.**")
    mine = unreleased[start:unreleased.index("### Changed")]
    assert "\u2014" not in mine and "\u2013" not in mine
    for jargon in ("AppContext", "bundle_id", "AX", "NSWorkspace", "thread"):
        assert jargon not in mine, jargon


def test_the_readme_says_what_is_noted_and_what_is_not_kept():
    readme = _flat("README.md")
    for part in ("the app you dictated into and its kind", "never sent to a provider",
                 "the title is not stored or logged", "the page address is never read"):
        assert part in readme, part
