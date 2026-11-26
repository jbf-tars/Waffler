# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Waffler, **please do not open a public issue.**

Instead, report it privately using GitHub's [private vulnerability reporting](https://github.com/jbf-tars/waffler/security/advisories/new).

Please include:
- A description of the vulnerability
- Steps to reproduce
- The potential impact
- Any suggested fix (optional)

We aim to acknowledge reports within 48 hours and provide a fix or mitigation plan within 7 days.

## Scope

Waffler handles sensitive data including:
- **Microphone audio** (dictations and meetings, and for meetings the computer's own sound): turned into text on the computer with "On this computer", or sent to Groq or OpenAI with the user's own API key, never routed through any Waffler-controlled server. Only with Waffler Plus, for an account that has it and has chosen it, does audio go to the Waffler service, which keeps none of it
- **API keys**: stored in a local `.env` file in Waffler's data folder, created readable by its owner only
- **Transcription history**: stored locally in the data folder (`history.json`), including any corrected text the user saves with Edit text
- **Meetings**: recordings, transcripts and notes stored locally in the data folder (`meetings/`). With the optional account, transcripts and notes (never recordings) are kept in the account so the user's other devices have them
- **Account session**: a sign-in token in the data folder (`account.json`), readable by the user only (on Windows through its permissions list); the Claude link to an account is a secret that can read every meeting in it

The data folder is `~/.waffler-hosted` in the user's home folder (on Windows, `%USERPROFILE%\.waffler-hosted`); on a Mac it is readable by its owner only.

We take the security of this data seriously. Relevant areas include:
- Credential handling and storage
- Audio data transmission
- Local file permissions
- Build and supply chain integrity (updates are taken only from Waffler's own releases and, on a Mac, must be signed by Waffler's own developer team)
- The window's bridge to the app (an allow-list of methods and a Content-Security-Policy)

## Supported Versions

We provide security fixes for the latest release only.

| Version | Supported |
|---------|-----------|
| Latest  | Yes       |
| Older   | No        |

## Responsible Disclosure

We ask that you give us reasonable time to address the issue before public disclosure. We're happy to credit you in the release notes (unless you prefer to remain anonymous).
