"""Starred words, and the Vocabulary as a Whisper prompt (src/vocab_rank.py).

Stars live in their own file, vocab_stars.json, so vocab.json stays the plain
list of strings that Waffler 3.15.1 (and 3.14) read: a word turned into an
object there would be dropped by an older version, and the word lost. Stars
only decide order: the page's "Starred first" sort, and which words are kept
when the prompt that OpenAI and the local model get has to be cut short. Groq
gets no prompt and is not touched.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import vocab_rank as R  # noqa: E402


# ── the stars file ───────────────────────────────────────────────────────────

def test_nothing_saved_is_no_stars(tmp_path):
    assert R.load_stars(["Groq"], tmp_path / "vocab_stars.json") == []


def test_stars_come_back_as_the_words_are_written_in_list_order(tmp_path):
    f = tmp_path / "vocab_stars.json"
    f.write_text(json.dumps(["posthog", "GROQ", "Gone"]), encoding="utf-8")
    assert R.load_stars(["Groq", "Morta", "PostHog"], f) == ["Groq", "PostHog"]


def test_a_file_that_cannot_be_read_is_no_stars(tmp_path):
    f = tmp_path / "vocab_stars.json"
    f.write_bytes(b"{ not json")
    assert R.load_stars(["Groq"], f) == []
    f.write_text(json.dumps({"Groq": True}), encoding="utf-8")
    assert R.load_stars(["Groq"], f) == []


def test_a_file_with_a_byte_order_mark_is_read(tmp_path):
    f = tmp_path / "vocab_stars.json"
    f.write_bytes(b"\xef\xbb\xbf" + json.dumps(["Sinéad"], ensure_ascii=False).encode("utf-8"))
    assert R.load_stars(["Sinéad"], f) == ["Sinéad"]


def test_clean_drops_what_is_not_a_listed_word_and_repeats():
    assert R.clean_stars(["a", "A", " b ", 5, None, "zzz"], ["a", "b", "c"]) == ["a", "b"]
    assert R.clean_stars("a", ["a"]) == []
    assert R.clean_stars(None, ["a"]) == []


def test_save_writes_utf8_atomically_and_only_for_listed_words(tmp_path):
    f = tmp_path / "vocab_stars.json"
    assert R.save_stars(["Sinéad", "Gone"], ["Sinéad", "Groq"], f) == ["Sinéad"]
    assert json.loads(f.read_text(encoding="utf-8")) == ["Sinéad"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["vocab_stars.json"]    # no temp file left


def test_saving_no_stars_does_not_make_a_file_but_empties_one_that_exists(tmp_path):
    f = tmp_path / "vocab_stars.json"
    assert R.save_stars([], ["Groq"], f) == []
    assert not f.exists()
    R.save_stars(["Groq"], ["Groq"], f)
    assert R.save_stars([], ["Groq"], f) == []
    assert json.loads(f.read_text(encoding="utf-8")) == []


def test_a_removed_word_takes_its_star_with_it_on_the_next_save(tmp_path):
    f = tmp_path / "vocab_stars.json"
    R.save_stars(["Groq", "Morta"], ["Groq", "Morta"], f)
    assert R.save_stars(R.load_stars(["Groq", "Morta"], f), ["Groq"], f) == ["Groq"]
    assert json.loads(f.read_text(encoding="utf-8")) == ["Groq"]


def test_the_default_path_is_resolved_when_called(tmp_path, monkeypatch):
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path))
    R.save_stars(["Groq"], ["Groq"])
    assert (tmp_path / "vocab_stars.json").exists()
    assert R.load_stars(["Groq"]) == ["Groq"]


# ── the prompt ───────────────────────────────────────────────────────────────

def test_a_short_list_with_no_stars_is_the_prompt_it_always_was():
    words = ["Waffler", "Groq", "PostHog", "Sinéad"]
    assert R.prompt_words(words, [], {}) == words
    assert ", ".join(R.prompt_words(words, [], {})) == ", ".join(words)


def test_a_short_list_with_stars_puts_them_first_and_leaves_the_rest_in_order():
    words = ["a1", "b2", "c3", "d4"]
    assert R.prompt_words(words, ["c3", "b2"], {"d4": 99}) == ["b2", "c3", "a1", "d4"]


def test_a_long_list_is_cut_to_the_entry_cap_starred_first_then_most_used():
    words = [f"Word{i:03d}" for i in range(300)]
    usage = {"word250": 40, "word100": 9, "word101": 9, "word007": 1}
    got = R.prompt_words(words, ["Word299"], usage, max_entries=5, max_chars=10_000)
    assert got == ["Word299", "Word250", "Word100", "Word101", "Word007"]


def test_it_is_cut_to_the_character_cap_too():
    words = ["x" * 30] * 1 + [f"{c}" * 30 for c in "abcdefghij"]
    got = R.prompt_words(words, [], {}, max_entries=100, max_chars=100)
    assert sum(len(w) for w in got) + 2 * (len(got) - 1) <= 100
    assert len(got) == 3


def test_a_word_too_long_for_what_is_left_is_passed_over_not_a_reason_to_stop():
    words = ["a" * 50, "b" * 50, "c" * 10, "d" * 10]
    got = R.prompt_words(words, [], {}, max_entries=100, max_chars=75)
    assert got == ["a" * 50, "c" * 10, "d" * 10]


def test_ties_keep_the_lists_own_order_so_the_prompt_is_the_same_every_time():
    words = [f"w{i}" for i in range(50)]
    first = R.prompt_words(words, [], {"w3": 1, "w9": 1}, max_entries=4, max_chars=1000)
    again = R.prompt_words(list(words), [], {"w9": 1, "w3": 1}, max_entries=4, max_chars=1000)
    assert first == again == ["w3", "w9", "w0", "w1"]


def test_usage_is_looked_up_in_any_case_and_only_when_the_list_has_to_be_cut():
    calls = []

    def usage():
        calls.append(1)
        return {"GROQ": 5}

    short = ["Groq", "Morta"]
    assert R.prompt_words(short, [], usage) == short
    assert calls == []                                   # nothing to rank, so nothing read
    long = ["Morta", "Groq", "x"]
    assert R.prompt_words(long, [], usage, max_entries=1, max_chars=100) == ["Groq"]
    assert calls == [1]


def test_usage_that_cannot_be_read_does_not_stop_the_prompt():
    def broken():
        raise OSError("history.json is locked")

    got = R.prompt_words(["a", "b", "c"], [], broken, max_entries=2, max_chars=100)
    assert got == ["a", "b"]


def test_junk_in_the_list_is_ignored():
    assert R.prompt_words(["a", None, 5, "", "  ", "b"], [], {}) == ["a", "b"]
    assert R.prompt_words(None, [], {}) == []
    assert R.prompt_words([], ["a"], {}) == []


def test_the_caps_are_what_a_whisper_prompt_can_use():
    # OpenAI's transcription reads about the last 224 tokens of a prompt; a name
    # is two to four of them, so a few hundred characters is all that can matter.
    assert R.PROMPT_MAX_ENTRIES <= 100
    assert 300 <= R.PROMPT_MAX_CHARS <= 1000


# ── after a save ─────────────────────────────────────────────────────────────

def test_sync_with_new_stars_replaces_them(tmp_path):
    f = tmp_path / "vocab_stars.json"
    assert R.sync_stars(["Groq", "Morta"], ["Morta"], f) == ["Morta"]
    assert json.loads(f.read_text(encoding="utf-8")) == ["Morta"]


def test_sync_without_stars_keeps_the_saved_ones_for_the_words_still_listed(tmp_path):
    f = tmp_path / "vocab_stars.json"
    f.write_text(json.dumps(["Groq", "Gone", "morta"]), encoding="utf-8")
    assert R.sync_stars(["Groq", "Morta"], None, f) == ["Groq", "Morta"]
    assert json.loads(f.read_text(encoding="utf-8")) == ["Groq", "Morta"]


def test_sync_does_not_write_when_nothing_changed(tmp_path):
    f = tmp_path / "vocab_stars.json"
    f.write_text(json.dumps(["Groq"]), encoding="utf-8")
    before = f.stat().st_mtime_ns
    assert R.sync_stars(["Groq", "Morta"], None, f) == ["Groq"]
    assert f.stat().st_mtime_ns == before
    assert R.sync_stars(["Groq"], None, tmp_path / "none.json") == []
    assert not (tmp_path / "none.json").exists()


def test_sync_leaves_an_unreadable_file_for_the_person_to_fix(tmp_path):
    f = tmp_path / "vocab_stars.json"
    f.write_bytes(b"{ not json")
    assert R.sync_stars(["Groq"], None, f) == []
    assert f.read_bytes() == b"{ not json"


def test_sync_with_stars_replaces_an_unreadable_file(tmp_path):
    f = tmp_path / "vocab_stars.json"
    f.write_bytes(b"{ not json")
    assert R.sync_stars(["Groq"], ["Groq"], f) == ["Groq"]
    assert json.loads(f.read_text(encoding="utf-8")) == ["Groq"]
