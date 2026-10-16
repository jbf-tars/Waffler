"""Styles as code (S6): case, the last full stop, and the greeting break.

src/style_post.py is pure: text in, text out. It runs after the clean-up (and
after the short-dictation bypass, which never reaches a model), so these rules
have to be code, and they have to be safe: they change only the case of the
first letter of a sentence, the terminal punctuation of the last sentence or
of short statements, and the line break after a greeting. Never a word.

Offline: no model, no keys, no files.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import app_categories  # noqa: E402
import languages  # noqa: E402
import style_post as sp  # noqa: E402
from style_post import apply_style  # noqa: E402


def casual(text, **kw):
    return apply_style(text, "casual", "other", **kw)


def very(text, category="personal", **kw):
    return apply_style(text, "very_casual", category, **kw)


def excited(text, **kw):
    return apply_style(text, "excited", "work", **kw)


def words(text):
    """The words of a text, with the case and the punctuation taken off: what a
    style must never change."""
    return [re.sub(r"[^\w']", "", w).lower().replace("\u0131", "i") for w in text.split() if re.sub(r"[^\w']", "", w)]


# ── the names ────────────────────────────────────────────────────────────────

def test_the_four_styles_are_the_ones_the_settings_offer():
    assert sp.STYLES == ("formal", "casual", "very_casual", "excited")
    assert sp.AUTO == "auto"


# ── formal and "auto" leave the text exactly as it was ───────────────────────

@pytest.mark.parametrize("text", [
    "Sounds good.", "Hi Sam,\n\nThanks for the update.\n\nBest,\nJames", "i think so",
    "- one\n- two", "", "   ", "A. B. C.", "Check https://example.com.",
])
@pytest.mark.parametrize("style", ["formal", "auto", "", None, "shouty", "CASUAL", 3])
def test_formal_and_anything_unknown_is_a_no_op(text, style):
    assert apply_style(text, style, "email", language="en") == text


@pytest.mark.parametrize("bad", [None, 5, b"x", ["a"]])
def test_not_text_comes_back_as_it_came(bad):
    assert apply_style(bad, "casual", "other") is bad


def test_empty_and_blank_text_is_unchanged_in_every_style():
    for style in sp.STYLES:
        assert apply_style("", style, "other") == ""
        assert apply_style("  \n ", style, "other") == "  \n "


# ── languages: Latin script only, nothing for the unknown ────────────────────

@pytest.mark.parametrize("code", ["en", "es", "fr", "de", "it", "pt", "nl", "pl", "tr", "id", "vi", "sv", "cy"])
def test_latin_script_languages_are_styled(code):
    assert very("Okay then. Fine.", language=code) == "okay then. fine"


@pytest.mark.parametrize("code", ["zh", "ja", "ko", "th", "ru", "uk", "ar", "he", "hi", "bn", "ur", "fa",
                                  "el", "bg", "sr", "ta", "te"])
def test_other_scripts_are_left_alone(code):
    for style in sp.STYLES:
        assert apply_style("Okay then. Fine.", style, "other", language=code) == "Okay then. Fine."


@pytest.mark.parametrize("code", ["xx", "english", "EN-GB", "e", "zz-ZZ", 7, ["en"]])
def test_an_unknown_language_does_nothing(code):
    assert very("Okay then. Fine.", language=code) == "Okay then. Fine."
    assert casual("Okay then.", language=code) == "Okay then."


@pytest.mark.parametrize("code", [None, ""])
def test_no_language_is_taken_as_english(code):
    assert very("Okay then. I agree.", language=code) == "okay then. I agree"


def test_every_language_in_the_picker_is_decided_one_way_or_the_other():
    for code, _name in languages.LANGUAGES:
        if code == "auto":
            continue
        assert (code in sp.LATIN_LANGUAGES) != (code in sp.OTHER_SCRIPT_LANGUAGES), code


def test_auto_detect_looks_at_the_text():
    assert very("Okay then. Fine.", language="auto") == "okay then. fine"
    assert very("Привет. Как дела.", language="auto") == "Привет. Как дела."
    assert very("你好。再见。", language="auto") == "你好。再见。"
    assert very("こんにちは。さようなら。", language="auto") == "こんにちは。さようなら。"
    assert very("สวัสดี. ลาก่อน.", language="auto") == "สวัสดี. ลาก่อน."
    assert very("مرحبا. وداعا.", language="auto") == "مرحبا. وداعا."


def test_text_in_another_script_is_left_alone_whatever_the_setting_says():
    assert very("Привет. Как дела.", language="en") == "Привет. Как дела."
    assert very("你好。再见。", language="en") == "你好。再见。"
    assert casual("Привет.", language="de") == "Привет."


def test_mostly_latin_text_with_a_few_foreign_letters_is_styled():
    assert very("Meet me at the 東京 station. Thanks.", language="auto") == "meet me at the 東京 station. thanks"


def test_eligible_is_the_same_decision():
    assert sp.eligible("Okay.", "en") is True
    assert sp.eligible("Okay.", "auto") is True
    assert sp.eligible("Okay.", "zh") is False
    assert sp.eligible("Okay.", "xx") is False
    assert sp.eligible("Привет.", "auto") is False
    assert sp.eligible("12345", "en") is False       # no letters, nothing to style
    assert sp.eligible("", "en") is False


# ── casual: drop the last full stop of a one-sentence message ────────────────

@pytest.mark.parametrize("text,want", [
    ("Sounds good.", "Sounds good"),
    ("Sounds Good.", "Sounds Good"),                       # the case is left alone
    ("Dr. Smith will call.", "Dr. Smith will call"),       # an abbreviation is not the end of a sentence
    ("I paid $3.50.", "I paid $3.50"),
    ("It is version 2.", "It is version 2"),
    ("Visit https://example.com/a.", "Visit https://example.com/a"),
    ("Write to sam@example.com.", "Write to sam@example.com"),
    ("Mr. and Mrs. Jones are here.", "Mr. and Mrs. Jones are here"),
    ("Ask J. K. Rowling.", "Ask J. K. Rowling"),
    ("Ask Max.", "Ask Max"),                               # a name that is also an abbreviation elsewhere
    ("Thanks, Al.", "Thanks, Al"),
    ("C'est très bien.", "C'est très bien"),
    ("Das ist gut.", "Das ist gut"),
    ("Sounds good.  ", "Sounds good  "),                   # what follows the text stays
    ("Sounds good.\n", "Sounds good\n"),
])
def test_casual_drops_the_final_full_stop_of_one_sentence(text, want):
    assert casual(text) == want


@pytest.mark.parametrize("text", [
    "Sounds good!", "Sounds good?", "Sounds good",                  # nothing to drop
    "Sounds good. See you then.",                                   # two sentences: keep everything
    "Is that OK? Yes.",
    "Thanks, see you at 5 p.m.",                                    # the dot belongs to p.m.
    "Bring bread, milk, etc.",
    "We met at Acme Inc.",
    "Wait..", "I will check...", "I will check\u2026",              # ellipses are never touched
    'He said "yes."', "She said \u201cno.\u201d", "It is 'fine.'",  # the stop is inside a quote
    "Run `npm install`.", "x = foo(3);", "Call foo_bar(1).",       # code
    "- one\n- two.", "1. Buy milk.", "Shopping:\n- milk\n- eggs.",   # lists
    "(see below.)", "He left (quietly.)",
])
def test_casual_leaves_everything_else_alone(text):
    assert casual(text) == text


def test_casual_keeps_the_greeting_break_and_drops_the_last_stop():
    assert casual("Hi Sam,\n\nThanks for the update.") == "Hi Sam,\n\nThanks for the update"


def test_casual_does_not_count_a_greeting_or_a_sign_off_as_sentences():
    full = "Hi Sam,\n\nThanks for the update.\n\nBest,\nJames"
    assert casual(full) == full                                     # the last line has no stop to drop
    assert casual("Hi Sam,\n\nThanks for the update.\n\nBest regards,\nJames Smith") == \
        "Hi Sam,\n\nThanks for the update.\n\nBest regards,\nJames Smith"


def test_casual_with_two_paragraphs_keeps_the_stops():
    assert casual("First point.\n\nSecond point.") == "First point.\n\nSecond point."


# ── very casual: lower case, and no last full stop ───────────────────────────

@pytest.mark.parametrize("text,want", [
    ("Sounds good.", "sounds good"),
    ("Sounds good. See you then.", "sounds good. see you then"),
    ("Really? Yes! Great.", "really? yes! great"),
    ("A cat sat. A dog ran.", "a cat sat. a dog ran"),
    ("5 minutes. 10 more.", "5 minutes. 10 more"),
    ("Éric called. Élan won.", "éric called. élan won"),
    ("First point.\n\nSecond point.", "first point.\n\nsecond point"),
    ("Dr. Smith is in. Mr. Jones is out.", "dr. Smith is in. mr. Jones is out"),
    ("It works, e.g. on a Mac. Fine.", "it works, e.g. on a Mac. fine"),
    ("The meeting is on Mon. 5th at ten. Fine.", "the meeting is on Mon. 5th at ten. fine"),
    ("Hmm... Okay. Fine.", "hmm... Okay. fine"),                    # after an ellipsis is not a new sentence
    ("I met Al. He left. Max stayed.", "I met Al. he left. max stayed"),
])
def test_very_casual_lowers_each_sentence_and_drops_the_last_stop(text, want):
    assert very(text) == want


@pytest.mark.parametrize("text,want", [
    ("I think so. I'm sure. I'll go. I've been. I'd say so.", "I think so. I'm sure. I'll go. I've been. I'd say so"),
    ("I\u2019m here. I\u2019ll wait.", "I\u2019m here. I\u2019ll wait"),
    ("Okay. I agree.", "okay. I agree"),
])
def test_very_casual_keeps_I(text, want):
    assert very(text) == want


def test_I_is_an_ordinary_word_outside_english():
    assert very("Okay. I ragazzi sono qui.", language="it") == "okay. i ragazzi sono qui"
    assert very("I ragazzi sono qui. Ciao.", language="it") == "i ragazzi sono qui. ciao"
    assert very("I think so.", language="en") == "I think so"


def test_acronyms_urls_emails_and_code_keep_their_capitals():
    for kept in ("NASA launched it", "API is down", "OK then", "USA wins", "B2B sales rose", "U.S. troops left",
                 "GitHub is down", "McDonald's is open", "JavaScript runs", "Wi-Fi is down", "PhD students came",
                 "README.md is old", "Example.com is down", "Sam@example.com wrote", "Foo_bar is set",
                 "C:/temp/x.txt is gone", "J. Smith left", "Node.js works", "A4 is paper", "H2O is water"):
        assert very(kept + ". Fine.") == kept + ". fine", kept


def test_a_sentence_that_starts_with_a_url_keeps_it():
    assert very("https://Example.com/A is up. Then wait.") == "https://Example.com/A is up. then wait"
    assert very("Www.example.com is up.") == "Www.example.com is up"


def test_words_in_the_vocabulary_keep_their_capitals():
    text = "Waffler is great. New York is big. Paris is nice."
    assert very(text, protected_words=("Waffler", "New York")) == "Waffler is great. New York is big. paris is nice"


def test_a_vocabulary_word_matches_whole_words_in_any_case():
    assert very("Samantha called.", protected_words=("Sam",)) == "samantha called"
    assert very("Waffler rocks.", protected_words=("waffler",)) == "Waffler rocks"
    assert very("Sam called.", protected_words=("Sam",)) == "Sam called"
    assert very("Sam's here.", protected_words=("Sam",)) == "Sam's here"
    assert very("Open AI is new.", protected_words=("Open AI",)) == "Open AI is new"


def test_the_longest_vocabulary_entry_wins_and_odd_entries_do_not_break_it():
    assert very("New Yorkers walk.", protected_words=("New York",)) == "new Yorkers walk"
    assert very("Sam called.", protected_words=("", None, 5, "Sam")) == "Sam called"
    assert very("Okay.", protected_words=None) == "okay"
    assert very("C++ is old. Fine.", protected_words=("C++",)) == "C++ is old. fine"


def test_mid_sentence_capitals_are_never_touched():
    assert very("Please ask Dave about the London office. Then call Sam.") == \
        "please ask Dave about the London office. then call Sam"


def test_german_nouns_keep_their_capitals_inside_a_sentence():
    assert very("Das Haus ist groß. Der Hund schläft im Garten.", language="de") == \
        "das Haus ist groß. der Hund schläft im Garten"
    assert casual("Das Haus ist groß.", language="de") == "Das Haus ist groß"


def test_spanish_marks_in_front_of_a_sentence_are_skipped():
    assert very("¿Vienes mañana? Sí, claro. ¡Genial!", language="es") == "¿vienes mañana? sí, claro. ¡genial!"


def test_turkish_dotted_and_dotless_I():
    assert very("Işık yandı. İstanbul güzel.", language="tr") == "ışık yandı. istanbul güzel"
    out = very("İstanbul güzel.", language="en")
    assert out == "istanbul güzel" and len(out) == len("İstanbul güzel")


def test_a_sentence_that_opens_with_a_quote_is_not_touched():
    assert very('"Let us go," she said. Then left.') == '"Let us go," she said. then left'
    assert very("\u201cLet us go,\u201d she said. Then left.") == "\u201cLet us go,\u201d she said. then left"
    assert very("\u201eGehen wir\u201c, sagte sie. Dann ging sie.", language="de") == \
        "\u201eGehen wir\u201c, sagte sie. dann ging sie"


def test_nothing_inside_a_quote_changes():
    assert very('She said "Stop. Now." and left. Then silence.') == 'she said "Stop. Now." and left. then silence'
    assert very('Say "Hello. World." Now.') == 'say "Hello. World." now'
    assert very("She said \u00abStop. Now.\u00bb and left.") == "she said \u00abStop. Now.\u00bb and left"


def test_a_quote_left_open_protects_the_rest():
    assert very('He said "go. Then wait. Fine.') == 'he said "go. Then wait. Fine.'


def test_lists_and_bullets_are_left_alone():
    assert very("Groceries:\n- Milk\n- Eggs\nThat is all.") == "groceries:\n- Milk\n- Eggs\nthat is all"
    assert very("1. Buy milk.\n2. Call Sam.") == "1. Buy milk.\n2. Call Sam."
    assert very("\u2022 Milk\n\u2022 Eggs") == "\u2022 Milk\n\u2022 Eggs"
    assert very("a) First\nb) Second") == "a) First\nb) Second"
    assert very("* Milk.\n* Eggs.") == "* Milk.\n* Eggs."


def test_code_lines_are_left_alone():
    code = "Run `git status` first. Then commit."
    assert very(code) == code
    for line in ("x = foo(3);", "const a = b => c;", "$ npm install", "if (x == 1) { run(); }",
                 "import os", "def Run():", "list.Append(item)", "<Div class=a>", "    Indented code line."):
        assert very(line) == line, line
        assert casual(line + ".") == line + ".", line
        assert excited(line + ".") == line + ".", line


def test_prose_with_a_bracket_or_a_plural_s_is_not_code():
    assert very("Send the file(s) today. Thanks.") == "send the file(s) today. thanks"
    assert very("Call me (tonight). Thanks.") == "call me (tonight). thanks"


def test_very_casual_after_a_greeting_joins_the_lines():
    assert very("Hi Sam,\n\nThanks for the update. I'll look today.", category="email") == \
        "hi Sam, thanks for the update. I'll look today"
    assert very("Hey Mum,\nI'm late.", category="personal") == "hey Mum, I'm late"
    assert very("Dear Sir or Madam,\n\nPlease find it attached.", category="email") == \
        "dear Sir or Madam, please find it attached"
    assert very("Good morning team,\n\nQuick update.", category="work") == "good morning team, quick update"


def test_the_greeting_join_leaves_other_things_alone():
    assert very("Hi Sam,") == "hi Sam,"
    assert very("Thanks,\n\nSee you.") == "thanks,\n\nsee you"            # not a greeting: a new paragraph
    assert very("Hi Sam\n\nSee you.") == "hi Sam\n\nsee you"               # no comma: not joined
    assert very("Hi Sam,\n\n- one\n- two") == "hi Sam,\n\n- one\n- two"      # a list under it keeps its break
    assert apply_style("Hi Sam,\n\nSee you.", "casual", "email") == "Hi Sam,\n\nSee you"
    assert apply_style("Hi Sam,\n\nSee you.", "excited", "email") == "Hi Sam,\n\nSee you!"
    assert apply_style("Hi Sam,\n\nSee you.", "formal", "email") == "Hi Sam,\n\nSee you."


def test_a_sign_off_name_is_not_lowered_after_its_comma():
    assert very("Thanks for the update.\n\nBest,\nJames", category="email") == \
        "thanks for the update.\n\nbest,\nJames"


def test_a_line_after_a_comma_is_a_continuation_not_a_new_sentence():
    assert very("I think that,\nHowever you see it. Fine.") == "I think that,\nHowever you see it. fine"


def test_windows_line_endings_survive():
    assert very("First.\r\n\r\nSecond.") == "first.\r\n\r\nsecond"
    assert casual("Hi Sam,\r\n\r\nSee you.") == "Hi Sam,\r\n\r\nSee you"


# ── excited: short statements end in an exclamation mark ─────────────────────

@pytest.mark.parametrize("text,want", [
    ("Thanks for the update.", "Thanks for the update!"),
    ("Hello.", "Hello!"),
    ("Thanks. See you tomorrow.", "Thanks! See you tomorrow!"),
    ("Are you coming? Yes.", "Are you coming? Yes!"),
    ("It is 3.5.", "It is 3.5!"),
    ("See you at 5.", "See you at 5!"),
    ("Dr. Smith will call.", "Dr. Smith will call!"),
    ("The meeting is on Mon. 5th at 10 a.m. in room B2.", "The meeting is on Mon. 5th at 10 a.m. in room B2!"),
    ("Hi Sam,\n\nThanks for the update.", "Hi Sam,\n\nThanks for the update!"),
    ("First.\n\nSecond.", "First!\n\nSecond!"),
])
def test_excited_turns_a_short_statements_stop_into_an_exclamation_mark(text, want):
    assert excited(text) == want


@pytest.mark.parametrize("text", [
    "Are you coming?", "Great!", "Great!!", "Wow?!", "Wow...", "Wow\u2026", "Thanks..", "Fine",
    "Details at https://example.com/x.", "Write to sam@example.com.", "Open foo.py.",
    "Run `ls`.", "- Milk.", "1. Milk.", "See you at 5 p.m.", "Bring bread, milk, etc.",
    'She said "great."', "It is (fine.)",
    "Sorry, I can't make it.", "Unfortunately the build failed.", "I won't be there.", "That is not ready.",
    "There is a problem with it.", "We cancelled the trip.", "He passed away.", "It is late.", "No.",
    "The site is down.", "It crashed again.", "I missed the call.", "We are stuck.",
])
def test_excited_leaves_these_alone(text):
    assert excited(text) == text


def test_excited_only_changes_short_sentences():
    short = "We are on track for Friday."
    long_ = " ".join(["Word"] + ["word"] * 19) + "."
    assert excited(short + " " + long_) == "We are on track for Friday! " + long_


def test_excited_over_the_word_budget_only_ends_the_last_sentence():
    lead = " ".join(["Alpha"] + ["alpha"] * 9)
    text = f"{lead}. {lead}. {lead}. {lead}. {lead}. {lead}. Done."
    got = excited(text)
    assert got.endswith(" Done!")
    assert got.count("!") == 1 and got.count(".") == 6
    # Under the budget every short statement gets one.
    small = f"{lead}. {lead}. Done."
    assert excited(small).count("!") == 3


def test_excited_never_stacks_and_is_idempotent():
    for text in ("Thanks. See you tomorrow.", "Great!", "Really? Yes. Fine.", "Hi Sam,\n\nThanks."):
        once = excited(text)
        assert "!!" not in once and "?!" not in once and "!?" not in once
        assert excited(once) == once


def test_excited_does_not_use_an_exclamation_mark_without_its_opening_partner():
    assert apply_style("Gracias.", "excited", "work", language="es") == "Gracias."
    assert apply_style("Gracias.", "excited", "work", language="auto") == "Gracias!"
    assert apply_style("\u00a1Gracias! Vale.", "excited", "work", language="auto") == "\u00a1Gracias! Vale."
    assert apply_style("Merci.", "excited", "work", language="fr") == "Merci!"
    assert apply_style("Danke.", "excited", "work", language="de") == "Danke!"


# ── the guarantee: never a word ──────────────────────────────────────────────

SAMPLES = [
    "Sounds good.", "I think so. I'm sure. Okay then.", "Hi Sam,\n\nThanks for the update. I'll look today.\n\nBest,\nJames",
    "Dr. Smith will call at 5 p.m. tomorrow. Bring the files, etc.", 'She said "Stop. Now." and left.',
    "Groceries:\n- Milk\n- Eggs\nThat is all.", "Visit https://example.com/a now. Then email sam@example.com.",
    "Das Haus ist groß. Der Hund schläft.", "Hmm... Okay. Fine.", "Really? Yes! Great.", "NASA and the U.S. agree.",
    "1. Buy milk.\n2. Call Sam.\n\nThanks.", "x = foo(3);\nThen run it.", "Éric a dit oui. Élan aussi.",
    "A. Smith left. J. K. Rowling stayed.", "What? No way. Wait.", "It is 3.5 miles. See Fig. 3.",
    "Okay. Okay. Okay.", "i already lower case. and again.", "ALL CAPS HERE. AND HERE.",
]


@pytest.mark.parametrize("style", sp.STYLES)
@pytest.mark.parametrize("text", SAMPLES)
@pytest.mark.parametrize("category", app_categories.CATEGORIES)
def test_a_style_never_changes_a_word(style, text, category):
    out = apply_style(text, style, category)
    assert words(out) == words(text)
    # Only case, terminal punctuation and the line break after a greeting differ.
    flat = lambda s: re.sub(r"[\s.!]+", "", s).lower()          # noqa: E731
    assert flat(out) == flat(text)


@pytest.mark.parametrize("style", sp.STYLES)
@pytest.mark.parametrize("text", SAMPLES)
def test_applying_a_style_twice_is_applying_it_once(style, text):
    once = apply_style(text, style, "email")
    assert apply_style(once, style, "email") == once


@pytest.mark.parametrize("style", sp.STYLES)
def test_the_length_changes_only_by_a_stop_and_a_break(style):
    text = "Hi Sam,\n\nThanks for the update. I'll look today."
    out = apply_style(text, style, "email")
    assert abs(len(out) - len(text)) <= 3


def test_the_text_given_is_not_modified_in_place_and_the_result_is_text():
    text = "Sounds good. See you."
    keep = str(text)
    out = very(text)
    assert text == keep and isinstance(out, str)


def test_a_very_long_message_is_handled_quickly():
    import time
    text = " ".join(f"Sentence number {i} says something." for i in range(3000))
    t0 = time.perf_counter()
    for style in sp.STYLES:
        apply_style(text, style, "other", protected_words=("Waffler", "New York"))
    assert time.perf_counter() - t0 < 5.0


@pytest.mark.parametrize("junk", ["\x00", "\u200b", "....", "!!!!", "?", ".", "...", "\u2026", "\"", "\u201c", "-", "- ",
                                  "1.", "(", ")", "\n\n\n", "a", "A", "I", "i.", "I.", " . ", ".\n.", "\U0001F600 ok."])
def test_odd_input_never_raises_and_never_adds_words(junk):
    for style in sp.STYLES:
        out = apply_style(junk, style, "other")
        assert isinstance(out, str)
        assert words(out) == words(junk)


# ── the example sentences the Settings page shows ────────────────────────────

def test_there_is_a_sample_for_every_kind_of_app_and_each_is_one_sentence():
    assert set(sp.SAMPLES) == set(app_categories.CATEGORIES)
    for category, sample in sp.SAMPLES.items():
        assert "\n" not in sample and sample.endswith(".")
        assert sample.count(". ") == 0, category
        assert "\u2014" not in sample and "\u2013" not in sample


def test_the_preview_is_the_real_rules_on_the_sample():
    for category in app_categories.CATEGORIES:
        sample = sp.SAMPLES[category]
        assert sp.preview("auto", category) == sample
        assert sp.preview("formal", category) == sample
        assert sp.preview("casual", category) == apply_style(sample, "casual", category)
        assert sp.preview("very_casual", category) == apply_style(sample, "very_casual", category)
        assert sp.preview("excited", category) == apply_style(sample, "excited", category)
    assert sp.preview("casual", "personal") == sp.SAMPLES["personal"][:-1]
    assert sp.preview("very_casual", "personal") == "sure, I'll be there at seven"
    assert sp.preview("excited", "work").endswith("!")


def test_the_preview_of_something_unknown_is_the_plain_sample():
    assert sp.preview("shouty", "work") == sp.SAMPLES["work"]
    assert sp.preview("casual", "moon") == apply_style(sp.SAMPLES["other"], "casual", "other")
    assert sp.preview(None, None) == sp.SAMPLES["other"]


# ── the rules are in the code, not in a prompt ───────────────────────────────

def test_the_module_is_pure():
    src = (ROOT / "src" / "style_post.py").read_text(encoding="utf-8")
    for forbidden in ("open(", "import os", "import json", "requests", "subprocess", "clipboard", "print("):
        assert forbidden not in src, forbidden


# ── a fuzz over awkward input: the guarantees hold for all of it ─────────────

def _fuzz_texts(n=600, seed=20261016):
    import random
    rng = random.Random(seed)
    bits = ["Okay", "I", "I'm", "the", "The", "A", "NASA", "GitHub", "Dr.", "Mr.", "e.g.", "p.m.", "etc.", "J.", "K.",
            "https://example.com/a", "sam@example.com", "foo_bar", "x.py", "3.5", "5", "Hello", "world", "Waffler",
            "Haus", "Éric", "über", "café", "¿Qué?", "sorry", "can't", "great", "Thanks",
            ".", "!", "?", "...", "…", ",", ":", ";", "\"", "“", "”", "(", ")", "-", "- item", "1. item",
            "`code`", "{", "}", "=>", "x = 1;", "\n", "\n\n", "\r\n", "  ", "Hi Sam,", "Best,", "James"]
    for _ in range(n):
        parts = [rng.choice(bits) for _ in range(rng.randint(1, 24))]
        out = ""
        for p in parts:
            out += p if p in (".", "!", "?", "...", "…", ",", ":", ";", ")", "\n", "\n\n", "\r\n") or out.endswith(("\n", "(", "\"")) else " " + p
        yield out.strip(" ")


def test_the_fuzz_never_changes_a_word_or_raises():
    flat = lambda s: re.sub(r"[\s.!]+", "", s).lower()          # noqa: E731
    seen = 0
    for text in _fuzz_texts():
        for style in sp.STYLES:
            for language in ("en", "auto", "de", "tr"):
                out = apply_style(text, style, "email", language=language, protected_words=("Waffler", "New York"))
                assert isinstance(out, str)
                assert words(out) == words(text), (style, language, text, out)
                assert flat(out).replace("ı", "i") == flat(text).replace("ı", "i").replace("İ", "i"), (style, text, out)
                seen += 1
    assert seen > 1000


def test_the_fuzz_is_stable_when_applied_twice():
    for text in _fuzz_texts(400, seed=7):
        for style in sp.STYLES:
            once = apply_style(text, style, "work", language="en")
            assert apply_style(once, style, "work", language="en") == once, (style, text, once)
