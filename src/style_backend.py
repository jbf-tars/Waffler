"""
Backend Styler
Routes LLM styling through the Waffler Railway backend instead of calling OpenAI/Groq directly.
"""

import requests


class BackendStyler:
    """Styler that proxies text cleanup through the Waffler backend API."""

    def __init__(self, backend_url: str, app_secret: str = "", prompt_style: str = "smart"):
        self.backend_url = backend_url.rstrip("/")
        self.app_secret = app_secret
        self.prompt_style = prompt_style

    def style(self, transcript: str, vocabulary=None) -> tuple:
        """Send transcript to backend for styling, return (styled_text, usage_dict)."""
        headers = {"Content-Type": "application/json"}
        if self.app_secret:
            headers["X-App-Secret"] = self.app_secret

        payload = {
            "transcript": transcript,
            "prompt_style": self.prompt_style,
        }
        if vocabulary:
            payload["vocabulary"] = vocabulary

        resp = requests.post(
            f"{self.backend_url}/style/style",
            json=payload,
            headers=headers,
            timeout=30,
        )

        if resp.status_code != 200:
            detail = resp.json().get("detail", resp.text) if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            raise RuntimeError(f"Backend styling failed ({resp.status_code}): {detail}")

        data = resp.json()
        usage = data.get("usage", {})
        usage["api_used"] = True
        usage["provider"] = "backend"

        return data["styled_text"], usage
