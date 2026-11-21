"""`--self-check` (src/self_check.py): a build says whether every part it needs
is inside it, without a window, a shortcut, a connection or the person's data.
And where a built Mac app looks for its computer-sound helper."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import self_check                      # noqa: E402
from src.meetings import recorder               # noqa: E402


def test_app_answers_self_check_with_json_and_writes_nothing(tmp_path):
    env = dict(os.environ, WAFFLER_DATA_DIR=str(tmp_path))
    done = subprocess.run([sys.executable, str(ROOT / "app.py"), "--self-check"], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    answer = json.loads(done.stdout)             # stdout holds the JSON and nothing else
    assert answer["ok"] is True
    assert answer["modules"]["missing"] == {} and answer["files"]["missing"] == []
    assert answer["modules"]["checked"] >= len(self_check.MODULES)
    assert set(answer["engines"]) == {"speech", "writing"}
    for engine in answer["engines"].values():
        assert engine["runs"] is engine["present"] or "error" in engine
    assert list(tmp_path.iterdir()) == []        # not even crash.log


def test_self_check_is_decided_before_crash_log_and_the_window():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    flag = source.index("'--self-check' in sys.argv")
    assert flag < source.index('"crash.log"') and flag < source.index("import webview")


def test_missing_files_are_named_and_fail_the_check(tmp_path):
    (tmp_path / "ui" / "meetings-app").mkdir(parents=True)
    (tmp_path / "ui" / "meetings-app" / "index.html").write_text(
        '<script type="module" src="./assets/index-abc.js"></script>'
        '<link rel="stylesheet" href="./assets/index-abc.css">', encoding="utf-8")
    (tmp_path / "ui" / "meetings-app" / "assets").mkdir()
    (tmp_path / "ui" / "meetings-app" / "assets" / "index-abc.css").write_text("", encoding="utf-8")
    missing = self_check.check_files(tmp_path)
    assert "ui/meetings-app/index.html" not in missing
    assert "ui/account.js" in missing and "config.yaml" in missing
    assert "ui/meetings-app/assets/index-abc.js" in missing          # named by the page, not there
    assert "ui/meetings-app/assets/index-abc.css" not in missing
    assert self_check.run(tmp_path)["ok"] is False


def test_a_module_that_does_not_import_is_named_with_the_reason():
    missing = self_check.check_modules(("account", "no_such_part_of_waffler"))
    assert list(missing) == ["no_such_part_of_waffler"]
    assert "ModuleNotFoundError" in missing["no_such_part_of_waffler"]


def test_complete_fails_when_an_optional_part_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(self_check, "check_modules", lambda names: {})
    monkeypatch.setattr(self_check, "check_files", lambda root: [])
    monkeypatch.setattr(self_check, "check_speech", lambda: {"present": True, "runs": True})
    monkeypatch.setattr(self_check, "check_writing", lambda: {"present": False, "runs": False})
    monkeypatch.setattr(self_check, "check_computer_sound", lambda: {"needed": True, "present": True})
    assert self_check.run(tmp_path)["ok"] is True                    # optional: reported only
    strict = self_check.run(tmp_path, complete=True)
    assert strict["ok"] is False and strict["optional_missing"] == ["writing engine"]


def test_built_app_finds_the_sound_helper_where_the_build_puts_it(tmp_path, monkeypatch):
    macos, frameworks = tmp_path / "MacOS", tmp_path / "Frameworks"
    macos.mkdir()
    frameworks.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(macos / "Waffler"))
    monkeypatch.setattr(sys, "_MEIPASS", str(frameworks), raising=False)
    monkeypatch.setenv("NATTER_SYSAUDIO", "/somewhere/else")         # never honoured in a built app
    assert recorder._mac_helper_path() == macos / "natter-sysaudio"  # neither is there: beside the executable
    (frameworks / "natter-sysaudio").write_text("")
    assert recorder._mac_helper_path() == frameworks / "natter-sysaudio"
    (macos / "natter-sysaudio").write_text("")
    assert recorder._mac_helper_path() == macos / "natter-sysaudio"
