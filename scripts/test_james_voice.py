"""Live prompt probe in James's actual speaking style.

Transcripts built from his history: British English, "So..." / "Yeah so..." /
"Okay so..." openers, technical context (Matchbook, Claude Code, AI Foundry,
betting bots, VPS, Azure), "like" as filler, hedges ("I suppose",
"not going to lie"), run-on sentences.

30s pause between calls.
"""
import os, sys, time
from pathlib import Path
env = Path.home() / ".waffler-hosted" / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from style_openai import OpenAIStyler  # noqa: E402
from transcribe_whisper import load_vocab  # noqa: E402

print(f"vocab = {load_vocab()}")
styler = OpenAIStyler(
    api_key=os.environ.get("OPENAI_API_KEY", ""),
    groq_api_key=os.environ.get("GROQ_API_KEY", ""),
)

VOCAB_POISONS = ["ashkan", "cobie", "cobieqc", "usain"]

TESTS = [
    # --- Numbered-list territory (James-voice versions) ---
    ("N1 reproducer in his voice — Matchbook cards",
     "Yeah so number one, check cards for Matchbook. Number two, check the betting bot cards and see if I can add more.",
     "numbered"),
    ("N2 first/second/third — AI Foundry write-up",
     "Right, so first of all I need to finish the AI Foundry write-up before Friday. Second, hand it off to engineering for a review. Third, book in a kickoff with the team next week.",
     "numbered"),
    ("N3 numbered with 'um' / 'uh' fillers",
     "Okay so number one, um, check the database is actually running, and number two, uh, make sure the backups fired overnight.",
     "numbered"),
    ("N4 prose lead-in + numbered — both blocks must survive",
     "Okay, so here's what I reckon we should do this sprint. Number one, hire another backend engineer. Number two, pair them with Rohan for the first week to get them up to speed.",
     "numbered-with-leadin"),
    ("N5 longer numbered items",
     "Right so number one, I need to go through the quarterly reports this afternoon and flag anything that looks a bit off before the board meeting on Thursday. And number two, I've got to follow up with accounts about that budget variance we spotted last week.",
     "numbered"),
    ("N6 self-correction inside a numbered list",
     "Yeah so number one, ping John — sorry, I mean Jane — about the contract. Number two, send the signed copy back to legal by end of day.",
     "numbered"),

    # --- Bullet territory ---
    ("B1 grocery list — his voice",
     "Right, so for the groceries I need eggs, milk, bread, and a bag of apples for the kids' lunches.",
     "bullet"),
    ("B2 'three things we're optimising for'",
     "So the three things we're really optimising for in this product are speed, accuracy, and cost to the end user.",
     "bullet"),
    ("B3 platforms with trailing qualifier",
     "Yeah so the desktop app runs on Mac, Windows, and Linux — although Linux support is a bit rough still, not going to lie.",
     "bullet"),

    # --- Prose territory (must NOT be listified) ---
    ("P1 conversational — meeting recap",
     "The meeting went well enough I suppose, but the product team pushed back pretty hard on the timeline, so we agreed to revisit it next Tuesday.",
     "prose"),
    ("P2 garage clean-out",
     "So today I finally got round to cleaning out the garage, honestly it took way longer than I thought because there was just so much rubbish in there.",
     "prose"),
    ("P3 VPS architecture musing",
     "Before we go for the VPS architecture, like, how needed is it? Is it even recommended to do it on a VPS? Because if it's not then I don't see the point.",
     "prose-with-questions"),

    # --- Vocab-injection guard (styler no longer sees vocab) ---
    ("V1 'cost' must stay 'cost' — not morph into 'COBie'",
     "Yeah so the cost of the project has gone up a lot since the quarterly review, so we need to have another look at the budget before we commit.",
     "no-vocab-injection"),
    ("V2 clean prose — no vocab words expected",
     "Okay so I like this a lot — there's some things I'd like to remove, but I've got some questions about it as well really.",
     "no-vocab-injection"),
]


def verdict(expected, transcript, styled):
    s = styled.lower()
    t = transcript.lower()
    has_bullet = "\n- " in styled or styled.lstrip().startswith("- ")
    has_numbered = any(f"\n{n}." in styled or styled.lstrip().startswith(f"{n}.") for n in range(1, 10))
    injected = [w for w in VOCAB_POISONS if w in s and w not in t]
    if injected:
        return f"FAIL — VOCAB INJECTED: {injected}"
    if expected == "numbered":
        return "PASS — numbered" if has_numbered else "FAIL — not numbered"
    if expected == "numbered-with-leadin":
        lead_kept = "sprint" in s
        return ("PASS — lead-in kept + numbered" if has_numbered and lead_kept
                else f"FAIL — lead_kept={lead_kept} numbered={has_numbered}")
    if expected == "bullet":
        return "PASS — bulleted" if has_bullet else "FAIL — still prose"
    if expected == "prose":
        return "PASS — prose kept" if not has_bullet and not has_numbered else "FAIL — unexpectedly listified"
    if expected == "prose-with-questions":
        has_q = "?" in styled
        return "PASS — prose + questions" if has_q and not has_bullet and not has_numbered else f"FAIL — q={has_q} bullet={has_bullet} num={has_numbered}"
    if expected == "no-vocab-injection":
        return "PASS — no vocab leaked"
    return "?"


fails = 0
for i, (label, transcript, expected) in enumerate(TESTS, 1):
    print(f"\n=== {i}/{len(TESTS)}  {label} ===")
    print(f"  in:      {transcript}")
    styled, usage = styler.style(transcript)
    print(f"  provider: {usage.get('provider', '?')}")
    print(f"  out:\n    " + styled.replace("\n", "\n    "))
    v = verdict(expected, transcript, styled)
    print(f"  verdict: {v}")
    if v.startswith("FAIL"):
        fails += 1
    if i < len(TESTS):
        print(f"  -- sleep 30s --", flush=True)
        time.sleep(30)

print(f"\n=== done: {len(TESTS)-fails}/{len(TESTS)} passed ===")
