"""Confirm the max_tokens fix prevents truncation on James's actual 732-word
dictation that originally got chopped at ~421 styled words.
"""
import json, os, sys, time
from pathlib import Path

env = Path.home() / ".waffler-hosted" / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from style_openai import OpenAIStyler  # noqa: E402

h = json.loads((Path.home() / ".waffler-hosted" / "history.json").read_text(encoding="utf-8"))
entry = next(x for x in h if x.get("timestamp") == "2026-04-30T09:41:17")

raw = entry["text"]
old_styled = entry["styled"]
print(f"raw word count:     {len(raw.split())}")
print(f"old styled (saved): {len(old_styled.split())} words   <-- truncated by max_tokens=512")
print()

styler = OpenAIStyler(
    api_key=os.environ.get("OPENAI_API_KEY", ""),
    groq_api_key=os.environ.get("GROQ_API_KEY", ""),
)

# Sanity-check the dynamic max_tokens calculation before the call.
word_count_in = len(raw.split())
expected_budget = max(1024, min(8192, word_count_in * 3))
print(f"input words: {word_count_in}")
print(f"computed max_out_tokens: {expected_budget}  (was hardcoded 512)")
print()

t0 = time.time()
new_styled, usage = styler.style(raw)
elapsed = time.time() - t0
print(f"new styled provider: {usage.get('provider', '?')}")
print(f"new styled word count: {len(new_styled.split())}  ({elapsed:.1f}s)")
print()

# Confirm not truncated: the styled output should END with a complete sentence,
# matching the END of the raw input. The raw ends with "obsessed with it." — the
# last meaningful phrase of the dictation. If truncation is fixed, the styled
# output should also reach that final phrase.
last_raw_phrase = raw.strip().split(".")[-2].strip().lower()  # second-to-last sentence (final period at end)
last_styled_phrase = new_styled.strip().split(".")[-2].strip().lower() if "." in new_styled else new_styled.lower()

print(f"raw final sentence:    ...{last_raw_phrase[-80:]}")
print(f"styled final sentence: ...{last_styled_phrase[-80:]}")
print()

# Look for "obsessed" anywhere in the styled output — it's the last distinctive
# word in the raw and was missing from the truncated version.
if "obsessed" in new_styled.lower():
    print("PASS — 'obsessed' (raw's final word) is present in styled output. No truncation.")
else:
    print("FAIL — 'obsessed' missing. Output may still be truncated.")

print()
print("===== STYLED OUTPUT (last 400 chars) =====")
print("..." + new_styled[-400:])
