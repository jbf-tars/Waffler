#!/usr/bin/env python3
"""
Evaluate current smart.txt prompt against real test cases,
identify failure patterns, write improved prompt.
"""

import json
import os
import time
from pathlib import Path
from openai import OpenAI

OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_KEY)

PROJECT = Path("/Users/tars/Desktop/waffler")
SMART_PROMPT_PATH = PROJECT / "prompts" / "smart.txt"
OUTPUT_PATH = PROJECT / "prompts" / "smart_realdata.txt"

# ── Test cases covering James's real usage patterns ─────────────────────────
# These include:
# 1. The known bad example (conversational question → bullet points)
# 2. Conversational questions
# 3. Analytical thinking / brain dumps
# 4. Self-corrections
# 5. Actual lists (shopping etc.)
# 6. Quick reminders
# 7. Single flowing thoughts

TEST_CASES = [
    # ── KNOWN BAD EXAMPLE (from James directly) ──────────────────────────────
    {
        "id": "BAD_001",
        "label": "Known bad: question → bullets",
        "raw": "okay also I'd like I'd like it to like how does it store transcripts that I use because I just used this and it converted everything to bullet points so how can we analyze what's going on or use the transcripts where do they get stored and how can we use them to train a better prompt",
        "expected_format": "prose_question",
        "expected_notes": "Should be one clean prose question, not bullet points. Never summarise. Just clean up the speech.",
        "good_example": "How does it store the transcripts? I just used it and it converted everything to bullet points — so where do they get stored, and how can we use them to train a better prompt?",
    },

    # ── CONVERSATIONAL QUESTIONS (should be prose questions) ─────────────────
    {
        "id": "Q_001",
        "label": "Question about pricing",
        "raw": "so like I've been thinking and I want to know how does the pricing actually work for this because like are there different tiers and like what happens if I go over the limit or whatever",
        "expected_format": "prose_question",
        "expected_notes": "Clean prose question. No bullets.",
        "good_example": "How does the pricing work? Are there different tiers, and what happens if I go over the limit?",
    },
    {
        "id": "Q_002",
        "label": "Multi-part question about a feature",
        "raw": "okay so like how does the hotkey actually work and like is it configurable and can I change it to something else and also like does it work when I'm in full screen mode",
        "expected_format": "prose_question",
        "expected_notes": "Clean prose question, all parts kept. Not bulleted.",
        "good_example": "How does the hotkey work? Is it configurable, can I change it, and does it work in full screen mode?",
    },
    {
        "id": "Q_003",
        "label": "Question with analytical thinking",
        "raw": "I'm wondering like whether we should go with postgres or mongo for this because like postgres is more structured but mongo is more flexible and I don't know which one makes more sense for this use case",
        "expected_format": "prose",
        "expected_notes": "Analytical thinking — clean prose sentences, not a bulleted comparison.",
        "good_example": "I'm wondering whether to use Postgres or MongoDB — Postgres is more structured but MongoDB is more flexible, and I'm not sure which makes more sense for this use case.",
    },

    # ── ANALYTICAL / THINKING OUT LOUD (prose paragraphs) ─────────────────
    {
        "id": "A_001",
        "label": "Single analytical thought",
        "raw": "so basically I think the onboarding is broken because users are coming in but they're not activating and I think the problem is we're showing too many steps too early and they get overwhelmed before they see the value",
        "expected_format": "prose",
        "expected_notes": "One flowing analytical paragraph. Not bullets.",
        "good_example": "I think the onboarding is broken — users are coming in but not activating. The problem is we're showing too many steps too early and they get overwhelmed before they see the value.",
    },
    {
        "id": "A_002",
        "label": "Brain dump — multiple points (SHOULD be prose paragraphs, not bullets)",
        "raw": "right brain dump time so number one I keep thinking about how competitors are positioning like the main one just dropped their price by twenty percent which is aggressive do we respond I don't think we should because racing to the bottom is bad and two like our differentiation is quality not price so we shouldn't let them drag us there and three like customers who choose us on price will churn when a cheaper option appears anyway",
        "expected_format": "prose",
        "expected_notes": "Despite 'number one, two, three' framing — this is analytical thinking, not a shopping list. Should be clean prose sentences or short paragraphs.",
        "good_example": "A competitor just dropped their price by 20%, which is aggressive — but I don't think we should respond. Our differentiation is quality, not price, so we shouldn't let them drag us down. Besides, customers who choose us on price will churn the moment something cheaper appears.",
    },
    {
        "id": "A_003",
        "label": "Thinking about a decision",
        "raw": "so the thing I've been going back and forth on is whether to hire a designer now or wait until after the next funding round because on one hand the product desperately needs design love but on the other hand cash runway is tight and I need to be careful about burn",
        "expected_format": "prose",
        "expected_notes": "Decision-making thought in prose. Not bullets.",
        "good_example": "I've been going back and forth on whether to hire a designer now or wait until after the next funding round. The product desperately needs design work, but cash runway is tight and I need to watch the burn.",
    },

    # ── SELF-CORRECTIONS (use the final version) ───────────────────────────
    {
        "id": "SC_001",
        "label": "Self-correction mid-thought",
        "raw": "so the feature should launch in Q1 — actually no wait, Q2 is more realistic — hmm actually let's say end of Q2 to give ourselves room",
        "expected_format": "prose",
        "expected_notes": "Use final decision. Not bullets.",
        "good_example": "The feature should launch by end of Q2.",
    },
    {
        "id": "SC_002",
        "label": "Self-correction on who to assign",
        "raw": "I should assign this to Tom — actually Tom is already overloaded — what about Sarah — she's on leave this week — okay Dave then — yeah Dave can do this, he's been looking for something new to own",
        "expected_format": "prose",
        "expected_notes": "Only the final answer: Dave. Not a list of people considered.",
        "good_example": "Assign this to Dave.",
    },

    # ── ACTUAL LISTS (these SHOULD be bullets) ─────────────────────────────
    {
        "id": "L_001",
        "label": "Shopping list",
        "raw": "um, I need, uh, bananas, cereal, and yogurt. That's it.",
        "expected_format": "bullet_list",
        "expected_notes": "Actual enumerated items — bullet list is correct here.",
        "good_example": "- Bananas\n- Cereal\n- Yogurt",
    },
    {
        "id": "L_002",
        "label": "Grocery run with self-correction",
        "raw": "Right, grocery run. I need, uh, apples, pears, some strawberries, Greek yogurt, granola, almond milk, and, uh, like, coffee pods — the Nespresso ones, not the, uh, not the cheap ones.",
        "expected_format": "bullet_list",
        "expected_notes": "Clear shopping list — bullets correct.",
        "good_example": "- Apples\n- Pears\n- Strawberries\n- Greek yogurt\n- Granola\n- Almond milk\n- Coffee pods (Nespresso)",
    },
    {
        "id": "L_003",
        "label": "Three things quickly",
        "raw": "Three things: coffee, milk, paracetamol.",
        "expected_format": "bullet_list",
        "expected_notes": "Short list — bullets.",
        "good_example": "- Coffee\n- Milk\n- Paracetamol",
    },

    # ── QUICK REMINDERS / TASKS (single sentence) ─────────────────────────
    {
        "id": "R_001",
        "label": "Simple reminder",
        "raw": "Remind me to call the dentist tomorrow morning.",
        "expected_format": "single_sentence",
        "expected_notes": "One clean sentence.",
        "good_example": "Remind me to call the dentist tomorrow morning.",
    },
    {
        "id": "R_002",
        "label": "Reminder with filler",
        "raw": "um, note to self: the meeting with Sarah is moved to Thursday, not Wednesday. Thursday at two.",
        "expected_format": "single_sentence",
        "expected_notes": "Clean note.",
        "good_example": "The meeting with Sarah is moved to Thursday at 2.",
    },
    {
        "id": "R_003",
        "label": "Task with details",
        "raw": "don't forget to send the invoice to the client before end of day Friday",
        "expected_format": "single_sentence",
        "expected_notes": "Clean action item.",
        "good_example": "Send the invoice to the client before end of day Friday.",
    },

    # ── BUILD COMMANDS (agentic) ────────────────────────────────────────────
    {
        "id": "CMD_001",
        "label": "Build request",
        "raw": "I want to build a simple web app that lets users upload a CSV file and it automatically generates charts from the data. Should work in the browser, no backend needed, just JavaScript. Show bar charts, line charts, auto-detect column types.",
        "expected_format": "single_clear_command",
        "expected_notes": "One clear actionable sentence with all requirements.",
        "good_example": "Build a browser-only JavaScript web app where users upload a CSV file and it auto-generates bar and line charts, auto-detecting column types.",
    },

    # ── EDGE CASES ────────────────────────────────────────────────────────
    {
        "id": "E_001",
        "label": "Empty / noise",
        "raw": "um... uh...",
        "expected_format": "empty",
        "expected_notes": "Return empty string.",
        "good_example": "",
    },
    {
        "id": "E_002",
        "label": "Single test word",
        "raw": "hello",
        "expected_format": "empty",
        "expected_notes": "Testing the mic — return empty.",
        "good_example": "",
    },
    {
        "id": "E_003",
        "label": "Mixed question and action",
        "raw": "okay so can you like check what the current balance is in the account and also like remind me when the payment is due",
        "expected_format": "prose",
        "expected_notes": "Two requests in prose, not bullets.",
        "good_example": "Can you check the current account balance and remind me when the payment is due?",
    },
]


def load_prompt(path: Path) -> str:
    with open(path) as f:
        return f.read()


def run_prompt(prompt_template: str, transcript: str) -> str:
    prompt = prompt_template.replace("{transcript}", transcript)
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.3,  # Lower temp for more consistent evaluation
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"ERROR: {e}"


def score_output(case: dict, output: str) -> tuple[int, str]:
    """Score an output against expected format. Returns (score, reason)."""
    expected = case["expected_format"]
    
    has_bullets = output.startswith("- ") or "\n- " in output
    is_empty = output.strip() == ""
    line_count = len([l for l in output.split("\n") if l.strip()])

    if expected == "empty":
        if is_empty:
            return 100, "Correctly empty"
        return 10, f"Should be empty, got: {output[:60]}"

    if expected == "bullet_list":
        if has_bullets:
            return 90, "Correct bullet format"
        return 30, f"Expected bullets, got prose: {output[:80]}"

    if expected in ("prose", "prose_question", "single_sentence", "single_clear_command"):
        if has_bullets:
            return 10, f"WRONG FORMAT: got bullets for {expected}. Output: {output[:100]}"
        if is_empty:
            return 20, "Incorrectly empty"
        # Check it's not summarizing (i.e. content matches raw)
        raw_words = set(case["raw"].lower().split())
        output_words = set(output.lower().split())
        # Output should share significant vocabulary with raw (not be a summary)
        if len(raw_words) > 10:
            overlap = len(raw_words & output_words) / len(raw_words)
            if overlap < 0.2:
                return 40, f"Possible summarization — low word overlap ({overlap:.0%})"
        # Good
        return 85, "Correct prose format"

    return 50, "Unknown expected format"


def evaluate_prompt(prompt_template: str, label: str) -> list[dict]:
    results = []
    print(f"\n{'='*60}")
    print(f"EVALUATING: {label}")
    print(f"{'='*60}")
    
    for case in TEST_CASES:
        print(f"\n[{case['id']}] {case['label']}")
        print(f"  Raw: {case['raw'][:80]}...")
        
        output = run_prompt(prompt_template, case["raw"])
        score, reason = score_output(case, output)
        
        print(f"  Output: {output[:120]}")
        print(f"  Score: {score}/100 — {reason}")
        
        results.append({
            "id": case["id"],
            "label": case["label"],
            "expected_format": case["expected_format"],
            "raw": case["raw"],
            "output": output,
            "score": score,
            "reason": reason,
            "good_example": case["good_example"],
        })
        time.sleep(0.3)  # Small delay to avoid rate limits
    
    avg = sum(r["score"] for r in results) / len(results)
    failures = [r for r in results if r["score"] < 60]
    
    print(f"\n{'='*60}")
    print(f"AVERAGE SCORE: {avg:.1f}/100")
    print(f"FAILURES ({len(failures)}/{len(results)}):")
    for f in failures:
        print(f"  [{f['id']}] {f['label']}: {f['score']}/100 — {f['reason']}")
    print(f"{'='*60}")
    
    return results


def analyze_failures(results: list[dict]) -> str:
    """Analyze failure patterns and return a summary."""
    failures = [r for r in results if r["score"] < 60]
    patterns = []
    
    bullet_when_prose = [r for r in failures if "bullets" in r["reason"].lower() and "WRONG" in r["reason"]]
    empty_wrong = [r for r in failures if "empty" in r["reason"].lower()]
    summary = [r for r in failures if "summariz" in r["reason"].lower()]
    
    if bullet_when_prose:
        patterns.append(f"❌ BULLET CREEP ({len(bullet_when_prose)} cases): Prose/question input wrongly formatted as bullet list")
        for r in bullet_when_prose:
            patterns.append(f"   [{r['id']}] {r['label']}")
            patterns.append(f"   Raw: {r['raw'][:80]}")
            patterns.append(f"   Got: {r['output'][:100]}")
    
    if summary:
        patterns.append(f"❌ SUMMARIZATION ({len(summary)} cases): Output summarized rather than cleaned")
        for r in summary:
            patterns.append(f"   [{r['id']}] {r['label']}")
    
    if empty_wrong:
        patterns.append(f"❌ WRONG EMPTY ({len(empty_wrong)} cases): Output incorrectly empty or vice versa")
    
    if not failures:
        patterns.append("✅ No major failures found")
    
    return "\n".join(patterns)


IMPROVED_PROMPT = """\
You are a voice-to-text cleanup tool. Your job is to clean up dictated speech — nothing more.

⚠️ CORE RULE: NEVER summarise, interpret, restructure, or add content. ONLY clean up what was said.

First, silently decide the type:
- **LIST** — explicitly enumerating concrete items (shopping lists, task lists, named items)
- **MESSAGE** — writing to someone (email, text, message)
- **COMMAND/TASK** — a single build/create/make instruction
- **QUESTION/THINKING** — questions, analytical thoughts, wondering out loud, thinking through a decision, brainstorming (even if it contains multiple sub-questions or points)
- **PROSE** — statements, stories, notes, observations
- **AGENTIC** — instructions dictated to an AI or agent

Then format accordingly:

**If LIST:**
Output as a clean bullet list. One concrete item per line. No preamble.
LIST means actual enumerable things — groceries, tasks, names. NOT questions, NOT analytical points.
Example:
- Bananas
- Cereal
- Milk

**If MESSAGE:**
Output as clean prose. Fix grammar, maintain tone. Remove filler.

**If COMMAND/TASK:**
Output as one clear actionable sentence with all requirements.

**If QUESTION/THINKING:**
Output as clean flowing prose — one or a few sentences. Keep all the sub-questions and ideas, just remove filler and clean up grammar.
NEVER use bullet points for questions, analysis, or thinking out loud.
NEVER summarise — preserve every idea the speaker expressed.
Even if they say "first... second... third..." — if it's analysis or questioning, write it as clean prose paragraphs, not bullets.
Example of a multi-part question:
  Raw: "like how does it store transcripts that I use because I just used this and it converted everything to bullet points so how can we analyze what's going on or use the transcripts where do they get stored and how can we use them to train a better prompt"
  Good: "How does it store the transcripts I record? I just used it and everything was converted to bullet points — so where do they get stored, and how can we use them to train a better prompt?"

**If PROSE:**
Clean up grammar and filler words. Output as natural flowing sentences. Do not restructure.

**If AGENTIC:**
Output clear instructions as dictated, preserving intent and specifics.

Rules that always apply:
- Remove all filler words: um, uh, like, you know, so, basically, literally, kind of, sort of.
- Use the FINAL version when they corrected themselves (ignore "wait", "no", "actually" reversals — only keep the final settled decision).
- Never add content they didn't say.
- Never add meta-commentary or explain what you did.
- If the audio was noise, filler only, or single test words, return an empty string.
- Preserve specifics exactly: numbers, dates, names, product names, prices.
- When in doubt between LIST and PROSE/QUESTION — if there's any analytical reasoning, wondering, or sub-questions, it's QUESTION/THINKING (prose), not LIST.

Transcript: {transcript}\
"""


def main():
    print("Loading current smart.txt prompt...")
    current_prompt = load_prompt(SMART_PROMPT_PATH)
    
    print("\n" + "="*60)
    print("PHASE 1: Evaluate current prompt")
    print("="*60)
    current_results = evaluate_prompt(current_prompt, "smart.txt (current)")
    current_avg = sum(r["score"] for r in current_results) / len(current_results)
    
    print("\n" + "="*60)
    print("FAILURE PATTERN ANALYSIS")
    print("="*60)
    print(analyze_failures(current_results))
    
    print("\n" + "="*60)
    print("PHASE 2: Evaluate improved prompt")
    print("="*60)
    improved_results = evaluate_prompt(IMPROVED_PROMPT, "smart_realdata.txt (improved)")
    improved_avg = sum(r["score"] for r in improved_results) / len(improved_results)
    
    print("\n" + "="*60)
    print("COMPARISON")
    print("="*60)
    print(f"Current smart.txt:   {current_avg:.1f}/100")
    print(f"Improved prompt:     {improved_avg:.1f}/100")
    print(f"Delta: {improved_avg - current_avg:+.1f}")
    
    # Per-case comparison
    print("\nPer-case comparison:")
    for curr, impr in zip(current_results, improved_results):
        delta = impr["score"] - curr["score"]
        if delta != 0:
            arrow = "✅ improved" if delta > 0 else "❌ worse"
            print(f"  [{curr['id']}] {curr['label']}: {curr['score']} → {impr['score']} ({delta:+d}) {arrow}")
    
    # Save improved prompt
    print(f"\nSaving improved prompt to {OUTPUT_PATH}")
    with open(OUTPUT_PATH, "w") as f:
        f.write(IMPROVED_PROMPT)
    print("✅ Saved smart_realdata.txt")
    
    # If improved, overwrite smart.txt
    if improved_avg > current_avg:
        print(f"\n✅ Improved prompt scores better ({improved_avg:.1f} > {current_avg:.1f})")
        print(f"Copying smart_realdata.txt → smart.txt")
        import shutil
        shutil.copy(OUTPUT_PATH, SMART_PROMPT_PATH)
        print("✅ smart.txt updated!")
    else:
        print(f"\n⚠️  Improved prompt does NOT score better ({improved_avg:.1f} <= {current_avg:.1f})")
        print("smart.txt NOT updated. Review smart_realdata.txt manually.")
    
    # Save full results
    results_path = PROJECT / "eval_results_realdata.json"
    with open(results_path, "w") as f:
        json.dump({
            "current_avg": current_avg,
            "improved_avg": improved_avg,
            "delta": improved_avg - current_avg,
            "current_results": current_results,
            "improved_results": improved_results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\nFull results saved to {results_path}")


if __name__ == "__main__":
    main()
