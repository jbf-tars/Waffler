"""LLM styling module — Groq LLaMA (fast) or OpenAI GPT-4o-mini (fallback)"""

from openai import OpenAI
from pathlib import Path
import time
import re
import os

# ── Try to load Groq SDK ────────────────────────────────────────────────────
_groq_mod = None
try:
    import groq as _groq_mod
except ImportError:
    pass


class OpenAIStyler:
    """Styles transcripts — Groq LLaMA 3.3 70B (fast) or GPT-4o-mini (fallback)"""

    def __init__(self, api_key: str = "", model: str = "gpt-4o-mini",
                 max_tokens: int = 1024, prompt_style: str = "normal",
                 groq_api_key: str = ""):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.prompt_style = prompt_style
        self.groq_api_key = groq_api_key
        self.client = OpenAI(api_key=api_key) if api_key else None
        self._groq_client = None
        self._use_groq = False

        # Priority 1: Groq for styling if available
        if groq_api_key and _groq_mod:
            self._groq_client = _groq_mod.Groq(api_key=groq_api_key)
            self._use_groq = True
            self._groq_model = "llama-3.3-70b-versatile"
            print(f"Styling: Groq {self._groq_model}")
        else:
            print(f"Styling: OpenAI {model}")

        # Load prompt template
        self.prompt_template = self._load_prompt_template()

    def _load_prompt_template(self) -> str:
        """Load the prompt template based on style setting"""
        prompt_path = Path(__file__).parent.parent / "prompts" / f"{self.prompt_style}.txt"

        if not prompt_path.exists():
            print(f"Prompt file not found: {prompt_path}, using default")
            return self._get_default_prompt()

        with open(prompt_path, 'r') as f:
            return f.read()

    def _get_default_prompt(self) -> str:
        """Default prompt if file not found"""
        return """You are a voice-to-text assistant. Clean up this speech transcript and rewrite it as clear, structured text. Remove filler words (um, uh, like, you know), fix backtracking, preserve all ideas and technical details. Output ONLY the cleaned text, nothing else.

Transcript: {transcript}"""

    def _is_simple(self, transcript: str) -> bool:
        """Short / already-clean transcript — skip API, just regex-clean."""
        words = transcript.split()
        if len(words) <= 5:
            return True
        fillers = {'um', 'uh', 'like', 'you know', 'basically', 'so', 'right', 'okay'}
        filler_count = sum(1 for w in words if w.lower().strip('.,!?') in fillers)
        return len(words) <= 10 and filler_count / len(words) < 0.15

    def style(self, transcript: str):
        """Convert raw transcript to styled text. Returns (styled_text, usage_dict)."""
        if self._is_simple(transcript):
            cleaned = self._basic_clean(transcript)
            return cleaned, {"input_tokens": 0, "output_tokens": 0, "api_used": False}

        start_time = time.time()

        # Load custom vocabulary
        try:
            from transcribe_whisper import load_vocab, load_settings
            vocab = load_vocab()
        except Exception:
            vocab = []

        # Load dialect/spelling setting
        dialect_instruction = "Use the same spelling as the user. Do not change spelling conventions."
        try:
            settings = load_settings()
            dialect = settings.get("dialect", "auto")
            if dialect == "en-GB":
                dialect_instruction = "Use British English spelling (e.g. colour, organise, centre, behaviour, realise, programme, defence, licence, favour, catalogue)."
            elif dialect == "en-US":
                dialect_instruction = "Use American English spelling (e.g. color, organize, center, behavior, realize, program, defense, license, favor, catalog)."
        except Exception:
            pass

        # Build prompt (vocab goes into system message, NOT appended to transcript)
        prompt = self.prompt_template.format(
            transcript=transcript,
            dialect_instruction=dialect_instruction,
        )
        self._vocab_system_extra = (
            f" If any of these words were intended by the speaker, use these exact spellings: {', '.join(vocab)}."
            if vocab else ""
        )

        # Priority 1: Try Groq (much faster), fall back to OpenAI
        if self._use_groq:
            try:
                return self._style_groq(prompt, start_time)
            except Exception as e:
                import traceback
                err_detail = traceback.format_exc()
                print(f"Groq styling failed ({e}), falling back to OpenAI")
                # Log the error to file so it's not silently swallowed
                from pathlib import Path
                from datetime import datetime
                try:
                    log_file = Path.home() / ".waffler-hosted" / "app.log"
                    with open(log_file, "a") as f:
                        ts = datetime.now().strftime("%H:%M:%S")
                        f.write(f"{ts}  [styling] Groq FAILED: {e}\n")
                        f.write(f"{ts}  [styling] {err_detail}\n")
                except Exception:
                    pass
                if not self.client:
                    return self._basic_clean(transcript), {"input_tokens": 0, "output_tokens": 0, "api_used": False}

        # Priority 2: OpenAI fallback
        return self._style_openai(prompt, transcript, start_time)

    def _style_groq(self, prompt: str, start_time: float):
        """Style using Groq LLaMA — ~200-400ms."""
        system_msg = (
            "You are a voice-to-text formatter. Output ONLY the final cleaned/formatted text. "
            "Follow ALL formatting rules in the user prompt exactly — including paragraph breaks, "
            "email structure, numbered lists, and bullet points. Preserve line breaks in your output. "
            "NEVER output your classification, reasoning, labels, or any meta-commentary. "
            "Do NOT prefix your output with things like 'This is a COMMAND' or 'Output:'. "
            "Just return the cleaned text directly."
            + getattr(self, '_vocab_system_extra', '')
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
        # Fix mid-sentence capitalization bug
        styled = self._fix_mid_sentence_caps(styled)
        latency = (time.time() - start_time) * 1000
        usage = response.usage
        return styled, {
            "input_tokens": usage.prompt_tokens if usage else 0,
            "output_tokens": usage.completion_tokens if usage else 0,
            "api_used": True,
            "provider": "groq",
        }

    def _style_openai(self, prompt: str, transcript: str, start_time: float):
        """Style using OpenAI GPT-4o-mini — fallback."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a voice-to-text formatter. Output ONLY the final cleaned text. No meta-commentary." + getattr(self, '_vocab_system_extra', '')},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=512,
                temperature=0.3,
            )
            styled = response.choices[0].message.content.strip()
            # Fix mid-sentence capitalization bug
            styled = self._fix_mid_sentence_caps(styled)
            usage = response.usage
            return styled, {
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
                "api_used": True,
                "provider": "openai",
            }
        except Exception as e:
            print(f"GPT styling error: {e}")
            return self._basic_clean(transcript), {"input_tokens": 0, "output_tokens": 0, "api_used": False}

    def _fix_mid_sentence_caps(self, text: str) -> str:
        """Fix incorrectly capitalized words mid-sentence.

        The LLM sometimes capitalizes words as if starting new sentences
        but doesn't add the period. E.g., "consumed What I mean" → "consumed what I mean"

        Conservative approach: Only lowercase common words that are obviously wrong.
        Preserve proper nouns, company names, and acronyms.
        """
        # Common words that should NEVER be capitalized mid-sentence
        # (unless after punctuation or at sentence start)
        common_words_to_fix = {
            'What', 'When', 'Where', 'Why', 'Who', 'How', 'Which', 'Whose',
            'These', 'Those', 'This', 'That', 'Then', 'There', 'Their',
            'Otherwise', 'However', 'Therefore', 'Moreover', 'Furthermore',
            'Because', 'Although', 'Though', 'While', 'Since', 'Unless',
            'The', 'And', 'But', 'Or', 'So', 'Yet', 'For', 'Nor',
            'Are', 'Was', 'Were', 'Been', 'Being', 'Have', 'Has', 'Had',
            'Do', 'Does', 'Did', 'Will', 'Would', 'Should', 'Could', 'Can',
            'May', 'Might', 'Must', 'Shall',
        }

        def should_lowercase(match):
            space = match.group(1)
            capital_word = match.group(2)

            # Only lowercase if it's in our list of common words
            if capital_word in common_words_to_fix:
                return space + capital_word.lower()

            # Otherwise, preserve the original (could be a company/proper noun)
            return match.group(0)

        # Use lookbehind to check for letter/digit before space, without consuming it
        # This prevents overlapping matches
        # (?<=[a-zA-Z0-9]) = preceded by letter/digit (not consumed)
        # (\s+) = whitespace (consumed)
        # ([A-Z][a-z]+) = Capitalized word (consumed)
        pattern = r'(?<=[a-zA-Z0-9])(\s+)([A-Z][a-z]+)'
        fixed = re.sub(pattern, should_lowercase, text)
        return fixed

    def _basic_clean(self, text: str) -> str:
        """Fallback basic cleaning if API fails"""
        fillers = ['um', 'uh', 'like', 'you know', 'so basically', 'basically']
        cleaned = text
        for filler in fillers:
            cleaned = re.sub(rf'\b{filler}\b', '', cleaned, flags=re.IGNORECASE)
        return re.sub(r'\s+', ' ', cleaned).strip()
