"""A meetings database that cannot be opened is not left open.

Store.db opens the file and sets it up (journal mode, secure delete). On a
file that is not a database the set-up raises, and the half-made connection
used to stay open until the garbage collector ran. On Windows an open file
cannot be deleted, so "Delete all my data" then reported the meetings as not
deleted (tests/test_meetings_delete_all_my_data.py, a database that cannot be
opened goes with its folder). Now the connection is closed before the error
goes on.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.meetings import store as store_module  # noqa: E402


def test_a_file_that_is_not_a_database_is_closed_again(tmp_path, monkeypatch):
    bad = tmp_path / "meetings.db"
    bad.write_bytes(b"this is not a database")
    opened = []
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        con = real_connect(*args, **kwargs)
        opened.append(con)
        return con

    monkeypatch.setattr(store_module.sqlite3, "connect", connect)
    with pytest.raises(sqlite3.DatabaseError):
        store_module.Store(bad)
    assert opened, "the store did try to open the file"
    for con in opened:
        with pytest.raises(sqlite3.ProgrammingError):    # "Cannot operate on a closed database"
            con.execute("SELECT 1")
    bad.unlink()                                          # on Windows this fails while it is open
    assert not bad.exists()
