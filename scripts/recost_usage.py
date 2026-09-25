#!/usr/bin/env python3
"""Recompute stored usage costs with the corrected rate table.

The Usage panel showed figures derived from rates that had drifted from the
models the app actually calls, in both directions, and Cerebras had no rate at
all so it was billed at OpenAI's. Every stored entry keeps the inputs the cost
was derived from (duration for transcription, token counts for cleanup), so the
history can be recomputed rather than written off.

Writes a timestamped backup next to the file before touching it. Run with
--dry-run first to see what would change.

    python scripts/recost_usage.py --dry-run
    python scripts/recost_usage.py
"""

import argparse
import ast
import json
import pathlib
import shutil
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
USAGE = pathlib.Path.home() / ".waffler-hosted" / "usage.json"


def load_from_app(*names: str) -> dict:
    """Run the named top-level assignments and functions of app.py in a fresh
    namespace, without importing it (app.py pulls in pywebview and audio
    hardware at module scope)."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    nodes = [
        node for node in tree.body
        if (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in names)
        or (isinstance(node, ast.FunctionDef) and node.name in names)
    ]
    ns: dict = {}
    exec(compile(ast.Module(nodes, []), "<app.py pricing>", "exec"), ns)
    missing = [n for n in names if n not in ns]
    if missing:
        raise SystemExit(f"not found in app.py: {', '.join(missing)}")
    return ns


def load_rates() -> dict:
    return load_from_app("MODEL_RATES")["MODEL_RATES"]


def cost_for(rates: dict, entry: dict, usage_cost) -> tuple[float, dict]:
    """Price one stored entry with app.py's own ``_usage_cost``, so a recost
    applies the same rules as a live dictation (including Groq's 10-second
    minimum per transcription request)."""
    provider, kind = entry.get("provider", "openai"), entry.get("type", "gpt")
    rate = rates.get(provider, {}).get(kind) or rates["openai"].get(kind, {})
    return usage_cost(
        rate, kind,
        float(entry.get("duration_seconds") or 0.0),
        int(entry.get("input_tokens") or 0),
        int(entry.get("output_tokens") or 0),
    ), rate


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not USAGE.exists():
        print(f"no usage file at {USAGE}")
        return 0

    pricing = load_from_app("MODEL_RATES", "_usage_cost")
    rates, usage_cost = pricing["MODEL_RATES"], pricing["_usage_cost"]
    data = json.loads(USAGE.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else data.get("entries", [])

    before = sum(float(r.get("cost_usd") or 0) for r in rows)
    skipped = 0
    by_provider: dict[str, list[float]] = {}

    for r in rows:
        new_cost, rate = cost_for(rates, r, usage_cost)
        if r.get("type") == "whisper" and not r.get("duration_seconds"):
            skipped += 1          # nothing to recompute from
            new_cost = float(r.get("cost_usd") or 0)
        else:
            r["cost_usd"] = round(new_cost, 6)
            r["model"] = rate.get("model", r.get("model", ""))
            r["rate_verified"] = bool(rate.get("verified", False))
        by_provider.setdefault(r.get("provider", "?"), []).append(new_cost)

    after = sum(float(r.get("cost_usd") or 0) for r in rows)

    print(f"entries      : {len(rows)}  (skipped {skipped} with no recompute inputs)")
    print(f"stored total : ${before:.4f}")
    print(f"correct total: ${after:.4f}   ({after - before:+.4f})")
    print("\nby provider (corrected):")
    for prov, vals in sorted(by_provider.items(), key=lambda kv: -sum(kv[1])):
        note = "" if rates.get(prov, {}).get("gpt", {}).get("verified", True) else "  [estimated rate]"
        print(f"  {prov:<10} ${sum(vals):.4f}  over {len(vals)} calls{note}")

    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = USAGE.with_name(f"usage.backup-{stamp}.json")
    shutil.copy2(USAGE, backup)
    USAGE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\nbackup written : {backup.name}")
    print(f"usage.json updated with corrected costs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
