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
- [ ] **[LOW] `tests/test_e2e_real.py` — replace em dashes (U+2014) in docstrings/comments with `--`** so pytest collection doesn't trip Windows cp1252.
- [ ] **[LOW] `tests/test_model_bakeoff.py` — same em-dash → `--` swap.**

### Re-survey OVERNIGHT_AUDIT.md against current main

The audit was written against v3.14.49. The user has shipped v3.14.69 with
two multi-agent review rounds + scratch purge + redaction, so several flagged
items may already be resolved. **One iteration is just confirming which are
still open** before doing more work.

- [ ] **[META] Re-survey OVERNIGHT_AUDIT.md's HIGH/MEDIUM items against current main.** Tick off any that are already fixed; downgrade severity for any partially addressed. Append a "## Status as of v3.14.69" section.

### Audit-flagged behaviour fixes (only after re-survey)

These bump the version. **Only ship after the re-survey confirms they're still open.**

- [ ] **[HIGH if open] Mic hot-swap PortAudio reinit** — `src/audio_device_monitor.py` + `src/audio.py`. Add `sd._terminate(); sd._initialize()` to the monitor's `_current_default_name()` and to `_create_stream()`. Bump version.
- [ ] **[MEDIUM if open] Stale `focus.signal` race at startup** — `src/single_instance.py:start_focus_watcher`. Initialise `last_processed_mtime` to `time.time()` so leftover signal files don't fire `window.show()` during pywebview bootstrap. Bump version.
- [ ] **[MEDIUM if open] PermissionsManager mic-perm check returns false-GRANTED on TCC denial** — `src/permissions_manager.py:55-86`. Replace the `sd.InputStream` probe with the AVFoundation status call that's now wired into startup banner. Bump version.

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
