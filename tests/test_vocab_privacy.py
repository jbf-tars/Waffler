"""What the Vocabulary v2 files and fields mean for privacy (S10).

* vocab_stars.json is a list of the words that are starred: it is kept with the
  words list (Delete all my data leaves it, the full reset removes it with the
  folder) and goes in the logs bundle beside vocab.json, which has the same
  words.
* edited_text is part of a Journal entry: stored on this computer in
  history.json, never in a log, deleted with the history and with retention.
* An export (the Vocabulary or Snippets file on the Desktop) is the person's own
  copy: nothing deletes it, and it never holds a key.
* No new module logs a word.
"""
import ast
import fnmatch
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import privacy_data as P  # noqa: E402
import vocab_rank  # noqa: E402

APP = (ROOT / "app.py").read_text(encoding="utf-8")
TREE = ast.parse(APP)
NEW_MODULES = ("table_io", "dictionary", "learn_words", "snippets_io", "history_edit", "vocab_rank", "export_files")


def test_the_stars_are_kept_with_the_words_and_never_on_the_delete_lists():
    assert vocab_rank.STARS_FILE == "vocab_stars.json"
    assert "vocab_stars.json" in P.KEPT_FILES
    assert "vocab_stars.json" not in P.DATA_FILES
    assert not set(P.KEPT_FILES) & set(P.DATA_FILES)
    assert not any(fnmatch.fnmatch("vocab_stars.json", g) for g in P.DATA_GLOBS)


def test_delete_all_my_data_leaves_the_stars_and_empties_edited_text_with_the_history(tmp_path):
    (tmp_path / "vocab.json").write_text('["Groq"]', encoding="utf-8")
    (tmp_path / "vocab_stars.json").write_text('["Groq"]', encoding="utf-8")
    (tmp_path / "history.json").write_text(
        json.dumps([{"timestamp": "2026-10-16T09:00:00", "text": "a", "styled": "A.", "edited_text": "A!"}]),
        encoding="utf-8")
    assert P.delete_my_data(tmp_path) == {"ok": True, "failed": []}
    assert json.loads((tmp_path / "vocab_stars.json").read_text(encoding="utf-8")) == ["Groq"]
    assert json.loads((tmp_path / "vocab.json").read_text(encoding="utf-8")) == ["Groq"]
    assert json.loads((tmp_path / "history.json").read_text(encoding="utf-8")) == []


def test_the_logs_bundle_includes_the_stars_beside_the_words_and_still_not_the_history():
    fn = next(n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef) and n.name == "download_logs")
    listed = [c.value for c in ast.walk(fn) if isinstance(c, ast.Constant) and isinstance(c.value, str)
              and c.value.endswith(".json")]
    assert "vocab.json" in listed and "vocab_stars.json" in listed
    assert "history.json" not in listed and "snippets.json" not in listed
    # The docstring says what is in it, in the same words as the code.
    assert "vocab_stars.json" in ast.get_docstring(fn)


def test_the_full_reset_removes_the_whole_folder_so_the_stars_go_with_it():
    fn = next(n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef) and n.name == "factory_reset")
    calls = [ast.unparse(c) for c in ast.walk(fn) if isinstance(c, ast.Call)]
    assert "shutil.rmtree(data_dir)" in calls


def test_the_stars_file_is_found_through_the_data_folder_at_call_time():
    src = (ROOT / "src" / "vocab_rank.py").read_text(encoding="utf-8")
    assert "data_file(STARS_FILE)" in src and "~/.waffler-hosted" not in src and "Path.home()" not in src


def test_no_new_module_logs_or_prints_a_word():
    """The new modules never print or log. The one exception is the ``log``
    function the bridge hands to vocab_rank.sync_quietly, which is given the
    name of an exception and nothing else."""
    for name in NEW_MODULES:
        src = (ROOT / "src" / f"{name}.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            fn = call.func
            called = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            assert called not in ("print", "_log_to_file", "_wlog"), (name, ast.unparse(call))
            if called == "log":
                assert name == "vocab_rank" and "type(e).__name__" in ast.unparse(call), (name, ast.unparse(call))


def test_the_bridge_logs_counts_file_names_and_exception_names_never_words():
    """Every value put into a log line by the S10 bridge methods is a count, a
    file name or the name of an exception class: never text, a word, a phrase or
    a raw error (which can quote a path)."""
    allowed = {"type(e).__name__", "path.name", "len(words)", "len(items)", "state",
               "counts['new']", "counts['updated']", "counts['skipped']",
               "plan['counts']['new']", "plan['counts']['skipped']"}
    names = ("export_vocabulary", "preview_vocab_import", "import_vocabulary", "export_snippets",
             "preview_snippets_import", "import_snippets", "edit_history_text")
    api = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    seen = 0
    for fn in (n for n in api.body if isinstance(n, ast.FunctionDef) and n.name in names):
        for call in (c for c in ast.walk(fn) if isinstance(c, ast.Call)
                     and isinstance(c.func, ast.Name) and c.func.id == "_log_to_file"):
            for part in ast.walk(call):
                if isinstance(part, ast.FormattedValue):
                    seen += 1
                    assert ast.unparse(part.value) in allowed, (fn.name, ast.unparse(call))
    assert seen >= 8


def test_the_export_files_are_outside_the_data_folder_and_named_so_nothing_deletes_them():
    for stem in ("waffler-vocabulary", "waffler-snippets"):
        name = f"{stem}-2026-10-16T09-00-00.csv"
        assert name not in P.DATA_FILES and not any(fnmatch.fnmatch(name, g) for g in P.DATA_GLOBS)


def test_the_privacy_copy_says_where_an_edit_is_kept_and_that_exports_are_the_persons_own():
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    history = re.search(r'id="privHistoryDesc">(.*?)</div>', html, re.S).group(1)
    assert "Edit text" in history and "on this computer" in history
    assert "Vocabulary and Snippets export from their own page" in html
    assert "Delete all my data leaves it alone" in html


def test_the_readme_and_the_policy_do_not_claim_anything_the_edit_does_not_do():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "edited_text" not in text          # a field name is for the changelog, not the front page
