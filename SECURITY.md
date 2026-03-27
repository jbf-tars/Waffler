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
- **Microphone audio** — sent to OpenAI/Groq via the user's own API key
- **API keys** — stored in a local `.env` file
- **Transcription history** — stored locally on disk

We take the security of this data seriously. Relevant areas include:
- Credential handling and storage
- Audio data transmission
- Local file permissions
- Build and supply chain integrity

## Supported Versions

We provide security fixes for the latest release only.

| Version | Supported |
|---------|-----------|
| Latest  | Yes       |
| Older   | No        |

## Responsible Disclosure

We ask that you give us reasonable time to address the issue before public disclosure. We're happy to credit you in the release notes (unless you prefer to remain anonymous).
