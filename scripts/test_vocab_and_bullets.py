"""Post-fix live test. Run with the real vocab.json in place:
  1) Styler must NOT inject vocab words into clean transcripts.
  2) Bullet cue must now fire for grocery-list / "three things are" cases.
  3) All previously-passing cases must still pass.
30s between calls so we stay well under Groq rate limits.
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

print("loaded vocab:", load_vocab())

styler = OpenAIStyler(
    api_key=os.environ.get("OPENAI_API_KEY", ""),
    groq_api_key=os.environ.get("GROQ_API_KEY", ""),
)

VOCAB_POISONS = ["ashkan", "cobie", "cobieqc", "usain"]

TESTS = [
    ("V1 clean prose — no vocab words expected in output",
     "So today I finally got round to cleaning out the garage, and honestly it took way longer than I thought it would because there was so much rubbish in there.",
     "no-vocab-injection"),
    ("V2 clean numbered list — no vocab words expected in output",
     "Number one, check the cards for Matchbook. Number two, check the betting bot cards and see if I can add more.",
     "no-vocab-injection"),
    ("V3 homophone-ish prose — word 'cost' must stay 'cost', not turn into 'COBie'",
     "The cost of the project has gone up a lot since the quarterly review, so we need to take another look at the budget before we commit.",
     "no-vocab-injection"),
    ("B1 grocery list — should BULLET now",
     "For the grocery run I need eggs, milk, bread, and a bag of apples for the kids' lunches.",
     "bullet"),
    ("B2 'the three things are' — should BULLET now",
     "The three things we optimise for in this product are speed, accuracy, and cost to the end user.",
     "bullet"),
    ("B3 'we support' platforms list — should BULLET",
     "The desktop app runs on Mac, Windows, and Linux, although Linux support is still experimental.",
     "bullet"),
    ("P1 conversational comma-run — must stay prose, NOT bulleted",
     "The meeting went well overall, but the product team pushed back pretty hard on the timeline, so we agreed to revisit it next Tuesday.",
     "prose"),
    ("N1 original reproducer — must still be numbered list",
     "Number one, check cards for Matchbook. Number two, check betting bot cards and see if I can add more.",
     "numbered"),
    ("N2 prose lead-in + numbered items — both blocks must survive",
     "Here's what we need to do this sprint. Number one, hire another backend engineer. Number two, onboard them and pair them with Rohan for the first week.",
     "numbered-with-leadin"),
]


def check(expected, transcript, styled):
    s = styled.lower()
    notes = []
    transcript_l = transcript.lower()

    # Vocab-injection check: every test
    injected = [w for w in VOCAB_POISONS if w in s and w not in transcript_l]
    if injected:
        notes.append(f"VOCAB INJECTED: {injected}")

    if expected == "no-vocab-injection":
        if not injected:
            notes.append("no vocab injected")
    elif expected == "bullet":
        has_bullet = "\n- " in styled or styled.lstrip().startswith("- ")
        notes.append("bulleted" if has_bullet else "NOT bulleted (expected bullets)")
    elif expected == "prose":
        has_bullet = "\n- " in styled or styled.lstrip().startswith("- ")
        has_numbered = any(f"\n{n}." in styled or styled.lstrip().startswith(f"{n}.") for n in range(1, 10))
        notes.append("prose kept" if not has_bullet and not has_numbered else "UNEXPECTEDLY LISTIFIED")
    elif expected == "numbered":
        has_numbered = any(f"\n{n}." in styled or styled.lstrip().startswith(f"{n}.") for n in range(1, 10))
        notes.append("numbered" if has_numbered else "NOT numbered (expected numbered)")
    elif expected == "numbered-with-leadin":
        has_numbered = any(f"\n{n}." in styled or styled.lstrip().startswith(f"{n}.") for n in range(1, 10))
        lead_kept = "sprint" in s
        notes.append(
            ("lead-in kept + numbered" if (has_numbered and lead_kept)
             else f"lead-in kept={lead_kept} numbered={has_numbered}")
        )
    return notes


for i, (label, transcript, expected) in enumerate(TESTS, 1):
    print(f"\n=== {i}/{len(TESTS)}  {label} ===")
    print(f"  input:   {transcript!r}")
    styled, usage = styler.style(transcript)
    provider = usage.get("provider", "?")
    print(f"  provider: {provider}")
    print(f"  output:\n    " + styled.replace("\n", "\n    "))
    notes = check(expected, transcript, styled)
    print(f"  check:   {'; '.join(notes)}")

    if i < len(TESTS):
        print(f"  -- sleep 30s --", flush=True)
        time.sleep(30)

print("\n=== done ===")
