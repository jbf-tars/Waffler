"""Self-correction regression harness for Waffler.

When the speaker corrects themselves mid-sentence, the wrong version
must be dropped from the output, not kept alongside the corrected one.

This covers many ways a speaker actually phrases corrections:
  - "X, sorry I mean Y"
  - "X, no wait, Y"
  - "X, actually Y"
  - "X — I meant Y"
  - "X, hmm no, Y"
  - implicit corrections without an explicit marker
  - multi-step corrections (X → Y → Z)
  - corrections inside lists / emails / questions
  - LEAD-IN corrections: the words the correction does NOT replace
    ("Let's meet on", "Send it to") must survive. Whisper often punctuates
    the correction as its own sentence ("Let's meet on Tuesday. No, wait,
    Wednesday."), and the model has been seen deleting the lead-in along
    with the wrong value ("Thursday at two."), which the truncation guard
    then refuses, so the user gets their uncorrected words pasted.

Drives the styler with text only, no Whisper involved.

A case PASSES only on the FINAL pasted output (after the truncation guard
and the deterministic layout passes), because that is what the user gets.
Separately, the harness captures the model's output BEFORE the truncation
guard (by wrapping the instance's _guard_truncation; src/ is untouched) and
reports a "model-correct" rate on it, so a prompt change can be judged even
when the guard hides the model's answer.

Run:
  python scripts/test_self_correction_corpus.py
  python scripts/test_self_correction_corpus.py --only "^LEAD" --runs 5
  python scripts/test_self_correction_corpus.py --prompt-file cand.txt --runs 5 \\
      --provider groq --json out.json

Options:
  --runs N           run every case N times (default 1)
  --prompt-file P    load P into the styler's prompt_template after
                     construction, so a candidate prompt can be measured
                     without editing prompts/normal.txt
  --only REGEX       only cases whose label matches REGEX (case-insensitive)
  --filter TEXT      only cases whose label contains TEXT (legacy substring)
  --json OUT         write per-run detail (model output before the guard,
                     final pasted output, guard reason, pass/fail reasons)
  --provider NAME    pin the styler to one provider with no fallback
  --force-llm        bypass the short-transcript shortcut (_is_simple) so
                     every case reaches the model; default is the app's path
  --delay SECONDS    pause between calls (default 1)
  --max-cost USD     stop before the estimated spend passes this (default 0.50)
"""
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List

env = Path.home() / ".waffler-hosted" / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from style_openai import OpenAIStyler  # noqa: E402


@dataclass
class Case:
    label: str
    raw: str
    must_contain: List[str] = field(default_factory=list)
    must_not_contain: List[str] = field(default_factory=list)
    must_match: List[str] = field(default_factory=list)
    must_not_match: List[str] = field(default_factory=list)
    note: str = ""


# Apostrophe-tolerant "let's" / "I'll" for regex checks (the model may emit a
# curly apostrophe; the transcript has a straight one).
_AP = "['’]"

CORPUS: List[Case] = [
    # ─── Day-of-week corrections (user's exact ask) ─────────────────────────
    Case("DAY-1 user's exact example",
         "Can we set up a meeting for Tuesday, sorry I mean Monday.",
         must_contain=["Monday"],
         must_not_contain=["Tuesday"],
         note="user reported this case isn't being corrected"),
    Case("DAY-2 'no wait' marker",
         "Let's do the standup at Tuesday, no wait, Monday.",
         must_contain=["Monday"],
         must_not_contain=["Tuesday"]),
    Case("DAY-3 'actually' marker (less explicit)",
         "Can we shift it to Tuesday, actually Monday works better.",
         must_contain=["Monday"],
         must_not_contain=["Tuesday"]),
    Case("DAY-4 em-dash + 'I meant'",
         "Send the doc on Tuesday — I meant Monday — before the standup.",
         must_contain=["Monday", "before the standup"],
         must_not_contain=["Tuesday"]),
    Case("DAY-5 'hmm no'",
         "Let's schedule it for Tuesday, hmm no, Monday morning.",
         must_contain=["Monday"],
         must_not_contain=["Tuesday"]),
    Case("DAY-6 abandoned without marker",
         "Tuesday — Monday at three would be better actually.",
         must_contain=["Monday", "three"],
         must_not_contain=["Tuesday"]),

    # ─── Name corrections ──────────────────────────────────────────────────
    Case("NAME-1 'sorry I mean'",
         "Send the report to John, sorry I mean Jane, by end of day.",
         must_contain=["Jane"],
         must_not_contain=["John"]),
    Case("NAME-2 multi-name correction",
         "Loop in Rohan and Sarah, no wait, Rohan and James.",
         must_contain=["Rohan", "James"],
         must_not_contain=["Sarah"]),
    Case("NAME-3 surname correction",
         "Ping James Smith, sorry, James Farrelly about the migration.",
         must_contain=["James Farrelly"],
         must_not_contain=["Smith"]),

    # ─── Number / time corrections ─────────────────────────────────────────
    Case("NUM-1 time correction",
         "The call is at three, no four, in the afternoon.",
         must_contain=["four"],
         must_not_contain=["at three"],
         note="'three' should be dropped, 'four' kept"),
    Case("NUM-2 price correction",
         "The quote was £500, sorry, £750 for the whole package.",
         must_contain=["£750"],
         must_not_contain=["£500"]),
    Case("NUM-3 count correction",
         "We've got about twenty users, no actually fifty users on the beta.",
         must_contain=["fifty"],
         must_not_contain=["twenty"]),

    # ─── Place / location corrections ──────────────────────────────────────
    Case("PLACE-1 location correction",
         "We're meeting in the boardroom, sorry I mean the cafeteria, at twelve.",
         must_contain=["cafeteria", "twelve"],
         must_not_contain=["boardroom"]),

    # ─── Multi-step corrections ────────────────────────────────────────────
    Case("MULTI-1 two corrections in one sentence",
         "Send it to John, sorry James, by Tuesday, no wait, Wednesday at three.",
         must_contain=["James", "Wednesday"],
         must_not_contain=["John", "Tuesday"],
         note="both the name AND day corrections must apply"),
    Case("MULTI-2 chain X→Y→Z",
         "Let's meet on Tuesday, no Wednesday, actually Thursday at two.",
         must_contain=["Thursday"],
         must_not_contain=["Tuesday", "on Wednesday"],
         note="only the final option in the chain survives"),

    # ─── Corrections inside structure ──────────────────────────────────────
    Case("STRUCT-1 correction inside numbered list",
         "Number one, ping John — sorry, I mean Jane — about the contract. Number two, book the room for Tuesday, no Monday.",
         must_contain=["Jane", "Monday"],
         must_not_contain=["John", "Tuesday"],
         must_match=[r"(?m)^\s*1\.\s*", r"(?m)^\s*2\.\s*"]),
    Case("STRUCT-2 correction inside email body",
         "Hi Rohan, can you sign off the budget by Tuesday, sorry I mean Monday. Cheers, James.",
         must_contain=["Monday", "Rohan"],
         must_not_contain=["Tuesday"],
         must_match=[r"^Hi Rohan,[ \t]*\n[ \t]*\n",
                     r"\n[ \t]*\n[ \t]*Cheers,[ \t]*\n[ \t]*James\b"]),
    Case("STRUCT-3 correction inside a question",
         "Are we meeting at three, sorry I mean four, this afternoon?",
         must_contain=["four", "?"],
         must_not_contain=["at three"]),

    # ─── Abandoned-phrase corrections (no explicit marker) ─────────────────
    Case("ABAND-1 abandoned start with em-dash",
         "We should ship — actually let me start over. The plan is to ship the dashboard refactor first.",
         must_contain=["dashboard refactor"],
         must_not_contain=["let me start over"]),
    Case("ABAND-2 partial then restart",
         "The thing about — what I'm trying to say is the migration is risky.",
         # Acceptable outputs: drop the abandoned start, OR fold the two halves
         # into smooth English. gpt-4.1-mini tends to preserve more text
         # (which is generally GOOD), so the assertion is just that the
         # meaning is preserved — migration + risky present in output.
         must_contain=["migration", "risky"],
         note="abandoned start; both 'drop entirely' and 'fold smoothly' are acceptable. "
              "The critical thing is content preservation."),

    # ─── NEGATIVE — 'I mean' as clarification, NOT a correction ────────────
    Case("NEG-1 'I mean' adds detail, doesn't replace",
         "Make sure the test is comprehensive — I mean cover the edge cases, the happy path, and the error states.",
         must_contain=["edge cases", "happy path", "error states", "comprehensive"],
         note="'I mean' here introduces a clarification, not a correction; nothing should be dropped"),
    Case("NEG-2 'actually' as emphasis not correction",
         "I actually really enjoyed working on this project, the whole team was great.",
         # 'actually' here is emphasis, not a correction. The prompt's
         # WHAT TO KEEP rule explicitly preserves "actually" when emphasising,
         # so the styler may legitimately keep it. The critical check is that
         # NOTHING gets dropped — "enjoyed" and "whole team" must survive.
         must_contain=["enjoyed", "whole team"],
         note="'actually' as emphasis is keep-able per prompt rule; the test is that no "
              "content gets dropped, not that 'actually' is removed"),
    Case("NEG-3 'sorry' as apology not correction",
         "Sorry for the late reply, the call ran over by twenty minutes.",
         must_contain=["late reply", "twenty minutes"],
         note="'Sorry' starting a sentence is an apology, not a correction marker — must not drop anything"),
    Case("NEG-4 'no wait' as suspense not correction",
         "I thought the deploy would take an hour, but no, wait until you see this — it took six.",
         must_contain=["six"],
         note="'no wait' as a rhetorical device, not a correction"),

    # ─── LEAD-IN corrections (the bug class, 2026-09) ──────────────────────
    # The correction replaces ONE slot (a day, a name, a number); everything
    # the speaker said before that slot is a shared lead-in and must stay.
    Case("LEAD-1 cross-sentence chain (exact bug transcript)",
         "Let's meet on Tuesday. No, wait, Wednesday. Actually, Thursday at two.",
         must_contain=["meet on", "Thursday at two"],
         must_not_contain=["Tuesday", "Wednesday"],
         must_match=[rf"(?i)\blet{_AP}s\s+meet\s+on\s+thursday\s+at\s+two\b"],
         note="live 5/5: model returned 'Thursday at two.', guard pasted raw"),
    Case("LEAD-2 one-sentence chain (exact bug transcript)",
         "Let's meet on Tuesday, no wait Wednesday, actually Thursday at 2.",
         must_contain=["meet on", "Thursday at 2"],
         must_not_contain=["Tuesday", "Wednesday"],
         must_match=[rf"(?i)\blet{_AP}s\s+meet\s+on\s+thursday\s+at\s+2\b"],
         must_not_match=[r"(?i)\bactually\s+thursday"],
         note="live 4/5: model returned 'Thursday at 2.' / 'actually Thursday at 2.'"),
    Case("LEAD-3 cross-sentence single correction",
         "Can you send it on Monday. Sorry, Tuesday.",
         must_contain=["send it on", "Tuesday"],
         must_not_contain=["Monday", "sorry"],
         must_match=[r"(?i)\bcan\s+you\s+send\s+it\s+on\s+tuesday\b"],
         note="8 words and no marker the _is_simple gate recognises, so the app "
              "may never ask the model; use --force-llm to measure the prompt alone"),
    Case("LEAD-4 chain inside a longer sentence",
         "I'll book the table for six, no wait, seven, actually eight people on Friday.",
         must_contain=["book the table for", "eight people on Friday"],
         must_not_contain=["six", "seven"],
         must_match=[rf"(?i)\bi{_AP}ll\s+book\s+the\s+table\s+for\s+eight\s+people\s+on\s+friday\b"]),
    Case("LEAD-5 cross-sentence number chain",
         "The budget is five thousand. No, six. Actually, let's say seven thousand.",
         must_contain=["budget is", "seven thousand"],
         must_not_contain=["five", "six"],
         note="lead-in 'The budget is' must survive; 'let's say' may stay or go"),
    Case("LEAD-6 name correction split by Whisper punctuation",
         "Please forward the invoice to Sarah. Sorry, Sophie.",
         must_contain=["forward the invoice to", "Sophie"],
         must_not_contain=["Sarah", "sorry"],
         must_match=[r"(?i)\bforward\s+the\s+invoice\s+to\s+sophie\b"],
         note="8 words and no marker the _is_simple gate recognises; see LEAD-3"),
    Case("LEAD-7 whole-clause lead-in",
         "We need to ship the dashboard by Monday. No, wait, by Wednesday.",
         must_contain=["We need to ship the dashboard", "Wednesday"],
         must_not_contain=["Monday"],
         must_match=[r"(?i)\bship\s+the\s+dashboard\s+by\s+wednesday\b"]),
    Case("LEAD-8 in-sentence control (works today)",
         "Send it to John, sorry, James, by Wednesday at three.",
         must_contain=["Send it to James", "Wednesday at three"],
         must_not_contain=["John", "sorry"],
         note="positive control: in-sentence corrections were 5/5 live"),
    Case("LEAD-9 cross-sentence correction then more content",
         "Book the flight for the 14th. Sorry, the 15th. And get a hotel near the office.",
         must_contain=["hotel near the office"],
         must_not_contain=["14th", "sorry"],
         must_match=[r"(?i)\bbook\s+the\s+flight\s+for\s+the\s+15th\b"],
         note="content AFTER the correction must survive too"),

    # ─── NEGATIVE controls for the lead-in fix: keep EVERYTHING ────────────
    Case("NEG-LEAD-1 'Actually' adds an option, not a correction",
         "Let's meet on Tuesday. Actually, Thursday works too.",
         must_contain=["Tuesday", "Thursday works too"],
         note="new information; short enough that the app may skip the model"),
    Case("NEG-LEAD-1b 'Actually' adds an option (reaches the model)",
         "Let's meet on Tuesday to go through the numbers. Actually, Thursday works too if you're busy.",
         must_contain=["Tuesday", "go through the numbers", "Thursday works too", "busy"]),
    Case("NEG-LEAD-2 rhetorical 'No, wait'",
         "No, wait until you see the numbers.",
         must_contain=["wait until you see the numbers"],
         must_match=[r"(?i)^\s*no\b"],
         note="rhetorical, not a correction: 'No' stays"),
    Case("NEG-LEAD-3 'Sorry' apology then a plan",
         "Sorry, I'm running late. Let's meet at two.",
         must_contain=["sorry", "running late", "meet at two"],
         note="apology; short enough that the app may skip the model"),
    Case("NEG-LEAD-3b 'Sorry' apology then a plan (reaches the model)",
         "Sorry, I'm running late this morning. Let's meet at two in the usual room.",
         must_contain=["sorry", "running late", "meet at two", "usual room"]),
]


def evaluate(case: Case, styled: str) -> List[str]:
    failures = []
    s_lower = styled.lower()
    for needle in case.must_contain:
        if needle.lower() not in s_lower:
            failures.append(f"missing: {needle!r}")
    for needle in case.must_not_contain:
        if needle.lower() in s_lower:
            failures.append(f"contains forbidden: {needle!r}")
    for pat in case.must_match:
        if not re.search(pat, styled, re.MULTILINE):
            failures.append(f"missing pattern: {pat!r}")
    for pat in case.must_not_match:
        if re.search(pat, styled, re.MULTILINE):
            failures.append(f"forbidden pattern matched: {pat!r}")
    return failures


# ── Harness plumbing ─────────────────────────────────────────────────────────

_SECRET_ENV = ("GROQ_API_KEY", "OPENAI_API_KEY", "CEREBRAS_API_KEY", "ELEVENLABS_API_KEY")


def _scrub(text) -> str:
    """Belt and braces: never let a key value reach stdout or the JSON."""
    s = "" if text is None else str(text)
    for name in _SECRET_ENV:
        val = os.environ.get(name, "")
        if len(val) >= 8 and val in s:
            s = s.replace(val, f"[{name} REDACTED]")
    return s


def load_prompt_file(path: str) -> str:
    """Read a candidate prompt exactly the way OpenAIStyler._load_prompt_template
    reads prompts/<style>.txt (plain open(), platform default encoding), so a
    candidate is decoded identically to how the app would decode it once it
    replaces prompts/normal.txt. Fails fast on a template .format() would reject."""
    with open(path, "r") as f:
        text = f.read()
    try:
        text.format(transcript="x", dialect_instruction="y")
    except (KeyError, IndexError, ValueError) as e:
        sys.exit(f"--prompt-file {path}: not a valid template for str.format ({e!r}); "
                 "it needs {transcript} and {dialect_instruction} and no other bare braces")
    for ph in ("{transcript}", "{dialect_instruction}"):
        if ph not in text:
            sys.exit(f"--prompt-file {path}: missing placeholder {ph}")
    return text


def _distinct(values):
    """[(value, count)] in first-seen order."""
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return list(out.items())


def run_once(styler, case: Case, captured: dict, retries: int, log):
    """Style one transcript. Returns a per-run dict. Retries infrastructure
    failures (every provider failed / rate limited), never content failures."""
    attempt = 0
    while True:
        attempt += 1
        captured.clear()
        # A 429 parks the provider for a cooldown; reset so a retry really
        # asks the model again instead of pasting basic_clean.
        styler._groq_skip_until = 0.0
        styler._cerebras_skip_until = 0.0
        buf = io.StringIO()
        t0 = time.time()
        err = None
        final, usage = "", {}
        try:
            with contextlib.redirect_stdout(buf):
                final, usage = styler.style(case.raw)
        except Exception as e:  # pragma: no cover - live harness
            err = f"exception: {e}"
        ms = (time.time() - t0) * 1000
        usage = usage or {}
        if not err and usage.get("fallback_reason"):
            err = f"all providers failed: {usage.get('fallback_reason')}"
        if err and attempt <= retries:
            wait = 10 * attempt
            log(f"      retry {attempt}/{retries} in {wait}s ({_scrub(err)[:90]})")
            time.sleep(wait)
            continue
        break

    model_called = "model_output" in captured
    guard_reason = usage.get("truncation_guard") if model_called else None
    if err:
        path = "error"
    elif not model_called:
        path = "simple"          # _is_simple shortcut: no model call at all
    elif guard_reason:
        path = "guarded"         # model answered, guard refused it, raw pasted
    else:
        path = "model"

    fails = [err] if err else evaluate(case, final)
    rec = {
        "path": path,
        "provider": (captured.get("model_usage") or {}).get("provider") or usage.get("provider"),
        "final_output": _scrub(final),
        "pass": not fails,
        "failures": [_scrub(f) for f in fails],
        "guard_reason": guard_reason,
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "ms": round(ms),
        "attempts": attempt,
    }
    if model_called:
        model_out = captured["model_output"] or ""
        # What would have been pasted had the guard NOT tripped: the same
        # deterministic post-passes style() applies after the guard.
        unguarded = styler._format_email_layout(
            styler._restore_dropped_signoff(model_out, case.raw))
        mfails = evaluate(case, unguarded)
        rec.update({
            "model_output": _scrub(model_out),
            "unguarded_output": _scrub(unguarded),
            "model_correct": not mfails,
            "model_failures": mfails,
            "finish_reason": (captured.get("model_usage") or {}).get("finish_reason"),
        })
    else:
        rec.update({"model_output": None, "unguarded_output": None,
                    "model_correct": None, "model_failures": []})
    log_lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    if log_lines:
        rec["styler_log"] = _scrub("\n".join(log_lines))[-600:]
    return rec


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Waffler self-correction harness")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between calls (default 1)")
    ap.add_argument("--filter", type=str, default=None,
                    help="only labels containing this substring (case-insensitive)")
    ap.add_argument("--only", type=str, default=None,
                    help="only labels matching this regex (case-insensitive)")
    ap.add_argument("--runs", type=int, default=1, help="runs per case (default 1)")
    ap.add_argument("--prompt-file", type=str, default=None,
                    help="evaluate this prompt template instead of prompts/normal.txt")
    ap.add_argument("--json", type=str, default=None, help="write per-run detail here")
    ap.add_argument("--provider", type=str, default=None,
                    choices=["groq", "cerebras", "openai"],
                    help="pin to one provider, no fallback (default: the app's order)")
    ap.add_argument("--force-llm", action="store_true",
                    help="bypass the _is_simple shortcut so every case reaches the model")
    ap.add_argument("--retries", type=int, default=2,
                    help="retries per run when every provider fails (default 2)")
    ap.add_argument("--max-cost", type=float, default=0.50,
                    help="stop before estimated spend passes this many USD (default 0.50)")
    ap.add_argument("--price-in", type=float, default=0.15,
                    help="USD per million input tokens (default 0.15, gpt-oss-120b on Groq)")
    ap.add_argument("--price-out", type=float, default=0.60,
                    help="USD per million output tokens (default 0.60)")
    args = ap.parse_args()

    styler = OpenAIStyler(
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        groq_api_key=os.environ.get("GROQ_API_KEY", ""),
        cerebras_api_key=os.environ.get("CEREBRAS_API_KEY", "") if args.provider == "cerebras" else "",
        provider_order=[args.provider] if args.provider else None,
    )
    if args.provider:
        # _normalize_provider_order appends the missing providers; re-pin.
        styler._provider_order = [args.provider]
    if args.prompt_file:
        styler.prompt_template = load_prompt_file(args.prompt_file)
    if args.force_llm:
        styler._is_simple = lambda _t: False

    # Capture the model's answer BEFORE the truncation guard can replace it.
    captured: dict = {}
    _orig_guard = styler._guard_truncation

    def _spy_guard(styled, usage, transcript):
        captured["model_output"] = styled
        captured["model_usage"] = dict(usage or {})
        return _orig_guard(styled, usage, transcript)

    styler._guard_truncation = _spy_guard

    cases = [c for c in CORPUS
             if (not args.filter or args.filter.lower() in c.label.lower())
             and (not args.only or re.search(args.only, c.label, re.IGNORECASE))]
    if not cases:
        print("no cases matched")
        return
    runs = max(1, args.runs)
    prompt_sha = hashlib.sha256(styler.prompt_template.encode("utf-8")).hexdigest()[:12]
    prompt_src = args.prompt_file or f"prompts/{styler.prompt_style}.txt (default)"
    width = max(len(c.label) for c in cases)

    def log(msg):
        print(msg, flush=True)

    log(f"\nRunning {len(cases)} self-correction cases x {runs} run(s)  delay={args.delay}s")
    log(f"prompt: {prompt_src}  sha256:{prompt_sha}  provider: {args.provider or 'app order'}"
        f"{'  FORCE-LLM' if args.force_llm else ''}")
    log(f"{'#':<3} {'LABEL':<{width}} FINAL   MODEL   GUARD  PATHS")
    log("─" * (width + 60))

    tok_in = tok_out = calls = 0

    def spent():
        return (tok_in * args.price_in + tok_out * args.price_out) / 1_000_000

    results = []
    stopped = None
    for i, case in enumerate(cases, 1):
        recs = []
        for r in range(runs):
            if spent() >= args.max_cost:
                stopped = f"stopped: estimated spend ${spent():.4f} reached --max-cost ${args.max_cost:.2f}"
                break
            rec = run_once(styler, case, captured, args.retries, log)
            rec["run"] = r + 1
            recs.append(rec)
            tok_in += rec["input_tokens"]
            tok_out += rec["output_tokens"]
            if rec["input_tokens"]:
                calls += 1
            if rec["path"] != "simple":
                time.sleep(args.delay)
        if recs:
            n = len(recs)
            p = sum(1 for x in recs if x["pass"])
            m_runs = [x for x in recs if x["model_correct"] is not None]
            m_ok = sum(1 for x in m_runs if x["model_correct"])
            g = sum(1 for x in recs if x["path"] == "guarded")
            paths = ",".join(f"{k}x{v}" if v > 1 else k for k, v in _distinct([x["path"] for x in recs]))
            mcol = f"{m_ok}/{len(m_runs)}" if m_runs else "n/a"
            ms = sum(x["ms"] for x in recs) / n
            log(f"{i:<3} {case.label:<{width}} {f'{p}/{n}':<7} {mcol:<7} {g:<6} {paths}  ({ms:.0f}ms avg)")
            results.append((case, recs))
        if stopped:
            break

    # ── Totals ────────────────────────────────────────────────────────────
    all_recs = [x for _, recs in results for x in recs]
    tot = len(all_recs)
    tot_pass = sum(1 for x in all_recs if x["pass"])
    cases_all_pass = sum(1 for _, recs in results if all(x["pass"] for x in recs))
    m_all = [x for x in all_recs if x["model_correct"] is not None]
    m_ok_all = sum(1 for x in m_all if x["model_correct"])
    guards = sum(1 for x in all_recs if x["path"] == "guarded")
    simple = sum(1 for x in all_recs if x["path"] == "simple")
    errors = sum(1 for x in all_recs if x["path"] == "error")
    non_pinned = sorted({x["provider"] for x in all_recs
                         if x["path"] in ("model", "guarded") and x["provider"] != "groq"})

    log(f"\n{'─' * (width + 60)}")
    log(f"PASSED {cases_all_pass}/{len(results)} cases (every run passed on the final pasted output)")
    if tot:
        log(f"final-output pass rate: {tot_pass}/{tot} runs ({tot_pass / tot:.0%})")
    if m_all:
        log(f"model-correct rate (before the guard): {m_ok_all}/{len(m_all)} model runs "
            f"({m_ok_all / len(m_all):.0%})")
    log(f"guard trips: {guards}   simple-path runs (no model call): {simple}   errors: {errors}")
    if non_pinned:
        log(f"NOTE: some runs were answered by {', '.join(non_pinned)}, not groq "
            f"(pass --provider groq to pin)")
    log(f"tokens: {tok_in} in / {tok_out} out over {calls} model calls  "
        f"est. cost ${spent():.4f} (at ${args.price_in}/M in, ${args.price_out}/M out)")
    if stopped:
        log(f"*** {stopped} (results are partial) ***")

    # ── Failure details ───────────────────────────────────────────────────
    # A case is listed when any pasted output failed OR the model itself got it
    # wrong (even if the guard then saved the final paste).
    failing = [(c, recs) for c, recs in results
               if not all(x["pass"] for x in recs)
               or any(x["model_correct"] is False for x in recs)]
    if failing:
        log("\n=== FAILURE DETAILS ===")
        for case, recs in failing:
            n = len(recs)
            p = sum(1 for x in recs if x["pass"])
            m_runs = [x for x in recs if x["model_correct"] is not None]
            m_ok = sum(1 for x in m_runs if x["model_correct"])
            mtxt = f"  model-correct {m_ok}/{len(m_runs)}" if m_runs else ""
            log(f"\n[{case.label}]  final {p}/{n}{mtxt}  {case.note}")
            log(f"  raw:    {case.raw}")
            fr = {x["final_output"]: x for x in recs}
            for out, cnt in _distinct([x["final_output"] for x in recs]):
                x = fr[out]
                tag = "PASS" if x["pass"] else "FAIL"
                log(f"  pasted x{cnt} [{tag}, {x['path']}]: {out!r}")
                for f in x["failures"]:
                    log(f"      - {f}")
            guarded = [x for x in recs if x["path"] == "guarded"]
            if guarded:
                gr = {x["model_output"]: x for x in guarded}
                for out, cnt in _distinct([x["model_output"] for x in guarded]):
                    x = gr[out]
                    ok = "model-correct" if x["model_correct"] else "model-wrong"
                    log(f"  model [{ok}] (refused by guard, {x['guard_reason']}) x{cnt}: {out!r}")
                    for f in x["model_failures"]:
                        log(f"      - {f}")

    if args.json:
        payload = {
            "meta": {
                "when": datetime.now().isoformat(timespec="seconds"),
                "argv": sys.argv[1:],
                "prompt_source": prompt_src,
                "prompt_sha256_12": prompt_sha,
                "provider": args.provider or "app order",
                "groq_model": getattr(styler, "_groq_model", None),
                "force_llm": args.force_llm,
                "runs": runs,
                "stopped": stopped,
            },
            "totals": {
                "cases": len(results),
                "cases_all_runs_pass": cases_all_pass,
                "runs": tot,
                "runs_pass": tot_pass,
                "model_runs": len(m_all),
                "model_correct": m_ok_all,
                "guard_trips": guards,
                "simple_path_runs": simple,
                "errors": errors,
                "input_tokens": tok_in,
                "output_tokens": tok_out,
                "model_calls": calls,
                "est_cost_usd": round(spent(), 5),
            },
            "cases": [
                {
                    "label": c.label,
                    "raw": c.raw,
                    "note": c.note,
                    "checks": {
                        "must_contain": c.must_contain,
                        "must_not_contain": c.must_not_contain,
                        "must_match": c.must_match,
                        "must_not_match": c.must_not_match,
                    },
                    "runs_pass": sum(1 for x in recs if x["pass"]),
                    "model_runs": sum(1 for x in recs if x["model_correct"] is not None),
                    "model_correct": sum(1 for x in recs if x["model_correct"]),
                    "guard_trips": sum(1 for x in recs if x["path"] == "guarded"),
                    "runs": recs,
                }
                for c, recs in results
            ],
        }
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
        log(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
