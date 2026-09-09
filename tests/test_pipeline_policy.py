#!/usr/bin/env python3
"""Superseded work must be kept; only explicit cancellation discards it.

Before this, pressing the hotkey again while the previous dictation was still
processing discarded that dictation's completed transcript. The user never
asked for that: they just started the next thought. Nothing was pasted and
nothing was saved, so the words were gone with no trace beyond the retained
audio.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pipeline_policy import decide  # noqa: E402


def test_normal_completion_pastes_and_saves():
    d = decide(superseded=False, cancelled=False)
    assert d["paste"] is True and d["save_history"] is True
    assert d["reason"] == "ok"


def test_superseded_keeps_the_words_but_does_not_paste():
    """THE FIX: a newer recording owns the focus and clipboard, so pasting
    would land in the wrong place, but the transcript is still valid work."""
    d = decide(superseded=True, cancelled=False)
    assert d["paste"] is False, "must not paste into the newer recording's target"
    assert d["save_history"] is True, "completed work must not be discarded"
    assert d["reason"] == "superseded"


def test_explicit_cancel_discards_everything():
    d = decide(superseded=False, cancelled=True)
    assert d["paste"] is False and d["save_history"] is False
    assert d["reason"] == "cancelled"


def test_cancel_wins_over_supersession():
    """If the user cancelled, that is an instruction, whatever else happened."""
    d = decide(superseded=True, cancelled=True)
    assert d["paste"] is False and d["save_history"] is False
    assert d["reason"] == "cancelled"


def test_paste_never_implied_without_save():
    """Anything safe enough to paste is certainly safe enough to keep."""
    for sup in (True, False):
        for can in (True, False):
            d = decide(superseded=sup, cancelled=can)
            if d["paste"]:
                assert d["save_history"], (sup, can)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
