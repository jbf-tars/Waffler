"""Snippets: the matching and the list that is saved (src/snippets.py).

Before: each snippet was a ``\\bTRIGGER\\b`` substitution in list order after the
clean-up, so a trigger whose text contained another trigger was expanded twice,
a shorter trigger could win over a longer one, a trigger that began or ended
with a symbol never matched, and a dictation that was only a trigger still went
to a model. These tests are the exact cases of the new module.
"""
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import snippets as S  # noqa: E402


def sn(*pairs):
    return [{"trigger": t, "expansion": e} for t, e in pairs]


ADDRESS = "12 Acacia Avenue\nLondon\nN1 1AA"


# ── apply: the plain cases ───────────────────────────────────────────────────

def test_a_trigger_inside_a_sentence_is_replaced():
    assert S.apply("Send it to my address please", sn(("my address", ADDRESS))) == \
        f"Send it to {ADDRESS} please"


@pytest.mark.parametrize("said", ["MY ADDRESS", "My Address", "my address", "mY aDdReSs"])
def test_any_capitals_match(said):
    assert S.apply(f"post it to {said}.", sn(("my address", "HOME"))) == "post it to HOME."


@pytest.mark.parametrize("text, expected", [
    ("my address.", "HOME."),
    ("my address,", "HOME,"),
    ("(my address)", "(HOME)"),
    ('"my address"', '"HOME"'),
    ("my address!", "HOME!"),
    ("my address?", "HOME?"),
    ("to my address; then", "to HOME; then"),
    ("my address's roof", "HOME's roof"),
])
def test_punctuation_next_to_a_trigger_stays(text, expected):
    assert S.apply(text, sn(("my address", "HOME"))) == expected


def test_only_whole_words_match():
    snips = sn(("addr", "ADDRESS"), ("cat", "feline"))
    for text in ("addresses", "the addr2 line", "concatenate", "category", "mycat", "cat9", "_cat"):
        assert S.apply(text, snips) == text, text
    assert S.apply("my cat sat", snips) == "my feline sat"
    assert S.apply("cat, cat. cat!", snips) == "feline, feline. feline!"


def test_a_longer_trigger_wins_wherever_both_could_start():
    snips = sn(("my", "MINE"), ("my address", "HOME"), ("my address book", "BOOK"))
    assert S.apply("my address book is at my address", snips) == "BOOK is at HOME"
    assert S.apply("my address", snips) == "HOME"
    assert S.apply("my dog", snips) == "MINE dog"


def test_the_order_of_the_list_does_not_change_the_result():
    a = sn(("my", "MINE"), ("my address", "HOME"))
    b = list(reversed(a))
    text = "my address and my dog"
    assert S.apply(text, a) == S.apply(text, b) == "HOME and MINE dog"


def test_a_longer_trigger_that_is_not_a_whole_word_gives_way_to_a_shorter_one():
    """'my address' must not eat the start of 'my addressing', and 'my' still
    matches on its own."""
    snips = sn(("my address", "HOME"), ("my", "MINE"))
    assert S.apply("my addressing", snips) == "MINE addressing"


def test_neighbours_each_expand_and_the_space_between_stays():
    snips = sn(("my address", "HOME"))
    assert S.apply("my address my address", snips) == "HOME HOME"
    assert S.apply("my address, my address", snips) == "HOME, HOME"
    assert S.apply("my address\nmy address", snips) == "HOME\nHOME"


def test_two_triggers_side_by_side():
    snips = sn(("sig", "Best, James"), ("addr", "1 High St"))
    assert S.apply("sig addr", snips) == "Best, James 1 High St"


def test_an_expansion_is_never_read_again():
    """One snippet's text can never set another off, or itself."""
    snips = sn(("a", "b c"), ("b", "BEE"), ("c", "SEA"), ("sig", "sig: James"))
    assert S.apply("a", snips) == "b c"
    assert S.apply("say a now", snips) == "say b c now"
    assert S.apply("sig", snips) == "sig: James"
    loop = sn(("one", "two"), ("two", "one"))
    assert S.apply("one", loop) == "two"
    assert S.apply("two", loop) == "one"
    assert S.apply("one two", loop) == "two one"


def test_it_replaces_in_one_pass_not_one_snippet_at_a_time():
    snips = sn(("x", "y"), ("y", "z"))
    assert S.apply("x y", snips) == "y z"


# ── apply: characters that mean something to a regular expression ────────────

@pytest.mark.parametrize("trigger", [
    "c++", "a.b", "(x)", "$100", "[todo]", "a|b", "\\d+", "x*y", "a?b", "^start", "end$",
    "{1,3}", "back\\slash", "q.", "a+b=c", "100%", "#tag", "50/50",
])
def test_regular_expression_characters_in_a_trigger_are_literal(trigger):
    snips = sn((trigger, "OK"))
    assert S.apply(f"say {trigger} now", snips) == "say OK now"
    # ...and are not read as a pattern against other text.
    for other in ("aab", "ab", "dd", "xxy", "ac", "start", "end", "d", "xy", "1"):
        assert S.apply(f"say {other} now", snips) == f"say {other} now", other


def test_a_trigger_that_begins_or_ends_with_a_symbol_matches():
    snips = sn(("@home", "12 Acacia Avenue"), ("c++", "C plus plus"))
    assert S.apply("mail @home", snips) == "mail 12 Acacia Avenue"
    assert S.apply("I write c++ daily", snips) == "I write C plus plus daily"
    assert S.apply("I write c++, mostly", snips) == "I write C plus plus, mostly"
    # Still whole: not glued to letters or digits.
    assert S.apply("me@home", snips) == "me@home"
    assert S.apply("c++x", snips) == "c++x"


def test_an_expansion_is_pasted_as_written_whatever_it_holds():
    nasty = "C:\\Users\\me\\1 \\g<0> \\1 $1 ${x} \\n %s {0} (?P<a>.)"
    assert S.apply("path", sn(("path", nasty))) == nasty
    assert S.apply("a path b", sn(("path", nasty))) == f"a {nasty} b"


# ── apply: words from other languages and other spacing ─────────────────────

@pytest.mark.parametrize("trigger, said, expected_in", [
    ("café", "Un CAFÉ por favor", "Un COFFEE por favor"),
    ("straße", "die Straße ist lang", "die COFFEE ist lang"),
    ("привет", "Сказал ПРИВЕТ всем", "Сказал COFFEE всем"),
    ("日本語", "これは日本語です", "これはCOFFEEです"),       # no spaces to separate words
    ("日本語", "これは 日本語 です", "これは COFFEE です"),
    ("住所", "私の住所は東京です", "私のCOFFEEは東京です"),
    ("ñandú", "vi un Ñandú hoy", "vi un COFFEE hoy"),
])
def test_other_languages(trigger, said, expected_in):
    assert S.apply(said, sn((trigger, "COFFEE"))) == expected_in


def test_a_unicode_expansion_comes_through_whole():
    text = "café – naïve – 日本語 – 🙂"
    assert S.apply("x", sn(("x", text))) == text


def test_spaces_inside_a_trigger_are_flexible():
    snips = sn(("my   address", "HOME"))          # typed with extra spaces
    assert S.apply("my address", snips) == "HOME"
    assert S.apply("my  address", snips) == "HOME"
    assert S.apply("my\naddress", snips) == "HOME"
    assert S.apply("my\taddress", snips) == "HOME"
    assert S.apply("myaddress", snips) == "myaddress"


def test_a_latin_trigger_next_to_unspaced_script_is_still_a_whole_word():
    """Only a trigger that itself starts or ends in Chinese, Japanese or Thai
    is allowed inside a run of that script."""
    assert S.apply("これはcatです", sn(("cat", "CAT"))) == "これはcatです"
    assert S.apply("これは cat です", sn(("cat", "CAT"))) == "これは CAT です"
    assert S.apply("catは猫", sn(("cat", "CAT"))) == "catは猫"


def test_a_trigger_typed_with_outer_space_matches():
    assert S.apply("my address", sn(("  my address  ", "HOME"))) == "HOME"


# ── apply: nothing to do ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["", None])
def test_no_text_comes_back_as_it_was(text):
    assert S.apply(text, sn(("a", "b"))) == text


@pytest.mark.parametrize("snips", [[], None, "nope", 5, {"trigger": "a"}, [None, 5, "x", {}, {"trigger": "a"},
                                   {"expansion": "b"}, {"trigger": "", "expansion": "b"},
                                   {"trigger": "  ", "expansion": "b"}, {"trigger": 1, "expansion": "b"},
                                   {"trigger": "a", "expansion": None}]])
def test_no_usable_snippets_leave_the_text_alone(snips):
    assert S.apply("a and b", snips) == "a and b"


def test_bad_entries_do_not_stop_the_good_ones():
    snips = [None, {"trigger": "a"}, {"trigger": "go", "expansion": "GO"}, 7]
    assert S.apply("go now", snips) == "GO now"


def test_duplicate_triggers_use_the_first():
    snips = sn(("Hi", "FIRST"), ("hi", "SECOND"), ("HI ", "THIRD"))
    assert S.apply("hi", snips) == "FIRST"


def test_the_list_is_not_changed():
    snips = sn(("my", "MINE"), ("my address", "HOME"))
    before = json.dumps(snips)
    S.apply("my address", snips)
    S.whole_utterance("my address", snips)
    assert json.dumps(snips) == before


def test_two_hundred_snippets_over_a_long_text_is_quick():
    snips = sn(*[(f"trigger {i} here", f"EXPANSION {i}") for i in range(200)])
    text = ("word " * 4000) + "trigger 199 here and trigger 0 here"
    start = time.perf_counter()
    for _ in range(20):
        out = S.apply(text, snips)
    assert (time.perf_counter() - start) / 20 < 0.25
    assert out.endswith("EXPANSION 199 and EXPANSION 0")


def test_a_changed_list_is_not_served_from_the_cache():
    assert S.apply("x", sn(("x", "one"))) == "one"
    assert S.apply("x", sn(("x", "two"))) == "two"
    assert S.apply("x", []) == "x"


# ── whole_utterance ──────────────────────────────────────────────────────────

SNIPS = sn(("my address", ADDRESS), ("Sign off", "Best,\nJames"), ("shrug", "¯\\_(ツ)_/¯"))


@pytest.mark.parametrize("said", [
    "my address", "My address.", "MY ADDRESS!", "  my address  ", "my address?", "\"My address.\"",
    "(my address)", "my address...", "My Address\n", "\tmy address", "My, address.", "my-address",
    "my  address",
])
def test_a_dictation_that_is_only_the_trigger_is_exactly_the_expansion(said):
    assert S.whole_utterance(said, SNIPS) == ADDRESS


def test_the_expansion_is_returned_exactly_as_saved():
    assert S.whole_utterance("sign off.", SNIPS) == "Best,\nJames"
    assert S.whole_utterance("Shrug", SNIPS) == "¯\\_(ツ)_/¯"


@pytest.mark.parametrize("said", [
    "my address please", "send my address", "my address and my address", "address", "my",
    "my addresses", "the my address", "", "   ", "...", None, "my address. Thanks.",
])
def test_anything_more_than_the_trigger_is_not_whole(said):
    assert S.whole_utterance(said, SNIPS) is None


def test_a_trigger_written_with_punctuation_matches_the_spoken_words():
    snips = sn(("e-mail me!", "ME@EXAMPLE.COM"))
    assert S.whole_utterance("E-mail me", snips) == "ME@EXAMPLE.COM"
    assert S.whole_utterance("email me", snips) is None          # a different word
    assert S.whole_utterance("e mail me", snips) == "ME@EXAMPLE.COM"


def test_a_trigger_of_symbols_only_can_be_expanded_inside_text_but_is_not_a_whole_dictation():
    snips = sn((":)", "SMILE"))
    assert S.whole_utterance(":)", snips) is None                # nothing to compare
    assert S.whole_utterance("...", snips) is None
    assert S.apply("ok :) bye", snips) == "ok SMILE bye"


def test_other_languages_are_whole_too():
    snips = sn(("mi dirección", "Calle Mayor 1"), ("日本語", "NIHONGO"), ("привет", "HELLO"))
    assert S.whole_utterance("Mi dirección.", snips) == "Calle Mayor 1"
    assert S.whole_utterance("日本語。", snips) == "NIHONGO"
    assert S.whole_utterance("Привет!", snips) == "HELLO"
    assert S.whole_utterance("mi direccion", snips) is None      # accents matter


def test_marks_and_digits_stay_part_of_the_words():
    snips = sn(("हिन्दी", "HINDI"), ("room 12b", "ROOM"))
    assert S.whole_utterance("हिन्दी।", snips) == "HINDI"
    assert S.whole_utterance("Room 12B.", snips) == "ROOM"
    assert S.whole_utterance("room 12", snips) is None


def test_the_first_of_two_triggers_that_read_the_same_wins():
    snips = sn(("my address", "FIRST"), ("my address!", "SECOND"))
    assert S.whole_utterance("my address", snips) == "FIRST"


def test_a_long_dictation_is_never_whole_and_is_quick_to_say_so():
    snips = sn(("my address", "HOME"))
    assert S.whole_utterance("my address " * 5000, snips) is None


def test_whole_utterance_without_snippets():
    assert S.whole_utterance("my address", []) is None
    assert S.whole_utterance("my address", None) is None


# ── clean: the list that is saved ────────────────────────────────────────────

def test_clean_strips_collapses_and_keeps_the_text():
    got, err = S.clean([{"trigger": "  my   address ", "expansion": "  line one\n\nline two \n"}])
    assert err is None
    assert got == [{"trigger": "my address", "expansion": "line one\n\nline two"}]


def test_clean_drops_what_is_empty_or_not_a_snippet():
    got, err = S.clean([
        {"trigger": "", "expansion": "x"}, {"trigger": "   ", "expansion": "x"},
        {"trigger": "a", "expansion": ""}, {"trigger": "a", "expansion": "   \n"},
        None, 5, "x", {"trigger": 1, "expansion": "x"}, {"trigger": "a"}, {"expansion": "x"},
        {"trigger": "ok", "expansion": "fine"},
    ])
    assert err is None and got == [{"trigger": "ok", "expansion": "fine"}]


def test_clean_drops_a_trigger_that_is_already_there_in_other_capitals():
    got, err = S.clean(sn(("Hello", "one"), ("hello", "two"), ("HELLO ", "three"), ("hello  there", "x"),
                          ("Hello There", "y")))
    assert err is None
    assert got == [{"trigger": "Hello", "expansion": "one"},
                   {"trigger": "hello there", "expansion": "x"}]


def test_clean_keeps_the_order_and_extra_keys_are_not_kept():
    got, _ = S.clean([{"trigger": "b", "expansion": "B", "x": 1}, {"trigger": "a", "expansion": "A"}])
    assert got == [{"trigger": "b", "expansion": "B"}, {"trigger": "a", "expansion": "A"}]


@pytest.mark.parametrize("bad", [None, "nope", 5, {"trigger": "a", "expansion": "b"}])
def test_clean_refuses_what_is_not_a_list(bad):
    got, err = S.clean(bad)
    assert got == [] and err == S.ERR_READ


def test_the_limits_are_exact():
    assert (S.MAX_TRIGGER, S.MAX_EXPANSION, S.MAX_SNIPPETS) == (60, 4000, 200)
    ok, err = S.clean(sn(("t" * 60, "e" * 4000)))
    assert err is None and len(ok) == 1
    assert S.clean(sn(("t" * 61, "x")))[1] == S.ERR_TRIGGER
    assert S.clean(sn(("t", "e" * 4001)))[1] == S.ERR_EXPANSION
    two_hundred = sn(*[(f"t{i}", "x") for i in range(200)])
    assert S.clean(two_hundred)[1] is None
    assert S.clean(two_hundred + sn(("one more", "x")))[1] == S.ERR_COUNT


def test_a_refused_list_returns_nothing_to_save():
    got, err = S.clean(sn(("fine", "ok"), ("t" * 61, "x")))
    assert got == [] and err


def test_clean_counts_characters_not_bytes():
    assert S.clean(sn(("é" * 60, "日" * 4000)))[1] is None
    assert S.clean(sn(("é" * 61, "x")))[1] == S.ERR_TRIGGER


def test_duplicates_do_not_count_toward_the_cap():
    many = sn(*[("same", "x") for _ in range(500)])
    got, err = S.clean(many)
    assert err is None and len(got) == 1


def test_the_error_sentences_are_plain():
    for msg in (S.ERR_READ, S.ERR_TRIGGER, S.ERR_EXPANSION, S.ERR_COUNT):
        assert msg.endswith(".") and "\u2014" not in msg and "\u2013" not in msg
    assert "4,000" in S.ERR_EXPANSION and "60" in S.ERR_TRIGGER and "200" in S.ERR_COUNT


# ── load ─────────────────────────────────────────────────────────────────────

def test_load_reads_what_is_saved(tmp_path):
    f = tmp_path / "snippets.json"
    f.write_text(json.dumps(sn(("a", "A"), ("b", "B")), ensure_ascii=False), encoding="utf-8")
    assert S.load(f) == sn(("a", "A"), ("b", "B"))


def test_load_reads_a_file_with_a_byte_order_mark_and_other_languages(tmp_path):
    f = tmp_path / "snippets.json"
    f.write_bytes(b"\xef\xbb\xbf" + json.dumps(sn(("café", "naïve 日本語")), ensure_ascii=False).encode("utf-8"))
    assert S.load(f) == sn(("café", "naïve 日本語"))


@pytest.mark.parametrize("raw", [b"", b"not json", b"{}", b"[1, 2]", b'{"trigger": "a"}', b"\xff\xfe\x00bad"])
def test_load_gives_nothing_for_a_file_it_cannot_use(tmp_path, raw):
    f = tmp_path / "snippets.json"
    f.write_bytes(raw)
    assert S.load(f) == []


def test_load_of_a_missing_file_is_empty(tmp_path):
    assert S.load(tmp_path / "nope.json") == []


def test_load_keeps_only_the_usable_entries(tmp_path):
    f = tmp_path / "snippets.json"
    f.write_text(json.dumps([{"trigger": " a ", "expansion": "A"}, {"trigger": "b"}, None,
                             {"trigger": "", "expansion": "x"}, {"trigger": "c", "expansion": ""}]),
                 encoding="utf-8")
    assert S.load(f) == [{"trigger": "a", "expansion": "A"}, {"trigger": "c", "expansion": ""}]
