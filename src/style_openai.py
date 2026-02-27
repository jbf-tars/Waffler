"""LLM styling module — Self-hosted backend (priority) or Groq LLaMA (fast) or OpenAI GPT-4o-mini (fallback)"""

from openai import OpenAI
from pathlib import Path
import time
import re
import os
import requests

# ── Try to load Groq SDK ────────────────────────────────────────────────────
_groq_mod = None
try:
    import groq as _groq_mod
except ImportError:
    pass

# ── Try to load backend auth module ─────────────────────────────────────────
_backend_auth = None
try:
    import natter_auth_backend as _backend_auth
except ImportError:
    pass


class OpenAIStyler:
    """Styles transcripts — Self-hosted backend (priority) or Groq LLaMA 3.3 70B (fast) or GPT-4o-mini (fallback)"""

    def __init__(self, api_key: str = "", model: str = "gpt-4o-mini",
                 max_tokens: int = 1024, prompt_style: str = "adhd_ramble",
                 groq_api_key: str = ""):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.prompt_style = prompt_style
        self.groq_api_key = groq_api_key
        self.client = OpenAI(api_key=api_key) if api_key else None
        self._groq_client = None
        self._use_groq = False
        self._use_backend = False
        self._backend_url = os.getenv("BACKEND_URL", "")

        # Priority 1: Self-hosted backend (no API keys needed!)
        if self._backend_url and _backend_auth:
            if _backend_auth.is_logged_in():
                self._use_backend = True
                print(f"⚡ Styling: Self-hosted backend ({self._backend_url})")
            elif _backend_auth.check_backend_health():
                print(f"⚠️  Backend available but not logged in. Sign in to use self-hosted styling.")

        # Priority 2: Groq for styling if available
        if not self._use_backend and groq_api_key and _groq_mod:
            self._groq_client = _groq_mod.Groq(api_key=groq_api_key)
            self._use_groq = True
            self._groq_model = "llama-3.3-70b-versatile"
            print(f"⚡ Styling: Groq {self._groq_model}")
        elif not self._use_backend:
            print(f"⚡ Styling: OpenAI {model}")

        # Load prompt template
        self.prompt_template = self._load_prompt_template()
        
    def _load_prompt_template(self) -> str:
        """Load the prompt template based on style setting"""
        prompt_path = Path(__file__).parent.parent / "prompts" / f"{self.prompt_style}.txt"
        
        if not prompt_path.exists():
            print(f"⚠️  Prompt file not found: {prompt_path}, using default")
            return self._get_default_prompt()
            
        with open(prompt_path, 'r') as f:
            return f.read()
            
    def _get_default_prompt(self) -> str:
        """Default prompt if file not found"""
        return """You are a voice-to-text assistant for ADHD brains. Clean up this rambling speech transcript and rewrite it as clear, structured text. Remove filler words (um, uh, like, you know), fix backtracking, preserve all ideas and technical details. Output ONLY the cleaned text, nothing else.

Transcript: {transcript}"""
        
    def _is_simple(self, transcript: str) -> bool:
        """Short / already-clean transcript — skip API, just regex-clean."""
        words = transcript.split()
        if len(words) <= 5:
            return True
        fillers = {'um', 'uh', 'like', 'you know', 'basically', 'so', 'right', 'okay'}
        filler_count = sum(1 for w in words if w.lower().strip('.,!?') in fillers)
        # < 15% filler words AND short → skip API
        return len(words) <= 10 and filler_count / len(words) < 0.15

    def style(self, transcript: str):
        """Convert raw transcript to styled text. Returns (styled_text, usage_dict)."""
        # Fast path: skip API for short/clean transcripts
        if self._is_simple(transcript):
            cleaned = self._basic_clean(transcript)
            print(f"⚡ Fast-path clean (no API)")
            return cleaned, {"input_tokens": 0, "output_tokens": 0, "api_used": False}

        start_time = time.time()

        # Load custom vocabulary for all providers
        try:
            from transcribe_whisper import load_vocab
            vocab = load_vocab()
        except Exception:
            vocab = []

        # Priority 1: Try self-hosted backend first
        if self._use_backend:
            try:
                return self._style_backend(transcript, vocab, start_time)
            except Exception as e:
                print(f"⚠️  Backend styling failed ({e}), falling back to Groq/OpenAI")

        # Priority 2: Try Groq (much faster), fall back to OpenAI
        if self._use_groq:
            vocab_hint = (f"\nPreserve these exact spellings: {', '.join(vocab)}." if vocab else "")
            prompt = self.prompt_template.format(transcript=transcript) + vocab_hint
            try:
                return self._style_groq(prompt, start_time)
            except Exception as e:
                print(f"⚠️  Groq styling failed ({e}), falling back to OpenAI")
                if not self.client:
                    return self._basic_clean(transcript), {"input_tokens": 0, "output_tokens": 0, "api_used": False}

        # Priority 3: OpenAI fallback
        vocab_hint = (f"\nPreserve these exact spellings: {', '.join(vocab)}." if vocab else "")
        prompt = self.prompt_template.format(transcript=transcript) + vocab_hint
        return self._style_openai(prompt, transcript, start_time)

    def _style_backend(self, transcript: str, vocab: list, start_time: float):
        """Style using self-hosted backend with serverless GPU — ~1-3s."""
        import requests

        api_key = _backend_auth.get_api_key()
        if not api_key:
            raise Exception("Not authenticated with backend")

        response = requests.post(
            f"{self._backend_url}/style/style",
            json={
                "transcript": transcript,
                "api_key": api_key,
                "prompt_style": self.prompt_style,
                "vocabulary": vocab
            },
            timeout=30
        )

        if response.status_code == 429:
            raise Exception("Monthly quota exceeded. Upgrade your plan or wait until next month.")
        elif response.status_code == 503:
            raise Exception("Backend LLM service not configured")
        elif response.status_code != 200:
            error = response.json().get("detail", f"Backend error: {response.status_code}")
            raise Exception(error)

        data = response.json()
        styled = data["styled_text"]
        latency = (time.time() - start_time) * 1000

        usage_dict = {
            "input_tokens": data["usage"]["input_tokens"],
            "output_tokens": data["usage"]["output_tokens"],
            "api_used": True,
            "provider": "backend",
            "quota_used": data["usage"].get("quota_used", 0),
            "quota_remaining": data["usage"].get("quota_remaining", 0)
        }
        print(f"⚡ Backend styling complete ({latency:.0f}ms) — Quota: {usage_dict['quota_remaining']} remaining")
        return styled, usage_dict

    def _style_groq(self, prompt: str, start_time: float):
        """Style using Groq LLaMA — ~200-400ms."""
        system_msg = (
            "You are a voice-to-text formatter. Output ONLY the final cleaned/formatted text. "
            "NEVER output your classification, reasoning, labels, or any meta-commentary. "
            "Do NOT prefix your output with things like 'This is a COMMAND' or 'Output:'. "
            "Just return the cleaned text directly."
        )
        response = self._groq_client.chat.completions.create(
            model=self._groq_model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            max_tokens=512,
            temperature=0.3,
        )
        styled = response.choices[0].message.content.strip()
        latency = (time.time() - start_time) * 1000
        usage = response.usage
        usage_dict = {
            "input_tokens": usage.prompt_tokens if usage else 0,
            "output_tokens": usage.completion_tokens if usage else 0,
            "api_used": True,
            "provider": "groq",
        }
        print(f"⚡ Groq styling complete ({latency:.0f}ms)")
        return styled, usage_dict

    def _style_openai(self, prompt: str, transcript: str, start_time: float):
        """Style using OpenAI GPT-4o-mini — fallback."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a voice-to-text formatter. Output ONLY the final cleaned text. No meta-commentary."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=512,
                temperature=0.3,
            )
            styled = response.choices[0].message.content.strip()
            latency = (time.time() - start_time) * 1000
            usage = response.usage
            usage_dict = {
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
                "api_used": True,
                "provider": "openai",
            }
            print(f"✅ GPT styling complete ({latency:.0f}ms)")
            return styled, usage_dict
        except Exception as e:
            print(f"❌ GPT styling error: {e}")
            return self._basic_clean(transcript), {"input_tokens": 0, "output_tokens": 0, "api_used": False}
            
    def _basic_clean(self, text: str) -> str:
        """Fallback basic cleaning if API fails"""
        fillers = ['um', 'uh', 'like', 'you know', 'so basically', 'basically']
        cleaned = text
        for filler in fillers:
            cleaned = re.sub(rf'\b{filler}\b', '', cleaned, flags=re.IGNORECASE)
        return re.sub(r'\s+', ' ', cleaned).strip()
