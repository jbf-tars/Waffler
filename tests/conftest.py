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
