"""Importing a corpus script reads no keys.

Tests import scripts/auto_test_corpus.py and its neighbours for their case
lists. Those scripts read ~/.waffler-hosted/.env, and used to do it when
imported, so a test run put the owner's keys into the test process. They now
read it only when run (load_env_keys() from main()).
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ["auto_test_corpus.py", "test_self_correction_corpus.py", "flaky_check.py"]


@pytest.mark.parametrize("name", SCRIPTS)
def test_import_reads_no_env_file(name, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".waffler-hosted").mkdir(parents=True)
    (home / ".waffler-hosted" / ".env").write_text("WAFFLER_SENTINEL_KEY=leaked\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("WAFFLER_SENTINEL_KEY", raising=False)
    spec = importlib.util.spec_from_file_location(f"_noenv_{name[:-3]}", ROOT / "scripts" / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import os
    assert "WAFFLER_SENTINEL_KEY" not in os.environ
    # Running it would read the file: the loader is there and main calls it.
    mod.load_env_keys()
    assert os.environ.get("WAFFLER_SENTINEL_KEY") == "leaked"
