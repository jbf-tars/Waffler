"""LLM styling module — Groq LLaMA (fast), Gemini (mid), or OpenAI GPT-4o-mini (fallback)"""

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

# ── Try to load Gemini SDK ─────────────────────────────────────────────────
_gemini_mod = None
try:
    from google import genai
    _gemini_mod = genai
except ImportError:
    pass


class OpenAIStyler:
    """Styles transcripts — Groq LLaMA 3.3 70B (fast) or GPT-4o-mini (fallback)"""

    def __init__(self, api_key: str = "", model: str = "gpt-4o-mini",
                 max_tokens: int = 1024, prompt_style: str = "normal",
                 groq_api_key: str = "", gemini_api_key: str = ""):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.prompt_style = prompt_style
        self.groq_api_key = groq_api_key
        self.gemini_api_key = gemini_api_key
        self.client = OpenAI(api_key=api_key) if api_key else None
        self._groq_client = None
        self._use_groq = False
        self._gemini_client = None
        self._use_gemini = False

        # Priority 1: Groq for styling if available
        if groq_api_key and _groq_mod:
            self._groq_client = _groq_mod.Groq(api_key=groq_api_key)
            self._use_groq = True
            self._groq_model = "llama-3.3-70b-versatile"
            print(f"Styling: Groq {self._groq_model}")
        # Priority 2: Gemini
        elif gemini_api_key and _gemini_mod:
            self._gemini_client = _gemini_mod.Client(api_key=gemini_api_key)
            self._use_gemini = True
            self._gemini_model = "gemini-2.5-flash"
            print(f"Styling: Gemini {self._gemini_model}")
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
        # Hard fillers only — meaning-bearing words like "like" / "basically"
        # are too context-sensitive for regex and must go to the LLM.
        hard_fillers = {'um', 'uh', 'erm', 'ah', 'er'}
        filler_count = sum(1 for w in words if w.lower().strip('.,!?') in hard_fillers)
        return len(words) <= 10 and filler_count / len(words) < 0.15

    def style(self, transcript: str):
        """Convert raw transcript to styled text. Returns (styled_text, usage_dict)."""
        if self._is_simple(transcript):
            cleaned = self._basic_clean(transcript)
            return cleaned, {"input_tokens": 0, "output_tokens": 0, "api_used": False}

        self._last_raw = transcript
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
                if not self.client and not self._use_gemini:
                    return self._basic_clean(transcript), {"input_tokens": 0, "output_tokens": 0, "api_used": False, "provider": "basic_clean", "fallback_reason": str(e)[:160]}

        # Priority 2: Try Gemini
        if self._use_gemini:
            try:
                return self._style_gemini(prompt, start_time)
            except Exception as e:
                import traceback
                err_detail = traceback.format_exc()
                print(f"Gemini styling failed ({e}), falling back to OpenAI")
                from pathlib import Path
                from datetime import datetime
                try:
                    log_file = Path.home() / ".waffler-hosted" / "app.log"
                    with open(log_file, "a") as f:
                        ts = datetime.now().strftime("%H:%M:%S")
                        f.write(f"{ts}  [styling] Gemini FAILED: {e}\n")
                        f.write(f"{ts}  [styling] {err_detail}\n")
                except Exception:
                    pass
                if not self.client:
                    return self._basic_clean(transcript), {"input_tokens": 0, "output_tokens": 0, "api_used": False, "provider": "basic_clean", "fallback_reason": str(e)[:160]}

        # Priority 3: OpenAI fallback
        return self._style_openai(prompt, transcript, start_time)

    def _style_groq(self, prompt: str, start_time: float):
        """Style using Groq — ~200-400ms."""
        system_msg = (
            "Clean up voice transcripts. Remove filler words (um, uh, like, yeah, you know). "
            "Preserve the speaker's exact wording. Never paraphrase or add words they didn't say. "
            "Never censor profanity. Return only the cleaned text, no commentary."
            + getattr(self, '_vocab_system_extra', '')
        )
        try:
            response = self._groq_client.chat.completions.create(
                model=self._groq_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=512,
                temperature=0.1,
            )
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "rate" in error_msg.lower():
                # Pull out the info the UI needs: which limit hit ("tokens per day",
                # "requests per minute", etc.) and Groq's suggested wait time.
                # Keeping the structured prefix so the pipeline can route without
                # re-parsing the whole message.
                import re as _re
                _limit_m = _re.search(r"on ([^:]+):", error_msg)
                _wait_m = _re.search(r"try again in ([0-9hmsd\. ]+)", error_msg, _re.IGNORECASE)
                _limit = _limit_m.group(1).strip() if _limit_m else "rate limit"
                _wait = _wait_m.group(1).strip() if _wait_m else ""
                raise RuntimeError(f"RATE_LIMIT|{_limit}|{_wait}|{error_msg[:60]}")
            elif "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                raise RuntimeError(f"CONNECTION: Groq connection failed — {error_msg[:100]}")
            raise
        styled = response.choices[0].message.content.strip()
        # Fix mid-sentence capitalization bug
        styled = self._fix_mid_sentence_caps(styled)
        styled = self._strip_hallucinations(styled, self._last_raw)
        latency = (time.time() - start_time) * 1000
        usage = response.usage
        return styled, {
            "input_tokens": usage.prompt_tokens if usage else 0,
            "output_tokens": usage.completion_tokens if usage else 0,
            "api_used": True,
            "provider": "groq",
        }

    def _style_gemini(self, prompt: str, start_time: float):
        """Style using Gemini."""
        system_msg = (
            "Clean up voice transcripts. Remove filler words (um, uh, like, yeah, you know). "
            "Preserve the speaker's exact wording. Never paraphrase or add words they didn't say. "
            "Never censor profanity. Return only the cleaned text, no commentary."
            + getattr(self, '_vocab_system_extra', '')
        )
        try:
            response = self._gemini_client.models.generate_content(
                model=self._gemini_model,
                contents=prompt,
                config={
                    "system_instruction": system_msg,
                    "temperature": 0.1,
                    "max_output_tokens": 512,
                },
            )
            styled = response.text.strip()
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                raise RuntimeError(f"RATE_LIMIT: Gemini rate limit hit — {error_msg[:100]}")
            elif "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                raise RuntimeError(f"CONNECTION: Gemini connection failed — {error_msg[:100]}")
            raise
        styled = self._fix_mid_sentence_caps(styled)
        styled = self._strip_hallucinations(styled, self._last_raw)
        latency = (time.time() - start_time) * 1000
        input_tokens = getattr(response, 'usage_metadata', None)
        return styled, {
            "input_tokens": input_tokens.prompt_token_count if input_tokens else 0,
            "output_tokens": input_tokens.candidates_token_count if input_tokens else 0,
            "api_used": True,
            "provider": "gemini",
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
                temperature=0.1,
            )
            styled = response.choices[0].message.content.strip()
            # Fix mid-sentence capitalization bug
            styled = self._fix_mid_sentence_caps(styled)
            styled = self._strip_hallucinations(styled, self._last_raw)
            usage = response.usage
            return styled, {
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
                "api_used": True,
                "provider": "openai",
            }
        except Exception as e:
            print(f"GPT styling error: {e}")
            return self._basic_clean(transcript), {"input_tokens": 0, "output_tokens": 0, "api_used": False, "provider": "basic_clean", "fallback_reason": str(e)[:160]}

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

    # Leading meta-commentary the LLM sometimes prepends despite being told not to.
    _PREAMBLE_RE = re.compile(
        r"^(?:\s*(?:"
        r"Here(?:'s| is| are)\s+(?:the\s+)?cleaned\s+(?:text|transcript|version|up\s+text)"
        r"|Here(?:'s| is)\s+(?:the\s+)?cleaned"
        r"|Cleaned(?:\s+(?:text|transcript|version))?"
        r"|Output"
        r"|The\s+cleaned\s+(?:text|transcript|version)(?:\s+is)?"
        r")\s*:\s*\n*)+",
        re.IGNORECASE,
    )

    # Greeting injected at start of the LLM output (only stripped when the raw
    # transcript didn't start with a greeting itself).
    _GREETING_RE = re.compile(
        r"^(?:Dear\s+[^,\n]{1,40},?|Hi(?:\s+[A-Z][a-z]+)?,?|Hello(?:\s+[A-Z][a-z]+)?,?|Hey(?:\s+[A-Z][a-z]+)?,?)\s*\n+",
    )

    # Sign-off followed by a [placeholder] — a strong signal of hallucination.
    _SIGNOFF_RE = re.compile(
        r"\n+\s*(?:Best(?:\s+regards)?|Kind\s+regards|Warm\s+regards|Sincerely|Regards|Cheers|Thanks|Yours(?:\s+truly|\s+sincerely)?)\s*,?\s*\n+\s*\[[^\]]+\]\s*\Z",
        re.IGNORECASE,
    )

    # 3+ newlines (possibly with whitespace between) collapse to exactly 2.
    _TRIPLE_NL_RE = re.compile(r"\n[ \t]*\n[ \t]*(?:\n[ \t]*)+")

    _GREETING_WORDS = ("hi", "hello", "hey", "dear", "howdy", "good morning",
                       "good afternoon", "good evening", "yo")

    def _raw_starts_with_greeting(self, raw: str) -> bool:
        head = raw.lstrip().lower()
        return any(head.startswith(w) for w in self._GREETING_WORDS)

    def _strip_hallucinations(self, output: str, raw_transcript: str) -> str:
        """Deterministic guardrail for known LLM failure modes.

        Strips leading meta-preamble, injected greetings/sign-offs, and
        collapses pathological whitespace. Never touches content the speaker
        actually dictated — the raw transcript is used as ground truth.
        """
        if not output:
            return output

        text = output

        # a) Leading meta-preamble ("Here is the cleaned text:\n\n")
        text = self._PREAMBLE_RE.sub("", text, count=1).lstrip()

        # b) Injected greeting at start — only if raw didn't start with one
        if not self._raw_starts_with_greeting(raw_transcript):
            text = self._GREETING_RE.sub("", text, count=1).lstrip()

        # c) Trailing sign-off with [placeholder]
        text = self._SIGNOFF_RE.sub("", text).rstrip()

        # d) Collapse 3+ newlines (and whitespace-only lines between) to \n\n
        text = self._TRIPLE_NL_RE.sub("\n\n", text)

        return text.strip()

    def _basic_clean(self, text: str) -> str:
        """Fallback basic cleaning if API fails. Only strips unambiguous fillers —
        meaning-bearing words (like, basically, actually, you know) are left alone
        because regex can't tell filler-use from meaning-use."""
        hard_fillers = ['um', 'uh', 'erm', 'ah', 'er']
        cleaned = text
        for filler in hard_fillers:
            escaped_filler = re.escape(filler)
            # Handle multi-word fillers (don't use \b for spaces)
            if ' ' in filler:
                cleaned = re.sub(rf'(?<!\w){escaped_filler}(?!\w)', '', cleaned, flags=re.IGNORECASE)
            else:
                cleaned = re.sub(rf'\b{escaped_filler}\b', '', cleaned, flags=re.IGNORECASE)
        return re.sub(r'\s+', ' ', cleaned).strip()
