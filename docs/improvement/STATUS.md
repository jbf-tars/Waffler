# Improvement run — status

**Branch:** `audit/maintainer-2026-09` · **Base:** `1b671a3` (v3.14.85)
**Worktree:** `C:\Users\james\waffler-audit` (the user's checkout and running
install are untouched) · **Run 1 started:** 2026-09-08

Budget for run 1: ≤8 substantive iterations / 2 h. **Live evaluation not
authorised** — no spend or call budget given, so all work below is offline.

## Baseline recorded before editing

* `git status` clean on tracked files; 14 pre-existing untracked files
  (`OVERNIGHT_AUDIT.md`, `audit_shots/`, `list_models.py`,
  `scripts/test_history_regression.py`, tray/icon PNGs) — **preserved, untouched**.
* Offline suite at baseline: **128 passed, 1 skipped**.
* Installed 3.14.84 ≠ source 3.14.85 (see AUDIT F1).

## Iterations

| # | Item | Hypothesis | Outcome | Commit |
|---|---|---|---|---|
| 1 | Baseline + version mismatch | Published fixes are not what is running | **Confirmed.** Update passes digest+Authenticode then silently no-ops; install log never created; `%TEMP%` cleanup and Defender excluded as explanations | — (evidence only) |
| 2 | ASR filter fidelity | Trailing-anchored patterns delete legitimate speech | **Confirmed and fixed.** 5 reproductions; 3 guards added (boundary / no-dangling-word / audio evidence); 29 controls | `a3723b3` |
| 3 | Provenance | The saved "original" is a filtered artifact | **Confirmed and fixed.** Untouched ASR response preserved; field meaning stamped; UI label corrected | `3095c56` |

**Suite:** 128 → **134 passed, 1 skipped** (33 new tests, 1 pre-existing test
refined with its cases preserved — see AUDIT F2 and the commit message).

## Current state

* Nothing is released. No tag, no build, no change to the running install.
* Branch is local only; the user's `main` and installed app are untouched.

## Next actions (ranked)

1. **A1 — make update failure visible.** Record the intended version before
   restart, verify it after, and surface a real error instead of silence.
   Highest leverage: while it is broken, no fix reaches the user (F1).
2. **A6 — versioned evaluation fixture set** with independently specified
   expected content, so fidelity claims stop resting on single examples, and
   `_retry_if_incomplete`'s heuristics (A2) can be measured rather than assumed.
3. **A4 — separate ASR routing from styling routing**, or state plainly in the
   UI how each stage reads the single `provider_order`.

## Known limitations of this run

* No audio exists for the September incident, so its root cause is narrowed
  (not styling, not the ASR filter) but **not identified** — see AUDIT F3.
* No live ASR/formatting evaluation was run: not authorised, and the user's Groq
  free-tier quota must not be consumed.
* macOS paths unchanged and untested here (Windows machine).
