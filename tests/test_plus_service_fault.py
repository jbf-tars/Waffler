"""Waffler Plus failing on its own side: the dictation goes on another way.

Before: a dictation the service answered with a 5xx (its own provider
refused it, or it broke) failed, and the recording waited under Not sent,
even with a key or the model on this computer there to use. On 26 Nov the
service's own Groq key was refused and every Plus dictation failed so. Now a
fault on the service's side is treated as Plus not being there for this
dictation, as no internet already was: the person's key when there is one,
else the model on this computer, with one quiet line saying so, and the
recording kept when there is neither. Plus then rests a few minutes, so each
dictation does not wait on it first, and is tried again by itself after.
A refusal about the plan or the allowance (402, 429, 413) keeps its own
sentence and is never sent another way (tests/test_plus_dictation.py).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import transcribe_whisper as tw  # noqa: E402
import user_messages as um  # noqa: E402
from src import account  # noqa: E402
from test_plus_dictation import plus, transcriber, wav  # noqa: E402,F401  (plus: the fixture)


def _asked(fake) -> int:
    return len(fake.calls("POST", "/api/dictate"))


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_fault_on_the_services_side_and_this_dictation_goes_through_the_key(plus, monkeypatch, status):
    plus.dictate_refusal = (status, "The service's provider refused it.", "")
    t, cloud = transcriber(monkeypatch)
    assert t.transcribe_sync(wav()) == "from my own key" and cloud.calls == 1
    assert t.last_plus_gone == "unavailable"


def test_with_no_key_a_fault_falls_back_to_this_computer(plus, monkeypatch):
    plus.model = "ready"
    plus.dictate_refusal = (503, "Busy.", "")
    t, cloud = transcriber(monkeypatch, groq="")
    assert t.transcribe_sync(wav()) == "heard on this computer"
    assert t.last_plus_gone == "unavailable" and t._backend == "local" and cloud.calls == 0


def test_with_nothing_to_fall_back_to_the_recording_is_kept_with_the_services_sentence(plus, monkeypatch):
    plus.dictate_refusal = (503, "Busy.", "")
    t, _ = transcriber(monkeypatch, groq="")
    with pytest.raises(tw.PlusError) as e:
        t.transcribe_sync(wav())
    assert str(e.value) == account.DICTATE_BUSY


def test_after_a_fault_plus_rests_then_is_tried_again(plus, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(tw.time, "monotonic", lambda: clock[0])
    plus.dictate_refusal = (503, "Busy.", "")
    t, cloud = transcriber(monkeypatch)
    assert t.transcribe_sync(wav()) == "from my own key" and _asked(plus) == 1
    clock[0] += 60                                        # a minute later: not asked, the key at once
    assert t.transcribe_sync(wav()) == "from my own key"
    assert _asked(plus) == 1 and t.last_plus_gone == "unavailable" and cloud.calls == 2
    plus.dictate_refusal = None                           # the service is back
    clock[0] += tw.PLUS_REST_S
    t.transcribe_sync(wav())
    assert _asked(plus) == 2 and t.last_plus_gone == "" and cloud.calls == 2, "Plus again, by itself"


def test_the_line_says_plus_had_a_problem_and_what_was_used():
    for local, using in ((False, "your key"), (True, "this computer")):
        heading, body = um.plus_gone_message("unavailable", local)
        assert heading == f"Plus unavailable. Using {using}."
        assert len(heading.split()) <= 6, "the pill's line"
        assert body == (f"Waffler Plus had a problem on its side, so this dictation used {using}. "
                        "Your words were pasted as usual.")


# ── the whole dictation (app.py _process) ───────────────────────────────────

from test_plus_dictation import dictate, pipeline  # noqa: E402


def test_the_dictation_is_pasted_through_the_key_with_one_quiet_line(plus, monkeypatch):
    plus.dictate_refusal = (502, "The service's provider refused it.", "")
    p = dictate(pipeline(plus, monkeypatch))
    assert p.clipboard.pastes, "pasted, not kept back"
    assert p.cloud.calls == 1
    (toast,) = p.overlay.toasts()
    assert toast["heading"] == "Plus unavailable. Using your key."
    assert p.stored == [], "Plus stays the chosen way"
