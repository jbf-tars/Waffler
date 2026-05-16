# Waffler Release Runbook

Exact procedure for shipping a new Waffler version. Both platforms build from
a single `v*` tag push; the website's download links are bumped from a
separate repo.

---

## 0) Preconditions

Clean working tree on `main`:

```bash
git checkout main
git pull origin main
git status
```

CI workflows exist and pass on `main`:

- `.github/workflows/ci.yml` — per-push CHANGELOG-version match + 4 test guards + doc-drift
- `.github/workflows/macos-release.yml` — fires on `v*` tag, produces signed DMG
- `.github/workflows/windows-release.yml` — fires on `v*` tag, produces Inno Setup EXE

`gh` CLI authenticated:

```bash
gh auth status
```

---

## 1) Bump `__version__` AND add a CHANGELOG entry

Both must happen in the same commit. CI's `CHANGELOG matches src/__init__.py version`
step (added in v3.14.39) fails the build if `__version__` advances without a
matching `## [X.Y.Z]` section in `CHANGELOG.md`.

```bash
# 1) edit src/__init__.py — bump __version__ = "X.Y.Z"
$EDITOR src/__init__.py

# 2) edit CHANGELOG.md — add a new section at the top:
#      ## [X.Y.Z] - YYYY-MM-DD
#      ### Fixed / ### Added / ### Changed
$EDITOR CHANGELOG.md

# 3) verify both line up locally
VER=$(grep -oE '"[0-9]+\.[0-9]+\.[0-9]+"' src/__init__.py | tr -d '"' | head -1)
grep -q "^## \[$VER\]" CHANGELOG.md && echo "✓ v$VER in CHANGELOG" || echo "✗ MISSING"

# 4) commit + push the bump (no tag yet)
git add src/__init__.py CHANGELOG.md
git commit -m "release: vX.Y.Z — <one-line summary>"
git push origin main
```

The push triggers `ci.yml`. Wait for it to pass before tagging:

```bash
gh run watch
```

---

## 2) Tag + push (this triggers the actual release builds)

```bash
git tag -a vX.Y.Z -m "vX.Y.Z — <one-line summary>"
git push origin vX.Y.Z
```

What happens:

- **macOS workflow** builds the DMG, signs it with an Apple Developer ID,
  notarizes it via `notarytool`, and uploads `Waffler-X.Y.Z-mac.dmg` to the
  GitHub release.
- **Windows workflow** builds the Inno Setup installer and uploads
  `Waffler-Setup-X.Y.Z.exe` to the GitHub release.

Both run for ~3 minutes. Watch them:

```bash
gh run list --workflow=macos-release.yml --limit 1
gh run list --workflow=windows-release.yml --limit 1
```

To rerun manually without re-tagging (both workflows have `workflow_dispatch`):

```bash
gh workflow run macos-release.yml --ref vX.Y.Z
gh workflow run windows-release.yml --ref vX.Y.Z
```

---

## 3) Verify both artifacts are live

```bash
gh release view vX.Y.Z --json assets \
  | python3 -c "import json,sys; [print(f\"  {a['name']} ({a['size']:,} bytes)\") for a in json.load(sys.stdin)['assets']]"
```

Expected output:

```
  Waffler-X.Y.Z-mac.dmg          (~24 MB)
  Waffler-Setup-X.Y.Z.exe        (~32 MB)
```

Sanity-check the macOS DMG opens cleanly (no Gatekeeper warning — current
DMGs have been signed + notarized since v3.8.5):

```bash
curl -sSL -o /tmp/Waffler-test.dmg \
  "https://github.com/jbf-tars/Waffler/releases/download/vX.Y.Z/Waffler-X.Y.Z-mac.dmg"
hdiutil verify /tmp/Waffler-test.dmg
# expected: "checksum ... is VALID"
```

---

## 4) Bump the website download links

The website lives in the separate `jbf-tars/waffler-website` repo. The
download buttons point at a hardcoded version in `src/data/release.ts`. Use
the one-liner script (added in v3.14.39, see `waffler-website/scripts/bump-release.mjs`):

```bash
cd /Users/james/waffler-website
git pull origin main
npm install                          # one-off
npm run bump-release                 # auto-fetches latest stable tag from Waffler repo
git add src/data/release.ts
git commit -m "Bump release.ts to vX.Y.Z"
git push origin main
```

The script:

1. Queries `https://api.github.com/repos/jbf-tars/Waffler/releases` for the
   latest non-prerelease tag.
2. HEAD-checks both the macOS DMG and Windows EXE assets exist for that
   version (refuses to bump to a broken release).
3. Rewrites `src/data/release.ts` with the new version, date, and download
   URLs.

The push to `main` triggers Cloudflare Pages deploy (see `DEPLOYMENT.md` in
the website repo for the secret-setup and recovery procedure if the deploy
fails).

Smoke-test the live site:

```bash
curl -s https://wafflerai.com | grep -oE 'Waffler-[0-9.]+-mac\.dmg' | head -1
# expected: Waffler-X.Y.Z-mac.dmg
```

---

## 5) Post-release verification checklist

- [ ] CI workflow on the release commit passes (CHANGELOG match, tests, doc-drift)
- [ ] Tag `vX.Y.Z` exists on GitHub
- [ ] macOS release workflow succeeded
- [ ] Windows release workflow succeeded
- [ ] `Waffler-X.Y.Z-mac.dmg` attached to the release (signed + notarized)
- [ ] `Waffler-Setup-X.Y.Z.exe` attached to the release
- [ ] Website `release.ts` bumped, Cloudflare Pages deploy succeeded
- [ ] Fresh download tested on both platforms (no Gatekeeper warning on Mac;
      SmartScreen warning on Windows is expected — see Troubleshooting below)

---

## Troubleshooting

### A) Windows SmartScreen warning (still unsigned)

**Symptom:** "Windows protected your PC" on first launch of the installer.

**What to tell users:** Click **More info** → **Run anyway**.

Windows builds are not yet code-signed. The long-term fix is acquiring a
Windows code-signing certificate; until then SmartScreen will warn until
the installer earns enough reputation organically.

### B) CI release workflow fails

Most common causes:

- **CHANGELOG mismatch** — `__version__` was bumped without a `## [X.Y.Z]`
  section in `CHANGELOG.md`. Edit `CHANGELOG.md`, commit, push.
- **Test flake** — `tests/test_fn_handler_chatter.py` has timing-sensitive
  cases. The one that's most likely to flake (`test_18_07_oscillation_pattern_replay`)
  is already skipped on CI (`if os.environ.get("CI"): return`).
  If a different test flakes, look at recent edits to `src/audio.py` or
  `src/mac_hotkey_monitor.py`.
- **Doc-drift guard fired** — see the error message in the failed log;
  the guard prints the exact remediation line per match. Add
  `# doc-drift-ok` to legitimate historical references; otherwise fix the
  stale claim.

### C) Website deploy fails

See `waffler-website/DEPLOYMENT.md`. Almost always missing Cloudflare
secrets (`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`); the fail-fast
step in `deploy.yml` prints the exact `gh secret set` commands.

---

## Release announcement template

```text
Waffler vX.Y.Z is live 🥞

Downloads (latest, signed):
- macOS: https://github.com/jbf-tars/Waffler/releases/download/vX.Y.Z/Waffler-X.Y.Z-mac.dmg
- Windows: https://github.com/jbf-tars/Waffler/releases/download/vX.Y.Z/Waffler-Setup-X.Y.Z.exe
- Direct from wafflerai.com (auto-detected per OS)

Highlights:
- <one-bullet from the CHANGELOG ## [X.Y.Z] entry>
- <one-bullet from the CHANGELOG ## [X.Y.Z] entry>

Full notes: https://github.com/jbf-tars/Waffler/releases/tag/vX.Y.Z
```

macOS users won't see a Gatekeeper warning — the DMG is signed with our
Apple Developer ID and notarized by Apple. Windows users will still see
SmartScreen on first install (see Troubleshooting A).
