# Prompt Templates

Waffler's styling step (the second LLM call after transcription) is driven by a plain-text prompt file. Each line in here is one "mode" the user can select.

## Files in this directory

| File | Status | Purpose |
|------|--------|---------|
| [`normal.txt`](./normal.txt) | **Active** — the only mode currently exposed in the sidebar | General-purpose voice-to-text cleanup: filler-word removal, light grammar, list detection, vocab preservation. Heavy with explicit hard-rules accumulated across v3.14.x (e.g. v3.14.38's anti-filler-tail rule, v3.14.0's em-dash strip). |
| [`email.txt`](./email.txt) | **Hidden** since v3.14.6 (was active v3.14.0–v3.14.5) | Inherits every Normal rule and adds permissive paragraphing + dedicated greeting / sign-off lines when the speaker actually dictated them. Still loadable via `PROMPT_STYLE=email` env var but no UI affordance; expect to come back when more dictation patterns are covered. |

## How a prompt gets used

1. Waffler resolves the active mode from `~/.waffler-hosted/settings.json` (`prompt_style`), or the `PROMPT_STYLE` env var, or defaults to `normal`. See [`src/config.py`](../src/config.py).
2. [`src/style_openai.py`](../src/style_openai.py) opens `prompts/<mode>.txt` once at startup.
3. For each recording, the styler substitutes `{transcript}` with the cleaned Whisper output and sends the prompt to whichever provider is up in the fallback chain (Groq → Cerebras → OpenAI — Groq first so the free 100k tokens/day allowance gets spent before any paid Cerebras tokens).
4. The provider's response goes through `_strip_em_dashes` (v3.14.0) and `_strip_hallucinations` (catches YouTube-outro phrases, the v3.14.39 "and more" filter, etc.) before reaching the clipboard.

## Editing `normal.txt`

The prompt is opinionated and has shipped through many bug-induced iterations — every "NEVER" line in there exists because the model violated it in production at some point. Don't shorten without checking.

**Before shipping a prompt edit:**

```bash
# Run the full corpus (101 cases) against your changes.
python scripts/auto_test_corpus.py

# Or scope to one category if you're iterating:
python scripts/auto_test_corpus.py --filter FT        # filler-tail anti-abridgement (v3.14.38)
python scripts/auto_test_corpus.py --filter SOLO-NUM  # solo-number-not-list cases
python scripts/auto_test_corpus.py --filter HALLUC    # hallucination-bait cases
python scripts/auto_test_corpus.py --filter EM        # email-mode cases
```

Each case declares `must_contain` / `must_not_match` / `retention_min` assertions, so a regression on any of them fails loudly with the exact raw input and styled output for inspection.

## Creating a new mode

1. Create `prompts/your_style.txt` with your rules.
2. Use the literal token `{transcript}` once where the speaker's text should be substituted.
3. Either:
   - Set `PROMPT_STYLE=your_style` in your `.env` (advanced override), or
   - Add it to the sidebar dropdown via `app.py`'s `get_modes()` and re-launch.
4. **Add a few regression cases to [`scripts/auto_test_corpus.py`](../scripts/auto_test_corpus.py)** so future edits don't silently break your mode.

## Guidelines that apply to every mode

These are non-negotiable across modes — they're what makes the output trustworthy to paste into business comms or code:

- **Remove fillers, preserve content.** Drop um/uh/you-know-as-filler, but never drop a whole sentence or clause the speaker said.
- **Never invent.** No greetings, sign-offs, names, or content the speaker didn't dictate.
- **Never abridge.** No "and more / and so on / etc / amongst others" substitutions for content that wasn't actually spoken (v3.14.38 hard-rule).
- **Never paraphrase.** Use the speaker's own vocabulary. "How needed is it" stays "how needed", not "how necessary".
- **Never censor.** Profanity stays exactly as spoken.
- **Never add meta-commentary.** No "Here is the cleaned version:" preambles, no "Cleaned:" prefixes.
