#!/usr/bin/env python3
"""An update that silently fails must not look like one that succeeded.

The 2026-07-29 v3.14.85 update passed digest and Authenticode verification,
exited into the install batch, and came back 1h43m later still running
v3.14.84 — with no error in app.log and nothing shown to the user. The batch
ignores the installer's exit code, relaunches unconditionally and deletes
itself, and nothing on the next start checks whether the version actually
changed. So "installed" and "silently did nothing" are indistinguishable,
which is exactly what the user kept reporting.

These tests pin the missing half: an intention is recorded before the restart
and reconciled against reality afterwards.

Offline only — every path takes an explicit base directory, so no test reads
or writes the user's real ~/.waffler-hosted.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import updater  # noqa: E402


def test_no_marker_means_nothing_to_report(tmp_path):
    assert updater.check_pending_update("3.14.84", base_dir=tmp_path) is None


def test_successful_update_is_recognised(tmp_path):
    updater.record_pending_update("3.14.85", base_dir=tmp_path)
    res = updater.check_pending_update("3.14.85", base_dir=tmp_path)
    assert res is not None
    assert res["ok"] is True
    assert res["expected"] == "3.14.85"
    assert res["actual"] == "3.14.85"


def test_silent_failure_is_detected(tmp_path):
    """The exact 3.14.85 shape: asked for .85, came back running .84."""
    updater.record_pending_update("3.14.85", base_dir=tmp_path)
    res = updater.check_pending_update("3.14.84", base_dir=tmp_path)
    assert res is not None
    assert res["ok"] is False
    assert res["expected"] == "3.14.85"
    assert res["actual"] == "3.14.84"
    assert "3.14.85" in res["message"] and "3.14.84" in res["message"]


def test_installer_exit_code_is_surfaced_when_available(tmp_path):
    """The batch records the installer's exit code; a failure report must
    include it so the cause is diagnosable instead of guessed."""
    updater.record_pending_update("3.14.85", base_dir=tmp_path)
    (tmp_path / updater.PENDING_RESULT_NAME).write_text("1223\n", encoding="utf-8")
    res = updater.check_pending_update("3.14.84", base_dir=tmp_path)
    assert res["ok"] is False
    assert res["installer_exit_code"] == 1223
    assert "1223" in res["message"]


def test_marker_is_consumed_so_it_reports_once(tmp_path):
    updater.record_pending_update("3.14.85", base_dir=tmp_path)
    assert updater.check_pending_update("3.14.84", base_dir=tmp_path) is not None
    assert updater.check_pending_update("3.14.84", base_dir=tmp_path) is None


def test_corrupt_marker_is_ignored_not_crashed(tmp_path):
    (tmp_path / updater.PENDING_MARKER_NAME).write_text("{not json", encoding="utf-8")
    assert updater.check_pending_update("3.14.84", base_dir=tmp_path) is None


def test_marker_records_what_was_intended(tmp_path):
    updater.record_pending_update("3.14.85", base_dir=tmp_path)
    data = json.loads((tmp_path / updater.PENDING_MARKER_NAME).read_text(encoding="utf-8"))
    assert data["expected_version"] == "3.14.85"
    assert data.get("recorded_at")


def test_recording_never_raises_on_unwritable_dir(tmp_path):
    """Update must proceed even if the marker cannot be written — the marker
    is diagnostics, not a gate."""
    target = tmp_path / "file_not_dir"
    target.write_text("x", encoding="utf-8")
    updater.record_pending_update("3.14.85", base_dir=target)  # must not raise


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
