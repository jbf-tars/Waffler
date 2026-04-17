import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import local_backend


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


def test_check_ollama_running_true_on_200():
    with patch("local_backend.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(200, {"version": "0.1.0"})
        assert local_backend.check_ollama_running() is True
        mock_get.assert_called_once_with(
            "http://localhost:11434/api/version", timeout=0.5
        )


def test_check_ollama_running_false_on_connection_error():
    import requests
    with patch("local_backend.requests.get") as mock_get:
        mock_get.side_effect = requests.ConnectionError()
        assert local_backend.check_ollama_running() is False


def test_check_ollama_running_false_on_timeout():
    import requests
    with patch("local_backend.requests.get") as mock_get:
        mock_get.side_effect = requests.Timeout()
        assert local_backend.check_ollama_running() is False


def test_check_ollama_running_false_on_500():
    with patch("local_backend.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(500)
        assert local_backend.check_ollama_running() is False


def test_check_model_installed_true_when_tag_present():
    with patch("local_backend.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(200, {
            "models": [{"name": "gemma4:e4b"}, {"name": "llama3:8b"}]
        })
        assert local_backend.check_model_installed() is True


def test_check_model_installed_false_when_tag_missing():
    with patch("local_backend.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(200, {
            "models": [{"name": "llama3:8b"}]
        })
        assert local_backend.check_model_installed() is False


def test_check_model_installed_false_on_error():
    import requests
    with patch("local_backend.requests.get") as mock_get:
        mock_get.side_effect = requests.ConnectionError()
        assert local_backend.check_model_installed() is False


def test_check_model_installed_custom_name():
    with patch("local_backend.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(200, {
            "models": [{"name": "custom:7b"}]
        })
        assert local_backend.check_model_installed("custom:7b") is True


def test_check_model_installed_false_when_models_not_list():
    """Guard against Ollama returning models=null or a non-list."""
    with patch("local_backend.requests.get") as mock_get:
        mock_get.return_value = FakeResponse(200, {"models": None})
        assert local_backend.check_model_installed() is False


import json as _json


def _ndjson_response(lines):
    """Build a fake streaming response yielding NDJSON lines."""
    resp = MagicMock()
    resp.status_code = 200
    resp.iter_lines.return_value = [_json.dumps(l).encode() for l in lines]
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: None
    return resp


def test_pull_model_streams_progress():
    progress = []

    def on_prog(pct):
        progress.append(pct)

    with patch("local_backend.requests.post") as mock_post:
        mock_post.return_value = _ndjson_response([
            {"status": "downloading", "total": 1000, "completed": 0},
            {"status": "downloading", "total": 1000, "completed": 250},
            {"status": "downloading", "total": 1000, "completed": 500},
            {"status": "downloading", "total": 1000, "completed": 1000},
            {"status": "success"},
        ])
        local_backend.pull_model("gemma4:e4b", on_progress=on_prog)

    assert progress == [0.0, 25.0, 50.0, 100.0]


def test_pull_model_raises_on_connection_error():
    import requests
    from errors import LocalUnavailableError
    with patch("local_backend.requests.post") as mock_post:
        mock_post.side_effect = requests.ConnectionError()
        try:
            local_backend.pull_model("gemma4:e4b", on_progress=lambda p: None)
            assert False, "should have raised"
        except LocalUnavailableError:
            pass


def test_pull_model_ignores_progress_without_total():
    """Ollama sometimes emits status-only lines (e.g. 'pulling manifest').
    These should not invoke on_progress."""
    progress = []
    with patch("local_backend.requests.post") as mock_post:
        mock_post.return_value = _ndjson_response([
            {"status": "pulling manifest"},
            {"status": "success"},
        ])
        local_backend.pull_model("gemma4:e4b", on_progress=progress.append)

    assert progress == []


def test_clean_text_returns_response_body():
    with patch("local_backend.requests.post") as mock_post:
        mock_post.return_value = FakeResponse(200, {
            "choices": [{"message": {"content": "cleaned text here"}}],
        })
        out = local_backend.clean_text("user prompt here")
        assert out == "cleaned text here"
        args, kwargs = mock_post.call_args
        assert args[0] == "http://localhost:11434/v1/chat/completions"
        body = kwargs["json"]
        assert body["model"] == "gemma4:e4b"
        assert body["temperature"] == 0
        assert body["messages"] == [{"role": "user", "content": "user prompt here"}]
        assert kwargs["timeout"] == 180  # covers cold-start Ollama model load


def test_clean_text_raises_on_connection_error():
    import requests
    from errors import LocalUnavailableError
    with patch("local_backend.requests.post") as mock_post:
        mock_post.side_effect = requests.ConnectionError()
        try:
            local_backend.clean_text("hi")
            assert False, "should have raised"
        except LocalUnavailableError:
            pass


def test_clean_text_raises_on_non_200():
    from errors import LocalUnavailableError
    with patch("local_backend.requests.post") as mock_post:
        mock_post.return_value = FakeResponse(500, text="boom")
        try:
            local_backend.clean_text("hi")
            assert False, "should have raised"
        except LocalUnavailableError:
            pass


def test_clean_text_raises_on_timeout():
    import requests
    from errors import LocalUnavailableError
    with patch("local_backend.requests.post") as mock_post:
        mock_post.side_effect = requests.Timeout()
        try:
            local_backend.clean_text("hi")
            assert False, "should have raised"
        except LocalUnavailableError:
            pass


def test_clean_text_raises_on_malformed_json():
    """Guard against Ollama returning non-JSON on 200."""
    from errors import LocalUnavailableError
    with patch("local_backend.requests.post") as mock_post:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        mock_post.return_value = resp
        try:
            local_backend.clean_text("hi")
            assert False, "should have raised"
        except LocalUnavailableError:
            pass


def test_clean_text_raises_on_empty_choices():
    """Guard against Ollama returning an empty choices array."""
    from errors import LocalUnavailableError
    with patch("local_backend.requests.post") as mock_post:
        mock_post.return_value = FakeResponse(200, {"choices": []})
        try:
            local_backend.clean_text("hi")
            assert False, "should have raised"
        except LocalUnavailableError:
            pass


def test_model_info_keys_are_present():
    """MODEL_INFO must contain keys the UI depends on."""
    assert "name" in local_backend.MODEL_INFO
    assert "display_name" in local_backend.MODEL_INFO
    assert "download_size_gb" in local_backend.MODEL_INFO
    assert "min_ram_gb" in local_backend.MODEL_INFO


def test_default_model_matches_model_info():
    """DEFAULT_MODEL alias stays in sync with MODEL_INFO['name']."""
    assert local_backend.DEFAULT_MODEL == local_backend.MODEL_INFO["name"]
