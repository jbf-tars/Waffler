# VoiceFlow for Windows (Early Testers)

## Install (no Python needed)

1. Download `VoiceFlow-Setup-<version>.exe` from Releases
2. Run installer
3. Launch **VoiceFlow** from Start menu
4. Add your OpenAI API key in Settings

> This early-tester build is **unsigned**. Windows may show SmartScreen warnings.
> Click **More info → Run anyway**.

---

## For Developers: release a new installer

### One-time setup
- Push this project to GitHub
- Ensure workflow file exists: `.github/workflows/windows-release.yml`

### Release flow
```bash
git add .
git commit -m "VoiceFlow: your change"
git push

# create release tag
git tag v1.0.1
git push origin v1.0.1
```

GitHub Actions will:
1. Build VoiceFlow on `windows-latest`
2. Package installer with Inno Setup
3. Upload `VoiceFlow-Setup-*.exe` to GitHub Release

Use that `.exe` link on your website download button.

---

## Known behavior
- Auto-paste now uses a debounced Windows paste path to prevent duplicate 3–4x pastes.
- If duplicate paste still appears in a specific app, disable **Auto-paste** in Settings for that app.
