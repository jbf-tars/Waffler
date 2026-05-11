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
         "And then we had a quick discussion about the budget. CLOSED CAPTION PROVIDED BY WKNO-MEMPHIS.",
         # IMPORTANT: the caption-credit strip lives at the TRANSCRIBE layer
         # (transcribe_whisper._strip_hallucinations), NOT the styler. By the
         # time content reaches the styler the credit should already be gone.
         # The styler's job is to faithfully preserve the input — so all this
         # test verifies is that the styler doesn't INVENT a fake credit.
         # (We test the actual strip in scripts/test_vocab_corpus.py? No,
         # via a direct unit test of _strip_hallucinations.)
         must_contain=["budget"],
         expect="styler preserves content as-is; caption-credit removal is the transcribe "
                "layer's job, not the styler's"),

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

    # ─── EXPANDED EMAIL: GREETING VARIATIONS ───────────────────────────────
    Case("EM11 informal Hey + name",
         "medium", "email",
         "Hey James, just a quick one — can you let me know if you're free for a chat tomorrow?",
         must_match=[r"^Hey James,[ \t]*\n[ \t]*\n"],
         expect="'Hey James,' on own line"),
    Case("EM12 Morning + name",
         "medium", "email",
         "Morning Sarah, hope you had a good weekend. Just wanted to check on the deployment status.",
         must_match=[r"^Morning Sarah,[ \t]*\n[ \t]*\n"],
         must_contain=["sarah", "deployment"],
         expect="time-of-day greeting recognised"),
    Case("EM13 Afternoon + group",
         "medium", "email",
         "Afternoon team, the dashboard rollout is on track for Friday — please ping me with any blockers.",
         must_match=[r"^Afternoon team,[ \t]*\n[ \t]*\n"],
         expect="afternoon + team group address"),
    Case("EM14 Dear formal Mr",
         "medium", "email",
         "Dear Mr Thompson, please find attached the quarterly financials. Happy to walk through anything that needs clarifying.",
         must_match=[r"^Dear Mr Thompson,[ \t]*\n[ \t]*\n"],
         must_contain=["thompson", "quarterly"],
         expect="Dear + Mr + surname formal"),
    Case("EM15 Hi everyone group",
         "medium", "email",
         "Hi everyone, just a heads up that the office is closed on Monday for the bank holiday.",
         must_match=[r"^Hi everyone,[ \t]*\n[ \t]*\n"],
         expect="everyone group address"),
    Case("EM16 Hi all group",
         "medium", "email",
         "Hi all, can someone pick up the support tickets that came in over the weekend?",
         must_match=[r"^Hi all,[ \t]*\n[ \t]*\n"],
         expect="all group address"),
    Case("EM17 multiple addressees",
         "medium", "email",
         "Hi James and Sarah, can the two of you sync up on the dashboard work this week and let me know what you decide?",
         must_match=[r"^Hi James and Sarah,[ \t]*\n[ \t]*\n"],
         expect="multi-name greeting kept together"),
    Case("EM18 Hi folks",
         "medium", "email",
         "Hi folks, the rollback completed successfully. Production is back to v3.11.6 for now.",
         must_match=[r"^Hi folks,[ \t]*\n[ \t]*\n"],
         expect="folks group address"),
    Case("EM19 Hey both",
         "medium", "email",
         "Hey both, are we still good to catch up at three this afternoon?",
         must_match=[r"^Hey both,[ \t]*\n[ \t]*\n"],
         must_contain=["?"],
         expect="both group address (small group)"),
    Case("EM20 Evening greeting",
         "medium", "email",
         "Evening Rohan, sorry for the late message but wanted to flag the API rotation needs doing tonight.",
         must_match=[r"^Evening Rohan,[ \t]*\n[ \t]*\n"],
         expect="evening greeting + name"),

    # ─── EXPANDED EMAIL: SIGN-OFF VARIATIONS ───────────────────────────────
    Case("EM21 Cheers no name",
         "medium", "email",
         "Hi James, the docs are now live on the staging site. Have a look when you get a sec. Cheers.",
         must_match=[r"^Hi James,[ \t]*\n[ \t]*\n"],
         must_contain=["cheers"],
         # Cheers WITHOUT a name should NOT split — it's a one-line sign-off.
         must_not_match=[r"\nCheers,[ \t]*\n"],
         expect="bare 'Cheers.' at end stays on the body's last line, no split"),
    Case("EM22 sign-off with full name",
         "medium", "email",
         "Hi Sarah, please find the report attached. Let me know if you need anything else. Best regards, James Farrelly.",
         must_match=[r"\n[ \t]*\n[ \t]*Best regards,[ \t]*\n[ \t]*James Farrelly\b"],
         expect="full name (first + last) preserved on signature line"),
    Case("EM23 Sincerely formal",
         "medium", "email",
         "Dear Mr Thompson, please find enclosed the proposal we discussed. I look forward to hearing your thoughts. Sincerely, James.",
         must_match=[r"\n[ \t]*\n[ \t]*Sincerely,[ \t]*\n[ \t]*James\b"],
         expect="Sincerely sign-off split"),
    Case("EM24 Speak soon",
         "medium", "email",
         "Hi Rohan, thanks for the call earlier — really appreciated your input. Speak soon, James.",
         must_match=[r"\n[ \t]*\n[ \t]*Speak soon,[ \t]*\n[ \t]*James\b"],
         expect="Speak soon sign-off split"),
    Case("EM25 All the best",
         "medium", "email",
         "Hi team, I'm logging off for the day. The deploy went cleanly. All the best, James.",
         must_match=[r"\n[ \t]*\n[ \t]*All the best,[ \t]*\n[ \t]*James\b"],
         expect="All the best sign-off split"),
    Case("EM26 Thanks again",
         "medium", "email",
         "Hi Sarah, the figures all check out. Really appreciate you turning that around so quickly. Thanks again, James.",
         must_match=[r"\n[ \t]*\n[ \t]*Thanks again,[ \t]*\n[ \t]*James\b"],
         expect="Thanks again sign-off split"),

    # ─── EMAIL: FULL SHAPES (combinations) ─────────────────────────────────
    Case("EM27 email with numbered list in body",
         "long", "email",
         "Hi James, three things I need from you this week. First, can you sign off the budget. Second, "
         "review the architecture doc. Third, schedule the kickoff with Rohan. Cheers, James.",
         must_match=[
             r"^Hi James,[ \t]*\n[ \t]*\n",
             r"\n\s*1\.\s*",
             r"\n\s*2\.\s*",
             r"\n\s*3\.\s*",
             r"\n[ \t]*\n[ \t]*Cheers,[ \t]*\n[ \t]*James\b",
         ],
         expect="email shape with numbered list inside the body"),
    Case("EM28 email with bullets in body",
         "long", "email",
         "Hi Sarah, just to recap what we agreed on the call. The three priorities are speed, accuracy, "
         "and cost. Let me know if I missed anything. Cheers, James.",
         must_match=[
             r"^Hi Sarah,[ \t]*\n[ \t]*\n",
             r"\n[ \t]*\n[ \t]*Cheers,[ \t]*\n[ \t]*James\b",
         ],
         must_contain=["speed", "accuracy", "cost"],
         expect="email + bullet list of three things in body"),
    Case("EM29 long-ish email multiple paragraphs",
         "very-long", "email",
         "Hi Rohan, just wanted to follow up on our chat earlier this week about the data residency stuff. "
         "I've gone through the policy doc and I think we're broadly OK, but there are a couple of grey areas "
         "I'd like your view on. The first one is whether transient processing in a US region counts as residency. "
         "The second is whether we need to log the data flow even when nothing is persisted. Let me know what you "
         "think when you get a chance — no rush. Cheers, James.",
         must_match=[
             r"^Hi Rohan,[ \t]*\n[ \t]*\n",
             r"\n[ \t]*\n[ \t]*Cheers,[ \t]*\n[ \t]*James\b",
         ],
         must_contain=["data residency", "transient processing"],
         retention_min=0.85,
         expect="multi-paragraph email; full content preserved; greeting + sign-off split"),
    Case("EM30 very short email",
         "short", "email",
         "Hi James, ok thanks. Cheers.",
         must_contain=["james"],
         retention_min=0.5,
         expect="tiny email — short enough that _is_simple may bypass the LLM. Just check content survives."),

    # ─── EXPANDED LISTS ────────────────────────────────────────────────────
    Case("L5 numbered with longer items",
         "long", "numbered list",
         "Right, so first of all I need to ship the API rotation by Wednesday. Second, the dashboard refactor "
         "needs a code review from Rohan before Friday. Third, I should book in the Q2 retrospective with "
         "the wider team — probably end of next week.",
         # Use multiline ^ anchor — output may start at line 1 with no preceding
         # newline if the lead-in ("Right, so first of all") was stripped entirely.
         must_match=[r"(?m)^\s*1\.\s*", r"(?m)^\s*2\.\s*", r"(?m)^\s*3\.\s*"],
         must_contain=["API rotation", "dashboard", "retrospective"],
         expect="3-item numbered list with longer items each"),
    Case("L6 mixed prose lead-in + bullet list",
         "long", "bulleted list",
         "OK so a quick recap of what we discussed. The three big risks I'm seeing right now are technical "
         "debt in the styling module, the Groq rate-limit pressure on free tier users, and the lack of "
         "automated regression coverage on the prompt. Anyway, that's the lot.",
         must_contain=["technical debt", "rate-limit", "regression"],
         retention_min=0.75,
         expect="prose lead-in then bullet list of three risks"),
    Case("L7 'I need' grocery-style bullet list (borderline case)",
         "medium", "bulleted list",
         "For the Friday demo I need slides, the working build, and Rohan to be in the room.",
         # Borderline: "I need slides, the working build, and Rohan" is a list of
         # discrete items, but the trailing "to be in the room" qualifier makes
         # this less obviously a bullet candidate. Either prose or bullets is
         # acceptable — the critical check is content preservation.
         must_contain=["slides", "working build", "Rohan"],
         expect="content preserved; bullet vs prose is a judgement call here"),

    # ─── EDGE: NUMBERS, DATES, CURRENCY, ACRONYMS ──────────────────────────
    Case("ED1 currency preserved",
         "medium", "code",
         "The quote came back at £2,500 plus VAT, so roughly $3,300 USD or €2,900 depending on the rate.",
         must_contain=["£2,500", "$3,300", "€2,900"],
         expect="currency symbols and amounts preserved exactly"),
    Case("ED2 dates and times",
         "medium", "code",
         "The meeting is on 12th May 2026 at 3pm UK time, which is 10am EST or 7am PST.",
         must_contain=["12th May 2026", "3pm", "10am EST", "7am PST"],
         expect="dates and timezones preserved"),
    Case("ED3 acronyms preserved",
         "medium", "code",
         "Send the PR to Rohan by EOD, and make sure the API key rotation is logged in the SIEM.",
         must_contain=["PR", "EOD", "API", "SIEM"],
         expect="standalone acronyms not lowercased or expanded"),
    Case("ED4 hyphenated names",
         "medium", "prose",
         "Mary-Jane is on holiday this week, so Jean-Luc will cover her shifts.",
         must_contain=["Mary-Jane", "Jean-Luc"],
         expect="hyphenated names preserved as one token"),
    Case("ED5 apostrophe in name",
         "medium", "prose",
         "I had a chat with O'Brien earlier about the migration plan, and she said it's mostly fine.",
         must_contain=["O'Brien"],
         expect="apostrophe in surname preserved"),
    Case("ED6 URLs preserved",
         "medium", "code",
         "Have a look at github.com/jbf-tars/Waffler/issues for the open bugs, especially the one about overlay positioning.",
         must_contain=["github.com/jbf-tars/Waffler/issues"],
         expect="URL kept verbatim, not split or stripped"),
    Case("ED7 email address preserved",
         "medium", "code",
         "Drop a note to support@example.com if you hit any issues with the migration.",
         must_contain=["support@example.com"],
         expect="email address kept verbatim"),
    Case("ED8 version number",
         "medium", "code",
         "We're on v3.12.5 in production, but staging has v3.13.0 from last night's build.",
         must_contain=["v3.12.5", "v3.13.0"],
         expect="version numbers preserved"),
    Case("ED9 file paths",
         "medium", "code",
         "Check the file at src/style_openai.py around line 200 — that's where the rate-limit logic lives.",
         must_contain=["src/style_openai.py", "200"],
         expect="file path kept exact, line number kept"),
    Case("ED10 mixed numbers spelled vs digit",
         "medium", "code",
         "We tested with twenty-five users on Friday and it held up fine, but the load test pushed it to 250 and we saw issues.",
         must_contain=["twenty-five", "250"],
         expect="speaker's number form preserved (don't normalize 25 ↔ twenty-five)"),

    # ─── EDGE: PROFANITY, DIALECT ──────────────────────────────────────────
    Case("ED11 profanity preserved",
         "short", "prose",
         "Honestly, this is a fucking disaster of a deploy and we need to roll back now.",
         must_contain=["fucking"],
         expect="profanity not censored or sanitised"),
    Case("ED12 British spelling preserved",
         "medium", "prose",
         "We need to organise a colour review before we finalise the catalogue, otherwise the licence renewal will be delayed.",
         must_contain=["organise", "colour", "finalise", "catalogue", "licence"],
         expect="British spelling preserved (not Americanised)"),

    # ─── REAL-WORLD DICTATION PATTERNS ─────────────────────────────────────
    Case("RW1 stand-up update",
         "long", "prose",
         "OK quick standup. Yesterday I shipped the rate-limit cooldown fix and ran the regression harness. "
         "Today I'm going to look at the Mac toast positioning issue. Blockers — none, but I might need a "
         "second pair of eyes on the styler refactor later in the week.",
         must_contain=["regression harness", "blockers"],
         retention_min=0.75,
         expect="standup format preserved, light cleanup"),
    Case("RW2 bug report",
         "long", "prose",
         "Bug report. Steps to reproduce: open the app, dictate a long sentence with 'I I' double-pronoun, then look at the styled output. "
         "Expected: the doubled pronoun is collapsed to single. Actual: stays as 'I I'. Happens on Windows v3.12.4, haven't tested Mac yet.",
         must_contain=["steps to reproduce", "expected", "actual"],
         expect="structured bug report kept readable"),
    Case("RW3 code review comment",
         "medium", "code",
         "Two thoughts on this PR. The renaming is good, but I'd be careful about the side effect on line forty-two — looks like it could break the cancel path. Otherwise LGTM.",
         must_contain=["LGTM"],
         expect="LGTM acronym preserved, casual review tone kept"),
    Case("RW4 slack-style casual",
         "medium", "prose",
         "yo can you grab me a coffee when you go on lunch break, the usual please, ta",
         must_contain=["coffee"],
         retention_min=0.6,
         expect="lowercase casual style preserved (don't formalise)"),
    Case("RW5 rambling thought process",
         "long", "prose",
         "OK so I'm thinking about this and the more I think about it the more I'm not sure. On one hand, "
         "doing the migration in a single big bang is risky. On the other hand, doing it gradually means "
         "we have to maintain both paths for months. I don't know, what do you think?",
         must_contain=["migration", "?"],
         retention_min=0.75,
         expect="rambling preserved, question mark kept"),

    # ─── HALLUCINATION GUARDS (specific failure-mode tests) ────────────────
    Case("HG1 sentence ends with 'thank you' — real not hallucination",
         "medium", "hallucination-bait",
         "Just wanted to say I really appreciate your work on this project. It was a pleasure to collaborate. Thank you.",
         must_contain=["appreciate", "pleasure"],
         # Real "Thank you." ending — should NOT be stripped, even though
         # it pattern-matches the Whisper hallucination. The styler has the
         # whole context; only the transcribe-layer strip considers it a
         # hallucination, and that's only when it's basically the entire
         # output.
         expect="genuine 'Thank you.' kept when surrounded by real context"),
    Case("HG2 dictated 'subscribe to the newsletter' — not Whisper outro",
         "medium", "hallucination-bait",
         "Mention in the email that they can subscribe to the newsletter from the footer link, no account required.",
         must_contain=["subscribe to the newsletter"],
         expect="legitimate use of 'subscribe' mid-body must survive"),
    Case("HG3 numbers transcribed as digits",
         "medium", "code",
         "We've got 50 users on the beta, average response time is 230ms, and the error rate is under 2%.",
         must_contain=["50 users", "230ms", "2%"],
         expect="digits, units, percentages preserved"),
    Case("HG4 trailing 'amen' — could trigger weird strip",
         "medium", "prose",
         "I really hope this thing works without breaking anything. Amen.",
         must_contain=["amen"],
         retention_min=0.6,
         expect="'Amen.' end is genuine speech, not a hallucination"),

    # ─── SELF-CORRECTION EDGE CASES ────────────────────────────────────────
    Case("SC1 multi-correction in one sentence",
         "medium", "self-correction",
         "Send the report to John — sorry, I mean James — by Tuesday, no wait, Wednesday at three.",
         must_contain=["james", "wednesday"],
         must_not_contain=["sorry, i mean", "no wait"],
         expect="John→James and Tuesday→Wednesday corrections both applied"),
    Case("SC2 'I mean' as clarification not filler",
         "medium", "self-correction",
         "Make sure the test is comprehensive — I mean cover the edge cases, the happy path, and the error states.",
         must_contain=["edge cases", "happy path", "error states"],
         # 'I mean' here is introducing a clarification, not filler; the "I mean"
         # phrase itself can be stripped or kept, but the clarification content
         # must survive.
         expect="clarification content preserved even if 'I mean' phrase trimmed"),
    Case("SC3 backtrack and restart",
         "medium", "self-correction",
         "We should ship the — actually let me start over. The plan is to ship the dashboard refactor first, then the styling module.",
         must_contain=["dashboard refactor", "styling module"],
         must_not_contain=["actually let me start over", "we should ship the —"],
         expect="abandoned start removed, kept the restart version"),

    # ─── MORE NEGATIVE GUARDS ──────────────────────────────────────────────
    Case("NG1 'Hi' as part of 'high'",
         "medium", "email",
         "Highest priority right now is the API rotation, then the dashboard refactor.",
         must_not_match=[r"^Hi\s"],
         expect="'Highest' isn't 'Hi-' + greeting — must not split"),
    Case("NG2 'Cheers' as celebration not sign-off",
         "medium", "prose",
         "Big cheers to Rohan for shipping the migration on time — top job from the whole team.",
         must_contain=["cheers", "rohan"],
         must_not_match=[r"\n[ \t]*Cheers,[ \t]*\n"],
         expect="mid-body 'cheers' as exclamation must not trigger sign-off split"),
    Case("NG3 'Thanks' inside a question",
         "medium", "prose",
         "Quick one — did you remember to send the thanks card to the design team for the rebrand?",
         must_contain=["thanks card", "?"],
         must_not_match=[r"\n[ \t]*Thanks,[ \t]*\n"],
         expect="'thanks' as adjective in body must not trigger sign-off"),

    # ─── ADDITIONAL VERY-SHORT EDGE CASES ──────────────────────────────────
    Case("VS6 bare yes",
         "very-short", "prose",
         "Yes.",
         retention_min=0.5,
         expect="single-word affirmation kept"),
    Case("VS7 acronym only",
         "very-short", "code",
         "ASAP please.",
         must_contain=["ASAP"],
         expect="acronym preserved in two-word utterance"),
    Case("VS8 dictated punctuation",
         "very-short", "prose",
         "Thanks question mark",
         # User dictated "question mark" as a verbal cue. Whisper usually
         # converts to "?". If not, the styler should at least preserve the
         # sentiment.
         must_contain=["thanks"],
         expect="verbal punctuation cue handled"),

    # ─── SOLO-NUMBER GUARD (the 09:39 bug user reported) ───────────────────
    Case("SOLO-NUM-1 user's exact 09:39 bug",
         "medium", "numbered list",
         "So number three, we'll just skip that at the moment, we just want to see if it works.",
         # MUST keep the reference to step three; MUST NOT convert to "1."
         must_contain=["number three", "skip", "see if it works"],
         must_not_match=[r"^\s*1\.\s"],
         expect="solo 'number three' is a reference to an external sequence, NOT a list trigger"),
    Case("SOLO-NUM-2 'step five' as reference",
         "medium", "numbered list",
         "I think step five is where it went wrong, can you have a look at that one?",
         must_contain=["step five"],
         must_not_match=[r"^\s*1\.\s", r"^\s*5\.\s"],
         expect="'step five' is a reference, not a list trigger"),
    Case("SOLO-NUM-3 'question two' as reference",
         "medium", "numbered list",
         "Question two is the trickiest, give me a minute on that.",
         must_contain=["Question two"],
         must_not_match=[r"^\s*1\.\s", r"^\s*2\.\s"],
         expect="'question two' is a reference, not a list trigger"),
    Case("SOLO-NUM-4 'number one' alone (still solo)",
         "medium", "numbered list",
         "Number one priority right now is the API rotation, nothing else really comes close.",
         # "Number one" used adjectivally / as an idiom, no second item.
         must_contain=["priority"],
         must_not_match=[r"^\s*1\.\s*Priority"],
         expect="'number one priority' is an idiom, not a list opener — no second item exists"),
    Case("SOLO-NUM-5 positive control — TWO items still work",
         "medium", "numbered list",
         "Number one, ship the API rotation. Number two, book the kickoff with Rohan.",
         must_match=[r"(?m)^\s*1\.\s", r"(?m)^\s*2\.\s"],
         must_not_contain=["number one", "number two"],
         expect="two items present — list rule SHOULD fire"),
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
