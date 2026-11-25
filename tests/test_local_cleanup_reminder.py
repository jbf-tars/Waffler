"""The model on this computer gets a short reminder of the rules it misses.

It reads the same clean-up prompt as the providers, but it is small (Gemma 4
E4B, 4-bit), and in about 5,600 tokens of rules it often missed three:
self-corrections ("Tuesday, no, sorry, Wednesday" kept whole), pause fillers
left in, and long sentences with no full stop. Measured on Windows (25 Nov)
on made-up dictations, a short reminder placed just before the transcript
made it drop the wrong half of a correction, keep "actually" and a clarifying
"I mean", and leave lists and emails as they were; the project's corpus
(scripts/auto_test_corpus.py, 107 cases) passed as often as before. Placed
before the transcript, it is part of the prompt the model reads ahead of the
first dictation (the primer), so it costs nothing per dictation. The
providers do not get it.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import style_openai  # noqa: E402


def _styler():
    for k in [k for k in os.environ if k.endswith("_API_KEY")]:
        os.environ.pop(k, None)
    return style_openai.OpenAIStyler()


def _prompt(styler, transcript):
    return styler.prompt_template.format(transcript=transcript,
                                         dialect_instruction=style_openai._dialect_instruction({}))


def test_the_reminder_sits_just_before_the_transcript():
    styler = _styler()
    said = "can we meet on tuesday no sorry wednesday at three"
    local = style_openai.OpenAIStyler._for_local(_prompt(styler, said))
    assert local.count(style_openai._LOCAL_REMINDER) == 1
    assert local.rstrip("\n").endswith("Transcript: " + said)     # the words stay last
    assert local.index(style_openai._LOCAL_REMINDER) < local.rindex("Transcript: ")


def test_the_reminder_names_the_three_rules_and_what_to_keep():
    r = style_openai._LOCAL_REMINDER
    for words in ("SELF-CORRECTION", "FILLER", "full stop", "I actually liked it",
                  "Sorry for the late reply", "So development can start"):
        assert words in r, words


def test_the_primer_reads_the_reminder_ahead_of_the_first_dictation():
    styler = _styler()
    system, primer = styler._local_primer()
    local = style_openai.OpenAIStyler._for_local(_prompt(styler, "a real dictation"))
    shared = os.path.commonprefix([primer, local])
    assert style_openai._LOCAL_REMINDER in shared                 # read once, kept by the engine
    assert shared.endswith("Transcript: ")


def test_a_prompt_with_no_transcript_marker_still_gets_it():
    assert style_openai.OpenAIStyler._for_local("Tidy this: hello") == \
        "Tidy this: hello" + style_openai._LOCAL_REMINDER


def test_the_providers_are_not_given_it():
    src = (ROOT / "src" / "style_openai.py").read_text(encoding="utf-8")
    for name in ("_style_groq", "_style_cerebras", "_style_openai"):
        start = src.index(f"    def {name}(")
        body = src[start:src.index("\n    def ", start + 10)]
        assert "_for_local" not in body and "_LOCAL_REMINDER" not in body, name
