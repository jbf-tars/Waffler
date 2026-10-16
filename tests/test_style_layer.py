"""The Style section as the pipeline reads it (S6): clean-up On or Off, a style
for each kind of app, and the opt-in email signature.

src/style_layer.py holds the choices and their checks, the style a dictation
gets, and the decision table for the signature. It reads no file and no field:
the pipeline hands it settings and, for the signature, a function that reads
the text before the cursor, so every case here runs with fakes.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import app_categories  # noqa: E402
import style_layer as sl  # noqa: E402
import style_post  # noqa: E402
import user_messages as um  # noqa: E402

KINDS = app_categories.CATEGORIES


# ── clean-up on or off ───────────────────────────────────────────────────────

def test_clean_up_is_on_unless_settings_say_off():
    assert sl.cleanup_mode({}) == "on" and not sl.cleanup_is_off({})
    assert sl.cleanup_mode({"cleanup": "on"}) == "on"
    assert sl.cleanup_mode({"cleanup": "off"}) == "off" and sl.cleanup_is_off({"cleanup": "off"})


@pytest.mark.parametrize("junk", [None, "", "OFF", "Off", " off", False, 0, True, 1, [], {}, "no", "disabled"])
def test_anything_else_is_on(junk):
    assert sl.cleanup_mode({"cleanup": junk}) == "on"
    assert not sl.cleanup_is_off({"cleanup": junk})


@pytest.mark.parametrize("settings", [None, "x", 5, [], ()])
def test_unreadable_settings_mean_the_defaults(settings):
    assert sl.cleanup_mode(settings) == "on"
    assert sl.styles_by_category(settings) == {k: "auto" for k in KINDS}
    assert sl.signature_settings(settings) == {"enabled": False, "text": ""}
    assert sl.style_for(settings, "email") == "auto"


def test_off_has_its_own_provider_tag_and_costs_nothing():
    usage = sl.off_usage()
    assert usage["provider"] == "off" == sl.PROVIDER_OFF
    assert usage["api_used"] is False
    assert usage["input_tokens"] == 0 and usage["output_tokens"] == 0
    assert "fallback_reason" not in usage
    assert sl.off_usage() is not sl.off_usage(), "each caller gets its own dict"


# ── a style for each kind of app ─────────────────────────────────────────────

def test_the_choices_for_each_kind():
    assert sl.STYLE_CHOICES["personal"] == ("auto", "formal", "casual", "very_casual")
    for kind in ("work", "email", "other"):
        assert sl.STYLE_CHOICES[kind] == ("auto", "formal", "casual", "excited")
    assert tuple(sl.STYLE_CHOICES) == ("personal", "work", "email", "other") == KINDS


def test_every_style_the_choices_name_is_one_the_rules_have():
    for kind, choices in sl.STYLE_CHOICES.items():
        for c in choices:
            assert c == "auto" or c in style_post.STYLES, (kind, c)


def test_each_kind_starts_on_auto_which_is_todays_behaviour():
    assert sl.styles_by_category({}) == {"personal": "auto", "work": "auto", "email": "auto", "other": "auto"}
    for kind in KINDS:
        assert sl.style_for({}, kind) == "auto"


def test_the_saved_style_of_each_kind_is_read():
    s = {"style_by_category": {"personal": "very_casual", "work": "excited", "email": "casual", "other": "formal"}}
    assert sl.styles_by_category(s) == s["style_by_category"]
    assert [sl.style_for(s, k) for k in KINDS] == ["very_casual", "excited", "casual", "formal"]


def test_a_style_the_kind_does_not_offer_is_auto():
    s = {"style_by_category": {"personal": "excited", "work": "very_casual", "email": "very_casual",
                               "other": "very_casual"}}
    assert sl.styles_by_category(s) == {k: "auto" for k in KINDS}


@pytest.mark.parametrize("junk", [None, "casual", 5, [], {"personal": 5, "work": None, "email": ["casual"],
                                                           "other": "CASUAL"},
                                  {"moon": "casual"}])
def test_junk_in_the_file_is_auto(junk):
    got = sl.styles_by_category({"style_by_category": junk})
    assert got == {k: "auto" for k in KINDS}


def test_an_unknown_kind_gets_the_style_of_other():
    s = {"style_by_category": {"other": "casual", "work": "excited"}}
    for kind in ("moon", "", None, 7, "OTHER", "Email"):
        assert sl.style_for(s, kind) == "casual"


# ── what save_settings checks ────────────────────────────────────────────────

def test_good_payloads_have_no_problem():
    for payload in ({}, {"cleanup": "on"}, {"cleanup": "off"}, {"style_by_category": {"work": "excited"}},
                    {"style_by_category": {k: "auto" for k in KINDS}},
                    {"style_by_category": {"personal": "very_casual", "work": "casual", "email": "formal",
                                           "other": "excited"}},
                    {"email_signature": {"enabled": True, "text": "Best,\nJames"}},
                    {"email_signature": {"enabled": False}}, {"email_signature": {"text": ""}},
                    {"email_signature": {"enabled": True, "text": "x" * 500}},
                    {"email_signature": {"enabled": True, "text": "  " + "x" * 500 + "  "}},
                    {"auto_paste": False, "dialect": "en-GB"}):
        assert sl.settings_problem(payload) is None, payload


@pytest.mark.parametrize("bad", ["", "ON", "Off", "none", None, True, False, 0, 1, ["off"], {"a": 1}])
def test_a_clean_up_choice_that_is_not_on_or_off_is_refused(bad):
    assert sl.settings_problem({"cleanup": bad}) == um.CLEANUP_CHOICE_UNKNOWN


@pytest.mark.parametrize("payload", [
    {"style_by_category": "casual"}, {"style_by_category": None}, {"style_by_category": []},
    {"style_by_category": {"moon": "casual"}}, {"style_by_category": {"personal": "excited"}},
    {"style_by_category": {"work": "very_casual"}}, {"style_by_category": {"email": "CASUAL"}},
    {"style_by_category": {"other": None}}, {"style_by_category": {"other": 3}},
    {"style_by_category": {"other": ["casual"]}}, {"style_by_category": {"personal": ""}},
    {"style_by_category": {"work": "excited", "email": "shouty"}},
])
def test_a_style_that_is_not_offered_is_refused(payload):
    assert sl.settings_problem(payload) == um.STYLE_CHOICE_UNKNOWN


@pytest.mark.parametrize("payload", [
    {"email_signature": "Best"}, {"email_signature": None}, {"email_signature": ["x"]},
    {"email_signature": {"enabled": "yes"}}, {"email_signature": {"enabled": 1}},
    {"email_signature": {"enabled": None}}, {"email_signature": {"text": 5}},
    {"email_signature": {"text": None}}, {"email_signature": {"text": ["a"]}},
    {"email_signature": {"enabled": True, "colour": "red"}},
])
def test_a_signature_that_is_not_enabled_and_text_is_refused(payload):
    assert sl.settings_problem(payload) == um.SIGNATURE_BAD


def test_a_signature_over_the_limit_is_refused_not_cut():
    assert sl.SIGNATURE_MAX_CHARS == 500
    assert sl.settings_problem({"email_signature": {"text": "x" * 501}}) == um.SIGNATURE_TOO_LONG
    assert sl.settings_problem({"email_signature": {"text": "x" * 500}}) is None
    assert str(sl.SIGNATURE_MAX_CHARS) in um.SIGNATURE_TOO_LONG
    # Counted after it is tidied: spaces round it do not count.
    assert sl.settings_problem({"email_signature": {"text": " \n" + "x" * 500 + "\n "}}) is None


def test_the_refusals_are_plain_sentences():
    for text in (um.CLEANUP_CHOICE_UNKNOWN, um.STYLE_CHOICE_UNKNOWN, um.SIGNATURE_BAD, um.SIGNATURE_TOO_LONG):
        assert text.endswith(".") and "—" not in text and "–" not in text
        for word in ("Exception", "bool", "dict", "None", "str", "JSON", "key"):
            assert word not in text, (word, text)


def test_a_signature_is_stored_tidy():
    assert sl.merged_signature({}, {"email_signature": {"enabled": True, "text": "  Best,\r\nJames \n\n "}}) == \
        {"enabled": True, "text": "Best,\nJames"}
    assert sl.merged_signature({}, {"email_signature": {"text": "a\x00b\x07c\td"}}) == {"enabled": False, "text": "abc\td"}
    assert sl.merged_signature({}, {"email_signature": {"text": "line\rtwo"}})["text"] == "line\ntwo"


def test_a_partial_signature_change_keeps_the_rest():
    stored = {"email_signature": {"enabled": True, "text": "Best, James"}}
    assert sl.merged_signature(stored, {"email_signature": {"enabled": False}}) == {"enabled": False, "text": "Best, James"}
    assert sl.merged_signature(stored, {"email_signature": {"text": "Cheers"}}) == {"enabled": True, "text": "Cheers"}
    assert sl.merged_signature(stored, {}) == {"enabled": True, "text": "Best, James"}


def test_a_partial_style_change_keeps_the_other_kinds():
    stored = {"style_by_category": {"personal": "casual", "work": "excited"}}
    assert sl.merged_styles(stored, {"style_by_category": {"email": "casual"}}) == \
        {"personal": "casual", "work": "excited", "email": "casual", "other": "auto"}
    assert sl.merged_styles(stored, {}) == {"personal": "casual", "work": "excited", "email": "auto", "other": "auto"}


def test_a_signature_in_a_hand_edited_file_is_read_safely():
    for junk in (None, "x", 5, [], {"enabled": "yes", "text": 7}, {"enabled": True}, {"text": ["a"]}):
        got = sl.signature_settings({"email_signature": junk})
        assert set(got) == {"enabled", "text"} and isinstance(got["enabled"], bool) and isinstance(got["text"], str)
    assert sl.signature_settings({"email_signature": {"enabled": True, "text": "Hi"}}) == {"enabled": True, "text": "Hi"}
    assert sl.signature_settings({"email_signature": {"enabled": "yes", "text": "Hi"}}) == {"enabled": False, "text": "Hi"}
    assert len(sl.signature_settings({"email_signature": {"enabled": True, "text": "x" * 900}})["text"]) == 500


# ── the style a dictation gets ───────────────────────────────────────────────

def test_auto_is_todays_text_and_records_no_style():
    for kind in KINDS:
        assert sl.finish_style("Sounds good. See you.", settings={}, category=kind, language="en",
                               protected_words=()) == ("Sounds good. See you.", "")


def test_the_style_comes_from_the_kind_of_app():
    s = {"style_by_category": {"personal": "very_casual", "work": "excited", "email": "casual", "other": "formal"}}

    def run(kind):
        return sl.finish_style("Sounds good.", settings=s, category=kind, language="en", protected_words=())
    assert run("personal") == ("sounds good", "very_casual")
    assert run("work") == ("Sounds good!", "excited")
    assert run("email") == ("Sounds good", "casual")
    assert run("other") == ("Sounds good.", "formal")
    assert run("moon") == ("Sounds good.", "formal"), "an unknown kind is other"
    assert run(None) == ("Sounds good.", "formal")


def test_a_language_that_is_not_styled_records_no_style():
    s = {"style_by_category": {"personal": "very_casual"}}
    assert sl.finish_style("Привет. Как дела.", settings=s, category="personal", language="ru",
                           protected_words=()) == ("Привет. Как дела.", "")
    assert sl.finish_style("Okay.", settings=s, category="personal", language="xx", protected_words=()) == ("Okay.", "")
    assert sl.finish_style("Okay.", settings=s, category="personal", language="en", protected_words=()) == ("okay", "very_casual")


def test_the_vocabulary_is_handed_to_the_rules():
    s = {"style_by_category": {"personal": "very_casual"}}
    assert sl.finish_style("Waffler works.", settings=s, category="personal", language="en",
                           protected_words=("Waffler",)) == ("Waffler works", "very_casual")


# ── is it the start of a message ─────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Hi Sam, thanks for the update", "hello there", "Hey,", "Dear Sir or Madam,", "Good morning team", "good afternoon",
    "Hiya Sam", "Howdy", "  Hi Sam", "Hi", "Greetings and salutations",
    "one two three four five six seven eight nine ten", "Thanks for sending that over, it really does help us a lot.",
])
def test_these_look_like_the_start_of_a_message(text):
    assert sl.looks_like_message_start(text) is True


@pytest.mark.parametrize("text", [
    "", "   ", "Sounds good.", "Thanks.", "nine words here are not ten words okay right", "High five", "Hint: try it",
    "Heyday of the thing", "Hello" + "o" * 3 + " world", "Yes please do that",
])
def test_these_do_not(text):
    assert sl.looks_like_message_start(text) is False


def test_nine_words_are_not_enough_and_ten_are():
    assert sl.looks_like_message_start(" ".join(["word"] * 9)) is False
    assert sl.looks_like_message_start(" ".join(["word"] * 10)) is True
    assert sl.SIGNATURE_MIN_WORDS == 10


# ── the signature decision table ─────────────────────────────────────────────

SIG = {"email_signature": {"enabled": True, "text": "Best,\nJames Farrelly"}}
LONG = "Thanks for sending that over, it really does help the whole team a lot today."


class Field:
    """Stands in for the read of the text before the cursor."""

    def __init__(self, before="", raises=False):
        self.before, self.raises, self.calls = before, raises, []

    def __call__(self, chars):
        self.calls.append(chars)
        if self.raises:
            raise RuntimeError("the app did not answer")
        return self.before


def decide(settings=SIG, category="email", text=LONG, field=None, **kw):
    kw.setdefault("app_key", "com.apple.mail")
    return sl.decide_signature(settings=settings, category=category, text=text, read_before=field, **kw)


def test_signature_off_never_reads_the_field():
    f = Field()
    for settings in ({}, {"email_signature": {"enabled": False, "text": "Best"}},
                     {"email_signature": {"enabled": True, "text": ""}},
                     {"email_signature": {"enabled": True, "text": "   "}}):
        d = decide(settings, field=f)
        assert (d.add, d.reason) == (False, "disabled")
    assert f.calls == []


@pytest.mark.parametrize("category", ["personal", "work", "other", "moon", None])
def test_only_email_gets_a_signature_and_the_field_is_not_read_for_the_others(category):
    f = Field()
    d = decide(category=category, field=f)
    assert (d.add, d.reason) == (False, "not_email")
    assert f.calls == []


def test_a_snippet_and_a_selection_replace_never_get_one():
    f = Field()
    assert decide(field=f, snippet=True).reason == "snippet"
    assert decide(field=f, replaces_selection=True).reason == "selection"
    assert not decide(field=f, snippet=True).add and not decide(field=f, replaces_selection=True).add
    assert f.calls == []


def test_a_short_remark_is_not_the_start_of_a_message():
    f = Field()
    d = decide(text="Sounds good, thanks.", field=f)
    assert (d.add, d.reason) == (False, "not_start")
    assert f.calls == []


def test_a_greeting_or_ten_words_is_the_start_of_a_message():
    assert decide(text="Hi Sam, quick one.", field=Field("")).add is True
    assert decide(text=LONG, field=Field("")).add is True


def test_an_empty_field_gets_the_signature():
    f = Field("")
    d = decide(field=f)
    assert (d.add, d.reason) == (True, "append")
    assert len(f.calls) == 1


def test_a_field_with_other_text_and_no_signature_gets_it():
    d = decide(field=Field("Dear Sam,\n\nI hope you are well.\n\n"))
    assert (d.add, d.reason) == (True, "append")


def test_a_signature_already_before_the_cursor_is_not_added_again():
    before = "Hi Sam,\n\nSee you Friday.\n\nBest,\nJames Farrelly\n\n"
    d = decide(field=Field(before))
    assert (d.add, d.reason) == (False, "already_there")


def test_the_check_ignores_spacing_and_case():
    d = decide(field=Field("see you.\n\n  best,   JAMES   farrelly  "))
    assert (d.add, d.reason) == (False, "already_there")
    d = decide(field=Field("see you. Best, James Farrelly"))
    assert d.reason == "already_there"


def test_a_signature_only_partly_there_is_not_there():
    assert decide(field=Field("Best,\nJames")).add is True


def test_asks_for_enough_text_to_hold_the_signature():
    f = Field("")
    decide(field=f)
    assert f.calls[0] >= len(SIG["email_signature"]["text"]) + 100
    f2 = Field("")
    decide({"email_signature": {"enabled": True, "text": "x" * 500}}, field=f2)
    assert f2.calls[0] >= 1000
    assert sl.read_chars("x" * 500) == f2.calls[0] <= 4000


def test_when_the_field_cannot_be_read_the_memory_decides():
    mem = sl.SignatureMemory(clock=lambda: 1000.0)
    # Nothing remembered: add it.
    for field in (Field(""), Field(raises=True), None, Field(None)):
        d = decide(field=field, memory=mem)
        assert (d.add, d.reason) == (True, "append"), field
    mem.note("com.apple.mail", now=1000.0)
    for field in (Field(""), Field(raises=True), None, Field(None)):
        d = decide(field=field, memory=mem, now=1100.0)
        assert (d.add, d.reason) == (False, "recent"), field


def test_when_another_app_is_in_front_there_is_no_signature():
    mem = sl.SignatureMemory()
    d = decide(field=lambda chars: sl.FIELD_MOVED, memory=mem)
    assert (d.add, d.reason) == (False, "moved")


def test_the_memory_is_for_ten_minutes_in_one_app():
    mem = sl.SignatureMemory()
    mem.note("com.apple.mail", now=0.0)
    assert mem.recent("com.apple.mail", now=599.0) is True
    assert mem.recent("com.apple.mail", now=600.0) is False
    assert mem.recent("com.apple.mail", now=5000.0) is False
    assert mem.recent("com.microsoft.outlook", now=10.0) is False
    assert sl.SIGNATURE_RECENT_S == 600
    d = decide(field=Field(""), memory=mem, now=599.0)
    assert d.reason == "recent"
    d = decide(field=Field(""), memory=mem, now=601.0)
    assert d.reason == "append"
    d = decide(field=Field(""), memory=mem, now=100.0, app_key="com.microsoft.outlook")
    assert d.reason == "append"


def test_a_field_that_can_be_read_beats_the_memory():
    mem = sl.SignatureMemory()
    mem.note("com.apple.mail", now=0.0)
    # Other text and no signature: the memory is out of date (a new email), add it.
    assert decide(field=Field("Hello there, this is a new email."), memory=mem, now=30.0).add is True
    # The signature is there: say so.
    assert decide(field=Field("Best,\nJames Farrelly"), memory=mem, now=30.0).reason == "already_there"


def test_no_app_means_the_memory_cannot_be_used():
    mem = sl.SignatureMemory()
    mem.note("", now=0.0)
    assert decide(field=Field(""), memory=mem, now=5.0, app_key="").add is True


def test_the_memory_forgets_and_stays_small():
    mem = sl.SignatureMemory()
    for i in range(500):
        mem.note(f"app{i}", now=float(i))
    assert len(mem) <= 64
    mem.note("com.apple.mail", now=1000.0)
    assert mem.recent("com.apple.mail", now=1001.0)
    mem.forget()
    assert not mem.recent("com.apple.mail", now=1002.0) and len(mem) == 0


def test_a_signature_is_added_under_the_text_after_a_blank_line():
    assert sl.with_signature("Hi Sam, see you Friday.", "Best,\nJames") == "Hi Sam, see you Friday.\n\nBest,\nJames"
    assert sl.with_signature("Hi Sam.\n\n", "Best") == "Hi Sam.\n\nBest"
    assert sl.with_signature("  text  ", "Best") == "  text\n\nBest"


def test_the_decision_never_raises_whatever_it_is_given():
    for bad in (None, 5, [], {}, "x"):
        d = sl.decide_signature(settings=bad, category=bad, text=bad, read_before=bad)
        assert d.add is False
    d = decide(text=None, field=Field(""))
    assert d.add is False


def test_the_table_in_one_place():
    """The decision table: each row is (settings, category, text, snippet,
    selection, field, remembered, expected reason)."""
    mem_empty, mem_full = sl.SignatureMemory(), sl.SignatureMemory()
    mem_full.note("com.apple.mail", now=0.0)
    rows = [
        ({}, "email", LONG, False, False, Field(""), mem_empty, "disabled"),
        (SIG, "work", LONG, False, False, Field(""), mem_empty, "not_email"),
        (SIG, "email", LONG, True, False, Field(""), mem_empty, "snippet"),
        (SIG, "email", LONG, False, True, Field(""), mem_empty, "selection"),
        (SIG, "email", "Thanks.", False, False, Field(""), mem_empty, "not_start"),
        (SIG, "email", LONG, False, False, Field("x Best,\nJames Farrelly"), mem_empty, "already_there"),
        (SIG, "email", LONG, False, False, Field("some other text"), mem_full, "append"),
        (SIG, "email", LONG, False, False, Field(""), mem_full, "recent"),
        (SIG, "email", LONG, False, False, Field(raises=True), mem_full, "recent"),
        (SIG, "email", LONG, False, False, Field(""), mem_empty, "append"),
        (SIG, "email", "Hi Sam,", False, False, Field(""), mem_empty, "append"),
    ]
    for settings, category, text, snippet, selection, field, mem, want in rows:
        d = sl.decide_signature(settings=settings, category=category, text=text, snippet=snippet,
                                replaces_selection=selection, read_before=field, app_key="com.apple.mail",
                                memory=mem, now=30.0)
        assert d.reason == want, (want, d)
        assert d.add is (want == "append")
