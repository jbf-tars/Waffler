"""LLM styling module — three-tier fallback chain:

  1. Cerebras Llama 3.3 70B  (fastest in the world for this model, ~2200+ tok/s)
  2. Groq Llama 3.3 70B       (very fast ~270 tok/s, but ~100k/day free-tier cap)
  3. OpenAI gpt-4.1-mini      (slower but always available)

All three speak OpenAI-compatible chat-completions APIs. The same prompt is
sent everywhere so behaviour is consistent.
"""

from openai import OpenAI
from pathlib import Path
import time
import re

# ── Try to load Groq SDK ────────────────────────────────────────────────────
_groq_mod = None
try:
    import groq as _groq_mod
except ImportError:
    pass


class OpenAIStyler:
    """Styles transcripts — Cerebras / Groq Llama 3.3 70B → OpenAI fallback."""

    def __init__(self, api_key: str = "", model: str = "gpt-4.1-mini",
                 max_tokens: int = 1024, prompt_style: str = "normal",
                 groq_api_key: str = "", cerebras_api_key: str = ""):
        self.api_key = api_key
        # Default styling model: gpt-4.1-mini.
        # Benchmark against the user's actual failing case (May 2026):
        #   gpt-4o-mini     2507ms, occasionally censored "fucking"
        #   gpt-4.1-mini    1608ms, reliably preserved "fucking"   <-- winner
        #   gpt-4.1-nano     628ms, censored profanity (skipped)
        #   gpt-4.1         1021ms, ~4× the cost of mini for marginal gain
        #   gpt-5 family    4-16s, returned empty outputs (reasoning-tuned,
        #                   not suited for low-latency formatting)
        # gpt-4.1-mini is ~35% faster and ~2.5× the input cost of 4o-mini,
        # which works out to fractions of a cent per dictation.
        # Power users can flip the choice without a release via
        # OPENAI_STYLE_MODEL env var (gpt-4.1, gpt-4o, etc).
        import os as _os
        env_override = _os.getenv("OPENAI_STYLE_MODEL", "").strip()
        if env_override:
            self.model = env_override
        else:
            self.model = model
        self.max_tokens = max_tokens
        self.prompt_style = prompt_style
        self.groq_api_key = groq_api_key
        self.cerebras_api_key = cerebras_api_key
        self.client = OpenAI(api_key=api_key) if api_key else None
        self._groq_client = None
        self._use_groq = False
        self._cerebras_client = None
        self._use_cerebras = False
        # Monotonic-clock deadlines: until these timestamps, skip the
        # respective provider entirely and try the next one. Set when a
        # provider returns a 429 so we honour its retry-after hint instead
        # of wasting a round-trip on every recording.
        self._groq_skip_until = 0.0
        self._cerebras_skip_until = 0.0

        # Priority 1: Cerebras (fastest in the world for these models;
        # ~hundreds-of-ms first-token even on the 235B Qwen). OpenAI-
        # compatible API.
        #
        # Model: qwen-3-235b-a22b-instruct-2507 — available on the free
        # tier (probed live on the user's key). The previously used
        # llama-3.1-8b is too small to follow the styler's nuanced
        # rules on multi-clause exploratory speech — it collapses long
        # transcripts and hallucinates greetings. The 235B Qwen is in
        # a different league for instruction-following while still
        # benefiting from Cerebras's wafer-scale inference speed.
        # Llama 3.3 70B is NOT available on this key (404). Power
        # users can override via CEREBRAS_MODEL env var.
        if cerebras_api_key:
            try:
                self._cerebras_client = OpenAI(
                    api_key=cerebras_api_key,
                    base_url="https://api.cerebras.ai/v1",
                )
                self._use_cerebras = True
                import os as _os
                self._cerebras_model = _os.getenv("CEREBRAS_MODEL", "").strip() or "qwen-3-235b-a22b-instruct-2507"
                print(f"Styling primary: Cerebras {self._cerebras_model}")
            except Exception as e:
                print(f"Cerebras init failed ({e}), skipping")

        # Priority 2: Groq for styling if available
        if groq_api_key and _groq_mod:
            self._groq_client = _groq_mod.Groq(api_key=groq_api_key)
            self._use_groq = True
            self._groq_model = "llama-3.3-70b-versatile"
            print(f"Styling fallback: Groq {self._groq_model}")
        elif not self._use_cerebras:
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

    # Phrases that signal the speaker corrected themselves mid-utterance.
    # If any of these appear in the transcript we MUST run the LLM — the
    # regex-only _basic_clean has no way to drop the abandoned phrase, so
    # bypassing the LLM here would leave both versions in the output
    # ("Tuesday, sorry I mean Monday" -> "Tuesday, sorry I mean Monday").
    # Matched case-insensitively against the whole transcript.
    # Better to over-trigger (extra LLM call) than under-trigger (silently
    # wrong output) — that's the trade-off these patterns make.
    _CORRECTION_MARKERS = (
        r"\bsorry,?\s+i\s+mean\b",          # "sorry I mean"
        r"\bi\s+meant\b",                   # "I meant X"
        r"\bno\s+wait\b",                   # "no wait"
        r"\bno,\s+wait\b",                  # "no, wait"
        r"\bhmm\s+no\b",                    # "hmm no"
        r"\bno\s+actually\b",               # "no actually"
        r",\s*sorry,\s+",                   # ", sorry, Y"  (sorry mid-sentence as correction)
        r",\s*no\s+\w+",                    # ", no <word>" — "Tuesday, no Monday" / "three, no four"
        r",\s*actually\b",                  # ", actually Y" — soft correction marker
        r"\blet\s+me\s+start\s+over\b",
        r"\bwhat\s+i\s+(?:'m\s+)?(?:meant\s+to\s+say|trying\s+to\s+say|wanted\s+to\s+say)\b",
        r"\bscratch\s+that\b",
        # Em-dash followed by a short replacement — pattern of "X — Y instead"
        r"—\s+\w+\s+(?:at|on|by|in)\s+\w+\s+(?:would|works|sounds)\s+(?:be\s+)?better\b",
    )

    def _is_simple(self, transcript: str) -> bool:
        """Short / already-clean transcript — skip API, just regex-clean.

        Returns False (i.e. forces the LLM path) when:
          - the transcript is longer than 10 words, OR
          - it contains self-correction markers the regex cleaner can't handle.
        """
        # Self-correction overrides length: even a 4-word transcript like
        # "Tuesday no Monday" needs the LLM to drop "Tuesday".
        lower = transcript.lower()
        for pat in self._CORRECTION_MARKERS:
            if re.search(pat, lower):
                return False

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

        # NOTE: Custom vocabulary is deliberately NOT passed to the styler.
        # Whisper consumes the vocab list via its `prompt=` parameter to bias
        # transcription spelling, and `apply_vocab_corrections` does a fuzzy
        # post-pass on the transcript. Adding the vocab to the LLM's system
        # prompt on top of that caused the styler to inject vocab words into
        # clean transcripts ("cost of the project" -> "COBie of the project"),
        # especially when combined with Whisper prompt echoes on silence.
        # The styler should only see the transcript text, not the vocab list.

        # Load dialect/spelling setting
        dialect_instruction = "Use the same spelling as the user. Do not change spelling conventions."
        try:
            from transcribe_whisper import load_settings
            settings = load_settings()
            dialect = settings.get("dialect", "auto")
            if dialect == "en-GB":
                dialect_instruction = "Use British English spelling (e.g. colour, organise, centre, behaviour, realise, programme, defence, licence, favour, catalogue)."
            elif dialect == "en-US":
                dialect_instruction = "Use American English spelling (e.g. color, organize, center, behavior, realize, program, defense, license, favor, catalog)."
        except Exception:
            pass

        # Build prompt from the template — transcript goes into the user message.
        prompt = self.prompt_template.format(
            transcript=transcript,
            dialect_instruction=dialect_instruction,
        )

        # Size the output token budget against the input length so long
        # dictations don't get truncated mid-sentence. Cleaning typically
        # shrinks input by 10-30% but list/bullet conversions can add a few
        # characters per item, so allow ~3 output tokens per input word.
        # Floor 1024 (default headroom for short utterances), ceiling 8192
        # (~6000 words / ~30 minutes of continuous speech — well past any
        # realistic single dictation).
        word_count = max(1, len(transcript.split()))
        self._max_out_tokens = max(1024, min(8192, word_count * 3))

        # Three-tier fallback chain. Each provider has its own skip-until
        # deadline that pauses further attempts on that provider after a 429.
        #
        # Order rationale: Groq (free 100K TPD) is tried first so the
        # daily free quota gets used up before any paid Cerebras tokens
        # are spent. Once Groq's TPD is exhausted (or it errors), Cerebras
        # Qwen-3 235B takes over — fast and smart on the paid tier.
        # OpenAI gpt-4.1-mini sits as a last-resort fallback for the rare
        # case both fast providers are unavailable.

        # Priority 1: Groq Llama 3.3 70B — free tier preserved.
        if self._use_groq and time.monotonic() >= self._groq_skip_until:
            try:
                return self._style_groq(prompt, start_time)
            except Exception as e:
                self._log_provider_failure("Groq", e)
                # fall through to Cerebras / OpenAI

        # Priority 2: Cerebras Qwen-3 235B — paid tier, ~500ms even on
        # long inputs.
        if self._use_cerebras and time.monotonic() >= self._cerebras_skip_until:
            try:
                return self._style_cerebras(prompt, start_time)
            except Exception as e:
                self._log_provider_failure("Cerebras", e)
                if not self.client:
                    return self._basic_clean(transcript), {
                        "input_tokens": 0, "output_tokens": 0,
                        "api_used": False, "provider": "basic_clean",
                        "fallback_reason": str(e)[:160],
                    }

        # Priority 3: OpenAI gpt-4.1-mini — last-resort fallback only.
        return self._style_openai(prompt, transcript, start_time)

    def _log_provider_failure(self, provider_name: str, exc: Exception):
        """Common provider-failure log emitter — keeps each call site small."""
        import traceback
        from datetime import datetime
        err_detail = traceback.format_exc()
        print(f"{provider_name} styling failed ({exc}), trying next provider")
        try:
            log_file = Path.home() / ".waffler-hosted" / "app.log"
            with open(log_file, "a") as f:
                ts = datetime.now().strftime("%H:%M:%S")
                f.write(f"{ts}  [styling] {provider_name} FAILED: {exc}\n")
                f.write(f"{ts}  [styling] {err_detail}\n")
        except Exception:
            pass

    def _style_groq(self, prompt: str, start_time: float):
        """Style using Groq — ~200-400ms."""
        system_msg = (
            "Clean up voice transcripts. Remove filler words (um, uh, like, yeah, you know). "
            "Preserve the speaker's exact wording. Never paraphrase or add words they didn't say. "
            "**NEVER censor profanity — keep swear words like 'fucking', 'shit', 'bloody' "
            "exactly as the speaker said them.** Return only the cleaned text, no commentary."
        )
        try:
            response = self._groq_client.chat.completions.create(
                model=self._groq_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=getattr(self, "_max_out_tokens", 4096),
                temperature=0.1,
            )
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "rate" in error_msg.lower():
                # Pull out the info the UI needs: which limit hit ("tokens per day",
                # "requests per minute", etc.) and Groq's suggested wait time.
                # Keeping the structured prefix so the pipeline can route without
                # re-parsing the whole message. The error text also contains the
                # org ID and service tier ("... on_demand on tokens per day (TPD):"),
                # so we anchor the regex on the known Groq limit vocabulary to
                # avoid leaking those fragments into the user-facing toast.
                import re as _re
                _limit_m = _re.search(
                    r"on\s+((?:tokens|requests|audio\s+seconds)\s+per\s+(?:minute|hour|day)\s*\([A-Z]+\))\s*:",
                    error_msg,
                    _re.IGNORECASE,
                )
                _wait_m = _re.search(r"try again in ([0-9hmsd\. ]+)", error_msg, _re.IGNORECASE)
                _limit = _limit_m.group(1).strip() if _limit_m else "rate limit"
                _wait = _wait_m.group(1).strip().rstrip(".") if _wait_m else ""

                # Cache the retry deadline so subsequent recordings skip Groq
                # and go straight to OpenAI until the window expires. If Groq
                # didn't give us a parseable duration, fall back to a short
                # default so we still stop hammering it.
                _cool = 0.0
                if _wait:
                    _parts = _re.match(r"^\s*(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?\s*$", _wait)
                    if _parts and _parts.group(0).strip():
                        _cool = (int(_parts.group(1) or 0) * 3600
                                 + int(_parts.group(2) or 0) * 60
                                 + float(_parts.group(3) or 0))
                if _cool <= 0:
                    _cool = 60.0
                self._groq_skip_until = time.monotonic() + _cool

                raise RuntimeError(f"RATE_LIMIT|{_limit}|{_wait}|{error_msg[:60]}")
            elif "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                # Network connectivity hiccup — set a SHORT skip so we
                # don't keep wasting round-trips while the network is
                # flaky, but recover quickly when it comes back.
                self._groq_skip_until = time.monotonic() + 30.0
                raise RuntimeError(f"CONNECTION: Groq connection failed — {error_msg[:100]}")
            elif "403" in error_msg or "401" in error_msg or "access denied" in error_msg.lower() or "permission" in error_msg.lower() or "unauthorized" in error_msg.lower():
                # Auth / network-policy block (most often a VPN exit-IP that
                # Groq blocks, sometimes a corporate firewall, occasionally a
                # revoked key). None of these clear up in a few seconds, so
                # skip Groq for the whole session — every retry was costing
                # ~1-2 s round-trip per recording and forcing a 6 s OpenAI
                # fallback. 1-hour cooldown means the user gets fast styling
                # via OpenAI immediately; on next launch (or after the hour),
                # we try Groq again in case the network state changed.
                self._groq_skip_until = time.monotonic() + 3600.0
                print(f"[styling] Groq returned auth/network error — skipping Groq for 1 hour")
                raise RuntimeError(f"AUTH: Groq auth/network blocked — {error_msg[:120]}")
            raise
        styled = response.choices[0].message.content.strip()
        # Fix mid-sentence capitalization bug
        styled = self._strip_em_dashes(styled)
        styled = self._fix_mid_sentence_caps(styled)
        styled = self._strip_hallucinations(styled, self._last_raw)
        styled = self._restore_censored_profanity(styled, self._last_raw)
        usage = response.usage
        return styled, {
            "input_tokens": usage.prompt_tokens if usage else 0,
            "output_tokens": usage.completion_tokens if usage else 0,
            "api_used": True,
            "provider": "groq",
        }

    def _style_cerebras(self, prompt: str, start_time: float):
        """Style using Cerebras Llama 3.3 70B — fastest in the world for this
        model (~2200+ tok/s output). OpenAI-compatible API."""
        system_msg = (
            "Clean up voice transcripts. Remove filler words (um, uh, like, yeah, you know). "
            "Preserve the speaker's exact wording. Never paraphrase or add words they didn't say. "
            "**NEVER censor profanity — keep swear words like 'fucking', 'shit', 'bloody' "
            "exactly as the speaker said them.** Return only the cleaned text, no commentary."
        )
        try:
            response = self._cerebras_client.chat.completions.create(
                model=self._cerebras_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=getattr(self, "_max_out_tokens", 4096),
                temperature=0.1,
            )
        except Exception as e:
            error_msg = str(e)
            lower = error_msg.lower()
            if "429" in error_msg or "rate" in lower or "quota" in lower:
                # Cerebras returns 429 in two shapes:
                #   1. Generic "high traffic" — global load-shedding on the
                #      free tier. Transient, recovers in seconds.
                #   2. Per-account / per-minute token quota — real cap that
                #      persists for tens of seconds to minutes.
                # We treat the "high traffic" pattern with a short 20-second
                # skip so the next dictation gets a fresh shot at Cerebras.
                # Other 429s get a longer 2-minute skip. Without distinguishing
                # the two, a single 429 disables Cerebras for 5 minutes and
                # the user ends up on OpenAI for every dictation after.
                import re as _re
                if "high traffic" in lower or "experiencing high" in lower:
                    cool = 20.0
                else:
                    wait_m = _re.search(r"(?:retry|try again).{0,40}?(\d+)\s*(?:s|sec|second)", lower)
                    cool = float(wait_m.group(1)) if wait_m else 120.0
                self._cerebras_skip_until = time.monotonic() + cool
                raise RuntimeError(f"RATE_LIMIT|Cerebras|{int(cool)}s|{error_msg[:80]}")
            elif "connection" in lower or "timeout" in lower:
                # Short skip for transient network issues.
                self._cerebras_skip_until = time.monotonic() + 30.0
                raise RuntimeError(f"CONNECTION: Cerebras connection failed — {error_msg[:100]}")
            elif "401" in error_msg or "403" in error_msg or "unauthor" in lower or "invalid" in lower:
                # Auth failure — usually a bad / expired API key. Don't
                # hammer the endpoint; fall through to Groq / OpenAI for
                # the session.
                self._cerebras_skip_until = time.monotonic() + 3600.0
                print(f"[styling] Cerebras returned auth error — skipping for 1 hour")
                raise RuntimeError(f"AUTH: Cerebras auth failed — {error_msg[:120]}")
            raise
        styled = response.choices[0].message.content.strip()
        styled = self._strip_em_dashes(styled)
        styled = self._fix_mid_sentence_caps(styled)
        styled = self._strip_hallucinations(styled, self._last_raw)
        styled = self._restore_censored_profanity(styled, self._last_raw)
        usage = response.usage
        return styled, {
            "input_tokens": usage.prompt_tokens if usage else 0,
            "output_tokens": usage.completion_tokens if usage else 0,
            "api_used": True,
            "provider": "cerebras",
        }

    def _pick_openai_model(self, transcript: str) -> str:
        """OpenAI is now a last-resort fallback only — used when both Groq
        and Cerebras are unavailable. We always use gpt-4.1-mini here:
        Cerebras Qwen-3 235B has been benchmarked at ~equal quality to
        full gpt-4.1 on long inputs (and 6x faster), so the old
        ≥200-word → gpt-4.1-full routing has been removed. Mini is plenty
        for the rare emergency-fallback case. Power users can still pin
        a model via the OPENAI_STYLE_MODEL env var."""
        import os as _os
        if _os.getenv("OPENAI_STYLE_MODEL", "").strip():
            return self.model
        return self.model  # gpt-4.1-mini default

    def _style_openai(self, prompt: str, transcript: str, start_time: float):
        """Style using OpenAI. Auto-routes to gpt-4.1 (full) on long inputs
        so we benefit from its higher per-token output speed."""
        chosen_model = self._pick_openai_model(transcript)
        try:
            response = self.client.chat.completions.create(
                model=chosen_model,
                messages=[
                    {"role": "system", "content": (
                        "You are a voice-to-text formatter. Clean up voice transcripts "
                        "by removing filler words (um, uh, like, yeah, you know) and "
                        "fixing obvious stammers. Preserve the speaker's exact wording. "
                        "Never paraphrase or add words they didn't say. **NEVER censor "
                        "profanity — keep swear words like 'fucking', 'shit', 'bloody' "
                        "exactly as the speaker said them.** Output ONLY the final "
                        "cleaned text. No meta-commentary."
                    )},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=getattr(self, "_max_out_tokens", 4096),
                temperature=0.1,
            )
            styled = response.choices[0].message.content.strip()
            # Fix mid-sentence capitalization bug
            styled = self._strip_em_dashes(styled)
            styled = self._fix_mid_sentence_caps(styled)
            styled = self._strip_hallucinations(styled, self._last_raw)
            styled = self._restore_censored_profanity(styled, self._last_raw)
            usage = response.usage
            # Log which model was actually used so the auto-routing decision
            # is visible in the app log.
            try:
                from datetime import datetime as _dt
                log_path = Path.home() / ".waffler-hosted" / "app.log"
                with open(log_path, "a") as _fp:
                    _fp.write(f"{_dt.now().strftime('%H:%M:%S')}  [styling] OpenAI model used: {chosen_model} ({len(transcript.split())} input words)\n")
            except Exception:
                pass
            return styled, {
                "input_tokens": usage.prompt_tokens if usage else 0,
                "output_tokens": usage.completion_tokens if usage else 0,
                "api_used": True,
                "provider": "openai",
            }
        except Exception as e:
            print(f"GPT styling error: {e}")
            return self._basic_clean(transcript), {"input_tokens": 0, "output_tokens": 0, "api_used": False, "provider": "basic_clean", "fallback_reason": str(e)[:160]}

    def _strip_em_dashes(self, text: str) -> str:
        """Em-dashes (—) and en-dashes (–) are the loudest 'AI wrote this'
        marker — bigger LLMs love them. The prompt forbids them, but as a
        safety net we strip any that slip through and replace with comma
        + space. Hyphens (-) inside compound words and identifiers
        ('voice-to-text', 'gpt-4.1-mini', 'Ctrl+Alt+S') are untouched —
        only the em/en-dash codepoints (U+2014, U+2013) are targeted.

        Handles the common typographic patterns:
            "X — Y"   → "X, Y"     (space-padded em-dash, most common)
            "X—Y"     → "X, Y"     (bare em-dash, no spaces)
            "X – Y"   → "X, Y"     (space-padded en-dash)
            "X–Y"     → "X, Y"     (bare en-dash, e.g. between words)

        Also collapses any accidental double punctuation that results
        (e.g. ", ." → "." or ", ," → ",") so we don't trade an em-dash
        for a punctuation glitch.
        """
        if not text:
            return text
        import re as _re
        # Replace em/en-dashes with comma+space, with or without surrounding
        # whitespace. The \s* on both sides eats any padding so we don't
        # leave "X ,Y" or "X , Y" behind.
        text = _re.sub(r"\s*[—–]\s*", ", ", text)
        # Clean up: if the replacement now sits right before another
        # punctuation mark (rare, but happens at sentence end), collapse it.
        text = _re.sub(r",\s*([,.;:!?])", r"\1", text)
        # Trim accidental leading ", " at the start of a paragraph (would
        # only happen if the input started with an em-dash, but defend
        # against it anyway).
        text = _re.sub(r"(^|\n)\s*,\s*", r"\1", text)
        return text

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

    # Profanity that gpt-4o-mini's safety training sometimes strips even when
    # the prompt and system message explicitly forbid censoring. Belt-and-braces:
    # if the speaker said it and the LLM dropped it, splice it back in.
    _PROFANITY_WORDS = (
        # Common UK/US swears the LLM has been observed to censor.
        "fucking", "fuck", "fucked", "fucker", "fucks",
        "shit", "shitty", "shite",
        "bloody",
        "bastard", "bollocks", "bullshit",
        "wanker", "twat",
        "ass", "arse", "asshole", "arsehole",
        "damn", "damned",
        "crap", "crappy",
        "piss", "pissed",
        "bitch", "bitching",
        "cunt",
    )

    def _restore_censored_profanity(self, styled: str, raw: str) -> str:
        """Restore swear words the LLM stripped despite the no-censor rule.

        Strategy: for each swear word present in raw but missing from styled,
        find the word IMMEDIATELY BEFORE the swear in raw, locate that same
        anchor word in styled, and splice the swear in directly after it.
        Falls back to anchoring on the word AFTER the swear if the preceding
        word isn't usable. Last resort: append at end with a marker.
        """
        if not styled or not raw:
            return styled
        import re as _re

        raw_lower = raw.lower()

        # Find swears actually present in raw, in order.
        swears_in_raw = []
        for w in self._PROFANITY_WORDS:
            for m in _re.finditer(rf"\b{_re.escape(w)}\b", raw_lower):
                swears_in_raw.append((m.start(), m.end(), w))
        swears_in_raw.sort()
        if not swears_in_raw:
            return styled

        result = styled
        for raw_start, raw_end, swear in swears_in_raw:
            # Already in current result? — nothing to do.
            if _re.search(rf"\b{_re.escape(swear)}\b", result.lower()):
                continue

            # Find the word IMMEDIATELY before the swear in raw, AND which
            # occurrence of that word it is (so we anchor on the right one
            # in styled when the word appears multiple times).
            before = raw_lower[:raw_start]
            prev_words = _re.findall(r"[a-z']+", before)
            prev = prev_words[-1] if prev_words else None
            prev_occurrence = sum(1 for w in prev_words if w == prev) if prev else 0

            anchored = False
            if prev and len(prev) >= 2 and prev_occurrence > 0:
                # Find the Nth occurrence of `prev` in result (case-insensitive).
                matches = list(_re.finditer(rf"\b{_re.escape(prev)}\b", result, _re.IGNORECASE))
                # Take the closest occurrence at or before prev_occurrence,
                # falling back to the last if styled has fewer.
                target = matches[min(prev_occurrence, len(matches)) - 1] if matches else None
                if target:
                    insert_at = target.end()
                    result = result[:insert_at] + " " + swear + result[insert_at:]
                    anchored = True

            if anchored:
                continue

            # Fallback: anchor on the FIRST word AFTER the swear in raw
            # that's also in styled.
            after = raw_lower[raw_end:]
            for word in _re.findall(r"[a-z']+", after):
                if len(word) < 3:
                    continue
                m = _re.search(rf"\b{_re.escape(word)}\b", result, _re.IGNORECASE)
                if m:
                    insert_at = m.start()
                    result = result[:insert_at] + swear + " " + result[insert_at:]
                    anchored = True
                    break

            if not anchored:
                # Last resort — append. Better than silently losing the word.
                trimmed = result.rstrip(".!?\n ")
                tail_punct = result[len(trimmed):]
                result = trimmed + " " + swear + tail_punct
        return result

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
        """Fallback basic cleaning if API fails (or _is_simple bypassed the LLM).
        Only strips unambiguous fillers and collapses literal token-level
        stutters — meaning-bearing words (like, basically, actually, you know)
        are left alone because regex can't tell filler-use from meaning-use.
        """
        hard_fillers = ['um', 'uh', 'erm', 'ah', 'er']
        cleaned = text
        for filler in hard_fillers:
            escaped_filler = re.escape(filler)
            # Handle multi-word fillers (don't use \b for spaces)
            if ' ' in filler:
                cleaned = re.sub(rf'(?<!\w){escaped_filler}(?!\w)', '', cleaned, flags=re.IGNORECASE)
            else:
                cleaned = re.sub(rf'\b{escaped_filler}\b', '', cleaned, flags=re.IGNORECASE)

        # Collapse word-level stutters: "I I think" -> "I think", "the the
        # report" -> "the report", "we we need" -> "we need". Same word
        # repeated immediately (any number of times, with whitespace between)
        # collapses to a single occurrence. Case-insensitive so "I i" or
        # "The the" also collapse. Punctuation between repeats blocks the
        # collapse on purpose ("I, I think" might be a deliberate restart).
        cleaned = re.sub(r"\b([A-Za-z]+)(?:\s+\1\b)+", r"\1", cleaned, flags=re.IGNORECASE)

        return re.sub(r'\s+', ' ', cleaned).strip()
