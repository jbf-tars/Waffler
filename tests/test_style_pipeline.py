"""The Style section through the real pipeline (S6).

The real app.py methods run against the fakes in tests/_pipeline_harness.py:
no network, no keys, no clipboard, no window, no private data. What is pinned:

* default settings leave today's output byte for byte, over a fixed set of
  dictations, in every kind of app;
* clean-up Off: no model is asked, the words are what was heard (after the
  Vocabulary and Snippets, with no filler taken out), its own provider tag,
  no chip, no toast, no cost, and the same in Redo and Try again;
* a style per kind of app, from the app in front when the key went down,
  after the clean-up and the short-dictation bypass, never on a snippet;
* the signature: when it is added and when it is not, the field read only when
  it matters, and Redo and Try again never add one.
"""
import json
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import history_ops  # noqa: E402
import style_layer  # noqa: E402
import text_context  # noqa: E402
import transcribe_whisper  # noqa: E402
from style_openai import OpenAIStyler  # noqa: E402
from text_context import AppContext  # noqa: E402
from _pipeline_harness import (  # noqa: E402
    FakeStyler, FakeTranscriber, Hang, fast_limits, history, make_pipeline, run_process,
)
from test_history_delete_redo import api_of  # noqa: E402

MAIL = AppContext("com.apple.mail", "Mail", "", "email")
SLACK = AppContext("com.tinyspeck.slackmacgap", "Slack", "", "work")
MESSAGES = AppContext("com.apple.MobileSMS", "Messages", "", "personal")
NOTES = AppContext("com.apple.Notes", "Notes", "", "other")
KINDS = {"personal": MESSAGES, "work": SLACK, "email": MAIL, "other": NOTES}

SIG = {"enabled": True, "text": "Best,\nJames Farrelly"}
LONG = "thanks for sending that over it really does help the whole team a lot today"


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    fast_limits(monkeypatch, TRANSCRIBE_DEADLINE_MIN_S=3.0, TRANSCRIBE_DEADLINE_BASE_S=3.0,
                TRANSCRIBE_DEADLINE_MAX_S=3.0, STYLE_DEADLINE_S=3.0)


class Field:
    """What the text before the cursor says, standing in for Accessibility."""

    def __init__(self, before="", after=""):
        self.before, self.after, self.calls = before, after, []

    def __call__(self, chars=300, timeout=1.5):
        self.calls.append(chars)
        if isinstance(self.before, Exception):
            raise self.before
        return self.before, self.after


@pytest.fixture
def screen(monkeypatch):
    """The app in front and its field. The test changes ``box["front"]`` to
    move the user to another app."""
    box = {"front": MAIL, "field": Field()}
    monkeypatch.setattr(text_context, "frontmost_app", lambda read_title=None: box["front"])
    monkeypatch.setattr(text_context, "get_text_around_cursor",
                        lambda chars=300, timeout=1.5: box["field"](chars, timeout))
    return box


def write_settings(tmp_path, **kw):
    (tmp_path / "settings.json").write_text(json.dumps(kw), encoding="utf-8")


def pipe(tmp_path, ctx=NOTES, said="ok so ship it on monday", styler=None, **settings):
    tmp_path.mkdir(parents=True, exist_ok=True)
    if settings:
        write_settings(tmp_path, **settings)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(text=said), styler=styler or FakeStyler())
    p._press_context = (1, ctx)
    p.usage = []
    p.ns["record_usage_safely"] = lambda *a, **k: p.usage.append((a, k))
    return p


def dictate(p, said=None):
    if said is not None:
        p.transcriber.text = said
    worker = run_process(p)
    worker.join(8)
    assert not worker.is_alive()
    return history(p)[-1]


class Plain:
    """A clean-up that returns the words in the case and punctuation it was
    told, so a test can say exactly what the style layer is given."""

    def __init__(self, out, provider="groq", hang=None):
        self.out, self.provider, self.calls, self.contexts, self.hang = out, provider, 0, [], hang

    def style(self, transcript, app_context=None):
        self.calls += 1
        self.contexts.append(app_context)
        if self.hang is not None:
            self.hang.wait()
        out = self.out(transcript) if callable(self.out) else self.out
        return out, {"input_tokens": 10, "output_tokens": 8, "api_used": True, "provider": self.provider}

    def _basic_clean(self, text):
        return text.strip()

    def _format_email_layout(self, text):
        return text


# ── default settings: today's output, byte for byte ──────────────────────────

FIXED = [
    "ok so ship it on monday",
    "hi sam thanks for the update i will look at it today",
    "um so I I think that that is fine er",
    "one apple two pears three plums",
    "Dr. Smith will call at 5 p.m. tomorrow about the NASA grant",
    "bonjour tout le monde",
    "你好，我们明天见。",
    "sounds good",
    "Thanks.",
    "send the file to sam@example.com and see https://example.com/a",
    "yes",
    LONG + " and then we should talk about the plan for next week because it matters",
]


def expected(said):
    return FakeStyler().style(said)[0]


@pytest.mark.parametrize("kind", list(KINDS))
def test_defaults_paste_exactly_what_the_clean_up_returned(tmp_path, kind):
    for said in FIXED:
        d = tmp_path / f"{kind}-{abs(hash(said))}"
        d.mkdir()
        p = pipe(d, KINDS[kind], said)
        entry = dictate(p, said)
        want = expected(said)
        assert p.clipboard.pastes == [want], (kind, said)
        assert entry["styled"] == want and entry["text"] == said
        assert entry["word_count"] == len(want.split())
        assert "style" not in entry and "quality" not in entry and "as_said" not in entry
        assert entry["provider"]["cleanup"] == "groq"
        assert [a[0] for a, _k in p.usage] == ["whisper", "gpt"]


def test_every_default_spelled_out_changes_nothing(tmp_path):
    spelled = dict(cleanup="on", style_by_category={k: "auto" for k in KINDS},
                   email_signature={"enabled": False, "text": "Best, James"})
    for n, said in enumerate(FIXED):
        for kind, ctx in KINDS.items():
            d = tmp_path / f"{n}-{kind}"
            d.mkdir()
            p = pipe(d, ctx, said, **spelled)
            entry = dictate(p, said)
            assert entry["styled"] == expected(said) and p.clipboard.pastes == [expected(said)]
            assert "style" not in entry


def test_defaults_never_read_the_field_and_read_the_vocabulary_once(tmp_path, screen, monkeypatch):
    reads = []
    monkeypatch.setattr(transcribe_whisper, "load_vocab", lambda: reads.append(1) or [])
    for kind, ctx in KINDS.items():
        p = pipe(tmp_path / kind, ctx, LONG)
        assert dictate(p)["styled"] == expected(LONG)
    assert screen["field"].calls == []
    assert len(reads) == len(KINDS), "the one read the vocabulary step has always made"


def test_the_short_dictation_bypass_is_unchanged_at_defaults(tmp_path):
    p = pipe(tmp_path, MESSAGES, styler=OpenAIStyler())
    for said in ("ok so ship it", "yes", "um okay thanks", "Sounds good."):
        want = OpenAIStyler()._basic_clean(said)
        assert dictate(p, said)["styled"] == want
        assert p.clipboard.pastes[-1] == want


# ── clean-up Off ─────────────────────────────────────────────────────────────

def test_off_asks_no_model_and_pastes_what_was_heard(tmp_path):
    p = pipe(tmp_path, NOTES, "um so I I think that that is fine er", cleanup="off")
    entry = dictate(p)
    assert p.styler.calls == 0
    assert p.clipboard.pastes == ["um so I I think that that is fine er"], "no filler or stutter is taken out"
    assert entry["styled"] == entry["text"] == "um so I I think that that is fine er"
    assert entry["word_count"] == 10


def test_off_never_removes_a_real_word(tmp_path):
    # "um" is "one" in Portuguese, "er" is "he" in German: not fillers there.
    for said in ("um dois tres", "er sagt es", "that that is what I said", "the the band"):
        p = pipe(tmp_path / said.replace(" ", "_"), NOTES, said, cleanup="off")
        assert dictate(p)["styled"] == said


def test_off_trims_only_the_ends(tmp_path):
    p = pipe(tmp_path, NOTES, "  hello there  \n", cleanup="off")
    assert dictate(p)["styled"] == "hello there"


def test_off_has_its_own_provider_tag_and_no_chip_toast_pause_or_cost(tmp_path):
    p = pipe(tmp_path, NOTES, "so the plan is to leave it be okay", cleanup="off")
    entry = dictate(p)
    assert entry["provider"] == {"stt": "groq", "cleanup": "off"}
    assert "quality" not in entry and "as_said" not in entry
    assert p.overlay.toasts() == [] and p.page.pauses == []
    assert [a[0] for a, _k in p.usage] == ["whisper"], "no clean-up cost"
    assert p.page.statuses[-1] == "done"
    quality_log = tmp_path / "quality.jsonl"
    row = json.loads(quality_log.read_text(encoding="utf-8").splitlines()[-1])
    assert row["styling_provider"] == "off" and row["level"] == "ok"


def test_off_with_a_long_unpunctuated_take_lights_no_chip(tmp_path):
    p = pipe(tmp_path, NOTES, LONG + " and that is the whole story really", cleanup="off")
    entry = dictate(p)
    assert "quality" not in entry


def test_off_still_expands_a_snippet_inside_the_words(tmp_path):
    (tmp_path / "snippets.json").write_text(
        json.dumps([{"trigger": "my address", "expansion": "12 Acacia Avenue"}]), encoding="utf-8")
    p = pipe(tmp_path, NOTES, "send it to my address please", cleanup="off")
    assert dictate(p)["styled"] == "send it to 12 Acacia Avenue please"
    assert p.styler.calls == 0


def test_a_whole_snippet_keeps_its_own_tag_under_off(tmp_path):
    (tmp_path / "snippets.json").write_text(
        json.dumps([{"trigger": "my address", "expansion": "12 Acacia Avenue"}]), encoding="utf-8")
    p = pipe(tmp_path, NOTES, "my address", cleanup="off")
    entry = dictate(p)
    assert entry["styled"] == "12 Acacia Avenue" and entry["provider"]["cleanup"] == "snippet"


def test_off_applies_the_vocabulary(tmp_path, monkeypatch):
    vocab = tmp_path / "vocab.json"
    vocab.write_text(json.dumps(["Waffler"]), encoding="utf-8")
    monkeypatch.setattr(transcribe_whisper, "VOCAB_FILE", vocab)
    p = pipe(tmp_path, NOTES, "i love using wafler every day", cleanup="off")
    entry = dictate(p)
    assert entry["styled"] == "i love using Waffler every day"
    assert entry["vocab_changes"] == [["wafler", "Waffler"]]


def test_off_does_not_style(tmp_path):
    p = pipe(tmp_path, MESSAGES, "Sounds good. See you.", cleanup="off",
             style_by_category={"personal": "very_casual"})
    entry = dictate(p)
    assert entry["styled"] == "Sounds good. See you." and "style" not in entry


def test_off_and_a_signature_the_signature_still_applies(tmp_path, screen):
    p = pipe(tmp_path, MAIL, "hi sam " + LONG, cleanup="off", email_signature=SIG)
    entry = dictate(p)
    assert entry["styled"] == "hi sam " + LONG + "\n\nBest,\nJames Farrelly"
    assert entry["provider"]["cleanup"] == "off" and "quality" not in entry


def test_off_is_read_for_every_dictation(tmp_path):
    p = pipe(tmp_path, NOTES, "so ship it on monday please")
    assert dictate(p)["provider"]["cleanup"] == "groq"
    write_settings(tmp_path, cleanup="off")
    assert dictate(p)["provider"]["cleanup"] == "off"
    write_settings(tmp_path, cleanup="on")
    assert dictate(p)["provider"]["cleanup"] == "groq"


# ── a style for each kind of app ─────────────────────────────────────────────

STYLES = {"personal": "very_casual", "work": "excited", "email": "casual", "other": "formal"}


def test_each_kind_of_app_gets_its_own_style(tmp_path):
    out = Plain("Sounds good. Thanks.")
    for kind, ctx in KINDS.items():
        p = pipe(tmp_path / kind, ctx, "sounds good thanks", styler=out, style_by_category=STYLES)
        entry = dictate(p)
        want = {"personal": "sounds good. thanks", "work": "Sounds good! Thanks!",
                "email": "Sounds good. Thanks.", "other": "Sounds good. Thanks."}[kind]
        assert entry["styled"] == want and p.clipboard.pastes == [want], kind
        assert entry["style"] == STYLES[kind]
        assert entry["category"] == kind


def test_casual_drops_the_last_full_stop_of_a_one_sentence_message(tmp_path):
    p = pipe(tmp_path, SLACK, "sounds good", styler=Plain("Sounds good."), style_by_category={"work": "casual"})
    entry = dictate(p)
    assert entry["styled"] == "Sounds good" and entry["style"] == "casual"
    assert entry["text"] == "sounds good", "what was heard is never styled"


def test_the_style_comes_from_the_app_the_key_went_down_in(tmp_path, screen):
    p = pipe(tmp_path, MESSAGES, "sounds good", styler=Plain("Sounds good."),
             style_by_category={"personal": "very_casual", "work": "excited"})
    screen["front"] = SLACK                       # the user moved on while the words were made
    entry = dictate(p)
    assert entry["styled"] == "sounds good" and entry["category"] == "personal"


def test_an_unknown_app_is_other(tmp_path):
    p = pipe(tmp_path, text_context.UNKNOWN, "sounds good", styler=Plain("Sounds good."),
             style_by_category={"other": "casual", "personal": "very_casual"})
    entry = dictate(p)
    assert entry["styled"] == "Sounds good" and entry["style"] == "casual"
    assert "category" not in entry


def test_a_kind_on_auto_is_not_styled_even_when_others_are(tmp_path):
    p = pipe(tmp_path, NOTES, "sounds good", styler=Plain("Sounds good."),
             style_by_category={"personal": "very_casual", "work": "excited"})
    entry = dictate(p)
    assert entry["styled"] == "Sounds good." and "style" not in entry


def test_formal_is_recorded_and_changes_nothing(tmp_path):
    p = pipe(tmp_path, NOTES, "sounds good", styler=Plain("Sounds good."), style_by_category={"other": "formal"})
    entry = dictate(p)
    assert entry["styled"] == "Sounds good." and entry["style"] == "formal"


def test_the_style_runs_on_the_short_dictation_that_never_reaches_a_model(tmp_path):
    p = pipe(tmp_path, MESSAGES, styler=OpenAIStyler(), style_by_category={"personal": "very_casual"})
    entry = dictate(p, "Sounds good.")
    assert entry["styled"] == "sounds good" and entry["style"] == "very_casual"
    assert "cleanup" not in entry["provider"], "no model, no provider tag: as before"
    p2 = pipe(tmp_path / "w", SLACK, styler=OpenAIStyler(), style_by_category={"work": "excited"})
    assert dictate(p2, "Thanks.")["styled"] == "Thanks!"


def test_the_style_runs_on_the_fallback_text_too(tmp_path):
    p = pipe(tmp_path, SLACK, "sounds good", styler=FakeStyler(fallback_reason="TIMEOUT|too slow"),
             style_by_category={"work": "casual"})
    entry = dictate(p)
    assert entry["provider"]["cleanup"] == "basic_clean"
    assert entry["style"] == "casual"


def test_a_snippet_is_pasted_exactly_and_never_styled(tmp_path):
    expansion = "Thanks. Regards.\n\nJames"
    (tmp_path / "snippets.json").write_text(
        json.dumps([{"trigger": "my sign off", "expansion": expansion}]), encoding="utf-8")
    p = pipe(tmp_path, MESSAGES, "my sign off", style_by_category={"personal": "very_casual"})
    entry = dictate(p)
    assert entry["styled"] == expansion and p.clipboard.pastes == [expansion]
    assert "style" not in entry and p.styler.calls == 0


def test_a_snippet_inside_the_words_is_not_restyled_either(tmp_path):
    expansion = "Thanks. Regards.\n\nJames"
    (tmp_path / "snippets.json").write_text(
        json.dumps([{"trigger": "my sign off", "expansion": expansion}]), encoding="utf-8")
    p = pipe(tmp_path, MESSAGES, "ok", styler=Plain("Okay. My sign off."), style_by_category={"personal": "very_casual"})
    entry = dictate(p, "ok my sign off")
    assert entry["styled"] == "okay. " + expansion
    assert entry["styled"].endswith("Thanks. Regards.\n\nJames")


def test_the_vocabulary_keeps_its_capitals(tmp_path, monkeypatch):
    vocab = tmp_path / "vocab.json"
    vocab.write_text(json.dumps(["Waffler"]), encoding="utf-8")
    monkeypatch.setattr(transcribe_whisper, "VOCAB_FILE", vocab)
    p = pipe(tmp_path, MESSAGES, "waffler is great", styler=Plain("Waffler is great. Paris is nice."),
             style_by_category={"personal": "very_casual"})
    assert dictate(p)["styled"] == "Waffler is great. paris is nice"


def test_a_language_that_is_not_styled_is_left_alone(tmp_path):
    p = pipe(tmp_path, MESSAGES, "你好", styler=Plain("你好。再见。"), language="zh",
             style_by_category={"personal": "very_casual"})
    entry = dictate(p)
    assert entry["styled"] == "你好。再见。" and "style" not in entry


def test_the_language_setting_reaches_the_rules(tmp_path):
    p = pipe(tmp_path, MESSAGES, "ich komme", styler=Plain("Ich komme. Das Haus ist groß."), language="de",
             style_by_category={"personal": "very_casual"})
    assert dictate(p)["styled"] == "ich komme. das Haus ist groß"


def test_a_style_does_not_light_a_chip(tmp_path):
    p = pipe(tmp_path, MESSAGES, LONG, styler=Plain(LONG.capitalize() + "."), style_by_category={"personal": "casual"})
    entry = dictate(p)
    assert entry["styled"] == LONG.capitalize() and "quality" not in entry


def test_a_real_cut_off_is_still_a_chip_under_a_style(tmp_path):
    p = pipe(tmp_path, MESSAGES, LONG, styler=Plain(LONG.capitalize() + " and then we sent it to the"),
             style_by_category={"personal": "casual"})
    entry = dictate(p)
    assert entry["quality"]["flags"] == ["truncated_midsentence"]


def test_the_style_is_read_for_every_dictation(tmp_path):
    p = pipe(tmp_path, SLACK, "sounds good", styler=Plain("Sounds good."))
    assert dictate(p)["styled"] == "Sounds good."
    write_settings(tmp_path, style_by_category={"work": "excited"})
    assert dictate(p)["styled"] == "Sounds good!"
    write_settings(tmp_path, style_by_category={"work": "auto"})
    assert dictate(p)["styled"] == "Sounds good."


def test_a_hand_edited_style_the_kind_does_not_offer_is_ignored(tmp_path):
    p = pipe(tmp_path, SLACK, "sounds good", styler=Plain("Sounds good."),
             style_by_category={"work": "very_casual", "other": 7})
    entry = dictate(p)
    assert entry["styled"] == "Sounds good." and "style" not in entry


def test_a_style_that_raises_costs_nothing(tmp_path, monkeypatch):
    import style_post
    monkeypatch.setattr(style_post, "apply_style", lambda *a, **k: 1 / 0)
    p = pipe(tmp_path, SLACK, "sounds good", styler=Plain("Sounds good."), style_by_category={"work": "casual"})
    entry = dictate(p)
    assert entry["styled"] == "Sounds good." and "style" not in entry
    assert not any("sounds good" in line.lower() for line in p.logged), "no words in the log"


def test_the_log_names_no_words(tmp_path):
    p = pipe(tmp_path, MAIL, "hi sam please check the numbers before the meeting on monday morning",
             styler=Plain("Hi Sam, please check the numbers before the meeting on Monday morning."),
             style_by_category={"email": "casual"}, email_signature=SIG)
    dictate(p)
    text = "\n".join(p.logged).lower()
    for word in ("numbers", "monday", "james farrelly", "best"):
        assert word not in text, word


# ── the signature ────────────────────────────────────────────────────────────

BODY = "Hi Sam, thanks for sending that over, it really does help the whole team a lot today."


def test_a_long_message_in_an_email_app_gets_the_signature(tmp_path, screen):
    p = pipe(tmp_path, MAIL, LONG, styler=Plain(BODY), email_signature=SIG)
    entry = dictate(p)
    assert entry["styled"] == BODY + "\n\nBest,\nJames Farrelly"
    assert p.clipboard.pastes == [entry["styled"]]
    assert entry["text"] == LONG, "what was heard has no signature"
    assert entry["word_count"] == len(BODY.split()), "the signature is not counted as words dictated"
    assert len(screen["field"].calls) == 1


def test_a_signature_does_not_light_a_chip(tmp_path, screen):
    p = pipe(tmp_path, MAIL, LONG, styler=Plain(BODY), email_signature=SIG)
    assert "quality" not in dictate(p)


def test_a_greeting_alone_is_enough(tmp_path, screen):
    p = pipe(tmp_path, MAIL, "hi sam", styler=Plain("Hi Sam,"), email_signature=SIG)
    assert dictate(p)["styled"] == "Hi Sam,\n\nBest,\nJames Farrelly"


def test_a_short_answer_gets_none_and_the_field_is_not_read(tmp_path, screen):
    p = pipe(tmp_path, MAIL, "sounds good", styler=Plain("Sounds good."), email_signature=SIG)
    assert dictate(p)["styled"] == "Sounds good."
    assert screen["field"].calls == []


@pytest.mark.parametrize("kind", ["personal", "work", "other"])
def test_only_email_gets_one_and_the_field_is_not_read_elsewhere(tmp_path, screen, kind):
    p = pipe(tmp_path, KINDS[kind], LONG, styler=Plain(BODY), email_signature=SIG)
    assert dictate(p)["styled"] == BODY
    assert screen["field"].calls == []


def test_a_signature_that_is_off_never_reads_the_field(tmp_path, screen):
    p = pipe(tmp_path, MAIL, LONG, styler=Plain(BODY), email_signature={"enabled": False, "text": "Best"})
    assert dictate(p)["styled"] == BODY
    assert screen["field"].calls == []


def test_a_whole_snippet_never_gets_one(tmp_path, screen):
    (tmp_path / "snippets.json").write_text(
        json.dumps([{"trigger": "my reply", "expansion": "Thanks, I will get back to you on this one soon."}]),
        encoding="utf-8")
    p = pipe(tmp_path, MAIL, "my reply", email_signature=SIG)
    assert dictate(p)["styled"] == "Thanks, I will get back to you on this one soon."
    assert screen["field"].calls == []


def test_a_signature_already_before_the_cursor_is_not_added_again(tmp_path, screen):
    screen["field"] = Field("Hi Sam,\n\nSee you Friday.\n\nBest,\nJames Farrelly\n\n")
    p = pipe(tmp_path, MAIL, LONG, styler=Plain(BODY), email_signature=SIG)
    assert dictate(p)["styled"] == BODY


def test_other_text_in_the_field_and_no_signature_gets_it(tmp_path, screen):
    screen["field"] = Field("Dear Sam,\n\nI hope you are well.\n\n")
    p = pipe(tmp_path, MAIL, LONG, styler=Plain(BODY), email_signature=SIG)
    assert dictate(p)["styled"].endswith("\n\nBest,\nJames Farrelly")


def test_when_the_field_cannot_be_read_a_second_one_in_ten_minutes_is_not_added(tmp_path, screen):
    p = pipe(tmp_path, MAIL, LONG, styler=Plain(BODY), email_signature=SIG)
    assert dictate(p)["styled"].endswith("James Farrelly")          # the field says nothing: first time
    assert not dictate(p)["styled"].endswith("James Farrelly")      # again, soon: the memory says no
    assert not dictate(p)["styled"].endswith("James Farrelly")
    p._signature_memory.forget()
    assert dictate(p)["styled"].endswith("James Farrelly")


def test_an_exception_reading_the_field_is_the_same_as_not_reading_it(tmp_path, screen):
    screen["field"] = Field(RuntimeError("the app did not answer"))
    p = pipe(tmp_path, MAIL, LONG, styler=Plain(BODY), email_signature=SIG)
    assert dictate(p)["styled"].endswith("James Farrelly")
    assert not dictate(p)["styled"].endswith("James Farrelly")


def test_a_field_that_hangs_costs_a_moment_not_the_dictation(tmp_path, screen, monkeypatch):
    hang = Hang(10)

    def slow(chars=300, timeout=1.5):
        hang.wait()
        return "", ""
    monkeypatch.setattr(text_context, "get_text_around_cursor", slow)
    import style_layer as sl
    monkeypatch.setattr(sl, "FIELD_READ_DEADLINE_S", 0.4)
    p = pipe(tmp_path, MAIL, LONG, styler=Plain(BODY), email_signature=SIG)
    t0 = time.monotonic()
    entry = dictate(p)
    hang.release.set()
    assert time.monotonic() - t0 < 4.0
    assert entry["styled"].endswith("James Farrelly"), "unreadable: the first one is added"
    assert p.page.statuses[-1] == "done"


def test_when_the_user_has_moved_to_another_app_there_is_no_signature(tmp_path, screen):
    screen["front"] = AppContext("com.apple.Notes", "Notes", "", "other")
    p = pipe(tmp_path, MAIL, LONG, styler=Plain(BODY), email_signature=SIG)
    assert dictate(p)["styled"] == BODY
    assert screen["field"].calls == [], "the field of another app is not read"


def test_the_same_app_under_another_window_is_the_same_app(tmp_path, screen):
    screen["front"] = AppContext("COM.APPLE.MAIL", "Mail", "Re: lunch", "email")
    p = pipe(tmp_path, MAIL, LONG, styler=Plain(BODY), email_signature=SIG)
    assert dictate(p)["styled"].endswith("James Farrelly")


def test_a_superseded_dictation_does_not_read_the_field(tmp_path, screen):
    hang = Hang(10)
    p = pipe(tmp_path, MAIL, LONG, styler=Plain(BODY, hang=hang), email_signature=SIG)
    worker = run_process(p)
    assert hang.entered.wait(5)               # in the clean-up
    p._processing_id = 2                      # a newer recording has started
    hang.release.set()
    worker.join(5)
    assert screen["field"].calls == []
    assert history(p)[-1]["styled"] == BODY, "kept for the Journal, with no signature, and not pasted"
    assert p.clipboard.pastes == []


def test_the_signature_goes_on_after_the_style_and_is_never_styled(tmp_path, screen):
    sig = {"enabled": True, "text": "Best Regards,\nJames Farrelly\nwww.Example.com"}
    p = pipe(tmp_path, MAIL, LONG, styler=Plain(BODY), email_signature=sig,
             style_by_category={"email": "casual"})
    entry = dictate(p)
    assert entry["styled"] == BODY[:-1] + "\n\nBest Regards,\nJames Farrelly\nwww.Example.com"
    assert entry["style"] == "casual"
    assert "quality" not in entry


def test_the_signature_reaches_the_clipboard_and_paste_last(tmp_path, screen):
    p = pipe(tmp_path, MAIL, LONG, styler=Plain(BODY), email_signature=SIG)
    entry = dictate(p)
    assert p.clipboard.copies[-1] == entry["styled"]


def test_a_signature_is_read_again_for_every_dictation(tmp_path, screen):
    p = pipe(tmp_path, MAIL, LONG, styler=Plain(BODY))
    assert dictate(p)["styled"] == BODY
    write_settings(tmp_path, email_signature=SIG)
    assert dictate(p)["styled"].endswith("James Farrelly")
    write_settings(tmp_path, email_signature={"enabled": False, "text": SIG["text"]})
    p._signature_memory.forget()
    assert dictate(p)["styled"] == BODY


def test_the_privacy_story_in_the_code(tmp_path):
    """The text before the cursor is read in one place, only when the signature
    is on, and never logged."""
    import ast
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    users = [n.name for n in klass.body if isinstance(n, ast.FunctionDef)
             and "get_text_around_cursor" in ast.unparse(n)]
    assert users == ["_signature_reader"], users
    for fn in klass.body:
        if isinstance(fn, ast.FunctionDef) and fn.name == "_signature_reader":
            for call in ast.walk(fn):
                if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "_log_to_file":
                    assert "before" not in ast.unparse(call), "the text before the cursor is never logged"


# ── Redo and Try again ───────────────────────────────────────────────────────

def kept_take(tmp_path, ctx, said="ok so ship it on monday please", styler=None, **settings):
    p = pipe(tmp_path, ctx, said, styler=styler, **settings)
    entry = dictate(p, said)
    return p, entry


def redo(p, entry):
    return api_of(p, "redo_history_item").redo_history_item(entry["id"])


@pytest.fixture
def shelf(monkeypatch):
    s = history_ops.UndoShelf(window_s=15.0, clock=lambda: 100.0, timer=False)
    monkeypatch.setattr(history_ops, "SHELF", s)


def test_redo_applies_the_style_of_the_kind_the_take_was_dictated_in(tmp_path, shelf):
    p, entry = kept_take(tmp_path, MESSAGES, styler=Plain("Ok so ship it on Monday."))
    write_settings(tmp_path, style_by_category={"personal": "very_casual", "work": "excited"})
    p.styler = Plain("Ok so ship it on Tuesday.")
    new = redo(p, entry)["item"]
    assert new["styled"] == "ok so ship it on Tuesday" and new["style"] == "very_casual"
    assert new["category"] == "personal"


def test_redo_with_a_kind_on_auto_has_no_style_field(tmp_path, shelf):
    p, entry = kept_take(tmp_path, MESSAGES, style_by_category={"personal": "casual"})
    assert entry["style"] == "casual"
    write_settings(tmp_path, style_by_category={"personal": "auto"})
    new = redo(p, entry)["item"]
    assert "style" not in new and new["styled"].endswith(".")


def test_redo_honours_clean_up_off(tmp_path, shelf):
    p, entry = kept_take(tmp_path, NOTES)
    write_settings(tmp_path, cleanup="off")
    p.styler = FakeStyler(error=RuntimeError("must not be asked"))
    p.transcriber.text = "um ok so I I ship it on tuesday"
    new = redo(p, entry)["item"]
    assert new["styled"] == "um ok so I I ship it on tuesday"
    assert new["provider"] == {"stt": "groq", "cleanup": "off"}
    assert "quality" not in new


def test_redo_never_adds_a_signature(tmp_path, shelf, screen):
    p, entry = kept_take(tmp_path, MAIL, said=LONG, styler=Plain(BODY), email_signature=SIG)
    assert entry["styled"].endswith("James Farrelly")
    calls = len(screen["field"].calls)
    new = redo(p, entry)["item"]
    assert new["styled"] == BODY
    assert len(screen["field"].calls) == calls, "the field is not read for a redo"


def test_redo_of_a_snippet_is_not_styled(tmp_path, shelf):
    (tmp_path / "snippets.json").write_text(
        json.dumps([{"trigger": "my reply", "expansion": "Thanks. Will do."}]), encoding="utf-8")
    p, entry = kept_take(tmp_path, MESSAGES)
    write_settings(tmp_path, style_by_category={"personal": "very_casual"})
    p.transcriber.text = "my reply"
    new = redo(p, entry)["item"]
    assert new["styled"] == "Thanks. Will do." and "style" not in new


def test_try_again_on_a_not_sent_card_uses_the_cards_kind_and_clean_up_off(tmp_path):
    p = pipe(tmp_path, MESSAGES, "so ship it on monday", style_by_category={"personal": "very_casual"})
    p.transcriber.error = RuntimeError("Connection error")
    dictate(p)
    [card] = [e for e in history(p) if e.get("failed")]
    assert card["category"] == "personal"
    p.transcriber.error = None
    p.styler = Plain("So ship it on Monday.")
    r = p.resend_unsent(card["unsent_id"])
    assert r["ok"] and r["item"]["styled"] == "so ship it on Monday" and r["item"]["style"] == "very_casual"


def test_try_again_with_clean_up_off(tmp_path):
    p = pipe(tmp_path, NOTES, "so ship it on monday", cleanup="off")
    p.transcriber.error = RuntimeError("Connection error")
    dictate(p)
    [card] = [e for e in history(p) if e.get("failed")]
    p.transcriber.error = None
    p.styler = FakeStyler(error=RuntimeError("must not be asked"))
    r = p.resend_unsent(card["unsent_id"])
    assert r["ok"] and r["item"]["styled"] == "so ship it on monday"
    assert r["item"]["provider"]["cleanup"] == "off"


def test_a_card_without_an_app_is_styled_as_other(tmp_path):
    p = pipe(tmp_path, text_context.UNKNOWN, "so ship it on monday",
             style_by_category={"other": "excited", "personal": "very_casual"})
    p.transcriber.error = RuntimeError("Connection error")
    dictate(p)
    [card] = [e for e in history(p) if e.get("failed")]
    p.transcriber.error = None
    p.styler = Plain("So ship it on Monday.")
    assert p.resend_unsent(card["unsent_id"])["item"]["styled"] == "So ship it on Monday!"


# ── the pieces the harness lifts ─────────────────────────────────────────────

def test_the_new_helpers_are_in_the_harness_lists():
    import _pipeline_harness as h
    for name in ("_style_words", "_add_signature", "_signature_reader"):
        assert name in h.PIPELINE_METHODS, name
