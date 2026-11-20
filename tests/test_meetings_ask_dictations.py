"""Ask across everything: which dictations are handed to the model (src/meetings_ctl.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.meetings_ctl import dictations_material  # noqa: E402


def entry(i, text, **kw):
    return {"id": f"h{i}", "timestamp": f"2026-11-{10 + i:02d}T09:00:00", "text": text, **kw}


def test_nothing_to_give_is_an_empty_string():
    assert dictations_material([], "anything") == ""
    assert dictations_material(None, "anything") == ""
    assert dictations_material([entry(1, "")], "anything") == ""


def test_matching_entries_come_first_then_recent_and_none_twice():
    hist = [entry(1, "The supplier quote is due on Tuesday."), entry(2, "Lunch at one."),
            entry(3, "Remind me about the dentist.")]
    out = dictations_material(hist, "When is the supplier quote due?")
    lines = out.splitlines()[1:]
    assert "supplier quote" in lines[0]                       # the match leads, though it is the oldest
    assert [l for l in lines if "dentist" in l] and [l for l in lines if "Lunch" in l]
    assert len(lines) == 3                                    # the match is not repeated among the recent


def test_the_persons_own_edit_wins_and_the_app_is_named():
    out = dictations_material([entry(1, "raw words", styled="Styled words.", edited_text="My words.", app="Mail")], "x")
    assert "My words." in out and "raw words" not in out and " in Mail:" in out


def test_unsent_takes_transforms_and_commands_are_left_out():
    hist = [entry(1, "never sent", failed=True), entry(2, "a transform", kind="transform"),
            entry(3, "a command", kind="command"), entry(4, "A real dictation.")]
    out = dictations_material(hist, "x")
    assert "A real dictation." in out and "never sent" not in out and "transform" not in out and "command" not in out


def test_it_stops_at_the_size_limit():
    hist = [entry(i % 20, "word " * 200, id=f"k{i}") for i in range(100)]
    for h, i in zip(hist, range(100)):
        h["id"] = f"k{i}"
    out = dictations_material(hist, "x", limit_chars=5000)
    assert 0 < len(out) < 5400
