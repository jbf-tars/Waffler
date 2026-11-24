"""The data folder is its owner's alone, and the updater knows Waffler's signer."""
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import data_paths  # noqa: E402
import updater  # noqa: E402


@pytest.mark.skipif(os.name == "nt", reason="modes mean nothing on Windows")
def test_the_data_folder_is_closed_to_other_accounts(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    folder.mkdir(mode=0o755)
    monkeypatch.setenv(data_paths.ENV_VAR, str(folder))
    data_paths.keep_private()
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_a_folder_that_is_not_there_is_no_error(tmp_path):
    data_paths.keep_private(tmp_path / "missing")


def test_start_up_closes_the_folder():
    source = open(os.path.join(os.path.dirname(__file__), "..", "app.py"), encoding="utf-8").read()
    body = source.split("def get_data_directory():", 1)[1].split("\ndef ", 1)[0]
    assert "_keep_private(data_dir)" in body


def test_the_mac_update_must_be_signed_by_wafflers_team(monkeypatch, tmp_path):
    ran = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(updater.subprocess, "run", lambda cmd, **kw: ran.append(cmd) or Done())
    updater._verify_macos_app_signature(tmp_path / "Waffler.app")
    pinned = [cmd for cmd in ran if any(str(part).startswith("-R=") for part in cmd)]
    assert len(pinned) == 1
    assert f'certificate leaf[subject.OU] = "{updater.MAC_TEAM_ID}"' in " ".join(pinned[0])
    assert "anchor apple generic" in updater.MAC_SIGNER_REQUIREMENT


def test_another_developers_signature_is_refused(monkeypatch, tmp_path):
    class Done:
        def __init__(self, code):
            self.returncode, self.stdout, self.stderr = code, "", "code failed to satisfy specified code requirement(s)"

    monkeypatch.setattr(updater.subprocess, "run",
                        lambda cmd, **kw: Done(3 if any(str(p).startswith("-R=") for p in cmd) else 0))
    with pytest.raises(RuntimeError, match="codesign team"):
        updater._verify_macos_app_signature(tmp_path / "Waffler.app")
