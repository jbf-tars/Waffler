"""Sign out must not leave the computer signed in on Windows.

Seen on a Windows 11 PC (Nov 2026): signing out straight after signing in,
deleting account.json failed now and then with "[WinError 32] being used by
another process" (a scanner or the indexer still held the file just written).
forget() ignored the error, so status() still read the session and the app
said signed in after Sign out (tests/test_account.py
test_signing_out_through_the_api_stops_the_sync_state failed about one run in
twelve there). The delete is now retried, as the replace already was
(src/atomic_json.py).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import account  # noqa: E402
import atomic_json  # noqa: E402


def _busy_then_free(monkeypatch, path, times):
    """os.unlink fails with a sharing violation `times` times for `path`."""
    real = os.unlink
    left = {"n": times}

    def unlink(p, *a, **k):
        if Path(p) == Path(path) and left["n"] > 0:
            left["n"] -= 1
            err = PermissionError(13, "being used by another process")
            err.winerror = 32
            raise err
        return real(p, *a, **k)

    monkeypatch.setattr(os, "unlink", unlink)
    return left


def test_a_briefly_held_file_is_deleted_on_a_later_try(tmp_path, monkeypatch):
    f = tmp_path / "account.json"
    f.write_text("{}", encoding="utf-8")
    waits = []
    left = _busy_then_free(monkeypatch, f, 2)
    assert atomic_json.unlink_with_retry(f, sleep=waits.append) is True
    assert not f.exists() and left["n"] == 0 and len(waits) == 2


def test_a_file_held_throughout_is_reported_not_raised(tmp_path, monkeypatch):
    f = tmp_path / "account.json"
    f.write_text("{}", encoding="utf-8")
    _busy_then_free(monkeypatch, f, 99)
    assert atomic_json.unlink_with_retry(f, sleep=lambda s: None) is False
    assert f.exists()


def test_a_missing_file_counts_as_gone(tmp_path):
    assert atomic_json.unlink_with_retry(tmp_path / "never.json", sleep=lambda s: None) is True


def test_sign_out_forgets_the_session_even_when_the_file_is_held_for_a_moment(tmp_path, monkeypatch):
    session = tmp_path / "account.json"
    monkeypatch.setattr(account, "_session_path", lambda: session)
    monkeypatch.setattr(account, "server_url", lambda: "https://account-test.invalid")
    session.write_text('{"server": "https://account-test.invalid", "token": "made-up"}', encoding="utf-8")
    assert account._session() is not None
    _busy_then_free(monkeypatch, session, 1)
    account.forget()
    assert not session.exists()
    assert account._session() is None
