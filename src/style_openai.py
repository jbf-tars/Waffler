"""OpenAI GPT styling module - replaces MiniMax"""

from openai import OpenAI
from pathlib import Path
import time
import re


class OpenAIStyler:
    """Styles transcripts into clean commands using OpenAI GPT-4o-mini"""
    
    def __init__(self, api_key: str, model: str = "gpt-4o-mini", max_tokens: int = 1024, prompt_style: str = "adhd_ramble"):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.prompt_style = prompt_style
        self.client = OpenAI(api_key=api_key)
        
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

        # Inject custom vocabulary hint into prompt
        try:
            from transcribe_whisper import load_vocab
            vocab = load_vocab()
            vocab_hint = (f"\nPreserve these exact spellings: {', '.join(vocab)}." if vocab else "")
        except Exception:
            vocab_hint = ""

        prompt = self.prompt_template.format(transcript=transcript) + vocab_hint

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                max_tokens=512,     # was 1024 — shorter = faster
                temperature=0.3     # was 0.7 — lower = faster, more deterministic
            )
            
            styled = response.choices[0].message.content.strip()
            latency = (time.time() - start_time) * 1000
            
            # Get usage info
            usage = response.usage
            usage_dict = {
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
                "api_used": True
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
