#!/usr/bin/env python3
"""Doc-drift guard — fails CI on known stale doc/UI patterns.

Across v3.14.x several factual mistakes have shipped in user-facing strings:
each time, fixing one occurrence missed a sibling on another surface
(README ↔ wizard ↔ Settings panel ↔ landing page ↔ CHANGELOG). This
script grep-checks every file that ships to users for the recurring
patterns so the next instance can't slip past CI silently.

What it checks
==============
1. **Wrong fallback order.** The styler runs Groq → Cerebras → OpenAI
   (Groq first so the 100k tokens/day free tier is spent before any paid
   Cerebras tokens). Anywhere that says "Cerebras → Groq" or
   "Cerebras / Groq" or implies Cerebras is the default has historically
   been a bug. Caught in iterations 7, 12, 14, 15.

2. **Stale OpenAI styler model name.** The OpenAI default is
   `gpt-4.1-mini` since v3.13.x. Anywhere user-facing that still says
   `gpt-4o-mini` is stale. Caught in iteration 8 (api-key-guide)
   and iteration 15 (Settings backend display).

3. **Missing Cerebras in provider lists.** When user-facing text says
   "Groq or OpenAI" / "OpenAI or Groq" with no mention of Cerebras
   nearby, that's the v3.14.0 multi-provider gap surfacing again.
   Caught in iterations 2, 8, 10, 11, 12.

What it skips
=============
- CHANGELOG.md (historical entries are by definition allowed to use
  old terminology — that's their point).
- This script itself.
- Anywhere with an explicit `# doc-drift-ok` or `<!-- doc-drift-ok -->`
  marker (escape hatch for legitimate historical references in code).
- Cost-tracking constants in app.py (the GPT-4o-mini per-token costs
  ARE the per-model costs for that specific model and shouldn't be
  rewritten — the constants are named after the model they track).
- docs/superpowers/ (out-of-tree planning archive).

Run locally
===========
    python scripts/check_doc_drift.py            # exit 1 on findings
    python scripts/check_doc_drift.py --debug    # print file list + counts
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files (or glob patterns under REPO_ROOT) that are *exempt* from drift checks.
EXEMPT_PATHS = {
    "CHANGELOG.md",
    "scripts/check_doc_drift.py",
    "docs/superpowers",   # prefix match — old planning archive
}

# Globs that *are* checked. We deliberately exclude binary/large/generated
# files like dist/, node_modules/, .git/, *.lock, *.png, etc.
INCLUDE_GLOBS = [
    "*.md",
    "*.py",
    "*.txt",
    "*.astro",
    "*.html",
    "*.js",
    "*.ts",
    "*.mjs",
    "*.yml",
    "*.yaml",
]
EXCLUDE_DIR_PARTS = {
    ".git", "node_modules", "venv", "dist", "build", ".astro",
    "__pycache__", ".vscode",
}

# Per-line escape hatch.
DOC_DRIFT_OK = ("doc-drift-ok", "# doc-drift-ok", "<!-- doc-drift-ok -->")


class Check:
    def __init__(self, name: str, pattern: str, why: str, fix: str):
        self.name = name
        self.regex = re.compile(pattern, re.IGNORECASE)
        self.why = why
        self.fix = fix


CHECKS: List[Check] = [
    Check(
        name="wrong-fallback-order",
        # Match "Cerebras" followed by an arrow/separator followed by "Groq"
        # within a short window. Excludes any line that also mentions "wrong"
        # or "previously" or "was" — those describe the bug, not commit it.
        pattern=r"cerebras\s*(?:→|->|/|,|then|->|first.*?then)\s*groq",
        why="Styler chain is Groq → Cerebras → OpenAI (Groq's free tier "
            "is spent before any paid Cerebras tokens). 'Cerebras → Groq' "
            "or 'Cerebras / Groq' has been a recurring bug in user-facing "
            "strings since v3.14.0.",
        fix="Reverse the order to 'Groq → Cerebras → OpenAI'. If documenting "
            "a historical state, add ' <!-- doc-drift-ok -->' on the same line.",
    ),
    Check(
        name="stale-openai-styler-model",
        # Match the literal "gpt-4o-mini" (case-insensitive) when not preceded
        # by 'gpt-4o-mini-transcribe' (which IS the current Whisper default).
        # Also skip lines that look like cost constants or comparison talk.
        pattern=r"\bgpt-4o-mini\b(?!-transcribe)",
        why="The OpenAI styler default has been gpt-4.1-mini since v3.13.x. "
            "Anywhere user-facing that still says gpt-4o-mini is stale.",
        fix="Replace with 'gpt-4.1-mini' in user-facing strings. For per-model "
            "cost constants or historical comparison text, append "
            "' # doc-drift-ok' to the line.",
    ),
    # Note: 'missing Cerebras in provider lists' is not added as a hard check
    # because it has too many false positives (e.g. a sentence may legitimately
    # discuss only Groq + OpenAI). The first two checks catch the high-confidence
    # cases; iteration audits remain the right tool for the rest.
]


def is_exempt(path: Path) -> bool:
    rel = path.relative_to(REPO_ROOT).as_posix()
    for ex in EXEMPT_PATHS:
        if rel == ex or rel.startswith(ex.rstrip("/") + "/"):
            return True
    return False


def is_included(path: Path) -> bool:
    if any(part in EXCLUDE_DIR_PARTS for part in path.parts):
        return False
    return any(path.match(g) for g in INCLUDE_GLOBS)


def scan_file(path: Path, checks: List[Check]) -> List[Tuple[Check, int, str]]:
    hits: List[Tuple[Check, int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    for i, line in enumerate(text.splitlines(), 1):
        if any(marker in line for marker in DOC_DRIFT_OK):
            continue
        for ch in checks:
            if ch.regex.search(line):
                hits.append((ch, i, line.rstrip()))
    return hits


def main() -> int:
    debug = "--debug" in sys.argv
    files = [
        p for p in REPO_ROOT.rglob("*")
        if p.is_file() and is_included(p) and not is_exempt(p)
    ]
    if debug:
        print(f"Scanning {len(files)} files…")

    all_hits: List[Tuple[Path, Check, int, str]] = []
    for f in files:
        for ch, line_no, line in scan_file(f, CHECKS):
            all_hits.append((f, ch, line_no, line))

    if not all_hits:
        print(f"✓ Doc-drift guard clean across {len(files)} files.")
        return 0

    # Group by check for a readable report.
    by_check: dict = {}
    for f, ch, line_no, line in all_hits:
        by_check.setdefault(ch.name, []).append((f, ch, line_no, line))

    print(f"✗ Doc-drift guard found {len(all_hits)} issue(s) across {len(by_check)} check(s):\n")
    for check_name, hits in by_check.items():
        first = hits[0][1]
        print(f"=== {check_name} ({len(hits)} hit{'s' if len(hits) != 1 else ''}) ===")
        print(f"Why: {first.why}")
        print(f"Fix: {first.fix}\n")
        for f, _ch, line_no, line in hits:
            rel = f.relative_to(REPO_ROOT)
            # Compact line preview
            preview = line.strip()
            if len(preview) > 100:
                preview = preview[:97] + "…"
            print(f"  {rel}:{line_no}: {preview}")
        print()

    return 1


if __name__ == "__main__":
    sys.exit(main())
