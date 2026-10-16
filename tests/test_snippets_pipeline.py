"""A dictation that is only a snippet's trigger is pasted as the snippet.

Runs the real app.py pipeline with fakes (tests/_pipeline_harness.py): no
network, no keys, no clipboard, no private data.

* The whole dictation is a trigger (any case, any punctuation): exactly the
  expansion is pasted and saved, the clean-up model is NOT called, no usage is
  recorded for it, and the Journal card carries no quality mark that the model
  or the shortening would otherwise have earned.
* Otherwise the clean-up runs and the triggers in its answer are expanded, once
  each, longest first. The text of an expansion is never searched again.
* Try again on a Not sent recording takes the same two paths.
* The words list is read from snippets.json; a file that cannot be read costs
  the dictation nothing.
"""
import json

import pytest

from _pipeline_harness import (FakeStyler, FakeTranscriber, fast_limits, history,
                               make_pipeline, run_process)
import quality

ADDRESS = "12 Acacia Avenue\nLondon\nN1 1AA"


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    fast_limits(monkeypatch, TRANSCRIBE_DEADLINE_MIN_S=5.0, TRANSCRIBE_DEADLINE_BASE_S=5.0,
                TRANSCRIBE_DEADLINE_MAX_S=5.0, STYLE_DEADLINE_S=5.0)


def _snippets(tmp_path, *pairs):
    (tmp_path / "snippets.json").write_text(
        json.dumps([{"trigger": t, "expansion": e} for t, e in pairs]), encoding="utf-8")


def _dictate(tmp_path, said, *pairs, styler=None, raw=None):
    _snippets(tmp_path, *pairs)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(text=said),
                      styler=styler or FakeStyler())
    usage = []
    p.ns["record_usage_safely"] = lambda *a, **k: usage.append((a, k))
    run_process(p).join(5)
    p.usage = usage
    return p


# ── the whole dictation is the trigger ───────────────────────────────────────

@pytest.mark.parametrize("said", ["my address", "My address.", "MY ADDRESS!", "my, address", "my address?"])
def test_a_trigger_on_its_own_pastes_the_expansion_and_skips_the_model(tmp_path, said):
    p = _dictate(tmp_path, said, ("my address", ADDRESS))
    assert p.styler.calls == 0, "the clean-up must not be called for a snippet"
    assert p.clipboard.copies == [ADDRESS]
    assert p.clipboard.pastes == [ADDRESS]
    assert p.page.statuses[-1] == "done"


def test_the_card_says_what_was_said_and_what_was_pasted(tmp_path):
    p = _dictate(tmp_path, "my address.", ("my address", ADDRESS))
    [entry] = history(p)
    assert entry["text"] == "my address."                 # What you said
    assert entry["styled"] == ADDRESS                      # what was pasted
    assert entry["word_count"] == len(ADDRESS.split()) == 6
    assert entry["text_is"] == "asr_filtered"
    assert "failed" not in entry and "as_said" not in entry
    assert p.page.items[-1] == entry


def test_no_model_usage_is_recorded_for_a_snippet(tmp_path):
    p = _dictate(tmp_path, "my address", ("my address", ADDRESS))
    kinds = [a[0] for a, _k in p.usage]
    assert "gpt" not in kinds
    assert "whisper" in kinds                               # the speech step did run


def test_no_toast_and_no_pause_for_a_snippet(tmp_path):
    p = _dictate(tmp_path, "my address", ("my address", ADDRESS))
    assert p.overlay.toasts() == []
    assert p.page.pauses == []


def test_a_snippet_card_carries_no_quality_mark(tmp_path):
    """Eight spoken words and two pasted, text with no full stop: the two
    rules for a model that cut words, or stopped mid-sentence, would both fire."""
    said = "send the quarterly report to the whole finance team"
    sig = "Kind regards\nJames Farrelly\n0123 456 789"
    p = _dictate(tmp_path, said, (said, sig), ("brief", "Done"))
    [entry] = history(p)
    assert entry["styled"] == sig
    assert "quality" not in entry, entry.get("quality")


def test_short_expansion_for_a_long_trigger_is_not_flagged_as_dropped_words(tmp_path):
    said = "please send me the document about our budget plan"
    p = _dictate(tmp_path, said, (said, "See attached."))
    [entry] = history(p)
    assert entry["styled"] == "See attached." and "quality" not in entry


def test_an_expansion_is_pasted_as_saved_and_never_searched_again(tmp_path):
    p = _dictate(tmp_path, "sig", ("sig", "Thanks, addr"), ("addr", "1 High Street"))
    assert p.clipboard.pastes == ["Thanks, addr"]
    assert history(p)[0]["styled"] == "Thanks, addr"


def test_a_log_line_names_the_provider_not_the_words(tmp_path):
    p = _dictate(tmp_path, "my address", ("my address", ADDRESS))
    text = "\n".join(p.logged)
    assert "styling (snippet)" in text
    assert "Acacia" not in text and "my address" not in text


# ── something else is said: the clean-up runs, then the triggers are expanded ──

def test_a_trigger_inside_a_sentence_goes_through_the_clean_up_first(tmp_path):
    p = _dictate(tmp_path, "send it to my address please", ("my address", ADDRESS))
    assert p.styler.calls == 1
    assert p.clipboard.pastes == [f"Send it to {ADDRESS} please."]
    [entry] = history(p)
    assert entry["text"] == "send it to my address please"
    assert entry["styled"] == f"Send it to {ADDRESS} please."


def test_longest_trigger_first_and_one_pass_after_the_clean_up(tmp_path):
    p = _dictate(tmp_path, "see my address book and my address",
                 ("my", "MINE"), ("my address", "HOME"), ("my address book", "BOOK"))
    assert p.clipboard.pastes == ["See BOOK and HOME."]


def test_a_dictation_with_no_trigger_in_it_is_untouched(tmp_path):
    p = _dictate(tmp_path, "ok so ship it on monday", ("my address", ADDRESS))
    assert p.styler.calls == 1
    assert p.clipboard.pastes == ["Ok so ship it on monday."]


def test_without_a_snippets_file_nothing_changes(tmp_path):
    p = make_pipeline(tmp_path)
    run_process(p).join(5)
    assert p.styler.calls == 1 and p.clipboard.pastes == ["Ok so ship it on monday."]


@pytest.mark.parametrize("raw", [b"not json", b"{}", b"[1, 2, null]", b"\xff\xfe"])
def test_a_snippets_file_that_cannot_be_read_costs_the_dictation_nothing(tmp_path, raw):
    (tmp_path / "snippets.json").write_bytes(raw)
    p = make_pipeline(tmp_path)
    run_process(p).join(5)
    assert p.styler.calls == 1 and p.clipboard.pastes == ["Ok so ship it on monday."]
    assert p.page.statuses[-1] == "done"


def test_a_snippet_that_fails_to_check_still_lets_the_dictation_through(tmp_path):
    _snippets(tmp_path, ("ok so ship it on monday", "X"))
    p = make_pipeline(tmp_path)
    p.ns["_snippets"] = type("Boom", (), {
        "load": staticmethod(lambda path: (_ for _ in ()).throw(OSError("locked"))),
        "whole_utterance": staticmethod(lambda *a: 1 / 0),
        "apply": staticmethod(lambda *a: 1 / 0)})
    run_process(p).join(5)
    assert p.styler.calls == 1
    assert p.clipboard.pastes == ["Ok so ship it on monday."]
    assert any("[snippets] not checked" in m for m in p.logged)


# ── Try again on a Not sent recording ────────────────────────────────────────

def _not_sent(tmp_path, said, *pairs):
    _snippets(tmp_path, *pairs)
    p = make_pipeline(tmp_path)
    p.transcriber.error = ConnectionError("Connection error.")
    run_process(p).join(5)
    entry = [e for e in history(p) if e.get("failed")][-1]
    p.transcriber.error = None
    p.transcriber.text = said
    return p, entry["unsent_id"]


def test_try_again_on_a_trigger_pastes_nothing_and_skips_the_model(tmp_path):
    p, uid = _not_sent(tmp_path, "My address.", ("my address", ADDRESS))
    assert p.resend_unsent(uid)["ok"] is True
    [item] = history(p)
    assert item["styled"] == ADDRESS and item["text"] == "My address."
    assert p.styler.calls == 0
    assert p.clipboard.pastes == []                     # Try again never pastes


def test_try_again_expands_a_trigger_inside_a_sentence(tmp_path):
    p, uid = _not_sent(tmp_path, "mail it to my address", ("my address", ADDRESS))
    assert p.resend_unsent(uid)["ok"] is True
    [item] = history(p)
    assert item["styled"] == f"Mail it to {ADDRESS}."
    assert p.styler.calls == 1


def test_try_again_never_searches_an_expansion_for_triggers(tmp_path):
    p, uid = _not_sent(tmp_path, "sig", ("sig", "Thanks, addr"), ("addr", "1 High Street"))
    assert p.resend_unsent(uid)["ok"] is True
    assert history(p)[0]["styled"] == "Thanks, addr"


# ── the quality rules, on their own ──────────────────────────────────────────

def _assess(provider, said_words, styled, speech=3.0):
    return quality.assess(speech_seconds=speech, transcript_words=said_words,
                          styled_words=len(styled.split()), asr_filtered=False,
                          styling_provider=provider, styled_text=styled)


def test_a_snippet_is_judged_on_the_audio_only():
    sig = "Kind regards\nJames Farrelly\n0123 456 789"
    assert _assess("snippet", 9, sig)["level"] == "ok"
    # The same numbers from a model are exactly what the rules are for.
    q = _assess("groq", 9, sig)
    assert "styled_dropped_words" in q["flags"] and q["level"] == "low"
    assert "unterminated_ending" in q["flags"]


def test_a_snippet_still_gets_the_word_rate_check():
    """Eleven seconds of speech and two words came back: that is about the
    recording, not the snippet, so it is still said."""
    q = _assess("snippet", 2, "Done", speech=12.0)
    assert "low_word_rate" in q["flags"]
