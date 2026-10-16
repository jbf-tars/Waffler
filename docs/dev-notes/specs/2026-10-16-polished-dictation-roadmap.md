# Waffler roadmap: the "polished dictation" layer (on 3.15.1)

Date: 2026-10-16. Base: `origin/main` at v3.15.1. Branch: `feature/parity-on-3.15`.

An earlier draft of this roadmap was written against v3.14.69 and partly duplicated work that 3.15 already shipped. This version replaces it. The old branch `feature/wispr-parity` is kept only as a source of code to port.

## Goal

Waffler's core (hotkey, Whisper, LLM clean-up, paste) is strong, and 3.15 added a new design, a persistent setup, a recent-audio buffer, quiet-audio lift, "sounds like" vocabulary and a processing watchdog. What is still missing is the layer around the core that the best commercial dictation apps have: pasting that does not clobber the clipboard, per-app tone, edit-by-voice, a usable Snippets editor, richer stats, and a few small touches that make the app feel finished.

## Non-goals

No hosted backend, accounts, sync, analytics, teams, billing, leaderboards, meeting notes, IDE plug-ins or headset integrations. Waffler stays bring-your-own-key: audio and text go only to the provider the user configured.

## What 3.15.1 already has (do not rebuild)

Start at login; persistent first-run setup with a live mic meter; Retry on "Not sent" cards and a last-10 recordings buffer (`recent_audio.py`, `unsent.py`); quiet-audio lift (`quality.py`); a processing watchdog (`pipeline_watchdog.py`, `pipeline_policy.py`); a custom hotkey recorder with Fn conflict handling; day-count history retention and a Privacy and data page (`privacy_data.py`); paged, cached Journal on `history.json` (`journal_data.py`, `atomic_json.py`); "sounds like" vocabulary with derived usage counts and a log-mining migration; Groq clean-up on `openai/gpt-oss-120b` with cooldowns; 85 test files (1,298 tests) enforced in CI, including AST scanners for file encoding and safe writes and a pinned SELF-CORRECTION prompt section.

## Dropped or deferred, and why

| Item | Why |
|---|---|
| SQLite history database | JSON is fast enough (4.5 ms parse at 5,000 entries) and is wired into the Journal, retention, Not-sent cards and ~140 tests. Revisit at ~25k entries or if an append exceeds 150 ms on Windows. |
| "Medium" clean-up level | Contradicts the maintainer's rule that a silent deletion is worse than an uncorrected paste. Deferred. Only **Off** and today's behaviour ship. |
| Re-enabling `prompts/email.txt` | Its permissive paragraphs contradict a rule added after a production failure. Email handling stays in `normal.txt`. |
| Replacement rules in the dictionary | "Sounds like" spellings already are replacement rules. |
| Persistent onboarding, start at login | Shipped in 3.15. |
| A fourth "Insights" page | Navigation is three tested pages. Stats go to Settings > Usage and the Journal rail. |
| Time-based audio retention (14 days / 24 h) | Settings promises "last 10 recordings"; a time window changes that promise. |
| Chunk-in-flight transcription | 3.14.71 to 3.14.97 showed duration-based chunking caused truncation. Only the cap rises (12 to 20 minutes) using the existing size-triggered splitter. |
| Writing samples for transforms, nine transform hotkeys, voice notes | Token cost on a free tier that allows about 30 clean-ups a day. Voice notes stay low priority. |

## Conventions every change must follow (maintainer rules, enforced by tests)

1. **Prompts.** `prompts/normal.txt` is formatted with only `{transcript}` and `{dialect_instruction}`. New instructions (language, style) go into the value of `dialect_instruction`. Default settings must produce a byte-identical prompt (unit-test it). The SELF-CORRECTION section is SHA-256 pinned; any other prompt edit needs the 107-case corpus.
2. **Never lose words silently.** Anything that can shorten text needs its own provider tag and quality threshold so Journal chips stay honest.
3. **Settings.** Read with `_load_settings_file()`, write through `Api._editing_settings()`. `get_settings` and `save_settings` list keys explicitly: add each new key to both, with validation, plus UI.
4. **Files.** Use `data_paths.data_dir()` at call time; atomic JSON helpers for writes; always pass an encoding (a scanner fails `open`/`read_text`/`write_text` without one); register every new data file in `privacy_data.DATA_FILES` / `DATA_FOLDERS`, the log-download exclusions and the Privacy copy.
5. **Messages.** Plain sentences in `user_messages.py`, no raw errors, no em dashes; toast actions go through `_on_toast_action`.
6. **UI.** Atelier design system (`tokens.css`, `components.css`, `setup.css`, `logic.js`): sentence case, line icons, contrast and tab-stop rules policed by tests. UI logic that can be pure lives in `logic.js` and is unit tested.
7. **Pipeline.** Every blocking step goes through `run.call` under the watchdog; no clipboard reads on timers; history writes through `append_history_safely`; `tests/_pipeline_harness.py` lifts named methods out of `app.py`, so a new helper called from `_process` must be added to its lists.
8. **Cross-platform.** macOS first. Mac-only code is guarded, Mac-only tests skip on Windows (Windows CI runs the full suite). CI uses Python 3.11.
9. **Privacy.** No transcript text in logs (use `transcript_for_log`); no new network destinations beyond the user's chosen provider (plus a search engine URL opened in the browser for "ask the web").

## Work packages (in dependency order)

**S1 Quick fixes.** Language picker (`src/languages.py` ported; rule fed through `dialect_instruction`; Atelier row; validation). Groq robustness: advance to the next model on "model gone" errors, give a retired model its own message (today it shows a fake "paused for an hour" rate limit), fix the OpenAI-first short-circuit that skips Groq and Cerebras. Snippets: rebuild the editor on the Vocabulary page; whole-utterance match; longest-first whole-word matching.

**S2 Safe paste.** Port `pasteboard.py` and `mac_input.py`; one `paste_text` through the watchdog with an outcome (`pasted`, `unverified`, `failed`); restore the previous clipboard (Mac only, setting `clipboard_after_paste`, default restore); failed paste leaves the text on the clipboard with a toast; "Paste last" and "Copy last" in the menu bar (show a word count, not a text preview); permission pre-check at key-down including a tap-alive check.

**S3 Notifier, sounds, hints.** One governor for the ~18 ad hoc toast sites (caps, minimum gaps, dismissal memory, one slot at a time, errors pre-empt tips); optional sounds; a quiet "Don't tap, hold" hint (once per 12 hours).

**S4 History details.** Optional `id` and metadata on entries (`duration_s`, `speech_s`, language, provider, paste outcome, app name); per-item delete; export as JSON/CSV/text; link an entry to its recent-audio file for "Redo this take"; crash marker so an interrupted take becomes a recoverable card; `usage.json` append-only fix (it is rewritten twice per dictation before the paste).

**S5 Context and shared LLM path.** `text_context.py` (frontmost app bundle and name, selected text via Accessibility with a clipboard-copy fallback) and `app_categories.py`; capture the target at hotkey press; one `_chat()` in the styler replacing three divergent bodies (state passed as arguments, not stored on `self`).

**S6 Style layer.** Clean-up Off switch; four styles implemented as deterministic code in `style_post.py` (case, final period, greeting break) with a prompt line only where unavoidable (Excited); per-category choices; opt-in email signature; a Settings section.

**S7 Transforms.** Selected-text Polish (rule checkboxes) and Prompt Engineer, a few user-defined transforms, an in-window Journal diff with Undo/Copy/Retry and overlay toast buttons (no new window), local-only.

**S8 Command mode.** Hold-to-talk instruction applied to the selection or "ask the web" (opens the chosen engine); tagged first line instead of JSON mode. Off by default, labelled beta.

**S9 Stats.** Week streak, peak hour, heatmap, week versus last, minutes saved, words per minute (from `speech_s` for new entries, backfilled from `quality.jsonl`), top app: pure additions to `compute_stats`, shown in Settings > Usage and the Journal rail.

**S10 Dictionary and snippets v2.** Vocabulary search, sort and multi-select; CSV import and export; starred entries (low priority); auto-learn from a Journal edit (sounds-like must be Whisper's spelling, not the styler's); Snippet import and export.

**S11 Shortcuts and long takes.** Cap 12 to 20 minutes via the existing splitter; one editor with an action row per shortcut (hands-free, cancel, paste last, transform, command), cross-action conflict warnings, tap-versus-hold and double-tap; optional extra mouse buttons.

**S12 Microphones and Flow Bar.** Ranked mic list by name and id, "use this mic?" prompt on hot-plug, early silence watchdog (5 s and 15 s) with one automatic re-open, follow-system when no mic is saved (today an explicit index is pinned), a small Flow Bar hover menu and remembered position.

## Verification

Each package ends with: new pytest tests, the full suite green (`python -m pytest tests -q`, baseline 1,298 passed and 13 skipped), `scripts/check_doc_drift.py`, pyflakes not above baseline, the import-every-module check, a CHANGELOG entry under `[Unreleased]`, and a local commit. Live provider checks for anything that changes a prompt or model. UI checked in a browser with a stubbed `pywebview.api`. Behaviour that needs real permissions (Accessibility reads, pasting into other apps, event taps, multi-display) is tested as far as this process allows and reported as unverified in a real session. Nothing is pushed, tagged or released.
