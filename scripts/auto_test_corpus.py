"""Automated prompt regression harness for Waffler.

Drives the styler directly with text inputs (skipping audio capture), runs
~30 cases covering:

  Lengths:    very-short / short / medium / long / very-long / extreme
  Categories: prose, numbered list, bulleted list, email, double-words,
              self-correction, hallucination-bait, code/technical

Each case declares a list of expected features ("must contain X",
"must NOT contain Y", word-retention range, structure shape, etc.).
A case PASSES iff every assertion holds.

Run:  python scripts/auto_test_corpus.py
Output: per-case verdict + pass rate + diff for every failure.
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

env = Path.home() / ".waffler-hosted" / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from style_openai import OpenAIStyler  # noqa: E402

# ── Helpers ─────────────────────────────────────────────────────────────────

def has_numbered_list(s: str) -> bool:
    return any(f"\n{n}. " in s or s.lstrip().startswith(f"{n}. ") for n in range(1, 10))

def has_bullets(s: str) -> bool:
    return "\n- " in s or s.lstrip().startswith("- ")

def has_email_shape(s: str) -> bool:
    """Greeting line + body + sign-off line, separated by blanks."""
    has_greet = bool(re.match(r"(?i)^(hi|hello|dear|hey)\b[^,\n]{0,40},?\s*\n", s))
    has_signoff = bool(re.search(r"(?i)\n\s*(?:cheers|thanks|best|regards|kind regards|warm regards|sincerely)[,\.]?\s*(?:\n[^\n]{0,40})?\s*\Z", s))
    return has_greet and has_signoff

# ── Test case definition ─────────────────────────────────────────────────────

@dataclass
class Case:
    label: str
    length: str          # very-short / short / medium / long / very-long / extreme
    category: str
    raw: str
    must_contain: List[str] = field(default_factory=list)        # case-insensitive substring asserts
    must_not_contain: List[str] = field(default_factory=list)
    must_match: List[str] = field(default_factory=list)          # regex asserts (any)
    must_not_match: List[str] = field(default_factory=list)
    retention_min: float = 0.0   # styled_words / raw_words >= this
    retention_max: float = 1.5   # styled_words / raw_words <= this
    custom: Optional[Callable[[str, str], Optional[str]]] = None  # (raw, styled) -> error or None
    expect: str = ""             # short human description of expected outcome


# ── The corpus ───────────────────────────────────────────────────────────────

CORPUS: List[Case] = [
    # ─── VERY-SHORT (1-3 words) ───────────────────────────────────────────
    Case("VS1 single hello",
         "very-short", "prose",
         "Hello.",
         must_contain=["hello"], retention_min=0.5,
         expect="trivially preserved"),
    Case("VS2 yeah affirm",
         "very-short", "prose",
         "Yeah.",
         retention_min=0.5,
         expect="trivially preserved"),
    Case("VS3 single word imperative",
         "very-short", "prose",
         "Sorted.",
         retention_min=0.5,
         expect="single word kept as-is"),
    Case("VS4 short question",
         "very-short", "prose",
         "Got a sec?",
         must_contain=["?"],
         retention_min=0.5,
         expect="question mark preserved on tiny clip"),
    Case("VS5 partial / clipped first word — typical short-clip failure",
         "very-short", "prose",
         "ello, can you hear me?",      # missing 'h' from "hello" — Whisper-on-clipped-mic
         must_contain=["?"],
         retention_min=0.5,
         expect="don't 'fix' a partial — keep what was actually heard, the user can re-record"),

    # ─── SHORT (4-10 words) — these bypass the LLM via _is_simple ─────────
    Case("S1 short clean prose",
         "short", "prose",
         "Can we meet tomorrow afternoon if possible?",
         must_contain=["meet tomorrow"], retention_min=0.7,
         expect="kept as-is by basic_clean"),
    Case("S2 short with um filler",
         "short", "prose",
         "Yeah, um, I think so.",
         must_not_contain=["um"], retention_min=0.4,
         expect="basic_clean strips um"),
    Case("S3 short with stutter",
         "short", "prose",
         "I I think we should ship it.",
         must_not_match=[r"\bI I\b", r"\bi i\b"],
         must_contain=["ship"],
         expect="strip 'I I' double-pronoun stutter"),
    Case("S4 short technical jargon",
         "short", "code",
         "Push to main, then deploy to prod.",
         must_contain=["main", "prod"],
         expect="don't expand or rephrase technical shorthand"),
    Case("S5 short with proper noun",
         "short", "prose",
         "Send it to Rohan by EOD.",
         must_contain=["Rohan", "EOD"],
         expect="proper noun and acronym preserved exactly"),
    Case("S6 short with discourse marker",
         "short", "prose",
         "So we're good then?",
         must_contain=["?"],
         retention_min=0.6,
         expect="connective 'so' kept, question preserved"),

    # ─── MEDIUM (11-30 words) ──────────────────────────────────────────────
    Case("M1 conversational prose",
         "medium", "prose",
         "Yeah, I do really like the job. I've kind of forced my way into the AI team now. I think it's the same with any job.",
         must_contain=["really like the job", "AI team"],
         must_not_contain=["yeah, i do"],
         retention_min=0.75, retention_max=1.05,
         expect="strip 'yeah' lead, preserve 'like' as verb"),
    Case("M2 numbered short",
         "medium", "numbered list",
         "Number one, check the database. Number two, verify the backups ran overnight.",
         must_match=[r"^\s*1\.\s*Check the database", r"^\s*2\.\s*Verify the backups"],
         must_not_contain=["number one", "number two"],
         expect="convert to 1./2. numbered list"),
    Case("M3 bullet three items",
         "medium", "bulleted list",
         "The three things we're optimising for are speed, accuracy, and cost.",
         custom=lambda raw, s: None if (has_bullets(s) and "speed" in s.lower() and "accuracy" in s.lower() and "cost" in s.lower()) else "expected bullet list with speed/accuracy/cost",
         expect="bullet list of three items with intro line"),
    Case("M4 prose with double words",
         "medium", "double-words",
         "I think that the the report needs needs to be sent to to the client by Friday.",
         must_not_match=[r"\bthe the\b", r"\bneeds needs\b", r"\bto to\b"],
         must_contain=["report"],
         expect="tighten obvious word repetitions"),
    Case("M5 self-correction",
         "medium", "self-correction",
         "Yeah so number one, ping John — sorry, I mean Jane — about the contract.",
         must_contain=["jane"],
         must_not_contain=["sorry, i mean", "ping john"],
         expect="use Jane, drop the John correction"),
    Case("M6 trailing 'thanks'",
         "medium", "prose",
         "We've finished the migration last night. Everything is running cleanly. Cheers.",
         must_contain=["migration", "running cleanly"],
         expect="genuine 'cheers' kept (real speech, not hallucination)"),

    # ─── LONG (31-100 words) ───────────────────────────────────────────────
    Case("L1 long monologue with fillers",
         "long", "prose",
         "Yeah, so basically, I've been thinking about how we approach the next phase of the rollout. "
         "Um, there's a few things I want to make sure we get right. The first one is, you know, just "
         "making sure the data residency stuff is sorted, because if we don't get that nailed early, "
         "everything else falls apart. Erm, the second is the testing strategy.",
         must_contain=["rollout", "data residency", "testing strategy"],
         retention_min=0.65, retention_max=0.95,
         expect="strip um/erm, preserve substance, keep voice"),
    Case("L2 numbered long items",
         "long", "numbered list",
         "Right, so number one, I need to go through the quarterly reports this afternoon and flag "
         "anything that looks a bit off before the board meeting on Thursday. And number two, I've got "
         "to follow up with accounts about that budget variance we spotted last week.",
         must_match=[r"^\s*1\.\s*", r"\n\s*2\.\s*"],
         must_contain=["quarterly", "board meeting", "budget variance"],
         must_not_contain=["number one", "number two"],
         expect="2-item numbered list with full content preserved"),
    Case("L3 email-shaped input in NORMAL mode (greeting auto-line-break)",
         "long", "email",
         "Hi Sam. Just a quick one — wanted to check whether the dashboard work is on track for "
         "Friday. If it's slipping, let me know early so we can re-prioritise. Also if you need any "
         "input from me on the API side, give us a shout. Cheers, James.",
         # Greeting must be on its own line — accept either comma or period
         # after the name (input has period; styler may keep period or convert).
         must_match=[r"^Hi Sam[,\.]\s*\n\s*\n"],
         must_contain=["sam", "dashboard", "friday", "cheers", "james"],
         retention_min=0.80,
         expect="normal mode auto-formats the greeting line: 'Hi Sam.' / 'Hi Sam,' on its own line, "
                "blank line, then the body. Sign-off preserved at end."),
    Case("L4 trailing fillers / drift-off",
         "long", "prose",
         "OK so basically the way I'm thinking about this is, we've got the existing pipeline, we've "
         "got the new ingestion path, and they kind of need to talk to each other in a way that doesn't "
         "break either of them. I don't know, that's the bit I'm not sure about, like, whether we should "
         "do it gradually or just rip the band-aid off. Yeah.",
         must_contain=["pipeline", "ingestion", "band-aid"],
         must_not_match=[r"yeah\.\s*$"],   # trailing 'yeah' filler should be trimmed
         expect="trim trailing 'Yeah.' filler"),

    # ─── VERY-LONG (100-300 words) ─────────────────────────────────────────
    Case("VL1 multi-paragraph monologue",
         "very-long", "prose",
         ("Right, so let's go through where I've got to with the project. The first thing was to map "
          "out all of the existing data sources we currently pull from, and that took longer than I "
          "thought because half the docs were out of date and the other half were just wrong. Anyway, "
          "I got there in the end and we've now got a clean picture of what's flowing in and out. "
          "The second thing was to actually understand what the business actually wants from this, "
          "as opposed to what they say they want, which as anyone who's been in this game for more "
          "than five minutes will know are very different things. So I had a series of conversations "
          "with the operations team, the finance team, and a couple of the senior people, and what "
          "came out of that was actually pretty different from the original brief. Anyway, that's "
          "where we are. Next steps are basically to write all of this up properly so we've got "
          "something to point at when people ask, and then start sketching out the architecture."),
         must_contain=["project", "data sources", "operations team", "architecture"],
         retention_min=0.70, retention_max=1.05,
         expect="long prose preserved, light cleanup, no truncation"),

    Case("VL2 long email-shaped input in NORMAL mode (preserve content + numbered list)",
         "very-long", "email",
         ("Hi Rohan. Just wanted to follow up on our chat earlier. Three things really. First, on the "
          "API rotation we discussed, can you confirm whether we're using the new tokens or still on "
          "the old ones, because the logs are a bit ambiguous and I want to make sure before "
          "Wednesday's release. Second, the dashboard refactor — I think we agreed you'd take the "
          "first pass and I'd review, but happy to swap if you'd rather. And third, I've added a "
          "couple of items to the Linear board around the data residency stuff, can you have a look "
          "and tell me if I've missed anything obvious. No rush on any of this, but ideally by end "
          "of week. Cheers, James."),
         must_match=[r"^\s*1\.\s*", r"\n\s*2\.\s*", r"\n\s*3\.\s*"],
         must_contain=["rohan", "API rotation", "dashboard", "linear", "cheers", "james"],
         expect="normal mode: numbered-list rule fires on First/Second/Third inside the body, "
                "greeting and sign-off preserved as text (no email-shape line breaks — that's email mode's job)"),

    # ─── EXTREME (500+ words) — tests the v3.12.2 max_tokens fix ──────────
    Case("EXT1 long stream-of-consciousness (max_tokens stress)",
         "extreme", "prose",
         ("Yeah, let's go through a few things. So I would say, yeah, so I've worked in the "
          "information management team. That was about last February, last March. I've automated a "
          "lot of processes for them, simply using a mix of understanding the architecture, the "
          "requirements really, and just saving them a lot of time. So I've done three for them so "
          "far. One of them which saves them about half a day to a day for a project, that's a web "
          "scraper. So essentially they get email domains from like 200 lists of emails on a project "
          "and they need to fill out the contact information for that and they were doing that "
          "manually. So we built first of all a script that first of all web scrapes so it goes to "
          "the domain of that email address, scrapes all of the information off it then using AI to "
          "form, file GPT and Mini to reformat that and then having a fallback. And then it "
          "structures it into an Excel format and repopulates the sheet that they want. And this is "
          "triggered. They just send me an email. And then it automatically gets processed back to "
          "them. So that was one. I ended up winning a hackathon with that, actually, which is quite "
          "nice. Then these verification reports, so they have, I don't know if you've ever heard of "
          "Airtable, but they use Airtable to populate a lot of information, just automating the "
          "export of reports with a few extra tabs, like summary tabs, et cetera, et cetera. And "
          "then one of the most recent ones is building an app for them. this checker essentially "
          "it's a quality control checker some of the work that they do and that take well at the "
          "moment they they buy a tool but that takes about three hours to complete each time and "
          "I've replicated that with the click of a button. So yeah those are the main things in "
          "work I've done a few other things suppose demonstrating just the capabilities of AI."),
         retention_min=0.75, retention_max=1.0,
         must_contain=["information management", "web scraper", "Airtable", "hackathon", "click of a button"],
         expect="full content preserved end-to-end, no max_tokens truncation"),

    # ─── HALLUCINATION-BAIT (specifically tests the strip) ────────────────
    Case("H1 ends with genuine 'cheers'",
         "medium", "hallucination-bait",
         "Right, that's the lot for now. I'll catch up with you tomorrow morning. Cheers.",
         must_contain=["catch up", "cheers"],
         expect="real 'cheers' must NOT be stripped"),
    Case("H2 dictated dictation that mentions subscriptions",
         "medium", "hallucination-bait",
         "We need to look at how to charge users — maybe a yearly subscription model would work better than monthly. Let me think.",
         must_contain=["yearly subscription"],
         must_not_match=[r"please\s+subscribe"],
         expect="legitimate use of 'subscription' must survive"),
    Case("H3 caption-credit at end (WKNO-style Whisper hallucination)",
         "medium", "hallucination-bait",
         # Note: the strip is applied at the transcribe layer, not the styler.
         # We assert that running the raw through the strip clears the tail.
         "And then we had a quick discussion about the budget. CLOSED CAPTION PROVIDED BY WKNO-MEMPHIS.",
         must_not_contain=["wkno", "closed caption", "memphis"],
         must_contain=["budget"],
         custom=lambda raw, s: None,  # styler-level test, see below
         expect="closed-caption credit stripped at transcribe layer; styler-only test "
                "checks the styler doesn't reintroduce it"),

    # ─── CODE/TECHNICAL ─────────────────────────────────────────────────────
    Case("C1 paths and identifiers",
         "medium", "code",
         "Open the file at src slash style underscore openai dot py and find the function called underscore style underscore groq.",
         must_contain=["style", "openai", "groq"],
         expect="don't garble technical identifiers"),

    # ─── EMAIL GREETING AUTO-FORMAT (the user's actual complaint) ──────────
    Case("EM1 greeting auto-line-break — user's exact example",
         "medium", "email",
         "Hi James, I'd really like to discuss further what we talked about in our meeting.",
         must_match=[r"^Hi James,\s*\n\s*\n", r"I'd really like to discuss"],
         retention_min=0.85,
         expect="'Hi James,' on its own line, blank line, then body"),
    Case("EM2 greeting with team address",
         "medium", "email",
         "Hi team, just wanted to flag that the data residency review is now blocking the rollout. "
         "Can someone pick this up before Friday?",
         must_match=[r"^Hi team,\s*\n\s*\n", r"data residency"],
         must_contain=["?"],
         expect="'Hi team,' on own line, blank line, body preserved with question mark"),
    Case("EM3 Hello variant + name",
         "medium", "email",
         "Hello Rohan, can you check whether the API key rotation has gone through on the dev tier yet?",
         must_match=[r"^Hello Rohan,\s*\n\s*\n", r"API key rotation"],
         must_contain=["?"],
         expect="'Hello Rohan,' on own line"),
    Case("EM4 Dear formal + body",
         "medium", "email",
         "Dear Sam, please find attached the quarterly report for review. Let me know if anything needs adjusting before the board meeting.",
         must_match=[r"^Dear Sam,\s*\n\s*\n", r"quarterly report"],
         expect="'Dear Sam,' on own line; formal register preserved"),
    Case("EM5 NEGATIVE — Hi at sentence start without name should NOT trigger",
         "medium", "email",
         "Hi guys would never work as a greeting if I just said hi guys mid-sentence about my mates. The rest of this thought continues normally.",
         # "Hi guys" with NO comma and not actually addressed as a greeting
         # — this is one connected sentence. We should NOT split it.
         must_not_match=[r"^Hi guys,\s*\n"],
         expect="false-positive guard: not every 'Hi' at start is a greeting"),
    Case("EM6 NEGATIVE — discussion that starts with 'Hi' word",
         "medium", "email",
         "I just got a high score on the test, which felt great, and then I went home and cooked dinner.",
         # 'Hi' is not even at start; this is just to verify the 'Hi <Name>,' pattern
         # is anchored properly and doesn't fire on words that contain 'hi'.
         must_not_match=[r"^Hi\s"],
         expect="no greeting trigger when input doesn't start with Hi/Hello/etc"),
    Case("EM7 sign-off split — full email shape user complained about",
         "medium", "email",
         "Hi James, thanks for meeting today, it was really useful and I appreciate your time. Regards, James.",
         must_match=[
             r"^Hi James,[ \t]*\n[ \t]*\n",          # greeting on own line + blank line
             r"\n[ \t]*\n[ \t]*Regards,[ \t]*\n[ \t]*James\b",  # blank line, then sign-off / name on consecutive lines
         ],
         # Forbid INLINE "Regards, James" — i.e. comma+space+James on the SAME line.
         must_not_match=[r"Regards,[ \t]+James"],
         expect="full email shape: greeting line, blank, body, blank, sign-off line, name line"),
    Case("EM8 sign-off variant — Cheers + name",
         "medium", "email",
         "Hi Sam, can you please review the attached PR when you get a chance. Cheers, James.",
         must_match=[r"\n[ \t]*\n[ \t]*Cheers,[ \t]*\n[ \t]*James\b"],
         must_not_match=[r"Cheers,[ \t]+James"],
         expect="'Cheers, James' splits into two lines"),
    Case("EM9 sign-off variant — Best regards + name",
         "medium", "email",
         "Hello team, the deployment finished cleanly and all checks are green. Best regards, James.",
         must_match=[r"\n[ \t]*\n[ \t]*Best regards,[ \t]*\n[ \t]*James\b"],
         must_not_match=[r"Best regards,[ \t]+James"],
         expect="'Best regards, James' splits into two lines"),
    Case("EM10 NEGATIVE — sign-off word in body should NOT split",
         "medium", "email",
         "Thanks for meeting today, James, it was really useful and I appreciate your time.",
         # "Thanks for meeting today, James, ..." is mid-body address, not a sign-off.
         # It should stay as a single line; do NOT split into Thanks,\nJames\n...
         must_not_match=[r"Thanks,[ \t]*\n[ \t]*James"],
         expect="false-positive guard: sign-off word in body doesn't split when content follows the name"),
]


# ── Runner ───────────────────────────────────────────────────────────────────

def evaluate(case: Case, styled: str) -> List[str]:
    """Return list of failure reasons (empty list = pass)."""
    failures = []
    s_lower = styled.lower()
    raw_words = max(1, len(case.raw.split()))
    styled_words = len(styled.split())
    ratio = styled_words / raw_words

    for needle in case.must_contain:
        if needle.lower() not in s_lower:
            failures.append(f"missing required substring: {needle!r}")
    for needle in case.must_not_contain:
        if needle.lower() in s_lower:
            failures.append(f"contains forbidden substring: {needle!r}")
    for pat in case.must_match:
        if not re.search(pat, styled, re.MULTILINE):
            failures.append(f"missing required pattern: {pat!r}")
    for pat in case.must_not_match:
        if re.search(pat, styled, re.MULTILINE):
            failures.append(f"contains forbidden pattern: {pat!r}")
    if ratio < case.retention_min:
        failures.append(f"word retention {ratio:.0%} below floor {case.retention_min:.0%}")
    if ratio > case.retention_max:
        failures.append(f"word retention {ratio:.0%} above ceiling {case.retention_max:.0%}")
    if case.custom:
        err = case.custom(case.raw, styled)
        if err:
            failures.append(err)
    return failures


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Waffler prompt regression harness")
    ap.add_argument("--delay", type=float, default=0.5,
                    help="Seconds between calls. Bump to ~3-5 to test on Groq "
                         "without tripping per-minute token limits. Default 0.5.")
    ap.add_argument("--filter", type=str, default=None,
                    help="Only run cases whose label matches this substring "
                         "(case-insensitive). Useful for targeted re-runs.")
    args = ap.parse_args()

    styler = OpenAIStyler(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
    )

    cases = [c for c in CORPUS if (not args.filter or args.filter.lower() in c.label.lower())]
    if not cases:
        print(f"no cases matched filter {args.filter!r}")
        return

    results = []
    width = max(len(c.label) for c in cases)
    print(f"\nRunning {len(cases)} cases with {args.delay}s delay between calls\n")
    print(f"{'#':<3} {'LABEL':<{width}} {'LENGTH':<11} {'CAT':<18} VERDICT")
    print("─" * (width + 50))

    for i, case in enumerate(cases, 1):
        try:
            t0 = time.time()
            styled, usage = styler.style(case.raw)
            elapsed = (time.time() - t0) * 1000
        except Exception as e:
            print(f"{i:<3} {case.label:<{width}} {case.length:<11} {case.category:<18} ERROR: {e!s:.80}")
            results.append((case, "", [f"styler exception: {e}"], 0))
            continue
        failures = evaluate(case, styled)
        verdict = "PASS" if not failures else f"FAIL ({len(failures)})"
        print(f"{i:<3} {case.label:<{width}} {case.length:<11} {case.category:<18} {verdict:<10} ({elapsed:.0f}ms via {usage.get('provider','?')})")
        results.append((case, styled, failures, elapsed))
        if i < len(cases):
            time.sleep(args.delay)

    print("\n" + "=" * (width + 50))
    passed = sum(1 for _, _, f, _ in results if not f)
    total = len(results)
    print(f"PASSED {passed}/{total}")

    print("\n=== FAILURE DETAILS ===")
    for case, styled, failures, _ in results:
        if not failures:
            continue
        print(f"\n[{case.label}]  expected: {case.expect}")
        print(f"  raw:    {case.raw[:160]}{'...' if len(case.raw) > 160 else ''}")
        print(f"  styled: {styled[:160]}{'...' if len(styled) > 160 else ''}")
        for f in failures:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
