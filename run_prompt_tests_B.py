#!/usr/bin/env python3
"""
VoiceFlow Prompt Quality Tester - Version B
Tests ADHD and Agentic prompts against 20 synthetic transcripts,
scores outputs, identifies weaknesses, and writes improved prompts.
"""

import json
import os
import time
from openai import OpenAI

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY = os.environ.get("OPENAI_API_KEY")
PROJECT_PATH = "/Users/tars/clawd/projects/voice-app-downloadable"
RESULTS_PATH = os.path.join(PROJECT_PATH, "test_results_B.json")
MODEL = "gpt-4o-mini"

client = OpenAI(api_key=API_KEY)

# ── Load prompts ──────────────────────────────────────────────────────────────
def load_prompt(name):
    path = os.path.join(PROJECT_PATH, "prompts", name)
    with open(path) as f:
        return f.read()

# ── Transcripts ───────────────────────────────────────────────────────────────
# REAL TEST CASE — test #1 for both modes
REAL_TEST = (
    "yes set up the synthetic transcript testing idea and then rate the responses out of 100 "
    "and improve based on them spawn multiple sub agents in to do this which are all working on "
    "the same thing in a different way and then send bugs improvements for the main dev to fix "
    "and then produce v9 however are we still still modes or just sticking to one because "
    "sometimes i feel like im rambling even this prompt which is a waste technically as it could "
    "be structured best for you to understand so would another mode like adhd or agentic "
    "engineering be better suited that style it in a different way to what the normal mode would "
    "do so is the normal mode for everyday use and then agentic mode for prompting do you get me "
    "you can even use this as a test for the agents and versions of this"
)

ADHD_TRANSCRIPTS = [
    # T1 — Real test case
    {"id": "A01", "type": "real_test_case", "transcript": REAL_TEST},

    # T2 — Long brain dump, 3+ topics
    {"id": "A02", "type": "long_multi_topic_brain_dump", "transcript": (
        "okay so i was thinking about the gym routine right and i need to sort that out "
        "maybe go Monday Wednesday Friday and then also i keep forgetting to drink water "
        "throughout the day which is bad anyway speaking of health i should book that dentist "
        "appointment i've been putting off for like three months now okay but actually the main "
        "thing i wanted to think about is this side project right so the idea is like a habit "
        "tracker but not a boring one something that actually gives you points like gamified "
        "and you can compete with friends and uh oh also i was going to say i need to email "
        "Sarah about the contract renewal before Friday that's important don't let me forget "
        "that and back to the habit tracker i was thinking it should sync with Apple Health "
        "because that way it can auto log workouts and sleep and then the gamification layer "
        "sits on top of that real data not fake self-reported stuff you know what i mean"
    )},

    # T3 — Heavy self-correction
    {"id": "A03", "type": "heavy_self_correction", "transcript": (
        "so the meeting is on Thursday no wait it's Wednesday yeah Wednesday at three pm "
        "actually no it was moved to four pm and it's in the Zoom link that Jane sent "
        "no actually it's Teams not Zoom she said Teams and the topic is the Q2 roadmap "
        "no not Q2 it's Q3 planning because Q2 is basically done the agenda includes "
        "the new pricing model and uh also the customer feedback from last sprint "
        "oh and there's a design review bit too Sarah is presenting the new onboarding flow "
        "actually i think it's Tom not Sarah i need to double check that"
    )},

    # T4 — Abandons idea, comes back to it
    {"id": "A04", "type": "idea_abandonment_and_return", "transcript": (
        "i want to write a blog post about productivity systems but actually let me "
        "first figure out the newsletter cadence so i was thinking weekly but then maybe "
        "biweekly is more sustainable for quality you know okay and then the newsletter "
        "should have a personal story section and then a tool recommendation and three "
        "quick links that's the format and the name should be something catchy not just "
        "'my newsletter' something like 'Second Brain Weekly' or 'The Clarity Letter' "
        "anyway back to that blog post idea from before i actually want to write about "
        "why most productivity systems fail specifically they fail because people optimise "
        "for capturing not for doing and that's the core thesis"
    )},

    # T5 — Mixed personal and work
    {"id": "A05", "type": "mixed_personal_work", "transcript": (
        "okay i need to think through the week ahead so work stuff first the PR for the "
        "auth refactor needs to be merged by Tuesday and i need to unblock Jamie on the "
        "backend endpoints he's been waiting for my review and that's blocking his sprint "
        "personally i need to call mum this weekend she's been asking about the holiday "
        "plans and we haven't decided if we're doing Portugal or Italy yet i lean Italy "
        "because the food is better and it's slightly cheaper flights right now "
        "back to work i should also ping the design team about the empty states we still "
        "haven't got assets for the error screen and the loading skeleton"
    )},

    # T6 — Topic hopping, 4 distinct topics
    {"id": "A06", "type": "four_topic_hop", "transcript": (
        "so climate change is actually a really interesting lens through which to look at "
        "urban planning and i've been reading about fifteen minute cities which is the idea "
        "that everything you need should be walkable within fifteen minutes and Paris is "
        "actually implementing this and it's fascinating then separately completely different "
        "topic i've been thinking about getting a mechanical keyboard the Keychron Q1 looks "
        "sick but it's two hundred quid and i'm not sure it's justified oh and i wanted to "
        "look into fasting protocols specifically five two versus sixteen eight for mental "
        "clarity not weight loss and also i keep meaning to set up Obsidian properly with "
        "the Zettelkasten method i tried once but gave up because linking notes felt tedious "
        "but i think the payoff is huge long term"
    )},

    # T7 — Highly fragmented, panic mode
    {"id": "A07", "type": "panic_fragmented", "transcript": (
        "wait wait wait okay so the deploy broke right the production deploy it's down "
        "users are seeing five hundreds okay so first we need to roll back "
        "no wait check the logs first actually Tyler said he saw a memory spike "
        "before it went down so maybe it's not the new deploy maybe it's the cron job "
        "that runs at midnight oh but we did push a new version of the image processor "
        "yesterday so it could be that the image processor plus the cron hitting at the same time "
        "okay so plan is check logs first then decide rollback or hotfix "
        "also someone needs to post a status update like right now the customers are pinging us"
    )},

    # T8 — Very slow, thoughtful, long pauses represented by ellipsis
    {"id": "A08", "type": "slow_thoughtful_ramble", "transcript": (
        "i think... what i'm really trying to say is that the current onboarding experience "
        "is too complex for new users... like there are seven steps and most people drop off "
        "around step four which is the integration setup... and i think the reason is not "
        "that the integration is hard... it's that we haven't explained the value before "
        "asking them to do the hard work... so the fix would be... show a demo first "
        "like a two minute interactive product tour... and then when they get to the integration "
        "step they already understand WHY they're doing it... and i think that would reduce "
        "dropout significantly... maybe forty percent reduction in drop-off... we could test it "
        "with an A/B... that would tell us pretty quickly"
    )},

    # T9 — Stream of consciousness with tangents
    {"id": "A09", "type": "stream_with_tangents", "transcript": (
        "so the book i'm reading right now is actually changing how i think about decision "
        "making it's called Thinking Fast and Slow Kahneman and the key insight is that "
        "system one thinking is automatic and emotional and system two is slow and deliberate "
        "and most of our mistakes come from using system one when we should use system two "
        "which is actually so relevant to product design because users operate in system one "
        "most of the time so if your product requires system two thinking to use it "
        "you've already lost them and this makes me want to audit our app for every "
        "place that requires deliberate thought and simplify it oh and i also need to "
        "buy the sequel it's called Noise also by Kahneman i think or maybe with a coauthor"
    )},

    # T10 — Contradictory instructions, final version should win
    {"id": "A10", "type": "contradictory_self_correcting", "transcript": (
        "we should launch the feature on Friday no actually Thursday gives us more "
        "buffer in case something breaks and we want to avoid weekend on-call "
        "the announcement should go on Twitter first no the newsletter first because "
        "our newsletter readers are the core users and they should hear it first "
        "the blog post should be technical no it should be accessible for a general audience "
        "because most people don't care about the implementation details they care about "
        "what it does for them the title should be something like 'Introducing X' "
        "no that's too boring make it benefit-led like 'Never lose a thought again' "
        "or something like that"
    )},
]

AGENTIC_TRANSCRIPTS = [
    # T11 — Real test case through agentic lens
    {"id": "G01", "type": "real_test_case_agentic", "transcript": REAL_TEST},

    # T12 — Crypto dashboard build
    {"id": "G02", "type": "clear_build_with_stack", "transcript": (
        "I want to build a dashboard that shows crypto prices updated every minute, "
        "using React, and it should have a dark theme and show percentage changes"
    )},

    # T13 — Vague idea, no stack
    {"id": "G03", "type": "vague_no_stack", "transcript": (
        "make something that tracks my habits"
    )},

    # T14 — Refactor with context
    {"id": "G04", "type": "refactor_with_context", "transcript": (
        "the current login is broken when the session expires"
    )},

    # T15 — Research comparison
    {"id": "G05", "type": "research_comparison", "transcript": (
        "should I use Postgres or MongoDB for this"
    )},

    # T16 — Multi-feature build, complex stack
    {"id": "G06", "type": "complex_multi_feature_build", "transcript": (
        "so i want to build a SaaS app for freelancers to track their time and invoice clients "
        "automatically it should be Next.js on the frontend probably with shadcn for the UI "
        "and then a Node Express backend with Postgres for the database "
        "the core flows are time tracking with a start stop timer per project "
        "and then generating a PDF invoice from the tracked time "
        "the invoice should include line items with hourly rate and total "
        "auth should be email plus password no OAuth for now "
        "payments is a phase two thing don't worry about that now "
        "the whole thing should be deployable on Railway"
    )},

    # T17 — Rambling refactor with implied requirements
    {"id": "G07", "type": "rambling_refactor", "transcript": (
        "okay so the whole file upload component is a mess right now "
        "it doesn't handle errors properly like if someone uploads a file that's too big "
        "it just silently fails which is terrible UX and then also "
        "we're not validating the file type on the frontend only on the backend "
        "so users get a confusing error after waiting for the upload to finish "
        "and also the progress bar doesn't actually reflect real progress it's fake "
        "it just animates to ninety percent and waits so i want to fix all of that "
        "keep the existing API contract don't change the endpoints "
        "just fix the frontend component to handle these edge cases properly"
    )},

    # T18 — Architecture decision, competing options
    {"id": "G08", "type": "architecture_decision", "transcript": (
        "i'm trying to decide between building a microservices architecture "
        "versus just keeping everything as a monolith for this new project "
        "it's a B2B SaaS tool expected maybe fifty enterprise clients to start "
        "my team is three engineers and we're early stage "
        "i'm leaning monolith because the team is small but i want to understand "
        "the tradeoffs especially around scaling and if we need to split later "
        "what's the migration path look like"
    )},

    # T19 — Agentic task with unclear scope
    {"id": "G09", "type": "unclear_scope_agentic", "transcript": (
        "can you just make the app faster you know what i mean "
        "it feels slow when you open it "
        "especially the dashboard page it takes like three seconds to load "
        "i think it's the API calls we're making too many at once "
        "but i'm not sure maybe it's the database queries "
        "can you profile it and fix the biggest bottlenecks"
    )},

    # T20 — Voice note that mixes build + research + question
    {"id": "G10", "type": "mixed_build_research_question", "transcript": (
        "so for the AI feature i'm thinking of adding to the app "
        "i need to figure out first should i use OpenAI or Anthropic "
        "cost is important because it'll be called like ten thousand times a day "
        "and then once i've picked the provider i want to build a rate limiter "
        "so we don't blow through the budget "
        "the rate limiter should be per user per day with a hard cap "
        "maybe like fifty requests per user per day in the free tier "
        "and the AI feature itself is a summarisation thing where users paste a URL "
        "and we fetch the article and summarise it in three bullet points "
        "also i want to cache the results so the same URL doesn't get summarised twice "
        "Redis for the cache probably"
    )},
]

ALL_TRANSCRIPTS = ADHD_TRANSCRIPTS + AGENTIC_TRANSCRIPTS


# ── LLM call ─────────────────────────────────────────────────────────────────
def call_llm(system_prompt, transcript):
    filled = system_prompt.replace("{transcript}", transcript)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": filled},
            {"role": "user", "content": "Process this transcript now."},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


# ── Scorer ────────────────────────────────────────────────────────────────────
SCORER_SYSTEM = """You are a strict quality evaluator for AI voice-to-text prompt outputs.

You will be given:
1. The original raw transcript
2. The processed output from an AI prompt
3. The prompt mode (adhd or agentic)

Score the output 0-100 on four dimensions, then give an overall:

ACCURACY (0-25): Did it capture every meaningful idea from the transcript?
  - 25: All ideas captured, nothing lost
  - 15: Most ideas captured, 1-2 minor omissions
  - 5: Significant information missing

FORMAT (0-25): Is the output in the RIGHT format for this content type?
  - 25: Perfect structure for the content type (bullet list, numbered steps, engineering prompt, etc.)
  - 15: Reasonable format but could be better
  - 5: Wrong format, hard to use

COMPLETENESS (0-25): No ideas lost, even ideas mentioned once mid-ramble?
  - 25: 100% of distinct ideas preserved
  - 15: ~80% preserved
  - 5: Key threads dropped

ACTIONABILITY (0-25): Could you paste this straight into Claude/Cursor and get good results?
  - 25: Paste-ready, extremely clear
  - 15: Needs minor tweaks
  - 5: Needs major rewrite before use

Return a JSON object exactly like this:
{
  "accuracy": <0-25>,
  "format": <0-25>,
  "completeness": <0-25>,
  "actionability": <0-25>,
  "total": <0-100>,
  "weaknesses": ["specific weakness 1", "specific weakness 2"],
  "notes": "one sentence overall assessment"
}
Return ONLY valid JSON. No explanation, no markdown fences."""


def score_output(transcript, output, mode):
    prompt = f"""Mode: {mode}

Original transcript:
{transcript}

Processed output:
{output}"""
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SCORER_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        return {"error": str(e), "raw": resp.choices[0].message.content, "total": 0}


# ── Run tests ─────────────────────────────────────────────────────────────────
def run_tests():
    adhd_prompt = load_prompt("adhd_ramble.txt")
    agentic_prompt = load_prompt("agentic_engineering.txt")

    results = {"adhd": [], "agentic": [], "meta": {}}

    print("=" * 60)
    print("RUNNING ADHD PROMPT TESTS")
    print("=" * 60)
    for t in ADHD_TRANSCRIPTS:
        print(f"\n[{t['id']}] {t['type']}")
        output = call_llm(adhd_prompt, t["transcript"])
        score = score_output(t["transcript"], output, "adhd")
        record = {
            "id": t["id"],
            "type": t["type"],
            "transcript": t["transcript"],
            "output": output,
            "score": score,
        }
        results["adhd"].append(record)
        print(f"  Score: {score.get('total', '?')}/100  |  {score.get('notes', '')}")
        time.sleep(0.5)

    print("\n" + "=" * 60)
    print("RUNNING AGENTIC PROMPT TESTS")
    print("=" * 60)
    for t in AGENTIC_TRANSCRIPTS:
        print(f"\n[{t['id']}] {t['type']}")
        output = call_llm(agentic_prompt, t["transcript"])
        score = score_output(t["transcript"], output, "agentic")
        record = {
            "id": t["id"],
            "type": t["type"],
            "transcript": t["transcript"],
            "output": output,
            "score": score,
        }
        results["agentic"].append(record)
        print(f"  Score: {score.get('total', '?')}/100  |  {score.get('notes', '')}")
        time.sleep(0.5)

    return results


# ── Analyse weaknesses ────────────────────────────────────────────────────────
def analyse_weaknesses(results):
    all_weaknesses = []
    scores_adhd = [r["score"].get("total", 0) for r in results["adhd"]]
    scores_agentic = [r["score"].get("total", 0) for r in results["agentic"]]

    for r in results["adhd"] + results["agentic"]:
        ws = r["score"].get("weaknesses", [])
        all_weaknesses.extend(ws)

    # Count frequencies
    freq = {}
    for w in all_weaknesses:
        key = w.lower().strip()
        freq[key] = freq.get(key, 0) + 1

    top = sorted(freq.items(), key=lambda x: -x[1])[:10]

    return {
        "avg_adhd": round(sum(scores_adhd) / len(scores_adhd), 1) if scores_adhd else 0,
        "avg_agentic": round(sum(scores_agentic) / len(scores_agentic), 1) if scores_agentic else 0,
        "min_adhd": min(scores_adhd) if scores_adhd else 0,
        "max_adhd": max(scores_adhd) if scores_adhd else 0,
        "min_agentic": min(scores_agentic) if scores_agentic else 0,
        "max_agentic": max(scores_agentic) if scores_agentic else 0,
        "top_weaknesses": [w for w, _ in top],
        "all_weaknesses_raw": all_weaknesses,
    }


# ── Ask GPT to synthesise findings ───────────────────────────────────────────
ANALYST_SYSTEM = """You are a senior prompt engineer analysing test results for two voice-to-text prompts.

I will give you all test scores, outputs, and weakness notes.
Synthesise the findings into:
1. Top 3 specific weaknesses in the ADHD prompt
2. Top 3 specific weaknesses in the Agentic prompt
3. Specific improvements to make to each prompt

Be concrete. Reference specific transcript types where the prompt failed.
Return JSON:
{
  "adhd_weaknesses": ["weakness 1", "weakness 2", "weakness 3"],
  "agentic_weaknesses": ["weakness 1", "weakness 2", "weakness 3"],
  "adhd_improvements": ["improvement 1", "improvement 2", "improvement 3"],
  "agentic_improvements": ["improvement 1", "improvement 2", "improvement 3"],
  "summary": "2-3 sentence overall summary"
}"""


def synthesise_findings(results, analysis):
    # Build compact summary for analyst
    adhd_summary = []
    for r in results["adhd"]:
        adhd_summary.append({
            "id": r["id"],
            "type": r["type"],
            "score": r["score"].get("total"),
            "output_preview": r["output"][:300],
            "weaknesses": r["score"].get("weaknesses", []),
            "notes": r["score"].get("notes", ""),
        })

    agentic_summary = []
    for r in results["agentic"]:
        agentic_summary.append({
            "id": r["id"],
            "type": r["type"],
            "score": r["score"].get("total"),
            "output_preview": r["output"][:300],
            "weaknesses": r["score"].get("weaknesses", []),
            "notes": r["score"].get("notes", ""),
        })

    payload = json.dumps({
        "adhd_results": adhd_summary,
        "agentic_results": agentic_summary,
        "pre_analysis": analysis,
    }, indent=2)

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": ANALYST_SYSTEM},
            {"role": "user", "content": payload},
        ],
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


# ── Generate improved prompts ─────────────────────────────────────────────────
IMPROVER_SYSTEM = """You are a master prompt engineer. You will be given:
1. The original prompt
2. Test failures and specific weaknesses identified
3. Improvement suggestions

Write an improved version of the prompt that fixes ALL identified weaknesses.
The improved prompt must:
- Keep the same general structure and intent
- Add explicit handling for the failure cases
- Be more robust for edge cases (very short input, contradictions, panic mode, mixed topics)
- Use the same {transcript} placeholder
- Return ONLY the improved prompt text. No explanation, no markdown fences, no preamble."""


def improve_prompt(original_prompt, findings_key, findings):
    weaknesses = findings.get(f"{findings_key}_weaknesses", [])
    improvements = findings.get(f"{findings_key}_improvements", [])

    payload = f"""ORIGINAL PROMPT:
{original_prompt}

IDENTIFIED WEAKNESSES:
{chr(10).join(f'- {w}' for w in weaknesses)}

SUGGESTED IMPROVEMENTS:
{chr(10).join(f'- {i}' for i in improvements)}

Write the improved prompt now."""

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": IMPROVER_SYSTEM},
            {"role": "user", "content": payload},
        ],
        temperature=0.3,
    )
    return resp.choices[0].message.content.strip()


# ── Re-test improved prompts (sample 5 each) ─────────────────────────────────
def retest_improved(improved_adhd, improved_agentic, results):
    print("\n" + "=" * 60)
    print("RE-TESTING IMPROVED PROMPTS (sample)")
    print("=" * 60)

    retest_results = {"adhd_v2": [], "agentic_v2": []}

    # Pick 5 ADHD transcripts (worst scorers first)
    adhd_sorted = sorted(results["adhd"], key=lambda r: r["score"].get("total", 100))
    agentic_sorted = sorted(results["agentic"], key=lambda r: r["score"].get("total", 100))

    for r in adhd_sorted[:5]:
        t = r["transcript"]
        output = call_llm(improved_adhd, t)
        score = score_output(t, output, "adhd")
        retest_results["adhd_v2"].append({
            "id": r["id"],
            "type": r["type"],
            "v1_score": r["score"].get("total", 0),
            "v2_score": score.get("total", 0),
            "improvement": score.get("total", 0) - r["score"].get("total", 0),
            "output": output,
            "score": score,
        })
        print(f"  [{r['id']}] v1:{r['score'].get('total')} → v2:{score.get('total')}  Δ{score.get('total',0)-r['score'].get('total',0):+d}")
        time.sleep(0.5)

    for r in agentic_sorted[:5]:
        t = r["transcript"]
        output = call_llm(improved_agentic, t)
        score = score_output(t, output, "agentic")
        retest_results["agentic_v2"].append({
            "id": r["id"],
            "type": r["type"],
            "v1_score": r["score"].get("total", 0),
            "v2_score": score.get("total", 0),
            "improvement": score.get("total", 0) - r["score"].get("total", 0),
            "output": output,
            "score": score,
        })
        print(f"  [{r['id']}] v1:{r['score'].get('total')} → v2:{score.get('total')}  Δ{score.get('total',0)-r['score'].get('total',0):+d}")
        time.sleep(0.5)

    return retest_results


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("\n🎤 VoiceFlow Prompt Quality Tester — Session B")
    print(f"   Model: {MODEL}")
    print(f"   Transcripts: {len(ALL_TRANSCRIPTS)} total ({len(ADHD_TRANSCRIPTS)} ADHD + {len(AGENTIC_TRANSCRIPTS)} agentic)")

    # 1. Run all tests
    results = run_tests()

    # 2. Analyse
    print("\n📊 Analysing weaknesses...")
    analysis = analyse_weaknesses(results)
    print(f"  Avg ADHD score:    {analysis['avg_adhd']}/100")
    print(f"  Avg Agentic score: {analysis['avg_agentic']}/100")

    # 3. Synthesise findings
    print("\n🔬 Synthesising findings with GPT...")
    findings = synthesise_findings(results, analysis)
    print("\nTop ADHD weaknesses:")
    for w in findings.get("adhd_weaknesses", []):
        print(f"  ✗ {w}")
    print("\nTop Agentic weaknesses:")
    for w in findings.get("agentic_weaknesses", []):
        print(f"  ✗ {w}")

    # 4. Improve prompts
    print("\n✍️  Writing improved prompts...")
    adhd_prompt_orig = load_prompt("adhd_ramble.txt")
    agentic_prompt_orig = load_prompt("agentic_engineering.txt")

    improved_adhd = improve_prompt(adhd_prompt_orig, "adhd", findings)
    improved_agentic = improve_prompt(agentic_prompt_orig, "agentic", findings)

    # Save improved prompts
    with open(os.path.join(PROJECT_PATH, "prompts", "adhd_ramble_v2.txt"), "w") as f:
        f.write(improved_adhd)
    with open(os.path.join(PROJECT_PATH, "prompts", "agentic_engineering_v2.txt"), "w") as f:
        f.write(improved_agentic)
    print("  ✅ Saved adhd_ramble_v2.txt")
    print("  ✅ Saved agentic_engineering_v2.txt")

    # 5. Re-test improved prompts
    retest = retest_improved(improved_adhd, improved_agentic, results)

    # 6. Calculate overall improvement
    v2_adhd_scores = [r["v2_score"] for r in retest["adhd_v2"]]
    v2_agentic_scores = [r["v2_score"] for r in retest["agentic_v2"]]
    v1_adhd_sample = [r["v1_score"] for r in retest["adhd_v2"]]
    v1_agentic_sample = [r["v1_score"] for r in retest["agentic_v2"]]

    avg_v1_adhd = round(sum(v1_adhd_sample) / len(v1_adhd_sample), 1) if v1_adhd_sample else 0
    avg_v2_adhd = round(sum(v2_adhd_scores) / len(v2_adhd_scores), 1) if v2_adhd_scores else 0
    avg_v1_agentic = round(sum(v1_agentic_sample) / len(v1_agentic_sample), 1) if v1_agentic_sample else 0
    avg_v2_agentic = round(sum(v2_agentic_scores) / len(v2_agentic_scores), 1) if v2_agentic_scores else 0

    # 7. Save full results
    full_results = {
        "meta": {
            "model": MODEL,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_transcripts": len(ALL_TRANSCRIPTS),
            "adhd_count": len(ADHD_TRANSCRIPTS),
            "agentic_count": len(AGENTIC_TRANSCRIPTS),
        },
        "adhd_results": results["adhd"],
        "agentic_results": results["agentic"],
        "analysis": analysis,
        "findings": findings,
        "retest": retest,
        "summary": {
            "avg_adhd_v1": analysis["avg_adhd"],
            "avg_adhd_v2_sample": avg_v2_adhd,
            "avg_adhd_improvement_sample": round(avg_v2_adhd - avg_v1_adhd, 1),
            "avg_agentic_v1": analysis["avg_agentic"],
            "avg_agentic_v2_sample": avg_v2_agentic,
            "avg_agentic_improvement_sample": round(avg_v2_agentic - avg_v1_agentic, 1),
            "top_3_weaknesses": findings.get("adhd_weaknesses", [])[:3],
            "top_3_agentic_weaknesses": findings.get("agentic_weaknesses", [])[:3],
        },
    }

    with open(RESULTS_PATH, "w") as f:
        json.dump(full_results, f, indent=2)
    print(f"\n💾 Results saved to {RESULTS_PATH}")

    # ── Final report ──────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("📋 FINAL REPORT")
    print("=" * 60)
    print(f"\n📊 Scores Before:")
    print(f"   ADHD prompt avg:    {analysis['avg_adhd']}/100  (range {analysis['min_adhd']}-{analysis['max_adhd']})")
    print(f"   Agentic prompt avg: {analysis['avg_agentic']}/100  (range {analysis['min_agentic']}-{analysis['max_agentic']})")
    print(f"\n📊 Scores After (on weakest 5 each):")
    print(f"   ADHD v2 avg:    {avg_v2_adhd}/100  (was {avg_v1_adhd})")
    print(f"   Agentic v2 avg: {avg_v2_agentic}/100  (was {avg_v1_agentic})")
    print(f"\n🔴 Top 3 ADHD weaknesses:")
    for i, w in enumerate(findings.get("adhd_weaknesses", [])[:3], 1):
        print(f"   {i}. {w}")
    print(f"\n🔴 Top 3 Agentic weaknesses:")
    for i, w in enumerate(findings.get("agentic_weaknesses", [])[:3], 1):
        print(f"   {i}. {w}")
    print(f"\n✅ Improvements made:")
    for i, imp in enumerate(findings.get("adhd_improvements", [])[:3], 1):
        print(f"   ADHD {i}: {imp}")
    for i, imp in enumerate(findings.get("agentic_improvements", [])[:3], 1):
        print(f"   Agentic {i}: {imp}")
    print(f"\n📝 Summary: {findings.get('summary', '')}")
    print("\n✅ All done!")


if __name__ == "__main__":
    main()
