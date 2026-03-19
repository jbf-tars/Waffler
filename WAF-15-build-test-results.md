# WAF-15: PyInstaller EXE Build Test Results
**Date:** 2026-03-12  
**Machine:** Windows 11 Home (10.0.26200), x64  
**Agent:** PC Dev (Paperclip)

## Environment
| Item | Value |
|---|---|
| Python | 3.13.5 |
| PyInstaller | 6.19.0 |
| OS | Windows 11 Home |

## Build Result: PASS

```
pyinstaller Waffler_windows.spec --noconfirm
Build complete! Results in: C:\Users\james\waffler\dist
```

- EXE: `dist\Waffler\Waffler.exe` — 35.4 MB (37,105,742 bytes)
- Total dist folder: ~323 MB
- Build time: ~3 minutes

## Asset Verification: PASS

All required files present in `_internal/`:

| Asset | Status |
|---|---|
| `ui/index.html` | OK |
| `ui/overlay.html` | OK |
| `prompts/normal.txt` | OK |
| `prompts/smart.txt` | OK |
| `config.yaml` | OK |
| `src/audio.py` | OK |

## Warnings (non-blocking)
- `missing module named posix/termios/pwd/grp` — expected on Windows, all are Unix-only
- `missing module named collections.abc` — false positive, standard lib is always available
- All other warnings are conditional imports in third-party libs (pandas, scipy, trio) — do not affect runtime

## SmartScreen / Antivirus
- EXE is **unsigned** (no Authenticode signature)
- **Windows SmartScreen WILL block** first run with: "Windows protected your PC"
- User workaround: Click "More info" → "Run anyway"
- Long-term fix: Code-sign with a certificate (EV cert ~$300/yr eliminates SmartScreen)
- AV false positives: PyInstaller-bundled EXEs are commonly flagged by Defender/VirusTotal
  - Mitigation: Submit to Microsoft for analysis, or use `--key` to encrypt bytecode

## Spec Issue: `ui/` directory path
- Spec references `('ui', 'ui')` but `ui/` does not exist at repo root
- UI files live in `src/` (mixed with Python modules)
- Build still succeeds because PyInstaller silently skips missing source dirs
- The correct files end up in `_internal/ui/` via the `('src', 'src')` entry copying them
- **Recommendation:** Separate UI files into a top-level `ui/` directory OR update spec to `('src/app.js', 'ui')` etc.

## Python 3.13 Compatibility
- Build and bundle completed successfully on Python 3.13.5
- Spec/README targets Python 3.10+ — 3.13 works but is newer than documented minimum
- No runtime test performed (requires display + audio hardware)

## Limitations
- EXE launch not tested interactively (requires WebView2/Edge Chromium + audio device)
- Antivirus false positive rate not measured (VirusTotal scan recommended)
- Windows 10 compatibility not verified (only tested on Windows 11)

## Next Recommendations
1. Add `dist/` and `build/` to `.gitignore` if not already present
2. Separate UI files to top-level `ui/` directory for cleaner spec
3. Obtain code-signing certificate to eliminate SmartScreen warnings
4. Add `build_windows.bat` CI step to GitHub Actions (WAF-4/WAF-6) on a Windows runner
5. Submit EXE to Windows Defender portal if AV false positives are reported
