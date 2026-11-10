"""Clean-up regression set: invented dictations through the real clean-up.

What the words look like after the clean-up is the product, so this drives
the app's own styler (src/style_openai.py, the prompt in prompts/normal.txt
and every pass after the model: the truncation guard, the dash rule, the
sentence-case guards, the email layout) with the dictations in
tests/fixtures/cleanup_regression_cases.json and checks each output with
plain string rules. The cases were written for the fixture; none is anybody's
real dictation. They cover what a review of real use found going wrong, or
worth guarding: unpunctuated speech-to-text, a lower-case first letter after
a filler is removed, a dictation that reads like an instruction or a question
to a chatbot, fillers, self-corrections, numbers, lists, names and jargon,
British spelling, emails, very short and very long takes.

It talks to the provider (one request per case that is not short enough to
skip the model), so it is a script and not part of the test suite;
tests/test_cleanup_regression.py checks the fixture and the checker offline.

Run (the key is read from GROQ_API_KEY, or from --env-file):
  python scripts/cleanup_regression.py --env-file ~/.waffler-hosted/.env
  python scripts/cleanup_regression.py --only "^UNPUNCT|^OBEY" --runs 2
  python scripts/cleanup_regression.py --prompt-file candidate.txt --json out.json

The app's data folder is never touched: WAFFLER_DATA_DIR is pointed at a
temporary folder (British spelling switched on there) before src is imported.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "cleanup_regression_cases.json"

CHECK_KEYS = frozenset({
    "has", "lacks", "has_cs", "lacks_cs", "re", "not_re", "min_ratio", "max_ratio",
    "starts_upper", "ends_with", "min_punct", "max_paras", "lines_re_count"})
CASE_KEYS = CHECK_KEYS | {"id", "cat", "text"}


def load_cases(path=FIXTURE) -> list:
    return json.loads(Path(path).read_text(encoding="utf-8"))["cases"]


def _words(text: str) -> int:
    return len(text.split())


def check(case: dict, output: str) -> list:
    """The reasons ``output`` fails ``case`` ([] when it passes)."""
    out = output or ""
    low = out.lower()
    problems = []
    for s in case.get("has", ()):
        if s.lower() not in low:
            problems.append(f"missing {s!r}")
    for s in case.get("lacks", ()):
        if s.lower() in low:
            problems.append(f"has {s!r}")
    for s in case.get("has_cs", ()):
        if s not in out:
            problems.append(f"missing (exact) {s!r}")
    for s in case.get("lacks_cs", ()):
        if s in out:
            problems.append(f"has (exact) {s!r}")
    for pattern in case.get("re", ()):
        if not re.search(pattern, out, re.M):
            problems.append(f"no match for /{pattern}/")
    for pattern in case.get("not_re", ()):
        if re.search(pattern, out, re.M):
            problems.append(f"matches /{pattern}/")
    ratio = _words(out) / max(1, _words(case["text"]))
    if "min_ratio" in case and ratio < case["min_ratio"]:
        problems.append(f"kept {ratio:.2f} of the words (< {case['min_ratio']})")
    if "max_ratio" in case and ratio > case["max_ratio"]:
        problems.append(f"grew to {ratio:.2f} of the words (> {case['max_ratio']})")
    if case.get("starts_upper"):
        first = next((c for c in out if c.isalnum()), "")
        if first.isalpha() and not first.isupper():
            problems.append("starts with a lower-case letter")
    if "ends_with" in case and out.rstrip()[-1:] not in case["ends_with"]:
        problems.append(f"ends with {out.rstrip()[-1:]!r}, not one of {case['ends_with']!r}")
    if "min_punct" in case:
        density = len(re.findall(r"[.!?,;:]", out)) / max(1, _words(out))
        if density < case["min_punct"]:
            problems.append(f"{density:.3f} punctuation marks a word (< {case['min_punct']})")
    if "max_paras" in case:
        paras = len([p for p in re.split(r"\n\s*\n", out.strip()) if p.strip()])
        if paras > case["max_paras"]:
            problems.append(f"{paras} paragraphs (> {case['max_paras']})")
    if "lines_re_count" in case:
        pattern, want = case["lines_re_count"]
        got = sum(1 for line in out.splitlines() if re.search(pattern, line))
        if got != want:
            problems.append(f"{got} lines match /{pattern}/, not {want}")
    return problems


def _key_from(env_file) -> str:
    if env_file:
        for line in Path(env_file).expanduser().read_text(encoding="utf-8").splitlines():
            name, _, value = line.strip().partition("=")
            if name == "GROQ_API_KEY":
                return value.strip().strip("'\"")
    return os.environ.get("GROQ_API_KEY", "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="regular expression on the case id")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--prompt-file", help="a candidate prompt in place of prompts/normal.txt")
    ap.add_argument("--env-file", help="a .env with GROQ_API_KEY")
    ap.add_argument("--json", help="write every output and verdict here")
    ap.add_argument("--gap", type=float, default=2.0, help="seconds between requests")
    ap.add_argument("--show", action="store_true", help="print each failing output")
    args = ap.parse_args(argv)

    key = _key_from(args.env_file)
    if not key:
        print("No GROQ_API_KEY (set it, or pass --env-file).")
        return 2

    data = tempfile.mkdtemp(prefix="waffler-cleanup-regression-")
    os.environ["WAFFLER_DATA_DIR"] = data
    Path(data, "settings.json").write_text(json.dumps({"dialect": "en-GB"}), encoding="utf-8")
    sys.path.insert(0, str(ROOT / "src"))
    import style_openai

    styler = style_openai.OpenAIStyler(api_key="", groq_api_key=key, provider_order=["groq"])
    if args.prompt_file:
        styler.prompt_template = Path(args.prompt_file).read_text(encoding="utf-8")

    cases = [c for c in load_cases() if not args.only or re.search(args.only, c["id"])]
    results, by_cat = [], {}
    for case in cases:
        for run in range(args.runs):
            for attempt in range(4):
                styler._groq_skip_until = 0.0
                output, usage = styler.style(case["text"])
                if usage.get("fallback_reason", "").startswith("RATE_LIMIT") and attempt < 3:
                    time.sleep(20 * (attempt + 1))      # back off, then ask again
                    continue
                break
            problems = check(case, output)
            if usage.get("fallback_reason"):
                problems.append("clean-up failed: " + usage["fallback_reason"][:60])
            ok = not problems
            passed, total = by_cat.get(case["cat"], (0, 0))
            by_cat[case["cat"]] = (passed + ok, total + 1)
            results.append({"id": case["id"], "cat": case["cat"], "run": run, "ok": ok,
                            "problems": problems, "output": output,
                            "provider": usage.get("provider", "")})
            print(f"{'PASS' if ok else 'FAIL'}  {case['id']:<12} {'; '.join(problems)}")
            if args.show and not ok:
                print("      " + output.replace("\n", "\n      "))
            if usage.get("api_used"):
                time.sleep(args.gap)

    print()
    for cat in sorted(by_cat):
        passed, total = by_cat[cat]
        print(f"{cat:<18} {passed}/{total}")
    passed = sum(r["ok"] for r in results)
    print(f"{'TOTAL':<18} {passed}/{len(results)}")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
