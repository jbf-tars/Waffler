"""Download Logs: what support needs, and nothing that says who the person is.

Security review item 7: the zip held the vocabulary files, device names, the
computer's name and the home path. It now leaves the vocabulary out (a count
stays in sysinfo.txt) and, in every text file, writes the home folder as "~",
the user name as <user>, the computer's name as <computer> and a device named
after its owner as "<name>'s ...". crash.log and hotkey.log are capped the
way app.log is.
"""
import ast
import json
import os
import sys
import types
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

import privacy_data as P

ROOT = Path(__file__).resolve().parent.parent
_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


def _download_logs(**ns):
    node = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == "download_logs")
    exec(compile(ast.Module([node], []), "<app.py Api>", "exec"), ns)
    return ns["download_logs"]


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    home = tmp_path / "Users" / "samlee"
    (home / "Desktop").mkdir(parents=True)
    data = home / ".waffler-hosted"
    data.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(P, "personal_strings", lambda home=None, _real=P.personal_strings, **k:
                        _real(home=home, user="samlee", host_names=("Sams-MacBook-Pro.local",)))
    (data / "app.log").write_text(
        f"09:00  started from {home}/Applications/Waffler.app on Sams-MacBook-Pro\n"
        "09:01  microphone: Sam's AirPods Pro (sample rate 16000)\n"
        "09:02  user samlee, SAMLEE in caps, samleex untouched\n", encoding="utf-8")
    (data / "crash.log").write_text(f'File "{home}/x.py", line 1\n', encoding="utf-8")
    (data / "hotkey.log").write_text("10:00  hook installed on SAMS-MACBOOK-PRO\n", encoding="utf-8")
    (data / "settings.json").write_text(json.dumps({
        "theme": "dark", "export_folder": str(home / "Documents"),
        "email_signature": {"enabled": True, "text": "Sam Lee, 07700 900123"}}), encoding="utf-8")
    (data / "vocab.json").write_text(json.dumps([{"word": "Kapoor"}, {"word": "Nimbus"}]), encoding="utf-8")
    (data / "vocab_stars.json").write_text(json.dumps(["Kapoor"]), encoding="utf-8")
    (data / "history.json").write_text(json.dumps([{"text": "a secret"}]), encoding="utf-8")
    me = types.SimpleNamespace(
        get_hotkey_config=lambda: {"keys": ["fn"]},
        get_audio_devices=lambda: [{"name": "Sam's AirPods Pro"}, {"name": "MacBook Pro Microphone"}])
    fn = _download_logs(
        Path=Path, datetime=datetime, json=json, os=os, sys=sys,
        DATA_DIR=data, PROJECT_ROOT=home / "waffler", _privacy=P, _pipeline=None,
        _log_to_file=lambda m: None,
        _platform=types.SimpleNamespace(system=lambda: "Linux"))   # never opens a folder
    r = fn(me)
    assert r["ok"] is True, r
    z = zipfile.ZipFile(r["path"])
    return {n: z.read(n).decode("utf-8") for n in z.namelist()}, home


def test_vocabulary_and_history_are_not_in_it(bundle):
    files, _ = bundle
    names = set(files)
    assert not [n for n in names if "vocab" in n or "history" in n], names
    assert "config/settings.json" in names and "logs/app.log" in names
    assert "Kapoor" not in "".join(files.values()) and "a secret" not in "".join(files.values())
    assert "Vocabulary      : 2 entries (the words are not included)" in files["sysinfo.txt"]


def test_no_home_path_user_or_computer_name_anywhere(bundle):
    files, home = bundle
    for name, text in files.items():
        assert str(home) not in text, name
        assert "samlee" not in text.lower().replace("samleex", ""), (name, text)
        assert "sams-macbook-pro" not in text.lower(), name
        assert "Sam's AirPods" not in text, name
    log = files["logs/app.log"]
    assert "started from ~/Applications/Waffler.app on <computer>" in log
    assert "<name>'s AirPods Pro (sample rate 16000)" in log
    assert "user <user>, <user> in caps, samleex untouched" in log
    assert files["logs/crash.log"].startswith('File "~/x.py"')
    assert "<computer>" in files["logs/hotkey.log"]
    assert "<name>'s AirPods Pro" in files["sysinfo.txt"]
    assert "MacBook Pro Microphone" in files["sysinfo.txt"]     # not named after anyone: kept
    assert "DATA_DIR        : ~/.waffler-hosted" in files["sysinfo.txt"]


def test_settings_keep_what_support_needs_without_the_signature(bundle):
    files, _ = bundle
    s = json.loads(files["config/settings.json"])
    assert s["theme"] == "dark" and s["export_folder"] == "~/Documents"
    assert "07700" not in files["config/settings.json"]


def test_the_readme_says_what_is_left_out(bundle):
    files, _ = bundle
    readme = files["README.txt"]
    assert "Vocabulary (vocab*.json)" in readme and "<computer>" in readme
    assert "logs/hotkey.log" in readme


def test_a_big_log_goes_in_as_its_last_two_megabytes(tmp_path, monkeypatch):
    home = tmp_path / "h"
    (home / "Desktop").mkdir(parents=True)
    data = tmp_path / "d"
    data.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (data / "app.log").write_bytes(b"x" * (3 * 1024 * 1024) + f"\nend {home}\n".encode())
    fn = _download_logs(Path=Path, datetime=datetime, json=json, os=os, sys=sys, DATA_DIR=data,
                        PROJECT_ROOT=tmp_path, _privacy=P, _pipeline=None, _log_to_file=lambda m: None,
                        _platform=types.SimpleNamespace(system=lambda: "Linux"))
    r = fn(types.SimpleNamespace(get_hotkey_config=dict, get_audio_devices=list))
    z = zipfile.ZipFile(r["path"])
    tail = z.read("logs/app.log.tail").decode()
    assert len(tail) <= 2 * 1024 * 1024 + 10 and tail.endswith("end ~\n")


# -- the caps ------------------------------------------------------------------

def test_hotkey_log_is_rotated_at_start_like_app_log(tmp_path):
    (tmp_path / "hotkey.log").write_bytes(b"h" * (P.LOG_MAX_BYTES + 1))
    r = P.tidy_logs_at_start(tmp_path, tmp_path / "settings.json")
    assert r["rotated_hotkey"] is True
    assert (tmp_path / "hotkey.log.1").exists() and not (tmp_path / "hotkey.log").exists()


def test_crash_log_is_rotated_before_it_is_opened():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    head = src[:src.index("faulthandler.enable(file=_crash_log)")]
    assert "_crash_path.stat().st_size > 5 * 1024 * 1024" in head
    assert 'os.replace(_crash_path, _crash_path.with_name("crash.log.1"))' in head
    assert head.index("os.replace(_crash_path") < head.index("_crash_log = open(_crash_path")


def test_the_rotated_copies_go_with_delete_all_my_data():
    for name in ("crash.log.1", "hotkey.log.1", "app.log.1"):
        assert name in P.DATA_FILES


def test_names_are_replaced_only_as_whole_words():
    pairs = P.personal_strings(home="/Users/sam", user="sam", host_names=("Studio.local",))
    out = P.scrub_for_bundle("sample rate; sam; /Users/sam/x; Studio; Studios", pairs)
    assert out == "sample rate; <user>; ~/x; <computer>; Studios"
