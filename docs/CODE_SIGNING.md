# Code signing the Windows installer

## Why this matters

The Windows installer is unsigned. Every new user therefore meets this before
they have seen anything the app does:

> **Windows protected your PC**
> Microsoft Defender SmartScreen prevented an unrecognised app from starting.
> Publisher: Unknown publisher

To continue they must click *More info*, then *Run anyway*. A lot of people do
not, and reasonably so: they have been told the software is untrusted.

This is the highest-impact remaining fix to setup, and it is not a code
problem. No amount of wizard polish outranks the first thing a user sees.

The macOS build does not have this problem: it is already signed with a
Developer ID certificate and notarised by Apple, so Gatekeeper stays quiet.

## What to buy

Two realistic options.

### Azure Trusted Signing (recommended)

- About **$9.99/month**, billed through an Azure subscription.
- No hardware token, no USB dongle to keep safe.
- Identity validation takes a few days; individuals are eligible but must have
  a verifiable public presence, and the requirements are stricter than for
  registered organisations.
- Certificates are short-lived and rotated for you, which suits CI.

### A traditional OV certificate

- Roughly **£200-400/year** from Sectigo, DigiCert and similar resellers.
- Since June 2023 the private key must live on a hardware token or an approved
  HSM, so signing from CI needs a cloud HSM or a self-hosted runner with the
  token attached. That is meaningfully more work than the option above.
- An EV certificate costs more and used to grant instant SmartScreen
  reputation. That advantage has largely gone; OV plus accumulated download
  reputation gets to the same place.

Either way, reputation still builds over time. Signing removes the "unknown
publisher" wording immediately, and the remaining "not commonly downloaded"
prompt fades as downloads accumulate under a stable identity.

## Wiring it up

The release workflow already has the signing step. It is skipped, with a note
in the log, until the secrets exist, so nothing breaks in the meantime.

Add two repository secrets under **Settings, Secrets and variables, Actions**:

| Secret | Value |
|---|---|
| `WINDOWS_CERT_PFX` | the `.pfx` file, base64 encoded |
| `WINDOWS_CERT_PASSWORD` | the password protecting that `.pfx` |

To produce the first value:

```bash
base64 -w0 certificate.pfx > cert.b64     # Linux/macOS
```

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("certificate.pfx")) | Set-Clipboard
```

Paste the result as the secret value. Nothing else needs to change: the next
tagged build signs and verifies the installer automatically, and fails the
build if signing is configured but does not verify.

Azure Trusted Signing does not issue a `.pfx`. If you go that route, replace
the signtool invocation with the `azure/trusted-signing-action` action and
supply its Azure credentials instead; the surrounding conditional can stay.

## Checking it worked

```powershell
Get-AuthenticodeSignature .\Waffler-Setup-<version>.exe | Format-List Status, SignerCertificate
```

`Status` should read `Valid`. The in-app updater already reports the
Authenticode status of anything it downloads, so `app.log` will start saying
`Authenticode status: Valid (signed)` instead of `NotSigned` once this is live.

## What signing does not fix

- It does not remove the macOS Gatekeeper flow, which is already handled
  separately by Apple notarisation.
- It does not change the SmartScreen "not commonly downloaded" prompt on day
  one. That is reputation, and it accrues.
- It is not a substitute for the SHA-256 digest check the updater performs.
  That verifies the bytes match the published release; signing attests to who
  built them. They answer different questions and both are worth having.
