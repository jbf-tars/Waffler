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

1. **Explicit trigger:** If the transcript begins with the word "email" followed by a greeting (e.g., "email hi Louis" or "email dear team"), treat the content as an email and strip the trigger word from the output. If "email" appears at the start but is followed by non-greeting content (e.g., "email is the worst form of communication"), do not treat it as a trigger — process as normal prose.
2. **Auto-detect:** If the transcript contains an email-style greeting (e.g., "Hi Louis", "Dear Sarah", "Hey team", "Hi all", "Hello", "Good morning") AND ends with a sign-off pattern (e.g., "thanks", "regards", "cheers", "kind regards", "best" optionally followed by a name), format it as an email.

Auto-detect is the primary mechanism. The explicit trigger is a convenience for edge cases where the content might not clearly read as an email.

**Partial matches:** If the transcript has a greeting but no sign-off (e.g., "Hi Louis, here is the document you asked for"), format it as normal prose. Both signals (greeting + sign-off) are required for auto-detect. The explicit "email" trigger overrides this — if the trigger is present, format as an email even without a sign-off.

**False positives:** It is acceptable for the LLM to occasionally format a non-email as an email if it strongly matches the pattern. This is a low-risk outcome — the user gets slightly more structured output, not broken output.

## Formatting Rules (When Email Detected)

- Greeting on its own line, followed by a blank line.
- Body split into logical paragraphs separated by blank lines (same logic as existing paragraph rules).
- Sign-off separated from the body by a blank line. The sign-off is the final closing phrase(s) and name — not full sentences. Full sentences before the closing belong to the body.
- The LLM determines the natural shape of the sign-off, including capitalisation and line breaks, as it would in a real email.
- The email greeting is NOT considered a "header" or "title" — it does not conflict with the existing "Do NOT add headers or titles" rule.

## Examples

### Auto-detect (greeting + sign-off)

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

### Explicit trigger

**Input transcript:**
> "Email hey team, quick update on the project. We've finished the first phase and will be moving to phase two next week. Let me know if you have any questions."

**Expected output:**
```
Hey team,

Quick update on the project. We've finished the first phase and will be moving to phase two next week.

Let me know if you have any questions.
```

(Note: no sign-off in dictation, but trigger word forces email formatting. Trigger word "Email" stripped from output.)

## What Stays the Same

- All existing content preservation rules.
- Self-correction handling.
- Disfluency removal.
- List detection (an email body can still contain a list).
- Dialect support.
- No headers, labels, or classification in the output (email greeting is not a header).

## Out of Scope

- Reply/forward patterns (e.g., "Regarding your email below...") — no special handling.
- Other structured content types (Slack messages, letters, memos) — emails only for now.

## Scope

- Normal prompt mode only (`prompts/normal.txt`).
- Single file change.

## Placement in Prompt

New "Email detection" block inserted after the existing formatting rules (lines 3-8 of current prompt, ending with the sentence about splitting overly long sentences) and before "Self-correction handling:" (line 9 of current prompt).
