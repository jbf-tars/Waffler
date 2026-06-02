# Waffler Open-Source Prep — Cleanup Plan

Tracking the work to get Waffler ready for a professional open-source release.
The recurring `/loop` (every 30 min) reads this file each iteration, picks the
next `[ ]` target, ships it, and ticks it off. Started 2026-05-21.

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
- [ ] **[MEDIUM — still open] Stale `focus.signal` race at startup** — `src/single_instance.py:218` still has `last_processed_mtime = [0.0]`, exactly the audit pattern. Need to initialise to `time.time()` so leftover signal files don't fire `window.show()` during pywebview bootstrap. Real behaviour change → bump version.
- [x] **[MEDIUM — already fixed via startup probe] PermissionsManager mic-perm check.** Iter-4 found AVFoundation `AVCaptureDevice.authorizationStatusForMediaType_` already wired in `app.py:3953` (the `[mic-tcc]` startup banner), which is the correct TCC check. The old broken `check_microphone_permission` is still present in `src/permissions_manager.py:55` but is superseded — flagged as a dead-code candidate below.

### Newly identified (iter-4 re-survey)

- [x] **[LOW] `PermissionsManager.check_microphone_permission` annotated as superseded.** Iter 5: grep confirmed the full IPC chain (`get_permission_status` → `get_permission_status_summary` → `check_all_permissions` → this method) has **zero JS callers**, so the broken false-GRANTED behaviour isn't reaching users — it's only kept alive by `tests/test_enhanced_permissions.py`. Added a docstring note flagging the supersession and warning future contributors not to add new callers without switching to AVCaptureDevice. Deleting the whole dead IPC chain is a bigger multi-file surgery deferred to a later iteration. Commit `6f0f7e8`.
- [ ] **[MEDIUM — needs deeper read] macOS updater swap ordering** — `src/updater.py` has `shutil.rmtree` at lines 554 + 601 and `shutil.copytree` at 581. Audit raised concern about the "remove-before-stage" window. Read the surrounding 100 lines and confirm there's a backup + rollback path (line 616 has a `shutil.rmtree(backup, ...)` which suggests there IS one — likely already safe, but worth confirming).
- [ ] **[LOW — deferred from iter 5] Delete the dead `get_permission_status` IPC chain entirely.** Grep confirmed zero JS callers for `pywebview.api.get_permission_status`, but the Python chain (`get_permission_status` → `get_permission_status_summary` → `check_all_permissions` → `check_microphone_permission`) is still alive. Deletion needs updating `tests/test_enhanced_permissions.py` too — multi-file commit, kept separate from the docstring annotation in iter 5.

### Dead code / referenced-nowhere

Each target must be verified zero references via `grep -rn "name"` against
`app.py src/ ui/ tests/` before deletion.

- [ ] **[LOW] `app.py:wizard_start_fn_detection`** — audit found no JS caller. Re-verify (the user may have added one in v3.14.50+) and delete if still dead. Pure refactor, no version bump.
- [ ] **[LOW] `src/fn_key_cgevent.FnKeyMonitor`** — audit found it only kept as a one-shot startup permission probe. Re-verify and either delete or document why it's preserved.

### OS-meta polish

The six files exist (`LICENSE`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md`,
`CODE_OF_CONDUCT.md`, `CHANGELOG.md`) — they need a polish pass for the public.

- [ ] **[META] README.md polish pass** — read fully, check it answers the three questions a first-time visitor needs: what does Waffler do, how do I install it, how do I contribute. Note polish items, then ship.
- [ ] **[META] CONTRIBUTING.md polish pass** — covers the contributor lifecycle (dev setup, test command, PR conventions, what's in scope).
- [ ] **[META] SECURITY.md polish pass** — covers reporting channel and what's in/out of scope.
- [ ] **[META] CODE_OF_CONDUCT.md polish pass** — matches the project's tone, no template-residue.
- [ ] **[META] CHANGELOG.md structural check** — version order correct, no duplicate entries, links to release tags work.
- [ ] **[META] LICENSE check** — the right license is named, no placeholder year/owner left.

### Cross-repo nudge (user-action items, not for the loop)

The loop will NOT touch the website repo (`C:/Users/james/waffler-website`)
or any branch other than `main`. These need the user's manual handling:

- [ ] **[USER ACTION — HIGH] Bump `waffler-website/src/data/release.ts`** from v3.14.29 → current (currently v3.14.69). Two URLs + the version label. Redeploy the site so new downloads aren't shipping a 40-version-stale build.
- [ ] **[USER ACTION — LOW] `feature/ai-helper` branch** is 40+ commits behind main. Either rebase onto current main before merging, or close the branch if abandoned.

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

1. Every `[ ]` in this file is `[x]`.
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
