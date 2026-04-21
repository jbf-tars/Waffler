import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import settings_store


def test_load_missing_file_returns_empty_dict(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "missing.json")
    assert settings_store.load() == {}


def test_load_reads_existing_file(monkeypatch, tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"private_mode": True}))
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", p)
    assert settings_store.load() == {"private_mode": True}


def test_set_persists_key(monkeypatch, tmp_path):
    p = tmp_path / "s.json"
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", p)
    settings_store.set_key("private_mode", True)
    assert json.loads(p.read_text())["private_mode"] is True


def test_set_preserves_other_keys(monkeypatch, tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"language": "en"}))
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", p)
    settings_store.set_key("private_mode", True)
    data = json.loads(p.read_text())
    assert data == {"language": "en", "private_mode": True}


def test_private_mode_default_false(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "missing.json")
    assert settings_store.is_private_mode() is False
