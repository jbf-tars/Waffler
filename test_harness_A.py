#!/usr/bin/env python3
"""
VoiceFlow Prompt Quality Tester - Round A
Tests smart.txt and normal.txt against 20 synthetic transcripts,
scores outputs, identifies weaknesses, and generates improved prompts.
"""

import json
import os
import sys
import time
from openai import OpenAI

# ── Config ────────────────────────────────────────────────────────────────────
PROJECT_PATH = "/Users/tars/clawd/projects/voice-app-downloadable"
API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = "gpt-4o-mini"

client = OpenAI(api_key=API_KEY)

# ── Load prompts ──────────────────────────────────────────────────────────────
with open(f"{PROJECT_PATH}/prompts/smart.txt") as f:
    SMART_PROMPT = f.read()

with open(f"{PROJECT_PATH}/prompts/normal.txt") as f:
    NORMAL_PROMPT = f.read()

# ── Test transcripts (20 cases) ───────────────────────────────────────────────
TRANSCRIPTS = [
    {
        "id": 1,
        "type": "ADHD_RAMBLE",
        "label": "Real test case — ADHD ramble (Smart mode)",
        "transcript": "yes set up the synthetic transcript testing idea and then rate the responses out of 100 and improve based on them spawn multiple sub agents in to do this which are all working on the same thing in a different way and then send bugs improvements for the main dev to fix and then produce v9 however are we still still modes or just sticking to one because sometimes i feel like im rambling even this prompt which is a waste technically as it could be structured best for you to understand so would another mode like adhd or agentic engineering be better suited that style it in a different way to what the normal mode would do so is the normal mode for everyday use and then agentic mode for prompting do you get me you can even use this as a test for the agents and versions of this",
        "expected_smart": "NOTES or structured bullets capturing: synthetic testing idea, rate responses, improve, spawn sub-agents, bugs to dev, produce v9, question about modes (normal everyday vs agentic for prompting), suggestion for ADHD/agentic mode",
        "expected_normal": "Cleaned prose version of the ramble, no bullets, filler removed",
    },
    {
        "id": 2,
        "type": "SHOPPING_LIST",
        "label": "Shopping list — natural speech",
        "transcript": "um so I need to get bananas and also milk oh and cereal and I think we're out of bread too and maybe some yogurt if it's on sale",
        "expected_smart": "Bullet list: Bananas, Milk, Cereal, Bread, Yogurt (if on sale)",
        "expected_normal": "Clean prose: I need to get bananas, milk, cereal, and bread, and maybe some yogurt if it's on sale.",
    },
    {
        "id": 3,
        "type": "QUICK_REMINDER",
        "label": "Quick reminder",
        "transcript": "remind me to call mum at 5",
        "expected_smart": "COMMAND: Remind me to call mum at 5.",
        "expected_normal": "Remind me to call mum at 5.",
    },
    {
        "id": 4,
        "type": "MESSAGE_TO_SOMEONE",
        "label": "Short message to John",
        "transcript": "tell John I'll be 10 minutes late",
        "expected_smart": "MESSAGE: Hey John, I'll be about 10 minutes late.",
        "expected_normal": "Tell John I'll be 10 minutes late.",
    },
    {
        "id": 5,
        "type": "SIMPLE_TASK",
        "label": "Simple computer task",
        "transcript": "create a folder called Projects on my desktop",
        "expected_smart": "COMMAND: Create a folder called 'Projects' on my desktop.",
        "expected_normal": "Create a folder called Projects on my desktop.",
    },
    {
        "id": 6,
        "type": "NOISE_GARBAGE",
        "label": "Single word noise — hello",
        "transcript": "hello",
        "expected_smart": "Empty string or minimal output",
        "expected_normal": "Hello. (or empty)",
    },
    {
        "id": 7,
        "type": "NOISE_GARBAGE",
        "label": "Testing filler noise",
        "transcript": "testing testing one two three",
        "expected_smart": "Empty string (noise/test input)",
        "expected_normal": "Empty or 'Testing, testing, one, two, three.'",
    },
    {
        "id": 8,
        "type": "NOISE_GARBAGE",
        "label": "Pure filler — um",
        "transcript": "um",
        "expected_smart": "Empty string",
        "expected_normal": "Empty string",
    },
    {
        "id": 9,
        "type": "MIXED_CONTENT",
        "label": "Mixed — starts reminder, pivots to list",
        "transcript": "so remind me to um buy stuff for the party actually no wait let me just make a list um we need cups plates napkins oh and balloons and some snacks like crisps and maybe dip",
        "expected_smart": "Bullet list (party supplies): Cups, Plates, Napkins, Balloons, Crisps/snacks, Dip",
        "expected_normal": "We need cups, plates, napkins, balloons, crisps, and maybe some dip.",
    },
    {
        "id": 10,
        "type": "NUMBERS_SPECIFICS",
        "label": "Meeting with numbers and date",
        "transcript": "the meeting is at 3pm on Thursday the 20th in room 4B and we need at least 6 people there",
        "expected_smart": "NOTES or clean sentence with all specifics preserved",
        "expected_normal": "The meeting is at 3pm on Thursday the 20th in room 4B, and we need at least 6 people there.",
    },
    {
        "id": 11,
        "type": "VERY_SHORT",
        "label": "1 word — name",
        "transcript": "pizza",
        "expected_smart": "Pizza (minimal, possibly list item)",
        "expected_normal": "Pizza.",
    },
    {
        "id": 12,
        "type": "VERY_SHORT",
        "label": "2-3 word command",
        "transcript": "call mum",
        "expected_smart": "Call mum.",
        "expected_normal": "Call mum.",
    },
    {
        "id": 13,
        "type": "VERY_LONG",
        "label": "Long ramble — project ideas",
        "transcript": "so I've been thinking a lot about the new app idea and I think the core feature should be the voice interface because people are sick of typing everything and honestly if we can nail the voice input and make it feel as natural as talking to a real person then we're onto something really special and I was also thinking we could add a calendar sync so when you dictate something that sounds like an appointment it automatically gets added to your calendar and then there's the smart formatting which we already talked about but maybe we should also have different output modes like one for developers and one for regular users because they have very different needs and the developer mode could include things like code snippets or task breakdowns and the regular user mode would just be clean plain text or a shopping list or a reminder and I think monetisation could work through a subscription model maybe 5 pounds a month or we could do a freemium thing where basic features are free and power features are locked behind a paywall",
        "expected_smart": "NOTES with bullets grouped by topic: core feature (voice), calendar sync, smart formatting, output modes (dev vs regular), monetisation options",
        "expected_normal": "Long clean prose, filler removed, all key points preserved",
    },
    {
        "id": 14,
        "type": "EMAIL_DRAFT",
        "label": "Email to boss dictated",
        "transcript": "um so I wanted to let Sarah know that the report is going to be a day late because we're still waiting on the data from the finance team and I'll have it to her by end of day Friday",
        "expected_smart": "MESSAGE: Professional email tone. Hi Sarah, the report will be a day late — waiting on data from finance. I'll have it to you by end of day Friday.",
        "expected_normal": "I wanted to let Sarah know that the report is going to be a day late because we're still waiting on the data from the finance team. I'll have it to her by end of day Friday.",
    },
    {
        "id": 15,
        "type": "SELF_CORRECTION",
        "label": "Self-correction mid-sentence",
        "transcript": "add eggs to the shopping list wait no actually add eggs and butter and some cheese no wait cheddar specifically",
        "expected_smart": "List: Eggs, Butter, Cheddar",
        "expected_normal": "Add eggs, butter, and cheddar to the shopping list.",
    },
    {
        "id": 16,
        "type": "NUMBERS_SPECIFICS",
        "label": "Technical specs with numbers",
        "transcript": "the server needs 16 gigabytes of RAM minimum and at least 500 gigabytes of storage and it should be running Ubuntu 22.04",
        "expected_smart": "NOTES or COMMAND: The server needs 16GB RAM minimum, 500GB storage, running Ubuntu 22.04.",
        "expected_normal": "The server needs 16 gigabytes of RAM minimum and at least 500 gigabytes of storage, and it should be running Ubuntu 22.04.",
    },
    {
        "id": 17,
        "type": "TASK_COMPLEX",
        "label": "Multi-step task",
        "transcript": "I need you to book a table for four at Nobu for Friday night around 7 and if they're full try Saturday same time and let me know the confirmation number",
        "expected_smart": "COMMAND: Book a table for 4 at Nobu, Friday night ~7pm. If unavailable, try Saturday same time. Confirm the reservation number.",
        "expected_normal": "I need you to book a table for four at Nobu for Friday night around 7. If they're full, try Saturday at the same time and let me know the confirmation number.",
    },
    {
        "id": 18,
        "type": "EMOTIONAL_TONE",
        "label": "Frustrated personal note",
        "transcript": "I am so done with this project like it's been three weeks and nothing works and I just need to write down that we need a complete architecture review before we add any more features",
        "expected_smart": "NOTES: Needs a complete architecture review before adding more features. (strips frustration, keeps the actionable point)",
        "expected_normal": "I'm done with this project. It's been three weeks and nothing works. We need a complete architecture review before we add any more features.",
    },
    {
        "id": 19,
        "type": "SHOPPING_LIST_DETAILED",
        "label": "Shopping list with quantities",
        "transcript": "two litres of whole milk and three packs of chicken breast and like a big bag of rice and oh don't forget the washing up liquid we've run out",
        "expected_smart": "List: 2L whole milk, 3 packs chicken breast, Large bag of rice, Washing up liquid",
        "expected_normal": "Two litres of whole milk, three packs of chicken breast, a large bag of rice, and washing up liquid — we've run out.",
    },
    {
        "id": 20,
        "type": "AMBIGUOUS",
        "label": "Ambiguous — could be task or note",
        "transcript": "so the login page needs a forgot password link and maybe also a remember me checkbox",
        "expected_smart": "TASK or NOTES: Login page needs: Forgot Password link, Remember Me checkbox",
        "expected_normal": "The login page needs a forgot password link and maybe a remember me checkbox.",
    },
]

# ── Scoring rubric (via GPT-4o-mini itself for consistency) ───────────────────
SCORER_PROMPT = """You are a strict quality rater for a voice-to-text formatting system.

Rate the OUTPUT on a scale of 0–100 based on these four criteria (25 points each):

1. **ACCURACY (0-25):** Did the output capture all information from the transcript? (Nothing important missing, nothing false added)
2. **FORMAT (0-25):** Is the output in the right format for the content type? (list if it's a list, prose if it's prose, command if it's a command, empty if it's noise)
3. **CLEANLINESS (0-25):** Were filler words removed? Are self-corrections handled correctly? Is it clean and readable?
4. **NO_HALLUCINATION (0-25):** Did it avoid adding words/ideas not in the original?

Return JSON only, no explanation:
{
  "accuracy": <0-25>,
  "format": <0-25>,
  "cleanliness": <0-25>,
  "no_hallucination": <0-25>,
  "total": <0-100>,
  "notes": "<one sentence critique>"
}

Transcript: {transcript}
Expected output style: {expected}
Actual output: {output}
"""

def call_llm(system_prompt: str, user_content: str) -> str:
    """Call gpt-4o-mini with a system prompt and user content."""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.1,
        max_tokens=1000,
    )
    return response.choices[0].message.content.strip()

def score_output(transcript: str, expected: str, output: str) -> dict:
    """Score an output using the LLM-based rubric."""
    prompt = SCORER_PROMPT.replace("{transcript}", transcript)\
                           .replace("{expected}", expected)\
                           .replace("{output}", output)
    raw = call_llm("You are a strict JSON-only evaluator.", prompt)
    # strip markdown fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw.strip())
    except Exception:
        return {"accuracy": 0, "format": 0, "cleanliness": 0, "no_hallucination": 0, "total": 0, "notes": f"PARSE ERROR: {raw[:200]}"}

def run_prompt(prompt_template: str, transcript: str) -> str:
    """Fill in the template and run."""
    # If template has {transcript} placeholder, put system prompt without it
    # and transcript in user turn for cleaner API usage
    if "{transcript}" in prompt_template:
        system = prompt_template.replace("Transcript: {transcript}", "").rstrip()
        user = f"Transcript: {transcript}"
    else:
        system = prompt_template
        user = f"Transcript: {transcript}"
    return call_llm(system, user)

# ── Main test loop ────────────────────────────────────────────────────────────
results = []
smart_scores = []
normal_scores = []

print(f"\n{'='*60}")
print("VoiceFlow Prompt Tester — Round A")
print(f"Testing {len(TRANSCRIPTS)} transcripts × 2 prompts = {len(TRANSCRIPTS)*2} API calls")
print(f"{'='*60}\n")

for t in TRANSCRIPTS:
    print(f"[{t['id']:02d}/{len(TRANSCRIPTS)}] {t['label']}", flush=True)

    # Smart mode
    smart_output = run_prompt(SMART_PROMPT, t["transcript"])
    time.sleep(0.3)
    smart_score = score_output(t["transcript"], t["expected_smart"], smart_output)
    time.sleep(0.3)
    smart_scores.append(smart_score["total"])

    # Normal mode
    normal_output = run_prompt(NORMAL_PROMPT, t["transcript"])
    time.sleep(0.3)
    normal_score = score_output(t["transcript"], t["expected_normal"], normal_output)
    time.sleep(0.3)
    normal_scores.append(normal_score["total"])

    result = {
        "id": t["id"],
        "type": t["type"],
        "label": t["label"],
        "transcript": t["transcript"],
        "smart": {
            "output": smart_output,
            "expected": t["expected_smart"],
            "score": smart_score,
        },
        "normal": {
            "output": normal_output,
            "expected": t["expected_normal"],
            "score": normal_score,
        },
    }
    results.append(result)

    print(f"    SMART  [{smart_score['total']:3d}/100] {smart_score['notes']}", flush=True)
    print(f"    NORMAL [{normal_score['total']:3d}/100] {normal_score['notes']}", flush=True)
    print(flush=True)

# ── Aggregate analysis ────────────────────────────────────────────────────────
avg_smart = round(sum(smart_scores) / len(smart_scores), 1)
avg_normal = round(sum(normal_scores) / len(normal_scores), 1)

# Find worst performers
worst_smart = sorted(results, key=lambda x: x["smart"]["score"]["total"])[:5]
worst_normal = sorted(results, key=lambda x: x["normal"]["score"]["total"])[:5]

# ── Weakness analysis (via LLM) ───────────────────────────────────────────────
print("Analysing weaknesses...")

all_smart_critiques = "\n".join([
    f"[ID {r['id']} — {r['label']}]\n  Transcript: {r['transcript'][:100]}...\n  Output: {r['smart']['output'][:150]}...\n  Score: {r['smart']['score']['total']}/100\n  Critique: {r['smart']['score']['notes']}"
    for r in results
])

all_normal_critiques = "\n".join([
    f"[ID {r['id']} — {r['label']}]\n  Transcript: {r['transcript'][:100]}...\n  Output: {r['normal']['output'][:150]}...\n  Score: {r['normal']['score']['total']}/100\n  Critique: {r['normal']['score']['notes']}"
    for r in results
])

weakness_analysis_smart = call_llm(
    "You are a prompt engineering expert. Analyse these test results and identify the top 3 specific weaknesses in the SMART voice-to-text prompt. Be concrete and specific, not generic.",
    f"SMART PROMPT TEST RESULTS:\n{all_smart_critiques}\n\nList exactly 3 weaknesses, numbered."
)

weakness_analysis_normal = call_llm(
    "You are a prompt engineering expert. Analyse these test results and identify the top 3 specific weaknesses in the NORMAL voice-to-text prompt. Be concrete and specific, not generic.",
    f"NORMAL PROMPT TEST RESULTS:\n{all_normal_critiques}\n\nList exactly 3 weaknesses, numbered."
)

print("\nSMART weaknesses:")
print(weakness_analysis_smart)
print("\nNORMAL weaknesses:")
print(weakness_analysis_normal)

# ── Generate improved prompts ─────────────────────────────────────────────────
print("\nGenerating improved prompts...")

smart_v2 = call_llm(
    """You are a world-class prompt engineer specialising in voice-to-text systems. 
Rewrite this SMART voice-to-text prompt to fix the identified weaknesses.
Keep ALL the original intent but fix the problems. 
Output ONLY the new prompt text, nothing else.
Do NOT wrap in markdown or quotes.""",
    f"""ORIGINAL SMART PROMPT:
{SMART_PROMPT}

WEAKNESSES TO FIX:
{weakness_analysis_smart}

ADDITIONAL IMPROVEMENTS TO ADD:
- Better handling of ADHD-style rambles (identify the core actionable items)
- Noise/garbage detection should be more aggressive (single words that are clearly just testing should return empty)
- Self-corrections should be handled more explicitly
- For mixed-content inputs, always default to the FINAL intent of the speaker
- Add an AGENTIC type for when someone is clearly dictating instructions to an AI/agent
- Preserve important specifics: numbers, dates, names, product names exactly as spoken"""
)

normal_v2 = call_llm(
    """You are a world-class prompt engineer specialising in voice-to-text systems.
Rewrite this NORMAL voice-to-text prompt to fix the identified weaknesses.
Keep ALL the original intent but fix the problems.
Output ONLY the new prompt text, nothing else.
Do NOT wrap in markdown or quotes.""",
    f"""ORIGINAL NORMAL PROMPT:
{NORMAL_PROMPT}

WEAKNESSES TO FIX:
{weakness_analysis_normal}

ADDITIONAL IMPROVEMENTS TO ADD:
- Be explicit that self-corrections should use ONLY the final intended version (discard the corrected version entirely)
- Noise threshold: single words or pure filler with no meaningful content → return empty string
- Preserve all numbers, dates, names, and proper nouns exactly
- For very long rambles, condense run-on sentences but keep ALL the points
- Punctuation: use natural punctuation (commas, full stops) appropriately"""
)

# ── Save improved prompts ─────────────────────────────────────────────────────
with open(f"{PROJECT_PATH}/prompts/smart_v2.txt", "w") as f:
    f.write(smart_v2)

with open(f"{PROJECT_PATH}/prompts/normal_v2.txt", "w") as f:
    f.write(normal_v2)

print("Improved prompts saved.")

# ── Re-test with v2 prompts (spot check: worst 5 from each) ───────────────────
print("\nSpot-checking improved prompts on worst performers...")

spot_check_results_smart = []
spot_check_results_normal = []

for r in worst_smart[:5]:
    t = next(t for t in TRANSCRIPTS if t["id"] == r["id"])
    new_output = run_prompt(smart_v2, t["transcript"])
    time.sleep(0.3)
    new_score = score_output(t["transcript"], t["expected_smart"], new_output)
    time.sleep(0.3)
    spot_check_results_smart.append({
        "id": t["id"],
        "label": t["label"],
        "old_score": r["smart"]["score"]["total"],
        "new_score": new_score["total"],
        "new_output": new_output,
        "new_notes": new_score["notes"],
    })
    print(f"  SMART v2 [{t['id']:02d}] {r['smart']['score']['total']} → {new_score['total']} | {new_score['notes']}")

for r in worst_normal[:5]:
    t = next(t for t in TRANSCRIPTS if t["id"] == r["id"])
    new_output = run_prompt(normal_v2, t["transcript"])
    time.sleep(0.3)
    new_score = score_output(t["transcript"], t["expected_normal"], new_output)
    time.sleep(0.3)
    spot_check_results_normal.append({
        "id": t["id"],
        "label": t["label"],
        "old_score": r["normal"]["score"]["total"],
        "new_score": new_score["total"],
        "new_output": new_output,
        "new_notes": new_score["notes"],
    })
    print(f"  NORMAL v2 [{t['id']:02d}] {r['normal']['score']['total']} → {new_score['total']} | {new_score['notes']}")

# Estimated v2 averages based on spot check deltas
smart_delta = sum(s["new_score"] - s["old_score"] for s in spot_check_results_smart) / len(spot_check_results_smart) if spot_check_results_smart else 0
normal_delta = sum(s["new_score"] - s["old_score"] for s in spot_check_results_normal) / len(spot_check_results_normal) if spot_check_results_normal else 0
est_smart_v2 = round(avg_smart + (smart_delta * 0.5), 1)  # conservative estimate
est_normal_v2 = round(avg_normal + (normal_delta * 0.5), 1)

# ── Final JSON output ─────────────────────────────────────────────────────────
final_output = {
    "run_info": {
        "model": MODEL,
        "num_transcripts": len(TRANSCRIPTS),
        "prompts_tested": ["smart.txt", "normal.txt"],
        "improved_prompts": ["smart_v2.txt", "normal_v2.txt"],
    },
    "scores_summary": {
        "smart_v1_avg": avg_smart,
        "normal_v1_avg": avg_normal,
        "smart_v2_estimated_avg": est_smart_v2,
        "normal_v2_estimated_avg": est_normal_v2,
        "smart_individual": [{"id": r["id"], "label": r["label"], "score": r["smart"]["score"]["total"]} for r in results],
        "normal_individual": [{"id": r["id"], "label": r["label"], "score": r["normal"]["score"]["total"]} for r in results],
    },
    "weaknesses": {
        "smart_prompt": weakness_analysis_smart,
        "normal_prompt": weakness_analysis_normal,
    },
    "spot_checks_v2": {
        "smart": spot_check_results_smart,
        "normal": spot_check_results_normal,
    },
    "full_results": results,
    "improved_prompts": {
        "smart_v2": smart_v2,
        "normal_v2": normal_v2,
    },
}

with open(f"{PROJECT_PATH}/test_results_A.json", "w") as f:
    json.dump(final_output, f, indent=2)

print(f"\n{'='*60}")
print("RESULTS SAVED TO test_results_A.json")
print(f"{'='*60}")
print(f"\n📊 FINAL SUMMARY")
print(f"  SMART  v1 avg: {avg_smart}/100")
print(f"  NORMAL v1 avg: {avg_normal}/100")
print(f"  SMART  v2 avg (estimated): {est_smart_v2}/100")
print(f"  NORMAL v2 avg (estimated): {est_normal_v2}/100")
print(f"\n🔍 TOP 3 WEAKNESSES — SMART:")
print(weakness_analysis_smart)
print(f"\n🔍 TOP 3 WEAKNESSES — NORMAL:")
print(weakness_analysis_normal)
print(f"\n✅ Improved prompts written to prompts/smart_v2.txt and prompts/normal_v2.txt")
