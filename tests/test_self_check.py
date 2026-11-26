"""`--self-check` (src/self_check.py): a build says whether every part it needs
is inside it, without a window, a shortcut, a connection or the person's data.
And where a built Mac app looks for its computer-sound helper."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def _all_there(monkeypatch):
    monkeypatch.setattr(self_check, "check_modules", lambda names: {})
    monkeypatch.setattr(self_check, "check_files", lambda root: [])
    monkeypatch.setattr(self_check, "check_speech", lambda: {"present": True, "runs": True})
    monkeypatch.setattr(self_check, "check_writing", lambda: {"present": True, "runs": True})
    monkeypatch.setattr(self_check, "check_computer_sound", lambda: {"needed": True, "present": True})
    monkeypatch.setattr(self_check, "check_libraries",
                        lambda: {"anthropic": {"present": True}, "audio_decoder": {"present": True}})


def test_complete_fails_when_an_optional_part_is_absent(tmp_path, monkeypatch):
    _all_there(monkeypatch)
    assert self_check.run(tmp_path, complete=True)["ok"] is True
    monkeypatch.setattr(self_check, "check_writing", lambda: {"present": False, "runs": False})
    monkeypatch.setattr(self_check, "check_libraries",
                        lambda: {"anthropic": {"present": False}, "audio_decoder": {"present": False}})
    assert self_check.run(tmp_path)["ok"] is True                    # optional: reported only
    strict = self_check.run(tmp_path, complete=True)
    assert strict["ok"] is False
    assert strict["optional_missing"] == ["writing engine", "anthropic", "decoder for Import audio"]


# ── Windows: the writing engine is llama.cpp's own server, never in a build ──

def _windows_without_llama_cpp(monkeypatch, tmp_path):
    """A Windows build: no llama-cpp-python in it, an empty data folder, no card.
    Returns llama_pack as the check imports it (its card is the one asked)."""
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *args, **kwargs:
                        None if name == "llama_cpp" else real_find_spec(name, *args, **kwargs))
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WAFFLER_LLAMA_SERVER", raising=False)
    pack = self_check._import("llama_pack")
    monkeypatch.setattr(pack._gpu, "card", lambda: None)
    return pack


def _server_here(tmp_path):
    from src import local_llm_server
    server = tmp_path / "llama" / local_llm_server._SERVER_NAME
    server.parent.mkdir()
    server.write_bytes(b"")
    return server


def test_on_windows_the_writing_engine_comes_down_with_the_model(tmp_path, monkeypatch):
    pack = _windows_without_llama_cpp(monkeypatch, tmp_path)
    writing = self_check.check_writing()
    assert writing["engine"] == "llama.cpp server" and writing["comes_with_model"] is True
    assert writing["present"] is False and writing["runs"] is False and "error" not in writing
    assert pack.CPU[0].file.name in writing["detail"]                # what this computer would fetch
    _all_there(monkeypatch)
    monkeypatch.setattr(self_check, "check_writing", lambda: writing)
    strict = self_check.run(tmp_path, complete=True)
    assert "writing engine" not in strict["optional_missing"] and strict["ok"] is True


def test_on_windows_a_server_already_here_is_asked_its_version(tmp_path, monkeypatch):
    _windows_without_llama_cpp(monkeypatch, tmp_path)
    server = _server_here(tmp_path)
    asked = []

    def run(args, **kwargs):
        asked.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "version: 11168 (abc1234)\nbuilt with clang for x86_64\n")

    monkeypatch.setattr(subprocess, "run", run)
    writing = self_check.check_writing()
    assert asked == [[str(server), "--version"]]
    assert writing["present"] is True and writing["runs"] is True and "comes_with_model" not in writing
    assert writing["detail"].startswith("version: 11168")


def test_on_windows_a_server_that_does_not_start_is_reported(tmp_path, monkeypatch):
    _windows_without_llama_cpp(monkeypatch, tmp_path)
    _server_here(tmp_path)
    monkeypatch.setattr(subprocess, "run",                           # 0xC0000135: a library it needs is missing
                        lambda args, **kwargs: subprocess.CompletedProcess(args, 3221225781, "", ""))
    writing = self_check.check_writing()
    assert writing["present"] is True and writing["runs"] is False and "3221225781" in writing["error"]


def test_the_meeting_libraries_are_reported_as_they_are_here():
    import importlib.util
    libraries = self_check.check_libraries()
    assert libraries["anthropic"]["present"] is (importlib.util.find_spec("anthropic") is not None)
    assert "ffmpeg" not in libraries and "audio_decoder" in libraries


# ── the decoder for Import audio (src/meetings/audio_decode.py) ─────────────

@pytest.mark.skipif(sys.platform != "darwin" or not Path("/usr/bin/afconvert").exists(),
                    reason="needs macOS's afconvert")
def test_on_a_mac_the_system_decoder_reads_a_two_second_m4a_made_here():
    decoder = self_check.check_audio_decoder()
    assert decoder["present"] is True and decoder["how"] == "system", decoder
    assert decoder["path"] == "/usr/bin/afconvert" and 1.8 <= decoder["seconds"] <= 2.3


def test_on_a_mac_without_afconvert_the_decoder_is_missing(monkeypatch):
    decode = self_check._import("meetings.audio_decode")
    monkeypatch.setattr(decode, "AFCONVERT", "/nowhere/afconvert")
    decoder = self_check.check_audio_decoder("darwin")
    assert decoder["present"] is False and "error" in decoder


def test_on_windows_the_audio_decoder_comes_down_on_first_use(tmp_path, monkeypatch):
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path))
    decoder = self_check.check_audio_decoder("win32")
    assert decoder["comes_on_first_use"] is True and decoder["present"] is False
    assert decoder["how"] == "download" and "error" not in decoder
    assert "audio-decoder" in decoder["detail"] and str(tmp_path) not in decoder["detail"]
    _all_there(monkeypatch)
    monkeypatch.setattr(self_check, "check_libraries",
                        lambda: {"anthropic": {"present": True}, "audio_decoder": decoder})
    strict = self_check.run(tmp_path, complete=True)
    assert "decoder for Import audio" not in strict["optional_missing"] and strict["ok"] is True


def test_on_windows_a_fetched_decoder_is_reported_present(tmp_path, monkeypatch):
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path))
    helper = tmp_path / "audio-decoder" / "ffmpeg.exe"
    helper.parent.mkdir()
    helper.write_bytes(b"")
    decoder = self_check.check_audio_decoder("win32")
    assert decoder["present"] is True and decoder["path"] == str(helper)
    assert "comes_on_first_use" not in decoder


def test_meeting_keys_are_reported_without_keyring(monkeypatch):
    # keyring is left out of the build on purpose; this is the path that ships.
    monkeypatch.setenv("NATTER_NO_KEYRING", "1")
    monkeypatch.delenv("NATTER_KEYS_FILE", raising=False)
    assert self_check.check_meeting_keys() == {"keyring": False, "store": "memory"}


def test_an_audio_file_is_put_through_import_audio(tmp_path, monkeypatch):
    import wave
    _all_there(monkeypatch)
    clip = tmp_path / "clip.wav"
    with wave.open(str(clip), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x01" * 16000)
    answer = self_check.run(tmp_path, audio=str(clip))
    assert answer["audio"]["decoded"] is True and answer["audio"]["needed_decoder"] is False
    assert answer["audio"]["seconds"] == 1.0 and answer["ok"] is True
    (tmp_path / "broken.m4a").write_bytes(b"not audio")
    monkeypatch.setenv("PATH", str(tmp_path))                        # no ffmpeg of the person's own
    broken = self_check.run(tmp_path, audio=str(tmp_path / "broken.m4a"))
    assert broken["audio"]["decoded"] is False and "error" in broken["audio"]
    assert broken["ok"] is False                                     # a file that was asked for must decode


# ── the build installs and names what meetings import inside functions ──────

def _names(requirements_file):
    import re
    text = (ROOT / requirements_file).read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and not line.strip().startswith("#")]
    return {re.split(r"[<>=;\s\[]", line, maxsplit=1)[0].lower() for line in lines}


def test_both_requirements_install_anthropic_and_leave_keyring_out():
    for name in ("requirements.txt", "requirements_windows.txt"):
        names = _names(name)
        assert "anthropic" in names, name
        assert "keyring" not in names, name


def test_the_mac_build_installs_no_ffmpeg():
    # No GPL program in the app (26 Nov 2026): macOS's afconvert decodes.
    assert "imageio-ffmpeg" not in _names("requirements.txt")


def test_both_specs_name_anthropic_among_the_hidden_imports():
    for name in ("Waffler_mac.spec", "Waffler_windows.spec"):
        spec = (ROOT / name).read_text(encoding="utf-8")
        hidden = spec[spec.index("hiddenimports=["):spec.index("excludes=[")]
        assert "'anthropic'," in hidden, name
        assert "keyring" not in hidden, name


def test_the_mac_spec_keeps_imageio_ffmpeg_out():
    spec = (ROOT / "Waffler_mac.spec").read_text(encoding="utf-8")
    hidden = spec[spec.index("hiddenimports=["):spec.index("excludes=[")]
    excludes = spec[spec.index("excludes=["):spec.index("noarchive=")]
    assert "'imageio_ffmpeg'," not in hidden and "'imageio_ffmpeg'," in excludes


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
