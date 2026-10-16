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

- **Microphone.** The microphone stays ready while Waffler runs, so your
  first word isn't clipped. Each recording is what you say while you hold
  the push-to-talk hotkey, plus the half-second before you pressed it. It is
  sent to the speech-to-text provider you configured (Groq or
  OpenAI) to be transcribed, and to the cleanup provider to be tidied into
  readable text. It is not sent anywhere else. If you switch on the optional
  sounds (off by default), Waffler plays a short tone through your speakers and
  silences the microphone for the fraction of a second the tone sounds in, so a
  tone is never part of a recording.
- **Keyboard.** A global low-level keyboard hook
  (`SetWindowsHookEx(WH_KEYBOARD_LL)` on Windows) detects your hotkey being
  pressed and released. Only the configured hotkey combination is acted on.
  Keystroke content is not recorded, stored or transmitted.
- **Clipboard.** Finished transcripts are placed on your clipboard so they can
  be pasted where you are typing. On macOS, unless you choose to keep your
  dictation on the clipboard (Settings, General), what you had copied is held
  in memory for about a second while Waffler pastes, then put back; it is
  never stored or logged. During first-run setup only, when you return
  to the setup window Waffler checks the clipboard once for an API key you have
  just copied, so you do not have to paste it by hand; anything that is not
  shaped like an API key is ignored and never read into the app.
- **Text field (macOS).** To confirm that a paste landed, Waffler uses the
  Accessibility permission to read whether the text field you are typing in
  changed (a fingerprint of its text, compared before and after). It never
  reads a password field, and it never stores, logs or sends that text.
- **Text before your cursor (macOS, only with an email signature on).** If you
  turn on the email signature (Settings, Style) and dictate into an email app,
  Waffler uses the Accessibility permission to read the text before your
  cursor, to see whether your signature is already there. It never reads a
  password field. That text is compared with your signature and dropped: it is
  never stored, logged or sent. If an app does not show its text, Waffler
  remembers instead, in memory only, that it added a signature in that app
  in the last ten minutes. The signature is a setting kept on your computer;
  its text is left out of the logs bundle. With the signature off, none of
  this happens.
- **Selected text (Transforms, only when you use it).** Transforms (Settings,
  Transforms) is off until you turn it on. When you then press its shortcut,
  or choose "Transform selected text" in the menu bar or the tray, Waffler
  reads the text selected in the app in front: on a Mac through the
  Accessibility permission, or, where an app does not share it, by pressing
  Copy and putting your clipboard back (held in memory for about a second); on
  Windows by pressing Ctrl+C the same way. It never reads a password field and
  does nothing at any other time. That text, with your rules, is sent to the
  clean-up provider you configured (Groq, OpenAI or Cerebras) to be rewritten;
  the rewrite then replaces your selection by an ordinary paste. Recordings and
  keys are not sent. The text before and after is kept in your history, and
  deleted with it. Your own rules, outline and transforms are settings on your
  computer, and their wording is left out of the logs bundle. The shortcut is
  read by the same keyboard listener as your hotkey and only watches for that
  one combination.
- **What you say, and the text around it (Command mode, only while you hold
  its shortcut).** Command mode (Settings, Command mode) is off until you turn
  it on. When you hold its shortcut, Waffler records what you say and sends
  that recording to the speech provider you configured, as it does for a
  dictation, to be turned into text; the recording is not kept. It then reads
  the text selected in the app in front (through the Accessibility permission
  or, where an app does not share it, by pressing Copy and putting your
  clipboard back) or, when nothing is selected, up to 500 characters before
  your cursor through the Accessibility permission. It never reads a password
  field and does nothing at any other time. What you said and that text are sent
  to the clean-up provider you configured to be carried out. If you asked a
  question, Waffler opens your browser at the search engine you chose with only
  your spoken question in the address: the selected text and the text before the
  cursor are never put there. What you said, the text before and after and the
  question are kept in your history and deleted with it. The Command shortcut is
  read by the same keyboard listener as your hotkey and only watches for that one
  combination.
- **The app you are in.** When you press the hotkey, Waffler notes which app
  is in front: its name (and, on a Mac, its identifier) and its kind, which it
  works out from a list built into Waffler: email, work chat, personal chat or
  other. This stays on your computer. It is kept with the history entry (see
  below) and is not sent to your speech or clean-up provider. For a web browser
  only, Waffler uses the Accessibility permission to read the title of the
  front window, to tell whether the tab is a well-known webmail or chat site.
  That title is held in memory for that moment and is never stored, logged or
  sent, and the address of the page is never read.
- **On your machine.** Your API keys, settings, dictation history and the most
  recent recordings are stored under `~/.waffler-hosted/` on your own computer.
  Recordings are kept to diagnose transcription faults, to redo a take from
  the Journal and to bring back a dictation that a crash cut short; they are
  capped at the last 10, and can be switched off or deleted at any time in Settings,
  Privacy and data, which can also delete your history, usage and logs. Each
  history entry notes, besides the words, the app you were in and its kind, the language,
  how long the recording was and which services handled it. You can delete any
  single entry (its recording goes with it) and export your history as a file
  on your Desktop in Settings, Privacy and data; that file is your own copy and
  is not removed by "Delete all my data". Small files remember which tips
  Waffler has shown or you have hidden (`notifications.json`: counts and times,
  no text), which new microphones it has asked you about and what you answered
  (`mic_devices.json`: device names and your answers, no text), what each call
  cost (`usage.jsonl`: amounts and times, never what
  was said) and which recording was being turned into text, so a take that a
  crash cut short can come back (`inflight.json`: a file name and a time, gone
  when the dictation ends). None of this is uploaded.
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
