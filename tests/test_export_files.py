"""Where the Vocabulary and Snippets exports land (src/export_files.py)."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import export_files as X  # noqa: E402
import history_export  # noqa: E402

NOW = datetime(2026, 10, 16, 9, 30, 5)


def test_the_name_has_the_time_and_a_number_when_it_is_taken(tmp_path):
    first = X.unique_path(tmp_path, "waffler-vocabulary", "csv", NOW)
    assert first.name == "waffler-vocabulary-2026-10-16T09-30-05.csv"
    first.write_bytes(b"x")
    assert X.unique_path(tmp_path, "waffler-vocabulary", "csv", NOW).name.endswith("-2.csv")


def test_save_writes_the_bytes_whole_and_leaves_no_temporary_file(tmp_path):
    path = X.save(b"\xef\xbb\xbfword\r\n", "waffler-vocabulary", "csv", tmp_path, NOW)
    assert path.read_bytes() == b"\xef\xbb\xbfword\r\n"
    assert [p.name for p in tmp_path.iterdir()] == [path.name]


def test_two_saves_in_one_second_do_not_overwrite_each_other(tmp_path):
    a = X.save(b"a", "waffler-snippets", "json", tmp_path, NOW)
    b = X.save(b"b", "waffler-snippets", "json", tmp_path, NOW)
    assert a != b and a.read_bytes() == b"a" and b.read_bytes() == b"b"


def test_a_failed_write_leaves_nothing_behind(tmp_path, monkeypatch):
    def boom(src, dst, **kw):
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(X, "replace_with_retry", boom)
    with pytest.raises(PermissionError):
        X.save(b"x", "waffler-vocabulary", "csv", tmp_path, NOW)
    assert list(tmp_path.iterdir()) == []


def test_the_desktop_is_used_when_there_is_one_else_home(tmp_path):
    (tmp_path / "Desktop").mkdir()
    assert X.desktop_folder(tmp_path) == tmp_path / "Desktop"
    other = tmp_path / "nodesk"
    other.mkdir()
    assert X.desktop_folder(other) == other


def test_it_shares_the_journal_exports_folder_and_reveal_helpers():
    assert X.desktop_folder is history_export.desktop_folder
    assert X.reveal_in_folder is history_export.reveal_in_folder


def test_an_export_is_not_in_any_list_delete_my_data_clears():
    import privacy_data
    for name in ("waffler-vocabulary-x.csv", "waffler-snippets-x.json"):
        assert not any(__import__("fnmatch").fnmatch(name, g) for g in privacy_data.DATA_GLOBS)
        assert name not in privacy_data.DATA_FILES
