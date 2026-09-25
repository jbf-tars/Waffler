"""An unrelated .env higher up the folder tree must not replace Waffler's keys.

config.py loaded ~/.waffler-hosted/.env and then called
load_dotenv(override=True) with no path. With no path python-dotenv searches
upward from config.py's folder, which in an installed app passes through the
user's home folder, so a developer's ~/.env holding GROQ_API_KEY or
OPENAI_API_KEY silently won over the key entered in Waffler.

These tests copy config.py into a temporary tree with a .env above it, the way
a home folder sits above an install, and load it from there. No real keys:
every value is a placeholder written by the test.
"""
import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

KEYS = ("GROQ_API_KEY", "OPENAI_API_KEY", "CEREBRAS_API_KEY")


@pytest.fixture
def clean_keys(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)


def _load_config_from(tree: Path):
    """Import a copy of src/config.py placed at <tree>/app/src/config.py."""
    target = tree / "app" / "src" / "config.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "src" / "config.py", target)
    spec = importlib.util.spec_from_file_location("_config_copy", target)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_env(path: Path, **pairs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in pairs.items()), encoding="utf-8")


def test_a_home_folder_env_does_not_replace_the_waffler_key(tmp_path, clean_keys,
                                                            _isolated_data_dir):
    _write_env(_isolated_data_dir / ".env", GROQ_API_KEY="waffler-key")
    _write_env(tmp_path / ".env", GROQ_API_KEY="unrelated-home-key",
               OPENAI_API_KEY="unrelated-openai-key")
    _load_config_from(tmp_path)
    assert os.environ["GROQ_API_KEY"] == "waffler-key"
    # Nothing above the app folder is read at all.
    assert "OPENAI_API_KEY" not in os.environ


def test_the_repo_env_still_fills_keys_for_developers(tmp_path, clean_keys,
                                                     _isolated_data_dir):
    """CONTRIBUTING: cp .env.example .env at the repo root. It fills a key the
    data folder lacks, and never replaces one it has."""
    _write_env(_isolated_data_dir / ".env", GROQ_API_KEY="waffler-key")
    _write_env(tmp_path / "app" / ".env", GROQ_API_KEY="repo-key",
               OPENAI_API_KEY="repo-openai-key")
    _load_config_from(tmp_path)
    assert os.environ["GROQ_API_KEY"] == "waffler-key"
    assert os.environ["OPENAI_API_KEY"] == "repo-openai-key"


def test_reload_env_follows_the_same_order(tmp_path, clean_keys, _isolated_data_dir):
    _write_env(tmp_path / ".env", GROQ_API_KEY="unrelated-home-key")
    mod = _load_config_from(tmp_path)
    assert "GROQ_API_KEY" not in os.environ
    _write_env(_isolated_data_dir / ".env", GROQ_API_KEY="waffler-key")
    cfg = object.__new__(mod.Config)
    cfg.config = {}
    cfg.reload_env()
    assert os.environ["GROQ_API_KEY"] == "waffler-key"
    assert cfg.groq_api_key == "waffler-key"


def test_config_never_searches_upward():
    src = (ROOT / "src" / "config.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    assert "load_dotenv(override" not in code
    assert "find_dotenv" not in code
