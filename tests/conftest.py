"""Pytest configuration for the offline suite.

Keep `pytest tests/` free of credentials, private data and spend.

`test_e2e_real.py` and `test_model_bakeoff.py` are live benchmark SCRIPTS, not
unit tests. They contribute zero test functions, but they do real work at
MODULE level — which pytest executes merely by importing them during
collection:

  * `load_dotenv(~/.waffler-hosted/.env)` — pulls the user's real API keys
    into the test process;
  * `json.loads(~/.waffler-hosted/history.json)` — reads their private
    dictation history;
  * `test_e2e_real` additionally asserts GROQ_API_KEY is present and builds a
    live styler, so on a machine without a key collection ERRORS.

So running the "offline" suite silently depended on the maintainer's personal
credentials and recordings, and would fail for any contributor or CI runner.
Ignoring them at collection time prevents the import entirely; run them
deliberately with `python tests/test_e2e_real.py` when live testing is
intended and authorised.

The data folder
---------------
Every module finds Waffler's data folder through `src/data_paths.py`, which
honours `WAFFLER_DATA_DIR`. Before this, a test run wrote fake events (retries,
macOS `hdiutil` lines, update markers) into the real user's
`~/.waffler-hosted/app.log`, which made the real retry rate of 3.2% read as 56%.

Two layers keep the suite out of the real folder:

  * At import of this file, before any test module is collected, the variable
    points at a throwaway folder. Module-level paths computed at import time
    (`transcribe_whisper.VOCAB_FILE`, `audio_devices.CONFIG_FILE`, ...) land
    there, and `config.py` loads no real `.env`.
  * The autouse `_isolated_data_dir` fixture then gives every test its own
    empty folder under `tmp_path`, for everything resolved at call time
    (`app.log`, `hotkey.log`, update markers).
"""

import os
import shutil
import tempfile

import pytest

collect_ignore = [
    "test_e2e_real.py",
    "test_model_bakeoff.py",
]

_SESSION_DATA_DIR = tempfile.mkdtemp(prefix="waffler-test-data-")
os.environ["WAFFLER_DATA_DIR"] = _SESSION_DATA_DIR


def pytest_unconfigure(config):
    shutil.rmtree(_SESSION_DATA_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path_factory, monkeypatch):
    """Point Waffler's data folder at an empty per-test folder.

    Made with tmp_path_factory rather than inside tmp_path, so a test that
    inspects its own tmp_path finds only what it put there.
    """
    data = tmp_path_factory.mktemp("waffler-data")
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(data))
    return data


@pytest.fixture(autouse=True)
def _no_real_routing_lookup():
    """No test starts `route`, `ifconfig` or PowerShell.

    The `[net]` line a failed provider call writes carries a description of
    the VPN (src/vpn_detect.py), which asks the system. In a test that would
    run a process on whatever computer the suite is on, and put its answer in
    the output. Every command "fails" here, so the answer is "unknown"; the
    tests of vpn_detect itself install their own `_run`.
    """
    import sys
    from pathlib import Path
    src = str(Path(__file__).resolve().parent.parent / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    import vpn_detect
    patched = []
    for name in ("vpn_detect", "src.vpn_detect"):
        mod = sys.modules.get(name)
        if mod is not None:
            patched.append((mod, mod._run, mod._cached))
            mod._run = lambda cmd, timeout: None
            mod._cached = None
    yield
    for mod, run, cached in patched:
        mod._run = run
        mod._cached = cached
    vpn_detect._cached = None


@pytest.fixture(autouse=True)
def _fresh_history_state():
    """Two things app.py keeps in memory for a whole run, so a test must not
    inherit them from the one before: the highest entry id handed out
    (src/history_ids.py) and the Journal's Undo shelf (src/history_ops.py)."""
    import sys
    for name in ("history_ids", "src.history_ids"):
        mod = sys.modules.get(name)
        if mod is not None:
            mod._floor = 0
    for name in ("history_ops", "src.history_ops"):
        mod = sys.modules.get(name)
        if mod is not None:
            mod.SHELF.clear()
    yield


@pytest.fixture(autouse=True)
def _fresh_audio_guard():
    """A bounded audio call a test left stuck (a fake CoreAudio that never
    answers) is remembered process-wide, and while one is, the next bounded call
    is not made (src/audio_guard.py). Forget it before and after every test, so
    one test's wedge cannot make another's microphone calls skip."""
    import sys

    def forget():
        for name in ("audio_guard", "src.audio_guard"):
            mod = sys.modules.get(name)
            if mod is not None:
                mod.reset()
    forget()
    yield
    forget()


# A test's id is also the value of an environment variable (PYTEST_CURRENT_TEST), and on
# Windows that cannot exceed 32,767 characters: a parametrized 60,000-character string
# made CI fail there with "the environment variable is longer than 32767 characters".
# Give such a case an explicit id (pytest.param(..., id="too_long")). Failing at collection
# means every machine finds it, not only the Windows runner.
_MAX_TEST_ID_CHARS = 20_000


def pytest_collection_modifyitems(items):
    too_long = [f"{len(i.nodeid)} chars: {i.nodeid[:110]}..." for i in items
                if len(i.nodeid) > _MAX_TEST_ID_CHARS]
    if too_long:
        import pytest
        raise pytest.UsageError(
            "These tests have names over %d characters, which Windows cannot carry in an "
            "environment variable. Give each case an explicit id:\n  %s"
            % (_MAX_TEST_ID_CHARS, "\n  ".join(too_long)))
