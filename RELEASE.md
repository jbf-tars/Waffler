# Waffler Release Runbook

This file is the exact process to ship a new Waffler version for both Windows and macOS.

---

## 0) Preconditions

- You are on `main` with clean working tree:

```bash
git checkout main
git pull origin main
git status
```

- GitHub workflows exist:
  - `.github/workflows/windows-release.yml`
  - `.github/workflows/macos-release.yml`

- `gh` CLI is authenticated (optional but recommended):

```bash
gh auth status
```

---

## 1) Tag a version (this triggers CI automatically)

Pick a semver tag like `v1.2.3`.

```bash
# 1) commit release-ready changes
git add .
git commit -m "release: v1.2.3"

# 2) push main first
git push origin main

# 3) create and push tag
git tag v1.2.3
git push origin v1.2.3
```

What happens on tag push:
- **Windows workflow** builds installer `.exe`, uploads artifact, and attaches it to GitHub Release.
- **macOS workflow** builds unsigned `.dmg`, uploads artifact, and attaches it to GitHub Release.

Both workflows are configured to run on `push.tags: v*`.

---

## 2) Trigger CI builds manually (if needed)

If you need to rerun without creating a new tag:

```bash
# Run workflows manually from Actions tab in GitHub UI
# (workflow_dispatch is enabled for both workflows)
```

Or with GitHub CLI:

```bash
gh workflow run windows-release.yml
gh workflow run macos-release.yml
```

Check build status:

```bash
gh run list --limit 10
```

---

## 3) Collect artifacts

### Option A: From GitHub Release (preferred)
1. Open: `https://github.com/<OWNER>/<REPO>/releases`
2. Open tag `v1.2.3`
3. Download assets:
   - `Waffler-Setup-*.exe`
   - `Waffler-mac-unsigned.dmg`

### Option B: From Actions artifacts
1. Open the successful workflow runs in Actions.
2. Download artifacts:
   - `Waffler-Setup` (Windows)
   - `Waffler-mac-unsigned` (macOS)

### Option C: With GitHub CLI

```bash
# download latest release assets to ./release-assets
gh release download v1.2.3 -D release-assets

# verify
ls -lh release-assets
```

---

## 3.5) Validate macOS DMG artifact integrity (recommended)

Use this before sharing the `.dmg` from Actions artifacts:

```bash
# Example: latest successful macOS run
RUN_ID=$(gh run list -R ns7v2h9k6h-web/waffler-app \
  --workflow "Build macOS App (unsigned for now)" \
  --status completed --json databaseId,conclusion \
  --jq '.[] | select(.conclusion=="success") | .databaseId' | head -n1)

# Download artifact
mkdir -p tmp/artifacts/macos-$RUN_ID
gh run download "$RUN_ID" -R ns7v2h9k6h-web/waffler-app \
  -n Waffler-mac-unsigned -D tmp/artifacts/macos-$RUN_ID

# Verify DMG checksum/container integrity
hdiutil verify tmp/artifacts/macos-$RUN_ID/Waffler-mac-unsigned.dmg

# Mount and confirm app bundle exists
MOUNT=$(hdiutil attach tmp/artifacts/macos-$RUN_ID/Waffler-mac-unsigned.dmg -nobrowse -readonly | awk '/\/Volumes\//{print $3; exit}')
ls -lah "$MOUNT"
test -d "$MOUNT/Waffler.app" && echo "Waffler.app present"
hdiutil detach "$MOUNT"
```

Expected:
- `hdiutil verify` ends with `checksum ... is VALID`
- Mounted volume contains `Waffler.app`

## 4) Update website download links

Update the website so each platform points to the **new release asset URLs**.

### 4.1 Get direct asset URLs
Use the GitHub release asset links in this format:

- Windows:
  - `https://github.com/<OWNER>/<REPO>/releases/download/v1.2.3/Waffler-Setup-1.2.3.exe`
- macOS:
  - `https://github.com/<OWNER>/<REPO>/releases/download/v1.2.3/Waffler-mac-unsigned.dmg`

> Exact filenames can vary slightly. Copy the final URLs directly from the release page.

### 4.2 Update links in site code
In this repo, update download buttons/links in website files (e.g. `landing-page/index.html` or your production site repo).

### 4.3 Deploy site
Commit/push the link update and deploy the website.

### 4.4 Smoke test
- Test Windows download link in a private/incognito window.
- Test macOS download link in a private/incognito window.
- Confirm both files download and launch instructions are correct.

---

## 5) Post-release verification checklist

- [ ] Tag exists on GitHub (`v1.2.3`)
- [ ] Windows workflow succeeded
- [ ] macOS workflow succeeded
- [ ] `.exe` attached to GitHub Release
- [ ] `.dmg` attached to GitHub Release
- [ ] Website links updated to new version
- [ ] Fresh download test passed on both platforms

---

## Troubleshooting

### A) Windows SmartScreen warning (unsigned installer)

**Symptom:** “Windows protected your PC” when opening installer.

**What to tell users:**
1. Click **More info**
2. Click **Run anyway**

Notes:
- This is expected for unsigned installers.
- Long-term fix is code signing certificate for Windows builds.

---

### B) macOS Gatekeeper warning (unsigned app/DMG)

**Symptom:** “App can’t be opened because Apple cannot check it for malicious software.”

**Quick user path:**
1. Try opening app once (expect block)
2. Go to **System Settings → Privacy & Security**
3. Click **Open Anyway** for Waffler

**Terminal workaround (power users):**

```bash
# remove quarantine recursively from extracted app folder
xattr -dr com.apple.quarantine "/path/to/Waffler-folder"
```

Repo helper script:

```bash
chmod +x fix_macos_gatekeeper.sh
./fix_macos_gatekeeper.sh "/path/to/Waffler-folder"
```

Notes:
- Current macOS artifact is intentionally **unsigned** (`Waffler-mac-unsigned.dmg`).
- Long-term fix is Apple Developer ID signing + notarization.

---

## Suggested release announcement template

```text
Waffler v1.2.3 is live 🎉

Downloads:
- Windows: <windows_url>
- macOS: <mac_url>

Notes:
- Windows/macOS builds are currently unsigned, so first-run security prompts are expected.
- Installation guidance is included in RELEASE.md and README-Windows.md.
```
