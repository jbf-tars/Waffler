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


def test_pull_gemma_model_runs_in_background_and_reports_progress(monkeypatch):
    import time
    progress_events = [25.0, 50.0, 100.0]

    def fake_pull(name, on_progress):
        for p in progress_events:
            on_progress(p)
            time.sleep(0.01)

    with patch("local_backend.pull_model", side_effect=fake_pull):
        from app import Api
        api = Api.__new__(Api)
        api.pull_gemma_model()

        # Poll until done (max 2s)
        deadline = time.time() + 2
        while time.time() < deadline:
            prog = api.get_model_pull_progress()
            if prog.get("done"):
                break
            time.sleep(0.02)

    final = api.get_model_pull_progress()
    assert final["done"] is True
    assert final["percent"] == 100.0
    assert final.get("error") is None


def test_get_model_pull_progress_before_any_pull(monkeypatch):
    """Calling the poll accessor before pull_gemma_model returns safe defaults."""
    from app import Api
    api = Api.__new__(Api)
    prog = api.get_model_pull_progress()
    assert prog == {"percent": 0.0, "done": False, "error": None, "running": False}


def test_get_model_info_returns_dict_copy():
    """Api.get_model_info returns a usable dict for JS."""
    from app import Api
    import local_backend
    api = Api.__new__(Api)
    info = api.get_model_info()
    assert info == local_backend.MODEL_INFO
    # Returned copy is independent — mutating it shouldn't affect the source
    info["name"] = "mutated"
    assert local_backend.MODEL_INFO["name"] == "gemma4:e4b"
