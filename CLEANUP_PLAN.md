# Waffler Open-Source Prep — Cleanup Plan

Tracking the work to get Waffler ready for a professional open-source release.
The recurring `/loop` (every 30 min) read this file each iteration, picked the
next `[ ]` target, shipped it, and ticked it off. Started 2026-05-21,
**concluded 2026-06-03 after 20 iterations** (all loop-actionable targets done;
3 user-action items remain — see below).

## Final summary

**Result: codebase is release-ready from the loop's side.** Every audit-flagged
behaviour item is resolved (4 shipped by the user in pre-loop multi-agent
rounds, 1 shipped by the loop as v3.14.70, 1 verified-safe by deeper read).
Every OS-meta file (LICENSE, README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT,
CHANGELOG) verified and any drift fixed. ~140 lines of dead code removed.
Test suite green (21 passed + 1 skipped) on Windows with **zero `--ignore`
exclusions** — anyone cloning the repo on either platform can now `pytest`
without surprises.

| Category | Closed |
|---|---|
| Behaviour fixes (audit HIGH/MEDIUM) | 1 shipped (v3.14.70 focus.signal), 4 verified-shipped, 1 verified-safe |
| Dead code removed | ~140 lines across 4 commits (`wizard_start_fn_detection`, full `get_permission_status` IPC chain) |
| Test-collection fixes (Windows) | 3 files (UTF-8 encoding + `pytest.importorskip` for rumps) |
| Documentation drift | README, CONTRIBUTING, CHANGELOG, style_openai docstring — all reconciled against live code |
| OS-meta verification | All 6 files (LICENSE / README / CONTRIBUTING / SECURITY / CODE_OF_CONDUCT / CHANGELOG) cross-checked against live code; concrete drifts fixed where found |

**Remaining work the loop can't do (3 user-action items below):**
1. **HIGH:** bump `waffler-website/src/data/release.ts` from v3.14.29 → v3.14.70 + redeploy (new downloads still serve a 40-version-stale build).
2. **LOW:** decide on `feature/ai-helper` (rebase onto main, or close as abandoned).
3. **LOW:** swap CODE_OF_CONDUCT enforcement channel from public GitHub Issues to a private route (email, or GitHub private advisories).

**Release-tagging note:** v3.14.70 (commit `df8a68c`) is on `main` but **was deliberately not tagged**. Tagging triggers the macOS + Windows release builds and auto-deploys via the in-app updater — that's a deployment decision for the user to make. Run `git push origin v3.14.70` when ready.

## How to read this

Each target has a severity tag and the rough scope. The loop prefers SAFE
targets first (test fixes, dead-code removal, docs/comment tidy) and only
moves to behaviour-changing fixes (the HIGH items from `OVERNIGHT_AUDIT.md`)
once the safe ones are done.

Every iteration:
1. `git pull origin main` (the user is working in parallel).
2. Pick the next `[ ]` item.
3. Compile-check + run `pytest` (21+ pass, 0 fail) before committing.
4. If a test fails after your change → `git restore .` and re-flag, don't ship.
5. Bump `__version__` only if real behaviour changed (HIGH items). Cleanup
   commits stay on the current version.

## Targets

### Test infrastructure — Windows-collectable

- [x] **[LOW] `tests/test_menubar_icon.py` — gate the `import rumps` with `pytest.importorskip` so collection succeeds on non-Mac.** Iter 1, commit pending.
- [x] **[LOW] `tests/test_e2e_real.py` — Windows-collectable.** Iter 2: the real bug was `Path.read_text()` without `encoding=` falling back to cp1252 on Windows (history.json contains a 0x81 byte that breaks cp1252). Added `encoding="utf-8"`; also swept 5 em dashes in comments to `--` for ASCII-source consistency. Commit `8020691`.
- [x] **[LOW] `tests/test_model_bakeoff.py` — Windows-collectable.** Iter 3: identical root cause to iter 2 — `Path.read_text()` line 23 had no `encoding=`. Added `encoding="utf-8"` and swept 2 em dashes in comments. Commit `b78113b`. **🎉 Milestone:** with iters 1+2+3 all shipped, the full test suite now passes on Windows with ZERO `--ignore` exclusions (21 passed + 1 skipped via the rumps gate). The loop prompt's exclusion flags can be removed on the next prompt refresh.

### Re-survey OVERNIGHT_AUDIT.md against current main

The audit was written against v3.14.49. The user has shipped v3.14.69 with
two multi-agent review rounds + scratch purge + redaction, so several flagged
items may already be resolved. **One iteration is just confirming which are
still open** before doing more work.

- [x] **[META] Re-survey OVERNIGHT_AUDIT.md's HIGH/MEDIUM items against current main.** Iter 4: surveyed each finding against `main` at v3.14.69. Three of the audit's behaviour items are **already fixed** (mic hot-swap PortAudio reinit, save_history atomicity, AVFoundation mic-TCC check). Two are still genuinely open. Details in the new "## Status as of v3.14.69" section below.

### Audit-flagged behaviour fixes (only after re-survey)

These bump the version. **Only ship after the re-survey confirms they're still open.**

- [x] **[HIGH — already fixed in v3.14.6x] Mic hot-swap PortAudio reinit.** Iter-4 grep confirmed `audio.py:265-266` calls `sd._terminate(); sd._initialize()` in the recreate path; comment on line 246 explicitly describes the reinit. No further action needed from this loop.
- [x] **[MEDIUM — iter 16, v3.14.70] Stale `focus.signal` race fixed.** Two-line behaviour fix in `src/single_instance.py:start_focus_watcher`: proactively `unlink()` any pre-existing signal at watcher-start AND initialise `last_processed_mtime = [time.time()]` (was `[0.0]`). Verified empirically: stale signal with mtime = now-60s is correctly ignored (0 spurious `show()` calls). **This closes the LAST audit-flagged behaviour item from OVERNIGHT_AUDIT.md** — every MEDIUM/HIGH from the original audit is now resolved (either shipped here, shipped earlier in the multi-agent rounds, or verified-safe by deeper read). Commit `df8a68c`.
- [x] **[MEDIUM — already fixed via startup probe] PermissionsManager mic-perm check.** Iter-4 found AVFoundation `AVCaptureDevice.authorizationStatusForMediaType_` already wired in `app.py:3953` (the `[mic-tcc]` startup banner), which is the correct TCC check. The old broken `check_microphone_permission` is still present in `src/permissions_manager.py:55` but is superseded — flagged as a dead-code candidate below.

### Newly identified (iter-4 re-survey)

- [x] **[LOW] `PermissionsManager.check_microphone_permission` annotated as superseded.** Iter 5: grep confirmed the full IPC chain (`get_permission_status` → `get_permission_status_summary` → `check_all_permissions` → this method) has **zero JS callers**, so the broken false-GRANTED behaviour isn't reaching users — it's only kept alive by `tests/test_enhanced_permissions.py`. Added a docstring note flagging the supersession and warning future contributors not to add new callers without switching to AVCaptureDevice. Deleting the whole dead IPC chain is a bigger multi-file surgery deferred to a later iteration. Commit `6f0f7e8`.
- [x] **[MEDIUM — iter 15 VERIFIED SAFE] macOS updater swap ordering is textbook stage-then-swap-with-rollback.** Iter 15: read `src/updater.py` lines 520-625 in full. The audit's concern was based on a superficial grep — the deeper read confirms a textbook 5-step pattern that's strictly safer than rm-before-cp:
    1. Locate-before-touch — fails fast if the new app isn't in the DMG, **before** touching `installed`.
    2. Verify sig of in-DMG app — fail-closed before any filesystem changes.
    3. Stage the new app to `/Applications/.waffler-update-staged-{pid}.app` via `copytree`. `installed` is **untouched** at this point.
    4. Re-verify the staged copy actually-on-disk.
    5. Atomic-ish swap: `os.rename(installed → backup)` then `os.rename(staged → installed)`; on any swap failure, restore via `os.rename(backup → installed)`. If restore ALSO fails, `preserve_backup = True` and the backup location is surfaced in the error message so the user can recover manually.
  The `shutil.rmtree(p, ignore_errors=True)` at line 554 (`_cleanup_paths`) only removes `staged` (and `backup` if `preserve_backup is False`) — it can never harm `installed`. The `shutil.rmtree(installed)` at line 601 only runs in the swap-failure rollback path, where `installed` at that moment is the partial-new-app (not the original), and it's deleted to make room for restoring the backup. The `shutil.rmtree(backup)` at line 616 only runs after the swap succeeded (i.e. `installed` is the new app and `backup` is the old). All invariants hold. No source change needed.
- [x] **[LOW — iter 19] CORRECTION to iter-17: corpus already has working `category=` field.** Re-checked via AST walk: `Case` takes `category` as the **3rd positional argument** (not a keyword), so the iter-17 `grep "category="` returned 0 — false negative. The categories exist (prose=34, email=33, code=13, numbered list=8, hallucination-bait=5, self-correction=4, bulleted list=3, double-words=1; total 101) and CONTRIBUTING's original counts were accurate. Restored the `--category hallucination-bait` and `--category email` examples in CONTRIBUTING; commit `d41a499`. Iter-17's `--filter SOLO-NUM-3` example kept (still a useful pattern demo). The follow-up target this once seemed to need is no longer applicable.
- [x] **[LOW — iter 18] Dead `get_permission_status` IPC chain deleted (~115 lines).** Removed the IPC entry point in `app.py:1410` plus the 3 dead `permissions_manager.py` methods (`check_microphone_permission`, `check_all_permissions`, `get_permission_status_summary`) it called. Updated `tests/test_enhanced_permissions.py` — the 2 calls into the dead chain were replaced with explanatory comments pointing future readers at the `[mic-tcc]` AVFoundation startup path; the live methods (`check_accessibility_permission`, `check_input_monitoring_permission`, `PERMISSION_EXPLANATIONS`) are still exercised. 3 files compile, 21 tests still pass + 1 skipped. Commit `0da6acc`.
- [x] **[LOW — iter 17] CONTRIBUTING corpus examples fixed.** Iter-17 deep dive into `scripts/auto_test_corpus.py`. Found two real drifts: (1) `--category` is a CLI flag but no `Case()` carries a `category=` field, so `--category email` / `--category hallucination-bait` both match **zero** cases; (2) `--filter` does case-insensitive substring-anywhere match on the label (line 868), so wide prefixes like `H` (55 hits) or `EM` (37 hits) catch unrelated cases — meaningful prefix isolation just isn't possible with the current script. Fix: dropped both broken `--category` example lines and replaced with a working pattern demo (`--filter SOLO-NUM-3` → 1 specific case). FT + SOLO-NUM examples above kept because their prefix strings are distinctive enough not to false-match. Commit `b6f97d2`.
- [x] **[LOW — iter 10] `src/style_openai.py` Cerebras model name drift fixed.** Iter-9 surface check found 2 mentions; iter-10 grep found **5** (L4 module doc, L31 class doc, L227 inline comment, L264 priority-2 comment, L567 OpenAI-selection rationale). First 4 were straight "Qwen-3 235B → gpt-oss-120b" swaps. L567 cited a specific benchmark against the retired model as the rationale for dropping the >=200-word auto-route — rewrote it as historical context (`the previous Cerebras model (qwen-3-235b-a22b-instruct-2507) was benchmarked…`) rather than fabricating a fresh benchmark against gpt-oss-120b. The intentional historical comment at L75 was left untouched. Zero current-behaviour drift remains. Commit `cd35084`.

### Dead code / referenced-nowhere

Each target must be verified zero references via `grep -rn "name"` against
`app.py src/ ui/ tests/` before deletion.

- [x] **[LOW] `app.py:wizard_start_fn_detection` deleted (25 lines).** Iter 6: re-verified zero callers across app.py, src/, ui/, tests/, docs/. Was a leftover from an older wizard layout where step 3 was Fn detection; current step 3 is API keys, and step-2 Fn detection goes through the live `wizard_init_step2` path on a separate `_wizard_step2_monitor` global. Commit `5d252a6`.
- [x] **[LOW] `src/fn_key_cgevent.FnKeyMonitor` — verified intentional, keep.** Iter 7: grep confirmed exactly one live caller (`app.py:1169` startup Input Monitoring TCC probe in `request_permissions`) plus a test reference. The module is a deliberately-preserved 60-line backward-compat shim already documented in two places — its own head comment AND `mac_hotkey_monitor.py:40`. Both docstrings accurately describe the architecture (real impl is `MacEventTap + FnHandler + SpaceHandler`; this shim exists so the TCC probe and `tests/test_fn_key.py` work without edits; safe because the probe runs sequentially before the real listener). Inlining would only save ~50 lines and complicate `request_permissions`. No source change needed.

### OS-meta polish

The six files exist (`LICENSE`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md`,
`CODE_OF_CONDUCT.md`, `CHANGELOG.md`) — they need a polish pass for the public.

- [x] **[META] README.md polish pass.** Iter 9: read all 196 lines. Structure is strong — answers what/install/contribute clearly, friendly "About this project" + "Known Issues" sections, all internal references (LICENSE badge + Section, CONTRIBUTING link, SECURITY link) check out. Found ONE concrete drift: line 39 (Features) + line 142 (Tech Stack) both named the retired Cerebras `Qwen-3 235B` instead of the current `gpt-oss-120b` (per v3.14.67 swap). Fixed both. Commit `fb24b80`. **Spotted but deferred to its own iter:** the same stale model name lives in `src/style_openai.py`'s module-level docstring (lines 4 + 8) — added below as the next concrete target.
- [x] **[META] CONTRIBUTING.md verified — all concrete claims accurate.** Iter 12: 154 lines, full read. All 4 referenced scripts (`auto_test_corpus.py`, `diagnose_overlay.py`, `diagnose_accessibility.py`, `diagnose_fn_key.py`) present. All 3 referenced tests present. All 3 CI workflow files (`ci.yml`, `macos-release.yml`, `windows-release.yml`) present. `prompts/email.txt` + `hooks/` directory referenced in the structure tree both present. Provider list (Groq free, Cerebras free+paid, OpenAI paid) matches reality. `~/.waffler-hosted/.env` path matches `app.py:701`. The "macOS 12+" prerequisite refers to **building** (notarytool needs Xcode 13+ for codesigning) while runtime supports `LSMinimumSystemVersion: 10.13.0` per `Waffler_mac.spec` — both true, could be clarified in a future polish but not drift. **Deferred to a future iter:** the specific corpus filter case counts (FT=5+2, SOLO-NUM=5, H-prefix=5, email=33) — quick regex check got close-but-not-equal numbers, needs a focused read of `auto_test_corpus.py` to verify properly. Logged below as a follow-up target.
- [x] **[META] SECURITY.md verified clean.** Iter 11: 41 lines, well-structured. Reporting channel correct (GitHub private vulnerability advisories), specific timeline commitments (48h ack / 7d fix-or-mitigation), Supported Versions table present, Responsible Disclosure section with credit-or-anonymous option. Data-path claims **verified against live code**: `~/.waffler-hosted/.env` matches `app.py:701` (`DATA_DIR / ".env"`), `~/.waffler-hosted/history.json` matches `app.py:110` (`HISTORY_FILE = DATA_DIR / "history.json"`), the "audio direct to user-keyed APIs, never Waffler-routed" claim matches the actual architecture. No source change.
- [x] **[META] CODE_OF_CONDUCT.md verified.** Iter 13: 56 lines, clean Contributor Covenant v2.1 adaptation. Attribution accurate (link to the v2.1 canonical text works). British spelling consistent throughout (`behaviour`, `sexualised`) — matches the README's tone (`knackered`). No template residue. **One genuine observation, flagged not fixed:** the Enforcement section directs reports to **public** [GitHub Issues](https://github.com/jbf-tars/waffler/issues). Most projects use a *private* channel for harassment reports (email, or — since SECURITY.md already uses it — GitHub's private vulnerability advisories). Functional as-is, but suboptimal. *Suggested user-action:* swap the link to a private contact channel before public launch. Logged as a user-action item below; not auto-fixed because the right channel is a UX call.
- [x] **[META] CHANGELOG.md structural check.** Iter 14: scanned all 30 version headers in CHANGELOG (v3.14.40 → v3.14.69) and cross-checked each against authoritative git tag timestamps. Patch-number order monotonically decreasing ✓; top entry matches `__version__` ✓. **Found 5 stale dates** (v3.14.50/51/52/68/69 — manual-edit drift); fixed all in `72dddea`. After fix, the date sequence is also non-decreasing (Keep a Changelog convention). No structural issues with duplicate entries, missing sections, etc.
- [x] **[META] LICENSE verified clean.** Iter 8: canonical MIT text (matches the SPDX MIT reference byte-for-byte), year 2026 (current), owner "Waffler contributors" (intentional generic, not a `[YOUR NAME]` placeholder). Cross-checked: `README.md:3` MIT badge links to `LICENSE`, `README.md:188` License section names MIT and links to `LICENSE` — fully consistent. No source change.

### Cross-repo nudge (user-action items, not for the loop)

The loop will NOT touch the website repo (`C:/Users/james/waffler-website`)
or any branch other than `main`. These need the user's manual handling:

- [x] **[USER ACTION — HIGH] Website `release.ts` bumped to v3.14.69.** User did this themselves (waffler-website commit `4c73fe0`). New downloads now serve the real latest release.
- [x] **[USER ACTION — LOW] `feature/ai-helper` branch deleted.** User requested removal; main is clean (zero `ai_helper`/`aiHelper` references confirmed via grep before delete).
- [ ] **[USER ACTION — LOW] `CODE_OF_CONDUCT.md` enforcement channel.** Currently points to public GitHub Issues — fine to ship but a private channel is the conventional norm for harassment reports. Either swap to GitHub's private vulnerability advisories (already configured per SECURITY.md), a dedicated email, or document why public Issues is the intentional choice.

## Status as of v3.14.69 (iter-4 re-survey)

Grep-verified state of each OVERNIGHT_AUDIT.md HIGH/MEDIUM item against current `main`:

| Audit finding | Status | Evidence |
|---|---|---|
| [HIGH] Mic hot-swap PortAudio reinit | ✅ FIXED | `audio.py:265-266` calls `sd._terminate(); sd._initialize()` in recreate; docstring on `:246` explains the reinit. |
| [HIGH] Website serves v3.14.29 downloads | ⚠️ USER ACTION | Separate repo — loop won't touch it. Logged as a user-action item below. |
| [MEDIUM] save_history WinError 5 / atomicity | ✅ FIXED (v3.14.65) | `app.py:148-156` uses `tempfile.mkstemp` + `os.replace` for atomic write. |
| [MEDIUM] AVFoundation mic-TCC check at startup | ✅ FIXED | `app.py:3953` calls `AVCaptureDevice.authorizationStatusForMediaType_` and writes `[mic-tcc]` to `app.log`. |
| [MEDIUM] Stale `focus.signal` race at startup | ❌ STILL OPEN | `single_instance.py:218` still has `last_processed_mtime = [0.0]`. |
| [LOW from audit] PermissionsManager.check_microphone_permission still uses sd.InputStream probe | ⚠️ SUPERSEDED | The startup AVFoundation check (above) is the actual TCC test. The old `permissions_manager.py:55` method is likely dead — flagged as a new cleanup target. |
| [MEDIUM] macOS updater rm-before-cp ordering | ⚠️ NEEDS DEEPER READ | `updater.py` has both `rmtree` and `copytree` calls + a backup-rollback path at `:616`. Likely already safe; defer for a focused read. |

**Headline:** the audit had a long list of behaviour findings; v3.14.50→69 closed all of them except the focus.signal race. The rest of the loop's behaviour-change work is just that one MEDIUM (and an investigative read on the updater).

## Stopping criteria

The loop self-deletes its cron + pushes a `PushNotification` when ALL of these are true:

1. Every **loop-actionable** `[ ]` in this file is `[x]`. **User-action items** (`[USER ACTION — …]` marker) are *out of scope* and never block stopping — the loop explicitly cannot touch the website repo, other branches, or make UX decisions for the user.
2. `pytest tests/ -q` collects + passes with zero exclusions (the 3 Windows-broken files all green or properly-skipped).
3. `grep -rnE "TODO|FIXME|XXX|HACK" app.py src/ ui/` returns no items the loop hasn't either resolved or documented as deliberate.
4. The OVERNIGHT_AUDIT.md re-survey shows zero open `[HIGH]` items.

## Log

| Iter | Time | Target | Outcome | Commit |
|------|------|--------|---------|--------|
| 1 | 2026-05-21 ~10:00 | Plan + `test_menubar_icon` rumps gate | shipped | `74bf29b` |
| 2 | 2026-05-21 ~10:13 | `test_e2e_real` UTF-8 encoding fix + em-dash sweep | shipped | `8020691` |
| 3 | 2026-05-21 ~10:43 | `test_model_bakeoff` UTF-8 encoding fix + em-dash sweep; full suite now passes with 0 exclusions | shipped | `b78113b` |
| 4 | 2026-05-21 ~11:13 | META re-survey of OVERNIGHT_AUDIT.md vs current main — 3 audit fixes confirmed shipped, 1 MEDIUM still open, 1 LOW newly identified | docs only | `978dd7c` |
| 5 | 2026-05-21 ~11:43 | `check_microphone_permission` docstring note (superseded by AVFoundation startup check) — deletion deferred to later iter | docs only | `6f0f7e8` |
| 6 | 2026-05-21 ~12:13 | Delete dead `wizard_start_fn_detection` (25 lines) | refactor | `5d252a6` |
| 7 | 2026-05-21 ~12:43 | Verify `FnKeyMonitor` is intentional shim (kept, no change) | docs only | `d76d6a4` |
| 8 | 2026-05-21 ~13:13 | LICENSE check — verified clean, no change | docs only | `4927e2a` |
| 9 | 2026-05-21 ~13:43 | README polish — fix stale Cerebras model name (Qwen-3 235B → gpt-oss-120b) | docs only | `fb24b80` |
| 10 | 2026-05-21 ~14:13 | style_openai.py — fix 5 stale Cerebras model refs (incl. honest L567 benchmark rewrite) | docs only | `cd35084` |
| 11 | 2026-05-21 ~14:43 | SECURITY.md verified clean — data paths match live code | docs only | `c71f4ec` |
| 12 | 2026-05-21 ~15:13 | CONTRIBUTING.md verified — all concrete claims accurate (scripts, tests, CI files, paths); corpus filter counts deferred for a focused read | docs only | `0ee086f` |
| 13 | 2026-05-21 ~15:43 | CODE_OF_CONDUCT.md verified — clean CC v2.1; flagged public-Issues-for-reports as a user-action UX call | docs only | `7b17b5e` |
| 14 | 2026-05-21 ~16:13 | CHANGELOG.md structural check — fixed 5 stale release dates vs authoritative git tag timestamps | docs only | `72dddea` |
| 15 | 2026-05-21 ~16:43 | macOS updater deeper read — VERIFIED SAFE (textbook stage-then-swap-with-rollback, all invariants hold) | docs only | `6c9d3b6` |
| 16 | 2026-06-03 ~10:13 | Stale focus.signal race fixed — v3.14.70, closes last audit-flagged behaviour item | **behaviour fix** | `df8a68c` |
| 17 | 2026-06-03 ~10:43 | CONTRIBUTING corpus examples fixed — broken `--category` lines replaced with working pattern | docs only | `b6f97d2` |
| 18 | 2026-06-03 ~11:13 | Delete dead `get_permission_status` IPC chain (~115 lines, 3 files) | refactor | `0da6acc` |
| 19 | 2026-06-03 ~11:43 | Restore `--category` examples I wrongly removed in iter-17 (counts were always correct; category is a positional arg) | docs only | `d41a499` |
| 20 | 2026-06-03 ~12:13 | Final summary + stopping criterion clarified + loop cron deleted | docs only | `3b102dd` |
