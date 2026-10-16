"""Journal chips stay honest with the Style section (S6).

* Clean-up Off is a choice, not a failure: provider "off" lights no chip for
  "the clean-up did not run", for "the clean-up dropped words" or for "ends
  without a full stop" (it is what was heard). A real cut-off (a take that
  stops on "the") is still said.
* A style that drops the last full stop on purpose (casual, very casual) must
  not make every card say "Ends without a full stop, so the last words may be
  missing".
* A signature at the end of an email is not the end of what was said, so the
  checks run on the words before it.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quality import assess  # noqa: E402


def _clean(**over):
    base = dict(
        speech_seconds=20.0, transcript_words=50, styled_words=50, asr_filtered=False,
        styling_provider="groq", styled_text="This is a normal dictation that ends properly.",
        retry_fired=False, deadline_fired=False,
    )
    base.update(over)
    return base


def test_off_is_quiet_for_what_whisper_wrote():
    r = assess(**_clean(styling_provider="off", styled_text="so this is what I said with no full stop at the end"))
    assert r["level"] == "ok" and r["flags"] == [], r


def test_off_is_not_a_clean_up_fallback_or_a_drop():
    r = assess(**_clean(styling_provider="off", transcript_words=60, styled_words=20, speech_seconds=25.0))
    assert "styling_fallback" not in r["flags"] and "styled_dropped_words" not in r["flags"], r
    assert r["level"] == "ok"


def test_off_still_says_when_the_take_stops_on_a_dangling_word():
    r = assess(**_clean(styling_provider="off", styled_text="and then we should probably send it to the"))
    assert r["flags"] == ["truncated_midsentence"] and r["level"] == "low"


def test_off_still_says_when_far_too_few_words_came_back():
    r = assess(**_clean(styling_provider="off", speech_seconds=57.0, transcript_words=18, styled_words=18))
    assert "low_word_rate" in r["flags"] and r["level"] == "low"


def test_a_failed_clean_up_is_still_a_failure():
    assert "styling_fallback" in assess(**_clean(styling_provider="basic_clean"))["flags"]
    assert "styling_fallback" in assess(**_clean(styling_provider="local"))["flags"]


def test_a_dropped_last_full_stop_on_purpose_is_not_a_missing_ending():
    text = "sounds good I will send it over in a bit"
    assert "unterminated_ending" in assess(**_clean(styled_text=text))["flags"]
    r = assess(**_clean(styled_text=text, ending_dropped_on_purpose=True))
    assert r["level"] == "ok" and r["flags"] == [], r


def test_a_dropped_stop_does_not_hide_a_real_cut_off():
    r = assess(**_clean(styled_text="sounds good I will send it over to the", ending_dropped_on_purpose=True))
    assert r["flags"] == ["truncated_midsentence"]


def test_the_default_is_unchanged_for_every_other_provider():
    for provider in ("groq", "openai", "cerebras", "snippet", "basic_clean", ""):
        for text in ("Fine.", "This ends well.", "this does not end well at all", "ends on the"):
            a = assess(**_clean(styling_provider=provider, styled_text=text))
            b = assess(**_clean(styling_provider=provider, styled_text=text, ending_dropped_on_purpose=False))
            assert a == b


@pytest.mark.parametrize("provider", ["Off", "OFF"])
def test_the_tag_is_read_in_any_case_like_the_others(provider):
    r = assess(**_clean(styling_provider=provider, styled_text="no full stop here at all friends"))
    assert r["flags"] == []
