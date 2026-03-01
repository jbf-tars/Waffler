"""
Backend Transcriber
Routes transcription through the Waffler Railway backend instead of calling OpenAI directly.
"""

import requests


class BackendTranscriber:
    """Transcriber that proxies audio through the Waffler backend API."""

    def __init__(self, backend_url: str, app_secret: str = ""):
        self.backend_url = backend_url.rstrip("/")
        self.app_secret = app_secret
        self._backend = "backend"

    def transcribe_sync(self, audio_bytes: bytes) -> str:
        """Send audio to backend for transcription, return text."""
        headers = {}
        if self.app_secret:
            headers["X-App-Secret"] = self.app_secret

        resp = requests.post(
            f"{self.backend_url}/transcribe/transcribe",
            files={"file": ("audio.wav", audio_bytes, "audio/wav")},
            headers=headers,
            timeout=30,
        )

        if resp.status_code != 200:
            detail = resp.json().get("detail", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            raise RuntimeError(f"Backend transcription failed ({resp.status_code}): {detail}")

        return resp.json()["text"]

    def get_duration_seconds(self) -> float:
        """Not available via backend — return 0."""
        return 0.0
