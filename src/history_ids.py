"""Ids for Journal entries.

An entry used to have no name but its time to the second, and two takes in one
second share a time. New entries get an ``id``: a whole number
that goes up by one with each, so it can be told from every other entry,
sorts the same way the file does, and can be used to page ("older than id
412") or to delete one entry without touching its neighbour. Entries saved
before this have no id. They are not changed: where one is needed they are
found by their time and words (src/history_ops.py).

How the next id is worked out: one more than the highest id in the history
that is about to be written, and never lower than any id handed out earlier in
this run. The first part makes it survive a restart without a counter file;
the second means an id is not given out again after the entry that had it was
deleted, so an undo that puts the old entry back cannot collide with a new
one. (After a restart the newest entries' ids can be reused if they were
deleted, which nothing in the app keeps hold of.)
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_floor = 0          # the highest id given out in this run


def is_id(value) -> bool:
    """True for a real id: a whole number above zero. (``True`` is not one.)"""
    return type(value) is int and value > 0


def highest(history) -> int:
    """The highest id in ``history``; 0 when no entry has one."""
    top = 0
    for item in history:
        if type(item) is dict:
            v = item.get("id")
            if type(v) is int and v > top:
                top = v
    return top


def next_id(history) -> int:
    """A new id for an entry about to be added to ``history``."""
    global _floor
    with _lock:
        _floor = max(_floor, highest(history)) + 1
        return _floor


def stamp(item, history):
    """Give ``item`` an id unless it has one. Returns ``item`` itself, so the
    caller's copy (which the window is told about) carries the id too."""
    if isinstance(item, dict) and not is_id(item.get("id")):
        item["id"] = next_id(history)
    return item


def parse(value):
    """An id from what the window sends: an int, or digits in a string. None
    for anything else (a missing id, text, a float, a bool)."""
    if is_id(value):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        n = int(value)
        return n if n > 0 else None
    return None
