"""LLM styling module — three-tier fallback chain:

  1. Groq Llama 3.3 70B       (very fast ~270 tok/s, ~100k tokens/day free)
  2. Cerebras gpt-oss-120b   (paid, ~500 ms even on long inputs, ~2200+ tok/s)
  3. OpenAI gpt-4.1-mini      (slower but always available, last-resort)

Order rationale: Groq is tried FIRST — not because it's fastest, but because
its free tier (100k tokens/day) means a typical user pays £0 for cleanup
until that allowance is exhausted. Only then does Cerebras's paid tier
take over. This is the opposite order from "fastest first" but it matches
what every BYOK user actually wants.

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


# Per-request timeout (seconds) for the cleanup LLM call. The OpenAI SDK
# default is 600 s (10 minutes) — that's the source of the "styling took
# 602347 ms" hangs in the wild: a wedged provider blocked the whole
# dictation for ten minutes instead of failing over. 30 s is far longer
# than a healthy cleanup call (typ. 0.5-2 s) but short enough that a hung
# provider is abandoned fast and we move to the next one in the order.
# Per-provider cap on a single cleanup call. Was 30.0, which let ONE hung
# provider stall a dictation for half a minute — and with a 3-provider chain,
# two hops could stack to 60-78s (observed live). 15s still leaves generous
# headroom over every measured healthy p95 (all < 6s).
_STYLE_TIMEOUT_S = 15.0

# Overall wall-clock budget for the WHOLE styling step (all fallback attempts).
#
# The bug this fixes: there was only ever a PER-PROVIDER timeout, never an
# aggregate one, so style() would keep grinding down the fallback chain until
# something answered — 30s, 60s, 78s. The documented promise that Waffler
# "pastes raw if the cleanup doesn't come through" therefore almost never
# fired: it only triggers when EVERY provider fails outright (9 times in 2347
# recordings), whereas 42 recordings sat >10s and 24 sat >30s because a
# provider eventually won. Users waited instead of getting their words.
#
# Scaled to input length on purpose. A flat 10-12s cap would CLIP legitimate
# long dictations: _max_out_tokens scales to 8192 and a 70B model at ~270
# tok/s can genuinely need ~30s to clean a very long transcript. So: an 8s
# base plus ~30ms/word, floored at 12s and capped at 30s.
#   30 words -> 12s | 300 words -> 17s | 1000+ words -> 30s
_STYLE_DEADLINE_FLOOR_S = 12.0
_STYLE_DEADLINE_CAP_S = 30.0
_STYLE_DEADLINE_BASE_S = 8.0
_STYLE_DEADLINE_PER_WORD_S = 0.03

# Don't start a provider call with less than this left on the clock — a
# sub-second attempt just burns the remainder and still fails.
_STYLE_MIN_ATTEMPT_S = 1.5


def _style_deadline_for(word_count: int) -> float:
    """Overall styling budget (seconds), scaled to transcript length."""
    scaled = _STYLE_DEADLINE_BASE_S + max(0, word_count) * _STYLE_DEADLINE_PER_WORD_S
    return max(_STYLE_DEADLINE_FLOOR_S, min(_STYLE_DEADLINE_CAP_S, scaled))

# Canonical provider order used when the user hasn't configured one.
# Groq first preserves its free daily quota before any paid Cerebras
# tokens are spent; OpenAI last as the always-available backstop.
_DEFAULT_PROVIDER_ORDER = ["groq", "cerebras", "openai"]


def _normalize_provider_order(order) -> list:
    """Return a clean [groq, cerebras, openai] permutation from user input.

    Accepts a list/tuple of provider names in any case, drops unknowns and
    duplicates, and appends any missing providers in canonical order so the
    result is always a full 3-element fallback chain. Bad input -> default.
    """
    valid = ["groq", "cerebras", "openai"]
    out = []
    try:
        for p in (order or []):
            p = str(p).strip().lower()
            if p in valid and p not in out:
                out.append(p)
    except Exception:
        return list(_DEFAULT_PROVIDER_ORDER)
    for p in _DEFAULT_PROVIDER_ORDER:
        if p not in out:
            out.append(p)
    return out


class OpenAIStyler:
    """Styles transcripts via a configurable provider fallback chain.

    Default order Groq -> Cerebras -> OpenAI; the user can reorder it (see
    ``provider_order``). Each provider call has a 30 s timeout so a wedged
    provider fails over instead of hanging the dictation.
    """

    def __init__(self, api_key: str = "", model: str = "gpt-4.1-mini",
                 max_tokens: int = 1024, prompt_style: str = "normal",
                 groq_api_key: str = "", cerebras_api_key: str = "",
                 provider_order=None):
        self.api_key = api_key
        # Default styling model: gpt-4.1-mini.
        # Benchmark against the user's actual failing case (May 2026):
        #   gpt-4o-mini     2507ms, occasionally censored "fucking"  # doc-drift-ok (benchmark comparison)
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
        # User-configurable fallback order (Settings → Provider order).
        self._provider_order = _normalize_provider_order(provider_order)
        # 30 s timeout + no SDK-level retries so a hung provider fails over
        # to the next in the order fast (see _STYLE_TIMEOUT_S).
        self.client = (
            OpenAI(api_key=api_key, timeout=_STYLE_TIMEOUT_S, max_retries=0)
            if api_key else None
        )
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
        # ~hundreds-of-ms first-token). OpenAI-compatible API.
        #
        # Model: gpt-oss-120b. We previously defaulted to
        # qwen-3-235b-a22b-instruct-2507, but Cerebras RETIRED that model
        # (it 404'd live mid-day on 2026-05-27 — "Model ... does not exist
        # or you do not have access to it" — after having worked an hour
        # earlier). When that happened, Groq-rate-limited dictations had no
        # working styler and silently fell through to raw basic_clean. The
        # key now exposes gpt-oss-120b (verified: strong instruction-follower,
        # cleans transcripts correctly) and zai-glm-4.7 (non-standard response
        # shape — skipped). Cerebras rotates models, so power users / a quick
        # hotfix can override via the CEREBRAS_MODEL env var without a release.
        if cerebras_api_key:
            try:
                self._cerebras_client = OpenAI(
                    api_key=cerebras_api_key,
                    base_url="https://api.cerebras.ai/v1",
                    timeout=_STYLE_TIMEOUT_S,
                    max_retries=0,
                )
                self._use_cerebras = True
                import os as _os
                self._cerebras_model = _os.getenv("CEREBRAS_MODEL", "").strip() or "gpt-oss-120b"
                print(f"Styling primary: Cerebras {self._cerebras_model}")
            except Exception as e:
                print(f"Cerebras init failed ({e}), skipping")

        # Priority 2: Groq for styling if available
        if groq_api_key and _groq_mod:
            self._groq_client = _groq_mod.Groq(
                api_key=groq_api_key, timeout=_STYLE_TIMEOUT_S, max_retries=0
            )
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
        # Floor 2048 (was 1024): reasoning-capable models (e.g. Cerebras
        # gpt-oss-120b) spend some output tokens on a reasoning pass BEFORE
        # the cleaned text. Even with reasoning_effort=low that's ~70-150
        # tokens of overhead; the higher floor guarantees the actual text
        # always has room. Ceiling 8192 (~6000 words / ~30 min of speech).
        word_count = max(1, len(transcript.split()))
        self._max_out_tokens = max(2048, min(8192, word_count * 3))

        # Arm the overall styling budget. Every provider attempt below is
        # bounded by whatever is LEFT on this clock, so the whole step can't
        # outrun it no matter how the fallback chain goes. When it runs out we
        # stop trying and paste the raw transcript — which is the behaviour
        # that was promised but never actually fired (see _STYLE_DEADLINE_*).
        self._deadline_at = time.monotonic() + _style_deadline_for(word_count)

        # Three-tier fallback chain. Each provider has its own skip-until
        # deadline that pauses further attempts on that provider after a 429.
        #
        # Order rationale: Groq (free 100K TPD) is tried first so the
        # daily free quota gets used up before any paid Cerebras tokens
        # are spent. Once Groq's TPD is exhausted (or it errors), Cerebras
        # gpt-oss-120b takes over — fast and smart on the paid tier.
        # OpenAI gpt-4.1-mini sits as a last-resort fallback for the rare
        # case both fast providers are unavailable.
        #
        # v3.14.19: track failures across providers so that when EVERYTHING
        # falls through to basic_clean, we surface the most actionable
        # reason (rate-limit > auth > connection > other). Previously when
        # Groq was rate-limited and the user had no Cerebras or OpenAI key,
        # ``_style_openai`` would crash on ``self.client.chat`` (NoneType)
        # and that meaningless ``AttributeError`` became the fallback reason
        # — so the toast just said "Pasted raw. See the log for details."
        # The fix is to (a) skip OpenAI when ``self.client is None``,
        # (b) synthesise a RATE_LIMIT-style reason for providers in
        # active cooldown so the toast can still explain WHY, and
        # (c) pick the most informative failure to propagate.

        failures: list[tuple[str, str]] = []  # (provider, error_str)

        # Try each configured provider in the user's chosen order. Each entry
        # knows how to (a) check it's usable, (b) report a cooldown reason, and
        # (c) run the actual cleanup. First success returns; otherwise we fall
        # through to basic_clean with the most informative failure reason.
        def _try_groq():
            if not self._use_groq:
                return None
            if time.monotonic() < self._groq_skip_until:
                wait_s = max(0, int(self._groq_skip_until - time.monotonic()))
                failures.append(("Groq", f"RATE_LIMIT|cooldown|{wait_s}s|Groq still in cooldown from previous limit"))
                return None
            try:
                _styled, _usage = self._style_groq(prompt, start_time)
                return self._guard_truncation(_styled, _usage, transcript)
            except Exception as e:
                self._log_provider_failure("Groq", e)
                failures.append(("Groq", str(e)))
                return None

        def _try_cerebras():
            if not self._use_cerebras:
                return None
            if time.monotonic() < self._cerebras_skip_until:
                wait_s = max(0, int(self._cerebras_skip_until - time.monotonic()))
                failures.append(("Cerebras", f"RATE_LIMIT|cooldown|{wait_s}s|Cerebras still in cooldown"))
                return None
            try:
                _styled, _usage = self._style_cerebras(prompt, start_time)
                return self._guard_truncation(_styled, _usage, transcript)
            except Exception as e:
                self._log_provider_failure("Cerebras", e)
                failures.append(("Cerebras", str(e)))
                return None

        def _try_openai():
            # Skip entirely if no client is configured so we don't crash on a
            # NoneType call and lose the real failure reason.
            if self.client is None:
                return None
            try:
                _styled, _usage = self._style_openai(prompt, transcript, start_time)
                return self._guard_truncation(_styled, _usage, transcript)
            except Exception as e:
                self._log_provider_failure("OpenAI", e)
                failures.append(("OpenAI", str(e)))
                return None

        _dispatch = {"groq": _try_groq, "cerebras": _try_cerebras, "openai": _try_openai}
        for _name in self._provider_order:
            _fn = _dispatch.get(_name)
            if _fn is None:
                continue
            # Aggregate budget guard: never START another provider once the
            # clock is spent. Without this the chain grinds on provider after
            # provider (30s each) and the user waits instead of getting text.
            if self._budget_left() < _STYLE_MIN_ATTEMPT_S:
                failures.append((
                    "deadline",
                    f"TIMEOUT|styling budget exhausted after "
                    f"{_style_deadline_for(word_count):.0f}s - pasted raw",
                ))
                break
            _result = _fn()
            if _result is not None:
                _styled, _usage = _result
                # Deterministic email layout so the greeting/sign-off sit on
                # their own lines regardless of which provider produced the
                # text (previously left to the LLM -> inconsistent Mac vs PC).
                return self._format_email_layout(_styled), _usage

        # ── All providers exhausted ─────────────────────────────────
        # Pick the most informative reason and fall back to basic_clean.
        best = self._pick_best_failure_reason(failures)
        return self._format_email_layout(self._basic_clean(transcript)), {
            "input_tokens": 0, "output_tokens": 0,
            "api_used": False, "provider": "basic_clean",
            "fallback_reason": best,
        }

    def _budget_left(self) -> float:
        """Seconds left on the overall styling budget.

        Returns the per-provider cap when no budget is armed (e.g. a provider
        method called directly in a test), so the absence of a deadline never
        accidentally starves a call.
        """
        deadline = getattr(self, "_deadline_at", None)
        if deadline is None:
            return float(_STYLE_TIMEOUT_S)
        return max(0.0, deadline - time.monotonic())

    def _attempt_timeout(self) -> float:
        """Timeout for the next provider call: the per-provider cap, clamped to
        whatever remains of the overall budget so one call can't overrun it."""
        return max(0.1, min(_STYLE_TIMEOUT_S, self._budget_left()))

    def _guard_truncation(self, styled, usage, transcript):
        """Backstop against the model silently deleting most of the content.

        The prompt forbids truncation, but LLMs still occasionally decide a
        chunk is "meaningless" — e.g. a mic check like "testing testing one
        two three four five six seven" — and drop it, turning a 14-word
        dictation into 3 words. That's silent content loss: the user only ever
        sees the styled text. If the styled output kept less than half the
        words on a non-trivial input (>= 8 words), distrust the model and fall
        back to the lightly-cleaned raw transcript, which preserves every word.

        basic_clean is a worse *polish* but never drops content, so a rare
        false trip just yields slightly-less-tidy text — never a lost
        sentence. We deliberately set `provider` (not `fallback_reason`) so
        this does NOT raise the "styling failed" toast: nothing failed, we
        just chose to keep the user's words.
        """
        try:
            raw_words = len(transcript.split())
            out_words = len((styled or "").split())

            # Signal 1 (PRECISE): the model hit its max_tokens cap. The API
            # tells us this exactly via finish_reason == "length" — the output
            # is DEFINITELY incomplete (cut mid-sentence), regardless of how
            # many words survived. This is what the 17:42 COBie/postcode bug
            # was: Cerebras gpt-oss-120b kept 85/130 words (65%, above the
            # ratio guard below) but finish_reason was "length". Catch it.
            finish_reason = (usage or {}).get("finish_reason")
            length_truncated = finish_reason == "length"

            # Signal 2 (HEURISTIC): the model silently dropped most content
            # (e.g. judged a mic-check "meaningless"). Only for >= 8 words.
            ratio_truncated = raw_words >= 8 and out_words < raw_words * 0.5

            if length_truncated or ratio_truncated:
                why = "finish_reason=length" if length_truncated else f"kept {out_words}/{raw_words} words"
                print(
                    f"[styler] truncation-guard ({why}) — using basic_clean "
                    f"to preserve all content",
                    flush=True,
                )
                guarded = self._basic_clean(transcript)
                usage = dict(usage or {})
                usage["api_used"] = False
                usage["provider"] = "basic_clean"
                usage["truncation_guard"] = why
                return guarded, usage
        except Exception as e:
            print(f"[styler] truncation-guard error (ignored): {e}", flush=True)
        return styled, usage

    @staticmethod
    def _pick_best_failure_reason(failures: list[tuple[str, str]]) -> str:
        """Pick the most actionable failure to surface to the user.

        Priority:
          1. ``RATE_LIMIT|...`` — tells the user to wait / add a fallback key
          2. ``AUTH:...`` — tells the user to check key / VPN
          3. ``CONNECTION...`` / ``timeout`` — tells the user to check network
          4. Anything else (last resort)

        When multiple providers are rate-limited, prefer the one with the
        shorter wait. The selected reason is prefixed with the provider
        name so the toast can say "Groq …" rather than just "rate limit".
        """
        if not failures:
            return "No styling providers configured. Add a key in Settings → API Keys."

        def category(err: str) -> int:
            if "RATE_LIMIT" in err:
                return 0
            if err.startswith("AUTH:"):
                return 1
            if "CONNECTION" in err or "timeout" in err.lower():
                return 2
            return 3

        # Sort failures by category, then prefer Groq over Cerebras over OpenAI
        # (the order the user is most likely to recognise as their primary).
        order = {"Groq": 0, "Cerebras": 1, "OpenAI": 2}
        failures_sorted = sorted(
            failures,
            key=lambda f: (category(f[1]), order.get(f[0], 99)),
        )
        provider, err = failures_sorted[0]

        # If it's a RATE_LIMIT, prepend the provider name to the message
        # so the toast handler can extract it. Format stays parseable:
        # "RATE_LIMIT|<limit>|<wait>|<snippet>"  — but we splice the
        # provider name into the snippet field so the toast can show it.
        if "RATE_LIMIT" in err and err.startswith("RATE_LIMIT|"):
            parts = err.split("|", 3)
            # parts[3] is the raw snippet; prefix with provider for clarity
            snippet = parts[3] if len(parts) > 3 else ""
            parts = parts[:3] + [f"{provider}: {snippet}"]
            return "|".join(parts)[:200]
        return f"{provider}: {err}"[:200]

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
            "exactly as the speaker said them.** "
            "**NEVER abbreviate content with filler-tail phrases like 'and many more', "
            "'and so on', 'etc.', 'amongst others', 'to name a few'.** If the speaker listed "
            "items, list those items verbatim. If they trailed off, output their actual "
            "trailing fragment, not a manufactured summary. Only keep these phrases if the "
            "speaker literally said them out loud. Return only the cleaned text, no commentary."
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
                # Bounded by whatever is LEFT of the overall styling budget, so
                # this call can never overrun the deadline (see _attempt_timeout).
                timeout=self._attempt_timeout(),
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
            "finish_reason": response.choices[0].finish_reason,
        }

    def _style_cerebras(self, prompt: str, start_time: float):
        """Style using Cerebras Llama 3.3 70B — fastest in the world for this
        model (~2200+ tok/s output). OpenAI-compatible API."""
        system_msg = (
            "Clean up voice transcripts. Remove filler words (um, uh, like, yeah, you know). "
            "Preserve the speaker's exact wording. Never paraphrase or add words they didn't say. "
            "**NEVER censor profanity — keep swear words like 'fucking', 'shit', 'bloody' "
            "exactly as the speaker said them.** "
            "**NEVER abbreviate content with filler-tail phrases like 'and many more', "
            "'and so on', 'etc.', 'amongst others', 'to name a few'.** If the speaker listed "
            "items, list those items verbatim. If they trailed off, output their actual "
            "trailing fragment, not a manufactured summary. Only keep these phrases if the "
            "speaker literally said them out loud. Return only the cleaned text, no commentary."
        )
        # reasoning_effort=low is CRITICAL for gpt-oss-120b. It's a reasoning
        # model: without this it spends 1000+ output tokens "thinking" before
        # emitting any cleaned text, blows the max_tokens budget, and returns
        # truncated (or empty) output — confirmed live, finish_reason=length,
        # 1024 completion tokens, 0 words of actual output. With it, reasoning
        # drops to ~70 tokens and the full faithful text comes back. Sent via
        # extra_body so non-reasoning models that ignore it don't 400.
        try:
            response = self._cerebras_client.chat.completions.create(
                model=self._cerebras_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=getattr(self, "_max_out_tokens", 4096),
                temperature=0.1,
                extra_body={"reasoning_effort": "low"},
                # Bounded by whatever is LEFT of the overall styling budget, so
                # this call can never overrun the deadline (see _attempt_timeout).
                timeout=self._attempt_timeout(),
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
            "finish_reason": response.choices[0].finish_reason,
        }

    def _pick_openai_model(self, transcript: str) -> str:
        """OpenAI is now a last-resort fallback only — used when both Groq
        and Cerebras are unavailable. We always use gpt-4.1-mini here:
        the previous Cerebras model (qwen-3-235b-a22b-instruct-2507) was
        benchmarked at ~equal quality to full gpt-4.1 on long inputs
        (and 6x faster), so the old
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
                        "exactly as the speaker said them.** "
                        "**NEVER abbreviate content with filler-tail phrases like "
                        "'and many more', 'and so on', 'etc.', 'amongst others', 'to "
                        "name a few'.** If the speaker listed items, list those items "
                        "verbatim. If they trailed off, output their actual trailing "
                        "fragment, not a manufactured summary. Only keep these phrases "
                        "if the speaker literally said them out loud. "
                        "Output ONLY the final cleaned text. No meta-commentary."
                    )},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=getattr(self, "_max_out_tokens", 4096),
                temperature=0.1,
                # Bounded by whatever is LEFT of the overall styling budget, so
                # this call can never overrun the deadline (see _attempt_timeout).
                timeout=self._attempt_timeout(),
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
                "finish_reason": response.choices[0].finish_reason,
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
        # First, normalise exotic hyphen codepoints the model sometimes swaps
        # in for a plain ASCII hyphen: U+2010 (hyphen), U+2011 (non-breaking
        # hyphen), U+2212 (minus sign). Observed live: Cerebras emitted
        # "rate‑limit" (U+2011) for spoken "rate-limit" — invisible on screen
        # but breaks search, diffs and downstream tooling in the pasted text.
        # These are word-joining hyphens, NOT asides, so they map to "-"
        # (the em/en-dash → comma rule below must not touch them).
        text = _re.sub(r"[‐‑−]", "-", text)
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

    # ── Deterministic email layout (v3.14.80) ──────────────────────────────
    # Line-break placement in styled emails used to be left entirely to the
    # LLM, so the SAME dictation came out formatted on one provider and run-on
    # on another — e.g. Cerebras put the sign-off on its own line while another
    # model glued "Thank you, James." onto the previous sentence. That made the
    # output differ between a user's Mac and PC (which land on different
    # providers). These regexes let _format_email_layout enforce the layout in
    # code, so it's identical on every machine and provider.
    #
    # A sign-off GLUED to the final sentence by a space (NOT a newline): a
    # sentence end [.!?], one-or-more spaces/tabs, a recognised closing phrase,
    # an OPTIONAL name (1-3 capitalised words), an optional trailing . or !,
    # then the very end of the text. The space-not-newline boundary means an
    # already correctly-formatted sign-off (preceded by \n) never matches, so
    # the pass is idempotent and won't touch good output.
    _EMAIL_GLUED_SIGNOFF_RE = re.compile(
        r"(?i)"
        r"(?P<body_end>[.!?])"
        r"[ \t]+"
        r"(?P<signoff>"
        r"(?:thank you so much|thanks so much|thank you|thanks again|thanks a lot|"
        r"many thanks|thanks|kindest regards|kind regards|warmest regards|"
        r"warm regards|best regards|best wishes|all the best|regards|cheers|"
        r"speak to you soon|speak soon|talk soon|yours sincerely|yours faithfully|"
        r"yours truly|sincerely|best)"
        r"(?:[ \t]*,?[ \t]+[A-Z][\w.'’-]*(?:[ \t]+[A-Z][\w.'’-]*){0,2})?"
        r"[ \t]*[.!]?"
        r"[ \t]*$"
        r")"
    )

    # A greeting at the very start, captured up to its first comma (e.g.
    # "Hi Jamie,", "Hello team,", "Dear Sir/Madam,"). Used to push a greeting
    # glued to the first sentence onto its own line.
    _EMAIL_GREETING_LEAD_RE = re.compile(
        r"(?i)^[ \t]*(?P<greeting>"
        r"(?:hi|hii|hiya|hey|heya|hello|dear|good\s+morning|good\s+afternoon|"
        r"good\s+evening)\b[^,\n]{0,40}?,)"
    )

    # A trailing sign-off that is its OWN paragraph but still one line (or two),
    # e.g. "Regards, James" / "Thank you, James." / "Cheers,\nJames". Used to
    # normalise the sign-off to the canonical TWO-line form (closing on its own
    # line ending with a comma, name on the next line, no trailing punctuation).
    # Doing this in code makes the layout 100% consistent regardless of which
    # LLM ran — the model was applying it only ~83% of the time (real flaky-rate
    # measurement), which read as "email formatting isn't consistent".
    _EMAIL_SIGNOFF_NAME_RE = re.compile(
        r"(?is)^\s*"
        r"(?P<closing>thank you so much|thanks so much|thank you|thanks again|"
        r"thanks a lot|many thanks|thanks|kindest regards|kind regards|"
        r"warmest regards|warm regards|best regards|best wishes|all the best|"
        r"regards|cheers|speak to you soon|speak soon|talk soon|talk later|"
        r"yours sincerely|yours faithfully|yours truly|sincerely|best)"
        r"[ \t]*,?[ \t\r\n]+"
        r"(?P<name>[A-Z][\w.'’-]*(?:[ \t]+[A-Z][\w.'’-]*){0,2})"
        r"[ \t]*[.!]?[ \t]*$"
    )

    def _raw_starts_with_greeting(self, raw: str) -> bool:
        head = raw.lstrip().lower()
        return any(head.startswith(w) for w in self._GREETING_WORDS)

    # Profanity that gpt-4o-mini's safety training sometimes strips even when  # doc-drift-ok (historical context)
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

        For each swear present in raw but missing from styled we anchor on the
        word IMMEDIATELY BEFORE it (preferred) or AFTER it (fallback) and splice
        the swear back into the matching clause of the styled text.

        Two things make this robust when MULTIPLE swears were censored:

        * We process swears RIGHT-TO-LEFT (by raw position) and recompute the
          anchor match against the *current* result on every iteration. Working
          right-to-left means each insertion lands to the right of all
          not-yet-restored anchors, so the left-to-right occurrence indices we
          rely on never go stale, and a freshly-spliced swear can't be mistaken
          for a later swear's anchor word.
        * Anchoring is FAIL-SAFE. The anchor occurrence index is counted over
          *non-swear* words only, and if styled genuinely doesn't contain that
          Nth occurrence (or the anchor is too short/ambiguous) we leave the
          swear censored rather than guess and splice it into the wrong place.

        Last resort, for the simple unanchorable case, we append the swear at
        the end with clean spacing/punctuation (no stray ".fucking" artifacts).
        """
        if not styled or not raw:
            return styled
        import re as _re

        raw_lower = raw.lower()
        swear_set = {w.lower() for w in self._PROFANITY_WORDS}
        # A "word" for anchoring purposes: alphabetic run that is NOT itself a
        # swear (swears are anchors for nothing — they're what we're inserting).
        word_re = _re.compile(r"[a-z']+")

        def non_swear_words(text: str) -> list[str]:
            return [w for w in word_re.findall(text) if w not in swear_set]

        # Find swears actually present in raw, in order.
        swears_in_raw = []
        for w in self._PROFANITY_WORDS:
            for m in _re.finditer(rf"\b{_re.escape(w)}\b", raw_lower):
                swears_in_raw.append((m.start(), m.end(), w))
        swears_in_raw.sort()
        if not swears_in_raw:
            return styled

        result = styled
        # Right-to-left so earlier (leftward) anchors keep their positions and
        # occurrence counts as we mutate `result`.
        for raw_start, raw_end, swear in sorted(swears_in_raw, reverse=True):
            # Already present in current result? — nothing to do.
            if _re.search(rf"\b{_re.escape(swear)}\b", result, _re.IGNORECASE):
                continue

            anchored = False

            # 1) Preferred: anchor on the non-swear word IMMEDIATELY before the
            #    swear in raw, matching the same (Nth) occurrence in result.
            prev_words = non_swear_words(raw_lower[:raw_start])
            prev = prev_words[-1] if prev_words else None
            prev_occurrence = sum(1 for w in prev_words if w == prev) if prev else 0
            if prev and len(prev) >= 2 and prev_occurrence > 0:
                matches = list(_re.finditer(rf"\b{_re.escape(prev)}\b", result, _re.IGNORECASE))
                # Only splice if styled actually has that occurrence; otherwise
                # the clause is ambiguous and we must not guess.
                if len(matches) >= prev_occurrence:
                    insert_at = matches[prev_occurrence - 1].end()
                    result = result[:insert_at] + " " + swear + result[insert_at:]
                    anchored = True

            # 2) Fallback: anchor on the first usable non-swear word AFTER the
            #    swear in raw, again on the matching occurrence in result. Skip
            #    insertion points at a sentence start (string start or just past
            #    .!? + space): splicing a lowercase swear there yields awkward
            #    ".  fucking Second" artifacts, so we'd rather try the next word.
            if not anchored:
                after_words = non_swear_words(raw_lower[raw_end:])
                seen: dict[str, int] = {}
                for word in after_words:
                    seen[word] = seen.get(word, 0) + 1
                    if len(word) < 3:
                        continue
                    matches = list(_re.finditer(rf"\b{_re.escape(word)}\b", result, _re.IGNORECASE))
                    occ = seen[word]
                    if len(matches) >= occ:
                        insert_at = matches[occ - 1].start()
                        preceding = result[:insert_at]
                        if not preceding.strip() or preceding.rstrip()[-1:] in ".!?":
                            continue  # sentence boundary — bad splice point
                        result = result[:insert_at] + swear + " " + result[insert_at:]
                        anchored = True
                        break

            # 3) Last resort: only when there were no usable anchors at all
            #    (heavy rewrite / one-word transcript). Append cleanly. If we
            #    couldn't anchor but DID have a candidate anchor that simply
            #    didn't line up, leaving it censored is the safe choice.
            if not anchored and not prev_words and not non_swear_words(raw_lower[raw_end:]):
                trimmed = result.rstrip(".!?\n ")
                tail_punct = result[len(trimmed):]
                sep = "" if (not trimmed or trimmed[-1].isspace()) else " "
                result = trimmed + sep + swear + tail_punct
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

    # ── Deterministic email layout ─────────────────────────────────────────
    def _format_email_layout(self, text: str) -> str:
        """Put an email greeting and sign-off each on their own line, in code.

        Runs on the FINAL styled output regardless of which LLM produced it, so
        every provider and machine yields the same layout (this is the fix for
        "works on my PC but the sign-off is glued onto one line on my Mac").

        Conservative by design: only reflows text that is already email- or
        multi-paragraph-shaped, and the sign-off matcher requires the closing to
        be glued to a real sentence end and sit at the very end of the text. A
        one-line note that merely ends with "thanks Bob" is left untouched.
        """
        if not text or not text.strip():
            return text
        # Gate: only touch email/multi-paragraph-shaped text. A single-line
        # note with no greeting is never reflowed.
        looks_emailish = ("\n" in text) or bool(
            self._EMAIL_GREETING_LEAD_RE.match(text.lstrip())
        )
        if not looks_emailish:
            return text
        out = self._split_trailing_signoff(text)
        out = self._split_signoff_name(out)
        out = self._split_leading_greeting(out)
        out = self._TRIPLE_NL_RE.sub("\n\n", out)
        return out.strip()

    def _split_trailing_signoff(self, text: str) -> str:
        """Promote a sign-off glued to the last sentence onto its own paragraph."""
        m = self._EMAIL_GLUED_SIGNOFF_RE.search(text)
        if not m:
            return text
        body = text[: m.start()].rstrip()
        return body + m.group("body_end") + "\n\n" + m.group("signoff").strip()

    def _split_signoff_name(self, text: str) -> str:
        """Normalise a trailing sign-off to the canonical two-line form.

        "Thank you, James." / "Regards, James" / "Cheers,\\nJames" all become:
            Closing,
            Name
        Operates only on the LAST paragraph and only when it is entirely a
        recognised closing + name, so a normal final sentence that merely ends
        in a name ("I'll see you on Monday, James.") is left untouched.
        Idempotent — an already two-line sign-off normalises to itself.
        """
        paras = text.split("\n\n")
        m = self._EMAIL_SIGNOFF_NAME_RE.match(paras[-1].strip())
        if not m:
            return text
        closing = m.group("closing").strip()
        closing = closing[0].upper() + closing[1:]  # capitalise as a sign-off
        # The name char class can absorb a trailing "." (for "Dr."/initials), so
        # strip a trailing sentence terminator — the name line takes no punctuation.
        name = m.group("name").strip().rstrip(".! ")
        paras[-1] = f"{closing},\n{name}"
        return "\n\n".join(paras)

    def _split_leading_greeting(self, text: str) -> str:
        """Push a greeting glued to the first sentence onto its own line."""
        head, sep, tail = text.partition("\n")
        m = self._EMAIL_GREETING_LEAD_RE.match(head)
        if not m:
            return text
        greeting = m.group("greeting").strip()
        after = head[m.end():].strip()
        if not after:
            return text  # greeting already alone on its line
        rebuilt = after + (("\n" + tail) if sep else "")
        return greeting + "\n\n" + rebuilt
