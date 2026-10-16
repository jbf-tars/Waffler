"""What the docs say about the Style section (S6): the changelog, the README,
the privacy policy and the Privacy page describe what the code does."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _flat(path):
    return " ".join((ROOT / path).read_text(encoding="utf-8").split())


def _mine():
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = log[log.index("## [Unreleased]"):log.index("## [3.15.1]")]
    start = unreleased.index("- **Clean-up, On or Off.**")
    return unreleased[start:unreleased.index("### Changed")]


def test_the_changelog_says_what_the_user_gets():
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = log[log.index("## [Unreleased]"):log.index("## [3.15.1]")]
    for headline in ("**Clean-up, On or Off.**", "**A style for each kind of app.**",
                     "**An email signature, if you want one.**"):
        assert headline in unreleased, headline
    mine = " ".join(_mine().split())
    for part in ("no clean-up request is made", "Try again on a Not sent card and Redo this take follow the switch",
                 "the email signature still is", "Casual takes the full stop off a one-sentence message",
                 "never change a word", "not a request to the model", "up to 500 characters",
                 "reads the text before your cursor", "never stores or logs it",
                 "the signature text is left out of the logs zip",
                 "a snippet is always pasted exactly"):
        assert part in mine, part


def test_the_changelog_entries_are_plain_words():
    mine = _mine()
    assert "—" not in mine and "–" not in mine
    for jargon in ("AppContext", "bundle_id", "AX ", "NSWorkspace", "thread", "provider tag", "settings.json",
                   "style_post", "style_layer", "dialect_instruction", "prompt"):
        assert jargon not in mine, jargon


def test_the_readme_has_the_feature_and_the_privacy_line():
    readme = _flat("README.md")
    for part in ("**Style for each kind of app.**", "never change a word", "an optional email signature",
                 "reads the text before your cursor in an email app", "It never stores or logs it",
                 "the signature text is left out of the logs zip"):
        assert part in readme, part


def test_the_policy_says_what_is_read_and_that_it_is_dropped():
    policy = _flat("docs/CODE_SIGNING_POLICY.md")
    for part in ("**Text before your cursor (macOS, only with an email signature on).**",
                 "to read the text before your cursor", "It never reads a password field",
                 "never stored, logged or sent", "in memory only", "its text is left out of the logs bundle",
                 "With the signature off, none of this happens"):
        assert part in policy, part


def test_the_privacy_page_says_it_too_on_a_mac():
    html = " ".join((ROOT / "ui" / "index.html").read_text(encoding="utf-8").split())
    block = html[html.index('id="privPasteReads"'):html.index('<h2 class="sh2">Help</h2>')]
    assert "The text before your cursor" in block
    assert ("Only if you turn on an email signature, in an email app: Waffler reads the text before your "
            "cursor to check your signature isn't already there. It never stores or logs it.") in block
    assert "—" not in block and "–" not in block
    assert "no signature text" in html


def test_the_docs_agree_with_the_limits_in_the_code():
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    import style_layer
    assert style_layer.SIGNATURE_MAX_CHARS == 500 and style_layer.SIGNATURE_RECENT_S == 600
    assert style_layer.SIGNATURE_MIN_WORDS == 10
    mine = " ".join(_mine().split())
    assert "500 characters" in mine and "ten words or more" in mine and "last ten minutes" in mine
