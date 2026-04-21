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
