"""Tests for Private Mode JS-callable API methods on the Api class."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))  # repo root so we can import app.py
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_get_private_mode_status_returns_shape(monkeypatch, tmp_path):
    import settings_store
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "s.json")

    with patch("local_backend.check_ollama_running", return_value=True), \
         patch("local_backend.check_model_installed", return_value=False):
        from app import Api
        api = Api.__new__(Api)
        status = api.get_private_mode_status()

    assert status == {
        "private_mode": False,
        "ollama_running": True,
        "model_installed": False,
    }


def test_set_private_mode_persists(monkeypatch, tmp_path):
    import settings_store
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "s.json")

    from app import Api
    api = Api.__new__(Api)
    api.set_private_mode(True)

    assert settings_store.is_private_mode() is True


def test_check_ollama_now_is_same_shape(monkeypatch, tmp_path):
    import settings_store
    monkeypatch.setattr(settings_store, "SETTINGS_FILE", tmp_path / "s.json")

    with patch("local_backend.check_ollama_running", return_value=False), \
         patch("local_backend.check_model_installed", return_value=False):
        from app import Api
        api = Api.__new__(Api)
        assert set(api.check_ollama_now().keys()) == {"private_mode", "ollama_running", "model_installed"}
