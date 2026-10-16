"""The roadmap's S10 appendix, the changelog and the README say what Vocabulary
and Snippets v2 do; this keeps them true to the code."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import dictionary  # noqa: E402
import history_edit  # noqa: E402
import learn_words  # noqa: E402
import privacy_data  # noqa: E402
import snippets_io  # noqa: E402
import table_io  # noqa: E402
import vocab_rank  # noqa: E402

DOC = ROOT / "docs" / "dev-notes" / "specs" / "2026-10-16-polished-dictation-roadmap.md"


def appendix():
    text = DOC.read_text(encoding="utf-8")
    return text[text.index("### S10 Vocabulary and Snippets v2"):]


def unreleased():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.index("## [3.16.0]")
    return text[start:text.index("\n## [", start + 5)]


def test_the_appendix_names_the_setting_the_files_and_the_numbers_the_code_uses():
    doc = appendix()
    assert "`learn_from_edits`" in doc and "LEARN_FROM_EDITS_UNKNOWN" in doc
    assert "`vocab_stars.json`" in doc and vocab_rank.STARS_FILE in doc
    assert "KEPT_FILES" in doc and "vocab_stars.json" in privacy_data.KEPT_FILES
    assert "`edited_text`" in doc and history_edit.KEY == "edited_text"
    assert f"`PROMPT_MAX_ENTRIES` = {vocab_rank.PROMPT_MAX_ENTRIES}" in doc
    assert f"`PROMPT_MAX_CHARS` = {vocab_rank.PROMPT_MAX_CHARS}" in doc
    assert "At most four" in doc and learn_words.MAX_SUGGESTIONS == 4
    assert "72 percent" in doc and learn_words.MIN_SIMILARITY == 0.72
    assert "2,000,000 characters and 5,000 rows" in doc
    assert (table_io.MAX_CHARS, table_io.MAX_ROWS) == (2_000_000, 5000)
    assert "first 30 names" in doc and dictionary.PREVIEW_LIST == 30
    assert "first 50 rows" in doc and dictionary.PREVIEW_PROBLEMS == 50
    assert "(60, 4,000, 200)" in doc and (snippets_io.MAX_TRIGGER, snippets_io.MAX_EXPANSION,
                                          snippets_io.MAX_SNIPPETS) == (60, 4000, 200)


def test_the_appendix_names_every_bridge_call_it_added():
    doc = appendix()
    for name in ("export_vocabulary", "preview_vocab_import", "import_vocabulary", "export_snippets",
                 "preview_snippets_import", "import_snippets", "edit_history_text", "set_vocab"):
        assert name in doc, name
        assert re.search(rf"def {name}\(", (ROOT / "app.py").read_text(encoding="utf-8")), name


def test_the_appendix_says_the_old_formats_are_unchanged_for_a_downgrade():
    doc = appendix()
    assert "invisible to Waffler 3.15.1" in doc and "plain list of strings" in doc


def test_the_changelog_says_what_a_person_sees():
    text = unreleased()
    for phrase in ("Find, sort and select your words", "**Stars.**", "Import and export your Vocabulary",
                   "Edit text in the Journal", "Snippets: import and export", "vocab_stars.json",
                   "Suggest words from my edits", "Delete all my data"):
        assert phrase in text, phrase
    for ch in ("—",):
        added = text[text.index("Find, sort and select your words"):]
        assert ch not in added.split("### Changed")[0]


def test_the_readme_lists_the_features_and_the_privacy_of_an_edit():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "import or export it as a CSV" in text and "Import and export as CSV or JSON" in text
    assert "correct a dictation with Edit text" in text
    assert "corrected text is kept with it, on your machine only" in text
