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
| 4 | Updater invisibility | A failed update is indistinguishable from a successful one | **Confirmed and fixed.** Intent recorded before restart, installer exit code captured, reconciled on next start, surfaced in UI | `89c7301` |
| 5 | CI / lint integrity | The checks cannot fail, and the suite reads private data | **Confirmed and fixed.** Always-green lint (hiding a real NameError), collection-time credential+history loading, a drifted hardcoded manifest, no Windows job | `d915f28` |
| 6 | Unavailable model re-probed | A 404 sets no cooldown, so every dictation pays a wasted round-trip | **Confirmed and fixed.** 458 occurrences in log; 1-hour cooldown + named error on both providers | `4fc2085` |

**Suite:** 128 → **147 passed, 1 skipped** (46 new tests, 1 pre-existing test
refined with its cases preserved — see AUDIT F2 and the commit message).
Offline isolation proven: the suite passes with `HOME`/`USERPROFILE`
redirected to an empty directory and all provider keys cleared.

## Current state

* Nothing is released. No tag, no build, no change to the running install.
* Branch is local only; the user's `main` and installed app are untouched.

## Next actions (ranked)

1. **A6 — versioned evaluation fixture set** with independently specified
   expected content. Everything about *recognition* fidelity is still
   unmeasured; needs the user to approve a fixture set and a spend/call budget.
   Unblocks A2.
2. **A2 — validate `_retry_if_incomplete`.** Its words-per-second threshold and
   1.25x "improvement" rule are unvalidated heuristics: an omission can pass the
   threshold, and a longer output can be a *hallucinated* one. It is currently
   trusted to overwrite a transcript.
3. **A4 — separate ASR routing from styling routing**, or state plainly in the
   UI how each stage reads the single `provider_order` (Cerebras is silently
   skipped for ASR).

## Release candidate / rollback

No build, tag or release was produced — deploying is a separate approval step.
The work is on branch `audit/maintainer-2026-09` only; `main`, the user's
checkout and the installed 3.14.84 app are untouched.

To build an RC from the branch (Windows): `build_windows.bat` after checking it
out. To roll back at any point: `git checkout main` (the branch is additive and
has not been merged), or reinstall the published v3.14.85 installer. No user
data, history or settings are migrated or rewritten by these changes, so
rollback needs no data steps.

## Known limitations of this run

* No audio exists for the September incident, so its root cause is narrowed
  (not styling, not the ASR filter) but **not identified** — see AUDIT F3.
* No live ASR/formatting evaluation was run: not authorised, and the user's Groq
  free-tier quota must not be consumed.
* macOS paths unchanged and untested here (Windows machine).
