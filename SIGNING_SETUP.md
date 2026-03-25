# Code Signing Setup Guide

Once you receive your Apple Developer certificate (within 24 hours), follow these steps:

## Step 1: Get Your Certificate in Xcode

1. Open **Xcode**
2. Go to **Settings** → **Accounts**
3. Click your Apple ID
4. Click **Manage Certificates**
5. Click **+** → **Developer ID Application**
6. Certificate will download automatically

## Step 2: Find Your Signing Identity

Run this command to see your certificate name:
```bash
security find-identity -v -p codesigning
```

Look for a line like:
```
1) ABC123... "Developer ID Application: Your Name (TEAM123)"
```

Copy the **full name in quotes**: `Developer ID Application: Your Name (TEAM123)`

## Step 3: Test Local Signing

Update the signing identity in `Waffler_mac.spec` (line ~138):
```python
# FROM:
icon='icon.icns',

# TO:
icon='icon.icns',
codesign_identity='Developer ID Application: Your Name (TEAM123)',
entitlements_file='entitlements.plist',
```

Then build and test:
```bash
python3 -m PyInstaller --clean Waffler_mac.spec
```

Install to /Applications and test if Fn key works!

## Step 4: Set Up GitHub Actions Signing

### Export Certificate

```bash
# Export certificate + private key to .p12 file
# Xcode → Settings → Accounts → Manage Certificates
# Right-click certificate → Export
# Save as: Waffler_Signing.p12
# Set a password (remember it!)
```

### Encode for GitHub Secrets

```bash
base64 -i Waffler_Signing.p12 | pbcopy
# This copies the base64 string to clipboard
```

### Add GitHub Secrets

Go to: https://github.com/jbf-tars/waffler/settings/secrets/actions

Add these secrets:
- `DEVELOPER_ID_APPLICATION_CERT` = the base64 string (from clipboard)
- `DEVELOPER_ID_APPLICATION_PASSWORD` = the password you set for .p12
- `SIGNING_IDENTITY` = `Developer ID Application: Your Name (TEAM123)`

## Step 5: Push a New Release

```bash
git tag v3.0.0 -m "First signed release"
git push origin v3.0.0
```

The GitHub Action will:
1. Import your certificate
2. Sign the app with your Developer ID
3. Create a DMG
4. Upload to GitHub Releases

## Step 6: Test Downloaded Build

Download the DMG from GitHub releases and test:
- Should install without Gatekeeper warnings
- Fn key should work immediately in /Applications
- Permissions should persist across updates

---

## Troubleshooting

**"codesign failed"** - Check signing identity name matches exactly

**"unable to find valid certificate"** - Make sure certificate is installed in login keychain

**Permissions still not working** - Check entitlements.plist is included in build

**Need help?** Just ask! 🚀
