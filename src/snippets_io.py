"""Snippets as a file: export, and import with a preview.

Export writes the saved snippets as CSV (two columns, ``trigger`` and
``expansion``, spreadsheet-safe, UTF-8 with a byte order mark: src/table_io.py)
or JSON (the very shape ``snippets.json`` has, so an older Waffler reads it
too). Import reads either, or any file close to a CSV: the columns in any order
and named a few ways, or with no header at all (trigger, then text); a
comma, a semicolon or a tab between them; a text in quotes with commas, quotes
and line breaks in it.

An import only adds. The editor's limits hold (60 characters for the phrase,
4,000 for the text, 200 snippets). A phrase that is already a snippet, in any
capitals, keeps the snippet it has and the row is listed as left out, so an
import can never change what Waffler pastes for something already set up. The
same phrase twice in the file keeps its first row.

``plan_import`` works on lists and changes none of them: it returns what the
preview shows (counts, the rows left out and why) and the list as it would be,
which the bridge then saves through the editor's own save (``snippets.clean``).
"""

from __future__ import annotations

import json

try:
    import snippets
    import table_io
    import user_messages
except ImportError:  # imported as src.snippets_io
    from src import snippets, table_io, user_messages

MAX_TRIGGER = snippets.MAX_TRIGGER
MAX_EXPANSION = snippets.MAX_EXPANSION
MAX_SNIPPETS = snippets.MAX_SNIPPETS

FORMATS = ("csv", "json")
CSV_COLUMNS = ("trigger", "expansion")

COLUMN_NAMES = {
    "trigger": {"trigger", "triggers", "phrase", "shortcut", "abbreviation", "abbr", "say",
                "you_say", "when_you_say", "snippet", "name", "key"},
    "expansion": {"expansion", "expansions", "text", "replacement", "body", "content", "expands_to",
                  "waffler_pastes", "pastes", "paste", "output", "value", "snippet_text"},
}

ERR_JSON = "Waffler couldn't read that file as snippets. Check it and try again."


# ── Reading a file ───────────────────────────────────────────────────────────

def _lines(text: str) -> str:
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def _from_json(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        raise table_io.TableError(ERR_JSON) from None
    if not isinstance(data, list):
        raise table_io.TableError(ERR_JSON)
    rows = []
    for n, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise table_io.TableError(ERR_JSON)
        t, x = item.get("trigger"), item.get("expansion")
        rows.append({"row": n, "trigger": t if isinstance(t, str) else "",
                     "expansion": _lines(x) if isinstance(x, str) else ""})
    return {"header": False, "rows": rows}


def parse(text: str) -> dict:
    """The rows of a snippets file: {"header", "rows": [{"row", "trigger",
    "expansion"}]}. ``row`` counts the file's rows from 1, the header
    included. Raises table_io.TableError for a file that cannot be read."""
    text = table_io.decode(text)
    if text.lstrip().startswith(("[", "{")):
        return _from_json(text)
    table = table_io.parse(text)
    if not table:
        return {"header": False, "rows": []}
    columns = table_io.column_map(table[0], COLUMN_NAMES)
    header = columns is not None
    if header:
        free = [i for i in range(len(table[0])) if i not in columns.values()]
        if "trigger" not in columns and free:
            columns["trigger"] = free.pop(0)
        if "expansion" not in columns and free:
            columns["expansion"] = free.pop(0)
        body, first = table[1:], 2
    else:
        columns = {"trigger": 0, "expansion": 1}
        body, first = table, 1

    def cell(row, name):
        i = columns.get(name)
        return table_io.undefuse(row[i]) if i is not None and i < len(row) else ""

    return {"header": header,
            "rows": [{"row": n, "trigger": cell(row, "trigger"), "expansion": _lines(cell(row, "expansion"))}
                     for n, row in enumerate(body, first)]}


# ── Writing a file ───────────────────────────────────────────────────────────

def render_csv(items) -> str:
    rows = [list(CSV_COLUMNS)]
    for s in items:
        rows.append([s.get("trigger", ""), s.get("expansion", "")])
    return table_io.render(rows)


def render_json(items) -> str:
    kept = [{"trigger": s.get("trigger", ""), "expansion": s.get("expansion", "")} for s in items]
    return json.dumps(kept, ensure_ascii=False, indent=2) + "\n"


def export_bytes(items, fmt: str) -> bytes:
    """The file's bytes: CSV with a byte order mark (Excel and Numbers show
    accents), JSON as plain UTF-8. ValueError for any other ``fmt``."""
    if fmt == "csv":
        return table_io.encode(render_csv(items))
    if fmt == "json":
        return render_json(items).encode("utf-8")
    raise ValueError(f"unknown snippets export format: {fmt!r}")


# ── What an import does ──────────────────────────────────────────────────────

def plan_import(parsed: dict, existing) -> dict:
    """What importing ``parsed`` (parse) into ``existing`` would do. Changes
    neither.

    Returns {"header", "snippets" (the list as it would be), "new" and
    "already" (triggers, in file order), "skipped" ([{row, trigger, reason,
    message}]) and "counts" ({rows, new, already, skipped})}."""
    final = [dict(s) for s in existing]
    have = {snippets.trigger_key(s["trigger"]): s for s in final}
    out = {"new": [], "already": [], "skipped": []}
    seen = set()

    def skip(row, trigger, reason, other=""):
        out["skipped"].append({"row": row["row"], "trigger": trigger, "reason": reason,
                               "message": user_messages.snippet_skip_message(reason, other)})

    for row in parsed.get("rows", []):
        trigger = " ".join(str(row.get("trigger") or "").split())
        expansion = str(row.get("expansion") or "").strip()
        if not trigger:
            skip(row, "", "empty")
            continue
        if not expansion:
            skip(row, trigger, "no_text")
            continue
        if len(trigger) > MAX_TRIGGER:
            skip(row, trigger, "trigger_long")
            continue
        if len(expansion) > MAX_EXPANSION:
            skip(row, trigger, "text_long")
            continue
        key = snippets.trigger_key(trigger)
        if key in have:
            current = have[key]
            if key in seen:
                skip(row, trigger, "repeat")
            elif current["expansion"] == expansion:
                out["already"].append(current["trigger"])
                seen.add(key)
            else:
                skip(row, trigger, "exists", current["trigger"])
                seen.add(key)
            continue
        if len(final) >= MAX_SNIPPETS:
            skip(row, trigger, "full")
            continue
        item = {"trigger": trigger, "expansion": expansion}
        final.append(item)
        have[key] = item
        seen.add(key)
        out["new"].append(trigger)
    out["snippets"] = final
    out["header"] = bool(parsed.get("header"))
    out["counts"] = {"rows": len(parsed.get("rows", [])), "new": len(out["new"]),
                     "already": len(out["already"]), "skipped": len(out["skipped"])}
    return out


def plan_from_payload(content, is_base64, existing) -> dict:
    """plan_import for what the page sent (text, or a file's bytes as base64).
    Raises table_io.TableError with a plain sentence."""
    return plan_import(parse(table_io.from_payload(content, bool(is_base64))), existing)


PREVIEW_LIST = 30
PREVIEW_PROBLEMS = 50


def preview_view(plan: dict) -> dict:
    """What the page's preview shows of a plan (plan_import), kept small for a
    long file: the counts, the first new phrases, and the first rows left out
    with their reasons, each with how many more there are."""
    new, skipped = plan["new"], plan["skipped"]
    return {"header": plan["header"], "counts": dict(plan["counts"]),
            "new": list(new[:PREVIEW_LIST]), "new_more": max(0, len(new) - PREVIEW_LIST),
            "skipped": list(skipped[:PREVIEW_PROBLEMS]),
            "skipped_more": max(0, len(skipped) - PREVIEW_PROBLEMS)}

