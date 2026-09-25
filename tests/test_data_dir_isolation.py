"""The test suite must never write into a real user's data folder.

Modules used to build ``~/.waffler-hosted`` themselves, at import time, so a
test run appended fake retries, macOS ``hdiutil`` lines and update markers to
the real ``app.log``. That noise made the real transcript retry rate (3.2%)
read as 56%. Every path now comes from ``src/data_paths.py``, which honours
``WAFFLER_DATA_DIR``, and ``tests/conftest.py`` points it at a temporary
folder for every test.
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import data_paths  # noqa: E402

REAL_DEFAULT = Path.home() / ".waffler-hosted"


def _is_inside(path: Path, folder: Path) -> bool:
    try:
        Path(path).resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


# ── the resolver ─────────────────────────────────────────────────────────────

def test_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path))
    assert data_paths.data_dir() == tmp_path
    assert data_paths.data_file("app.log") == tmp_path / "app.log"


def test_default_is_unchanged_for_the_installed_app(monkeypatch):
    """Users never set the variable, so they keep ~/.waffler-hosted."""
    monkeypatch.delenv("WAFFLER_DATA_DIR", raising=False)
    assert data_paths.data_dir() == Path.home() / ".waffler-hosted"


def test_blank_env_var_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("WAFFLER_DATA_DIR", "   ")
    assert data_paths.data_dir() == Path.home() / ".waffler-hosted"


def test_every_test_runs_with_a_temporary_data_folder(_isolated_data_dir):
    here = data_paths.data_dir()
    assert here == _isolated_data_dir
    assert not _is_inside(here, REAL_DEFAULT)


# ── the writers that used to hit the real app.log ────────────────────────────

def test_log_util_writes_to_the_isolated_folder(_isolated_data_dir):
    # Loaded from its file: test_fn_handler_chatter.py replaces
    # sys.modules["log_util"] with a silent stub for the whole session.
    import importlib.util
    spec = importlib.util.spec_from_file_location("_real_log_util", ROOT / "src" / "log_util.py")
    log_util = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(log_util)
    log_util.log("isolation probe")
    written = (_isolated_data_dir / "app.log").read_text(encoding="utf-8")
    assert "isolation probe" in written


def test_updater_log_and_markers_use_the_isolated_folder(_isolated_data_dir):
    import updater
    updater._log("isolation probe")
    assert "isolation probe" in (_isolated_data_dir / "app.log").read_text(encoding="utf-8")
    assert updater._pending_dir() == _isolated_data_dir


def test_style_openai_provider_failure_log_uses_the_isolated_folder(_isolated_data_dir):
    import style_openai
    styler = object.__new__(style_openai.OpenAIStyler)
    try:
        raise RuntimeError("isolation probe")
    except RuntimeError as exc:
        styler._log_provider_failure("probe", exc)
    assert "isolation probe" in (_isolated_data_dir / "app.log").read_text(encoding="utf-8")


def test_paths_fixed_at_import_are_not_the_real_folder():
    """Module-level paths are computed when the module is first imported,
    which conftest arranges to happen with the variable already set."""
    import audio_devices
    import single_instance
    import transcribe_whisper
    for path in (transcribe_whisper.VOCAB_FILE, transcribe_whisper.SETTINGS_FILE,
                 audio_devices.CONFIG_FILE, single_instance._FOCUS_SIGNAL_PATH):
        assert not _is_inside(path, REAL_DEFAULT), path


def test_config_reads_no_real_env_file_during_tests():
    import config
    assert not _is_inside(config._user_env, REAL_DEFAULT)


# ── no module builds the folder itself any more ──────────────────────────────

_HARDCODED = re.compile(r"""home\(\)\s*/\s*["']\.waffler-hosted""")


def test_no_module_hard_codes_the_data_folder():
    """A new module that writes ~/.waffler-hosted directly would put the test
    pollution straight back. Use data_paths.data_dir() instead."""
    offenders = []
    for path in sorted((ROOT / "src").glob("*.py")) + [ROOT / "app.py"]:
        if path.name == "data_paths.py":
            continue
        text = path.read_text(encoding="utf-8")
        for m in _HARDCODED.finditer(text):
            # app.py opens crash.log before src/ is importable; that one line
            # reads WAFFLER_DATA_DIR itself, first.
            if path.name == "app.py" and "WAFFLER_DATA_DIR" in text[max(0, m.start() - 200):m.start()]:
                continue
            line = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{path.name}:{line}")
    assert offenders == [], offenders


def test_the_variable_is_set_for_the_whole_session():
    """Belt and braces: set before collection, not only inside fixtures."""
    assert os.environ.get("WAFFLER_DATA_DIR")
