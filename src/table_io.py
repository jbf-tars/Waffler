"""The small CSV layer Vocabulary and Snippets import and export share.

Reading is forgiving, because the file may have been saved by Excel, Numbers,
Google Sheets or a text editor, in any of their habits:

* the bytes may be UTF-8 with or without a byte order mark, UTF-16 (Excel's
  "Unicode Text"), or Windows-1252 (Excel's plain "CSV" on Windows);
* the delimiter may be a comma, a semicolon (Excel in much of Europe) or a tab
  (a paste from a spreadsheet), and Excel's ``sep=;`` first line is understood;
* a cell may be quoted, hold quotes (doubled) and hold line breaks;
* the first row may name the columns in a few spellings, or may not.

Writing is careful in the other direction: a cell that starts with ``=``,
``+``, ``-``, ``@``, a tab or a carriage return gets a ``'`` in front, so a
spreadsheet shows it as text instead of running it as a formula (CSV
injection, the same rule as the Journal export), and the file starts with a
byte order mark so Excel and Numbers show accents. ``undefuse`` takes that mark
off again on the way in, so an exported list imports unchanged.

Nothing here reads a file or touches the window: the bridge hands in text or
bytes and gets text or bytes back.
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import re

# A cell a spreadsheet runs as a formula starts with one of these (OWASP "CSV
# injection"). Kept the same as src/history_export.py, and a test compares them.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")

MAX_CHARS = 2_000_000          # a list of words or snippets is nowhere near this
MAX_ROWS = 5000                # the caps (500 words, 200 snippets) are far below it
DELIMITERS = (",", ";", "\t")

ERR_UNREADABLE = "Waffler couldn't read that file. Save it as a CSV and try again."
ERR_TOO_BIG = "That file is too big to be a list of words or snippets."
ERR_TOO_MANY_ROWS = f"That file has more than {MAX_ROWS:,} rows. Split it up and try again."


class TableError(ValueError):
    """A file that cannot be read, with a plain sentence for the page."""


# ── Bytes and text ───────────────────────────────────────────────────────────

def decode(raw) -> str:
    """The text of a file's bytes. A byte order mark says UTF-8 or UTF-16;
    without one, UTF-8 if it is valid, else Windows-1252 (every byte there but
    five means a character), else Latin-1. NUL characters are dropped. Never
    raises."""
    if isinstance(raw, str):
        text = raw
    else:
        raw = bytes(raw or b"")
        text = None
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            try:
                text = raw.decode("utf-16")
            except UnicodeDecodeError:
                text = None
        if text is None:
            for enc in ("utf-8-sig", "cp1252"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
        if text is None:
            text = raw.decode("latin-1")
    return text.lstrip("﻿").replace("\x00", "")


def from_payload(content, is_base64: bool = False) -> str:
    """The text of what the page sent: the text itself, or (a file read in the
    page, so its encoding is not lost) its bytes as base64."""
    if not isinstance(content, str):
        raise TableError(ERR_UNREADABLE)
    if len(content) > MAX_CHARS * 2:
        raise TableError(ERR_TOO_BIG)
    if is_base64:
        try:
            raw = base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError):
            raise TableError(ERR_UNREADABLE) from None
        if len(raw) > MAX_CHARS * 2:
            raise TableError(ERR_TOO_BIG)
        text = decode(raw)
    else:
        text = decode(content)
    if len(text) > MAX_CHARS:
        raise TableError(ERR_TOO_BIG)
    return text


def encode(text: str) -> bytes:
    """A file's bytes: UTF-8 with a byte order mark, which is what makes Excel
    and Numbers show accents."""
    return str(text).encode("utf-8-sig")


# ── Reading ──────────────────────────────────────────────────────────────────

def sniff_delimiter(text: str) -> str:
    """The delimiter of a table: whichever of comma, semicolon and tab the
    first line has most of outside quotes (a comma wins a tie, and a line with
    none is a single column, so a comma)."""
    counts = {d: 0 for d in DELIMITERS}
    quoted = False
    has_content = False
    for ch in str(text or ""):
        if ch == '"':
            quoted = not quoted
            has_content = True
        elif quoted:
            continue
        elif ch in counts:
            counts[ch] += 1
            has_content = True
        elif ch in "\r\n":
            if has_content:
                break                       # the first line with anything in it decides
        elif not ch.isspace():
            has_content = True
    best = max(DELIMITERS, key=lambda d: (counts[d], -DELIMITERS.index(d)))
    return best if counts[best] else ","


def parse(text: str) -> list:
    """The rows of a table, each a list of trimmed cells. Rows with nothing in
    them are dropped. Raises TableError for a file that is not one."""
    text = decode(text)
    delimiter = None
    first, _, rest = text.partition("\n")
    m = re.fullmatch(r"\s*sep=(.)\s*", first)
    if m:
        delimiter, text = m.group(1), rest
    if delimiter is None:
        delimiter = sniff_delimiter(text)
    rows = []
    try:
        for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter):
            cells = [c.strip() for c in row]
            if not any(cells):
                continue
            rows.append(cells)
            if len(rows) > MAX_ROWS:
                raise TableError(ERR_TOO_MANY_ROWS)
    except csv.Error:
        raise TableError(ERR_UNREADABLE) from None
    return rows


def _norm(cell) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(cell or "").replace("﻿", "").casefold()).strip("_")


def column_map(header, aliases: dict):
    """{name: index} for a first row that names its columns, or None when it
    is data. ``aliases`` is {name: {spellings}}, written the way ``_norm``
    leaves them (lower case, words joined by an underscore): "Sounds like",
    "sounds-like" and "SoundsLike" all read as ``sounds_like`` if that is
    listed, and "soundslike" if that is. The first cell to match a name wins."""
    wanted = {}
    for name, spellings in aliases.items():
        for s in spellings:
            wanted[_norm(s)] = name
    found = {}
    for i, cell in enumerate(header):
        name = wanted.get(_norm(cell))
        if name and name not in found:
            found[name] = i
    return found or None


# ── Writing ──────────────────────────────────────────────────────────────────

def defuse(cell) -> str:
    """A cell's text, safe to open in a spreadsheet."""
    text = str(cell if cell is not None else "").replace("\x00", "")
    if text.startswith(FORMULA_STARTS):
        return "'" + text
    return text


def undefuse(cell) -> str:
    """What ``defuse`` made of a cell, put back as it was. A cell that merely
    starts with an apostrophe ('tis) is left alone: only the mark export puts
    in front of a formula character comes off."""
    text = str(cell if cell is not None else "")
    if len(text) > 1 and text[0] == "'" and text[1] in FORMULA_STARTS:
        return text[1:]
    return text


def render(rows, delimiter: str = ",") -> str:
    """Rows as CSV text: every cell defused, quoted where it needs it, CRLF
    line ends (what spreadsheets write)."""
    buf = io.StringIO(newline="")
    out = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    for row in rows:
        out.writerow([defuse(c) for c in row])
    return buf.getvalue()
