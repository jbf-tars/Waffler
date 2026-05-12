"""
Compare Cerebras Qwen-3 235B vs OpenAI GPT-4.1 (full) on the 5 longest
real-world transcripts from the user's history.

For each: record latency and full styled output, then print a side-by-side
comparison so the user can decide which to keep.
"""
import json
import os
import time
from pathlib import Path

from openai import OpenAI

# Load env from the user's data dir
env_path = Path.home() / ".waffler-hosted" / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# Load the same prompt the app uses
prompt_path = Path(__file__).parent.parent / "prompts" / "normal.txt"
prompt_template = prompt_path.read_text(encoding="utf-8")

# Render the system prompt the same way the app does (no dialect injection)
def render_system(transcript: str) -> str:
    return prompt_template.replace("{dialect_instruction}", "").replace("{transcript}", transcript)


# Get the 5 longest raw transcripts from history
history_path = Path.home() / ".waffler-hosted" / "history.json"
with open(history_path, "r", encoding="utf-8") as f:
    history = json.load(f)
longest = sorted(history, key=lambda x: len(x.get("text", "")), reverse=True)[:5]

# Set up clients
cerebras_key = os.environ.get("CEREBRAS_API_KEY")
openai_key = os.environ.get("OPENAI_API_KEY")
assert cerebras_key, "CEREBRAS_API_KEY missing"
assert openai_key, "OPENAI_API_KEY missing"

cerebras = OpenAI(api_key=cerebras_key, base_url="https://api.cerebras.ai/v1")
openai = OpenAI(api_key=openai_key)

CEREBRAS_MODEL = "qwen-3-235b-a22b-instruct-2507"
OPENAI_MODEL = "gpt-4.1"

def call(client, model, system_msg, user_msg):
    """Return (ms, output_text) or (ms, '<ERROR: ...>')."""
    t0 = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=4096,
            temperature=0.1,
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return elapsed_ms, resp.choices[0].message.content
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return elapsed_ms, f"<ERROR: {e}>"


results = []
for i, entry in enumerate(longest, 1):
    raw = entry["text"]
    words = len(raw.split())
    print(f"=== Test {i}: {words} words ({len(raw)} chars) ===")
    system_msg = render_system(raw)
    user_msg = raw

    # Cerebras first (slight warmup advantage doesn't matter here)
    c_ms, c_out = call(cerebras, CEREBRAS_MODEL, system_msg, user_msg)
    print(f"  Cerebras Qwen-235B: {c_ms}ms")

    # OpenAI
    o_ms, o_out = call(openai, OPENAI_MODEL, system_msg, user_msg)
    print(f"  OpenAI GPT-4.1:     {o_ms}ms")

    results.append({
        "idx": i,
        "words": words,
        "chars": len(raw),
        "raw": raw,
        "cerebras_ms": c_ms,
        "cerebras_out": c_out,
        "openai_ms": o_ms,
        "openai_out": o_out,
    })
    print()


# Save results
out_path = Path(__file__).parent / "compare_results.json"
out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Saved to {out_path}")

# Summary
print("\n=== SUMMARY ===")
print(f"{'#':<3} {'words':>6} {'cerebras':>10} {'gpt-4.1':>10} {'speedup':>8}")
total_c = total_o = 0
for r in results:
    total_c += r["cerebras_ms"]
    total_o += r["openai_ms"]
    speedup = r["openai_ms"] / r["cerebras_ms"] if r["cerebras_ms"] > 0 else 0
    print(f"{r['idx']:<3} {r['words']:>6} {r['cerebras_ms']:>9}ms {r['openai_ms']:>9}ms {speedup:>7.2f}x")
print(f"{'tot':<3} {'':>6} {total_c:>9}ms {total_o:>9}ms {total_o/total_c if total_c else 0:>7.2f}x")
