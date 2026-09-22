# Code signing policy

This document exists because the SignPath Foundation requires a published code
signing policy for the open-source projects it signs. It describes who can
release Waffler, how releases are built, and what the app does with your data.

Code signing provided by [SignPath.io](https://signpath.io/), certificate by
[SignPath Foundation](https://signpath.org/).

## Project

- **Waffler** — push-to-talk voice dictation for Windows and macOS
- Source: <https://github.com/jbf-tars/Waffler> (MIT licence)
- Downloads: <https://wafflerai.com/download/>

## Team roles

Waffler is a single-maintainer project.

| Role | Who |
|---|---|
| Author | James Farrelly (repository owner) |
| Reviewer | James Farrelly |
| Approver | James Farrelly |

The person responsible for code signing is the same person responsible for
development and for the source repository. Two-factor authentication is
enabled on the GitHub account that owns the repository and authorises releases.

## How releases are built

Releases are never built or signed on a developer machine. A release is cut by
pushing a `vX.Y.Z` tag, which triggers GitHub Actions workflows
(`.github/workflows/windows-release.yml` and the macOS equivalent) that build
from the tagged commit in the public repository. The workflow definitions are
public and auditable. Every published asset carries a SHA-256 digest recorded
by GitHub, which anyone can verify against their download.

Only artifacts built from this repository's own source are submitted for
signing.

## Privacy

Waffler is a dictation tool, so it necessarily handles your speech, your
keyboard and your clipboard. Specifically:

- **Microphone.** Audio is captured only while you hold the push-to-talk
  hotkey. It is sent to the speech-to-text provider you configured (Groq or
  OpenAI) to be transcribed, and to the cleanup provider to be tidied into
  readable text. It is not sent anywhere else.
- **Keyboard.** A global low-level keyboard hook
  (`SetWindowsHookEx(WH_KEYBOARD_LL)` on Windows) detects your hotkey being
  pressed and released. Only the configured hotkey combination is acted on.
  Keystroke content is not recorded, stored or transmitted.
- **Clipboard.** Finished transcripts are placed on your clipboard so they can
  be pasted where you are typing. During first-run setup only, when you return
  to the setup window Waffler checks the clipboard once for an API key you have
  just copied, so you do not have to paste it by hand; anything that is not
  shaped like an API key is ignored and never read into the app.
- **On your machine.** Your API keys, settings, dictation history and the most
  recent recordings are stored under `~/.waffler-hosted/` on your own computer.
  Recordings are kept only to diagnose transcription faults, are capped at the
  last 10, and can be deleted at any time. None of this is uploaded.
- **No telemetry.** Waffler has no analytics and no usage reporting.

## Uninstallation

Windows: standard Inno Setup uninstaller, via Settings → Apps → Waffler.
macOS: drag `Waffler.app` to the Trash.

Either way, removing the app leaves `~/.waffler-hosted/` in place so your
history and keys survive a reinstall. Delete that folder to remove everything.

## Changes to the system

Waffler installs to the per-user application directory and adds a Start Menu
entry, plus a desktop shortcut if you tick that option during setup
(`installer/windows/Waffler.iss`). It does **not** add a launch-at-login or
Startup entry, does not write to the `Run` registry key, installs no drivers
or services, and does not modify system-wide settings. The global hotkey is
registered by the running process and released when you quit.

Material changes are recorded in [CHANGELOG.md](../CHANGELOG.md).
