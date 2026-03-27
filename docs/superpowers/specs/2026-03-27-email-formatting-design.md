# Email-Aware Formatting in Normal Prompt

## Goal

Enable the Waffler "normal" prompt to detect when a user is dictating an email and format the output with proper email structure (greeting, body paragraphs, sign-off) instead of flat prose.

## Problem

Currently, dictating an email like:

> "Hi Louis, thanks for your email and I hope you're doing well. Would you be able to send me over the copies of the transcript for the previous meeting and the PowerPoint slides accompanying it? Thanks, regards James."

Outputs a single paragraph. The user expects structured email formatting with line breaks between the greeting, body paragraphs, and sign-off.

## Approach

Prompt-only change to `prompts/normal.txt`. No code changes required.

## Detection Logic

Two mechanisms, both handled by the LLM via prompt rules:

1. **Explicit trigger:** If the transcript begins with the word "email" (case-insensitive), treat the content as an email and strip the trigger word from the output.
2. **Auto-detect:** If the transcript contains a greeting addressed to a person (e.g., "Hi Louis", "Dear Sarah", "Hey team") AND ends with a sign-off pattern (e.g., "thanks", "regards", "cheers", "kind regards" followed by a name), format it as an email.

Auto-detect is the primary mechanism. The explicit trigger is a convenience for edge cases where the content might not clearly read as an email.

## Formatting Rules (When Email Detected)

- Greeting on its own line, followed by a blank line.
- Body split into logical paragraphs separated by blank lines (same logic as existing paragraph rules).
- Sign-off separated from the body by a blank line.
- The LLM determines the natural shape of the sign-off (e.g., whether "Thanks, regards" is one line or split across two).

## Example

**Input transcript:**
> "Hi Louis, thanks for your email and I hope you're doing well. Would you be able to send me over the copies of the transcript for the previous meeting and the PowerPoint slides accompanying it? Thanks, regards James."

**Expected output:**
```
Hi Louis,

Thanks for your email and I hope you're doing well.

Would you be able to send me over the copies of the transcript for the previous meeting and the PowerPoint slides accompanying it?

Thanks,
Regards,
James
```

## What Stays the Same

- All existing content preservation rules.
- Self-correction handling.
- Disfluency removal.
- List detection (an email body can still contain a list).
- Dialect support.
- No headers, labels, or classification in the output.

## Scope

- Normal prompt mode only (`prompts/normal.txt`).
- Single file change.

## Placement in Prompt

New "Email detection" block inserted between the existing "Formatting rules" section and the "Self-correction handling" section.
