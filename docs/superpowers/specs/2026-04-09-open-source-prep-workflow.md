# Open-Source Preparation Workflow - Waffler

**Date:** 2026-04-09
**Status:** Approved
**Objective:** Safely implement security fixes and code quality improvements before public open-source release

---

## Executive Summary

Implement all critical security fixes, code cleanup, and quality improvements identified in the comprehensive code review before making the Waffler repository public. Use git worktree isolation with backup branches to ensure safe, reversible changes across three phased implementation stages.

**Timeline:** 3-4 hours (mostly automated agent work with review checkpoints)

---

## Context

### Current State
- Repository: `jbf-tars/waffler` (private on GitHub)
- Branch: `main` (clean working tree, 121 commits)
- Issues identified: 4 Critical, 5 High Priority, 8 Medium Priority
- Build artifacts: 160MB DMG files committed to repo
- GitHub Actions: Already configured for automated releases

### Requirements
- Fix all security issues before public release
- Remove build artifacts from git history
- Improve code quality and professional appearance
- Maintain full rollback capability throughout process
- Clean up old GitHub Releases (keep only latest)

---

## Workflow Strategy

### Approach: Hybrid Sequential + Parallel
- **Phase 1 (Critical):** Sequential security fixes - too important to rush
- **Phase 2 (High Priority):** 3 parallel agents for independent cleanup work
- **Phase 3 (Polish):** Sequential improvements - quick fixes
- **Git Cleanup:** Purge DMGs from history after code fixes complete

### Safety Mechanisms
1. **Git worktree isolation** - main directory stays untouched
2. **Backup branch** - snapshot before destructive operations
3. **Atomic commits** - each phase = one commit, easy to revert
4. **Review checkpoints** - human verification after each phase
5. **Multiple rollback options** - from surgical reverts to nuclear reset

---

## Architecture

### Directory Structure
```
~/waffler/                          # Main repo (untouched during work)
  ├── .git/                         # Shared git database
  └── [main branch, clean]

~/waffler-open-source-prep/         # Worktree (work happens here)
  ├── [fix/open-source-prep branch]
  └── [all fixes applied]

Branches:
  - main                            # Protected, stable
  - backup/pre-cleanup              # Snapshot before git history rewrite
  - fix/open-source-prep            # Active development branch (in worktree)
```

### Workflow Phases
```
main branch (clean, untouched)
    ↓
Create backup branch: backup/pre-cleanup
    ↓
Create worktree: ~/waffler-open-source-prep/
    ↓
┌─────────────────────────────────────────┐
│ Phase 1: Critical Security Fixes        │
│ - JS injection fix                       │
│ - URL validation                         │
│ - System preference modification removal │
│ - Bare exception handler fix             │
│ → 1 commit, review checkpoint            │
└─────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────┐
│ Phase 2: Parallel Cleanup (3 agents)    │
│ Agent A: UI cleanup (auth code, XSS)    │
│ Agent B: Logging migration (print→log)  │
│ Agent C: File cleanup (DMGs, deps)      │
│ → 3 commits (one per agent), review     │
└─────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────┐
│ Phase 3: Polish & Medium Priority       │
│ - API key masking                        │
│ - Temp file permissions                  │
│ - Module docstrings                      │
│ - Shell=True fixes                       │
│ - History size limits                    │
│ - Input validation                       │
│ → 1 commit, review checkpoint            │
└─────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────┐
│ Git History Cleanup                      │
│ - Purge DMG files from entire history    │
│ - Force push (safe - private repo)       │
│ → Saves ~160MB permanently               │
└─────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────┐
│ Verification                             │
│ - Syntax checks                          │
│ - Smoke test app launch                  │
│ - Security verification                  │
│ - Review all commits                     │
└─────────────────────────────────────────┘
    ↓
Merge fix/open-source-prep → main
    ↓
Tag v3.4.0 → GitHub Actions builds release
    ↓
Delete old GitHub Releases (keep latest only)
    ↓
Repository ready for public open-source launch
```

---

## Phase 1: Critical Security Fixes

### Scope
All security issues that MUST be fixed before public release. These are vulnerabilities that could be immediately exploited or cause PR disasters.

### Implementation Details

**Fix 1: JavaScript Injection**
- **File:** `app.py:1420`
- **Issue:** Unsanitized f-string interpolation into `evaluate_js()`
- **Fix:**
  ```python
  # Before:
  _window.evaluate_js(f"window.waffler_status && window.waffler_status('{status}')")

  # After:
  _window.evaluate_js(f"window.waffler_status && window.waffler_status({json.dumps(status)})")
  ```
- **Verification:** Search for other `evaluate_js` calls with f-strings

**Fix 2: URL Validation**
- **File:** `app.py:514-528`
- **Issue:** `open_url()` accepts any URL scheme, `os.startfile()` can execute files
- **Fix:**
  ```python
  def open_url(self, url: str):
      from urllib.parse import urlparse
      parsed = urlparse(url)
      if parsed.scheme not in ("http", "https"):
          _log_to_file(f"open_url blocked: invalid scheme '{parsed.scheme}'")
          return
      # ... existing code
  ```

**Fix 3: System Preference Modification**
- **File:** `app.py:2165-2186`
- **Issue:** `_disable_input_source_shortcut()` silently modifies macOS keyboard shortcuts
- **Decision:** Comment out (don't delete) - preserves code for potential opt-in feature
- **Fix:**
  ```python
  # DISABLED: Silently modifying system preferences is inappropriate for open-source software
  # If needed, this should be opt-in with clear UI disclosure and restore mechanism
  # def _disable_input_source_shortcut():
  #     ...
  ```
- **Also:** Comment out the function call where it's invoked

**Fix 4: Bare Exception Handler**
- **File:** `app.py:2125`
- **Issue:** `except:` catches system exceptions (KeyboardInterrupt, SystemExit)
- **Fix:**
  ```python
  # Before:
  except:
      pass

  # After:
  except Exception:
      pass
  ```

### Deliverable
- **Commit:** `fix: resolve critical security issues for open-source release`
- **Commit message:**
  ```
  fix: resolve critical security issues for open-source release

  - Fix JS injection in notify_js_status() via json.dumps()
  - Add URL scheme validation in open_url() (http/https only)
  - Disable silent system preference modification
  - Fix bare except handler to allow system exceptions

  These fixes address security vulnerabilities identified in pre-release
  code review that could be exploited or cause negative PR if released.

  Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
  ```

### Review Checkpoint
Human verification before proceeding to Phase 2:
- All 4 fixes applied correctly
- No syntax errors (`python -m py_compile app.py`)
- Changes look safe and minimal

---

## Phase 2: High Priority Cleanup (Parallel Agents)

### Scope
Code quality and cleanup issues that make the codebase look unprofessional. These can be worked on in parallel since they touch different files.

### Agent A: UI Cleanup
**Responsibility:** Remove dead code and fix XSS vulnerabilities in JavaScript

**Tasks:**

1. **Remove Dead Authentication Code** (~150 lines)
   - Delete functions: `submitAuth`, `oauthSignIn`, `toggleAuthMode`, `showAuthSection`, `showMainSection`
   - Remove HTML: `authEmail`, `authPassword`, `authSignUpEmail`, `authSignUpPassword` inputs
   - Remove: Sign-up/sign-in form sections
   - Location: `ui/app.js:1086-1230+`
   - Rationale: No backend implementation exists, leftover from SaaS version

2. **Fix XSS Vulnerabilities**
   - Use existing `escHtml()` function or `textContent` instead of `innerHTML`
   - Locations to fix:
     - Line 310: `display` from `get_hotkey_config()`
     - Line 1760: Permission titles from backend
     - Line 1801: Recommendations array
     - Lines 1907-1912: Permission status details
     - Line 2173: Hotkey name in placeholder

**Deliverable:** `refactor(ui): remove dead auth code and fix XSS vulnerabilities`

### Agent B: Logging Migration
**Responsibility:** Replace print() statements with Python logging module

**Tasks:**

1. **Setup Logging Infrastructure**
   - Add logging configuration in `app.py` (use existing `config.yaml` settings)
   - Setup file handler to `~/.waffler-hosted/app.log`
   - Respect `logging.log_transcripts` config flag

2. **Replace Print Statements**
   - `src/audio.py`: 8 instances (lines 50, 55, 66, 88, 101, 108-109, 129, 138)
   - `src/clipboard.py`: 4 instances (lines 14, 24, 66, 103)
   - `app.py`: 20+ instances throughout
   - Other files as discovered

3. **Privacy Protection**
   - Don't log transcribed speech unless `logging.log_transcripts: true`
   - Redact API keys in error logs

**Deliverable:** `refactor: migrate from print() to logging module`

### Agent C: File Cleanup
**Responsibility:** Remove build artifacts, fix gitignore, clean dependencies

**Tasks:**

1. **Remove DMG Files** (from working directory only, history cleanup later)
   ```bash
   git rm Waffler-3.3.0-installer.dmg
   git rm Waffler-3.3.1-clean.dmg
   ```

2. **Update .gitignore**
   - Add: `*.dmg`
   - Add: `*.log`
   - Verify: `dist/` and `build/` patterns present

3. **Fix Requirements Files**
   - Remove duplicate: Choose `requirements-windows.txt` (hyphen), delete `requirements_windows.txt` (underscore)
   - Remove unused dependency: `requests>=2.31.0` (not imported anywhere)

4. **Update Version Number**
   - File: `src/__init__.py`
   - Update `__version__ = "2.1.19"` to `"3.3.1"` (match latest release)

**Deliverable:** `chore: remove build artifacts and clean up dependencies`

### Coordination
- No file conflicts expected (agents work on different files)
- All 3 agents can run simultaneously
- Each agent commits independently

### Review Checkpoint
Human verification of all 3 commits:
- Dead code actually removed (UI cleaner)
- Logging works, no print() statements remain
- DMG files gone, .gitignore updated

---

## Phase 3: Polish & Medium Priority

### Scope
Quick security and quality improvements. Not critical for launch but good to have.

### Implementation (Sequential, One Agent)

**Fix 1: API Key Masking**
- **File:** `app.py:404-409`
- **Change:** Show only last 4 chars instead of first 8 + last 4
  ```python
  def _mask(k):
      if len(k) > 4:
          return "sk-..." + k[-4:]
      return k
  ```

**Fix 2: Temporary File Permissions**
- **File:** `src/transcribe_whisper.py:286-305` (4 methods)
- **Change:** Add restrictive permissions after temp file creation
  ```python
  with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
      f.write(audio_bytes)
      tmp = f.name
  os.chmod(tmp, 0o600)  # Add this line
  ```

**Fix 3: Module Docstrings**
- **Files:** `app_detection.py`, `audio_devices.py`, `fn_key_cgevent.py`, `mac_hotkey_monitor.py`, `overlay.py`, `smart_hotkey.py`, `windows_hotkey.py`
- **Change:** Add brief module-level docstrings where empty `""" """` exists

**Fix 4: Subprocess shell=True**
- **Files:** `app.py:1556`, `permissions_manager.py:256`
- **Change:** Use `webbrowser.open()` for settings URLs instead of `shell=True`

**Fix 5: History File Size Limit**
- **File:** `app.py:109-112` (`load_history()`)
- **Change:** Limit to 10,000 entries, trim oldest
  ```python
  def load_history() -> list:
      ensure_history_dir()
      if not HISTORY_FILE.exists():
          return []
      try:
          with open(HISTORY_FILE, 'r') as f:
              history = json.load(f)
              # Trim to last 10,000 entries
              if len(history) > 10000:
                  history = history[-10000:]
              return history
      except Exception:
          return []
  ```

**Fix 6: Input Validation**
- **Files:** `app.py:296-305` (`set_vocab`), `app.py:1096-1103` (`set_snippets`)
- **Change:** Add bounds checking
  ```python
  def set_vocab(self, words: list) -> dict:
      if len(words) > 1000:
          return {"ok": False, "error": "Maximum 1000 vocabulary words"}
      words = [w[:200] for w in words]  # Truncate individual entries
      # ... existing code
  ```

### Deliverable
- **Commit:** `polish: improve security and code quality`
- All 6 fixes in one commit (they're quick and related)

### Review Checkpoint
- Verify all polish fixes applied
- Code quality improved, no regressions

---

## Git History Cleanup

### Objective
Remove 160MB DMG files from entire git history, permanently reducing repository size.

### Prerequisites
- All code fixes merged and committed
- Backup branch `backup/pre-cleanup` exists
- Repository still private (safe to rewrite history)

### Process

**Step 1: Install git-filter-repo**
```bash
pip install git-filter-repo
```

**Step 2: Purge DMG Files**
```bash
cd ~/waffler-open-source-prep

# Remove both DMG files from entire history
git filter-repo --path Waffler-3.3.0-installer.dmg --invert-paths --force
git filter-repo --path Waffler-3.3.1-clean.dmg --invert-paths --force
```

**Step 3: Force Push**
```bash
git push origin --force --all
git push origin --force --tags
```

**Step 4: Verification**
```bash
# DMG files should not appear in history
git log --all --oneline -- "*.dmg"  # Should return nothing

# Check repo size reduction (approximate)
du -sh .git  # Should be ~160MB smaller
```

### Impact
- ~10-15 commits will have new SHAs (commits after April 3rd when DMGs were added)
- Backup branch preserves old SHAs (can restore if needed)
- Safe operation since repo is private (no public clones to worry about)

### Rollback
If cleanup goes wrong:
```bash
git reset --hard backup/pre-cleanup
git push origin --force main
```

---

## Verification & Merge

### Pre-Merge Verification

**1. Syntax Checks**
```bash
cd ~/waffler-open-source-prep

# Python syntax
python -m py_compile app.py
python -m py_compile src/*.py

# Check for JavaScript syntax errors (manual)
# Open ui/app.js in editor, look for syntax highlighting errors
```

**2. Smoke Test**
```bash
cd ~/waffler-open-source-prep
python app.py

# Verify:
# - App launches without crashing
# - UI appears
# - Settings accessible
# - Basic recording flow works (if you have API key)
```

**3. Security Verification**
```bash
# No print statements should remain
grep -r "print(" src/ app.py | grep -v "# print" | grep -v "sprint"

# No evaluate_js with unsafe interpolation
grep -n "evaluate_js.*f\".*{.*}.*'" app.py

# DMG files removed
ls *.dmg  # Should error: "No such file or directory"

# .gitignore updated
grep "\.dmg" .gitignore  # Should show *.dmg pattern
```

**4. Review All Commits**
```bash
git log main..fix/open-source-prep --oneline

# Should show approximately:
# - fix: resolve critical security issues for open-source release
# - refactor(ui): remove dead auth code and fix XSS vulnerabilities
# - refactor: migrate from print() to logging module
# - chore: remove build artifacts and clean up dependencies
# - polish: improve security and code quality
```

### Merge to Main

```bash
# Switch to main repo
cd ~/waffler
git checkout main

# Merge with no-fast-forward (preserves branch history)
git merge fix/open-source-prep --no-ff -m "Merge open-source preparation fixes

All critical security fixes, code cleanup, and quality improvements
implemented before public release. See branch commits for details."

# Tag new version
git tag v3.4.0 -m "Release v3.4.0 - Open Source Ready

- Fixed critical security vulnerabilities
- Removed dead code and build artifacts
- Improved logging and code quality
- Ready for public open-source release"

# Push to GitHub
git push origin main
git push origin v3.4.0
```

### GitHub Actions Trigger
Pushing the `v3.4.0` tag automatically triggers:
- macOS workflow: Builds signed/notarized DMG
- Windows workflow: Builds installer EXE
- Both create GitHub Release with attached installers

### Post-Merge Cleanup

**Remove Worktree**
```bash
cd ~/waffler
git worktree remove ~/waffler-open-source-prep

# Verify removal
git worktree list  # Should only show main repo
```

**Optional: Delete Remote Branch**
```bash
git push origin --delete fix/open-source-prep
git branch -d fix/open-source-prep
```

**Delete Old GitHub Releases**
- Go to: `https://github.com/jbf-tars/waffler/releases`
- Delete all releases except `v3.4.0`
- Keeps releases page clean, only shows latest version

---

## Rollback Strategies

### During Implementation (Before Merge)

**Scenario 1: Specific commit has issues**
```bash
cd ~/waffler-open-source-prep
git revert <problematic-commit-sha>
# Creates new commit that undoes the problematic one
```

**Scenario 2: Want to redo a phase**
```bash
cd ~/waffler-open-source-prep
git reset --hard HEAD~1  # Undo last commit
# Or reset to specific commit
git reset --hard <commit-sha>
```

**Scenario 3: Complete restart needed**
```bash
cd ~/waffler
git worktree remove ~/waffler-open-source-prep --force
git branch -D fix/open-source-prep

# Start fresh
git worktree add ~/waffler-open-source-prep -b fix/open-source-prep-v2
# Main branch still untouched, can try again
```

### After Git History Cleanup

**Scenario: History cleanup broke something**
```bash
cd ~/waffler
git reset --hard backup/pre-cleanup
git push origin --force main
# Back to pre-cleanup state with old SHAs
```

### After Merge to Main

**Scenario 1: Merge not pushed yet**
```bash
cd ~/waffler
git reset --hard HEAD~1  # Undo merge commit
```

**Scenario 2: Merge already pushed**
```bash
cd ~/waffler
git revert -m 1 <merge-commit-sha>
# Creates new commit that undoes the merge
# Preserves history (recommended for public repos)
```

### Nuclear Option
**If everything goes catastrophically wrong:**
```bash
cd ~/waffler
git reset --hard backup/pre-cleanup
git push origin --force main

# Or restore from GitHub
git reset --hard origin/main  # If GitHub still has good state
```

---

## Success Criteria

**Technical:**
- ✅ All 4 critical security issues fixed
- ✅ All high priority issues addressed
- ✅ Medium priority polish completed
- ✅ DMG files purged from git history
- ✅ Repo size reduced by ~160MB
- ✅ All tests pass (syntax checks, smoke test)
- ✅ GitHub Actions builds succeed

**Process:**
- ✅ Main branch never touched during work
- ✅ Backup branch created and preserved
- ✅ Worktree used for isolation
- ✅ Each phase reviewed before proceeding
- ✅ Clean commit history with atomic changes

**Open Source Readiness:**
- ✅ No security vulnerabilities
- ✅ Professional code quality
- ✅ No build artifacts in repo
- ✅ Clean releases page (only latest version)
- ✅ GitHub Actions ready for public releases
- ✅ Repository ready for public visibility

---

## Timeline & Estimates

| Phase | Duration | Type |
|-------|----------|------|
| Worktree setup | 5 min | Manual |
| Phase 1: Critical fixes | 1-1.5 hours | Agent work + review |
| Phase 2: Parallel cleanup | 45 min - 1 hour | 3 agents + review |
| Phase 3: Polish | 30-45 min | Agent work + review |
| Git history cleanup | 15 min | Automated |
| Verification & testing | 30 min | Manual |
| Merge & tag | 10 min | Manual |
| **Total** | **3-4 hours** | Mostly automated |

**Review checkpoints add ~15 min each** (3 checkpoints = 45 min total, included in estimates above)

---

## Notes

### Why Worktrees Over Branches?
- **Isolation:** Separate directory means main workspace stays pristine
- **Safety:** Can delete entire worktree if things go wrong
- **Convenience:** Can compare both directories side-by-side
- **Flexibility:** Can run/test both versions simultaneously

### Why Backup Branch?
- **Insurance:** Git history cleanup is destructive
- **Rollback:** Can restore to pre-cleanup state if needed
- **Comparison:** Can diff against original if verification fails

### Why Phase the Work?
- **Review at logical boundaries:** Easier than one giant changeset
- **Incremental progress:** Can stop/resume between phases
- **Risk management:** Critical fixes done carefully, cleanup can be parallelized
- **Clear commits:** Each phase = one focused commit

### Post-Launch
After making repo public:
- Monitor GitHub Issues for bugs
- Watch GitHub Releases for download metrics
- Consider adding CONTRIBUTORS.md as people contribute
- Update README badges (build status, downloads, etc.)
