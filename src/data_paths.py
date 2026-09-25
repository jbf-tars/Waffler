"""Where Waffler keeps its data: settings, keys, history, usage and logs.

Every module asks here instead of building ``~/.waffler-hosted`` itself, so
there is exactly one answer. ``WAFFLER_DATA_DIR`` overrides it when set.

The override exists for the test suite. Modules used to hard-code the home
folder at import time, so running the tests wrote fake events into the real
user's ``app.log`` (fake retries, macOS ``hdiutil`` lines on Windows, update
markers). That noise made the real transcript retry rate of 3.2% read as 56%.
``tests/conftest.py`` now points this variable at a temporary folder for every
test. The installed app never sets it, so users see no change.

The folder is resolved on every call, not cached at import, so a test that
changes the variable is honoured even by modules imported earlier.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "WAFFLER_DATA_DIR"
DEFAULT_DIRNAME = ".waffler-hosted"


def data_dir() -> Path:
    """The data folder: ``$WAFFLER_DATA_DIR`` if set, else ``~/.waffler-hosted``.

    Does not create the folder. Callers that write should ``mkdir`` first, as
    they always have.
    """
    override = os.environ.get(ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_DIRNAME


def data_file(name: str) -> Path:
    """A file inside the data folder, e.g. ``data_file("app.log")``."""
    return data_dir() / name
