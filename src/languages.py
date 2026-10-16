"""Dictation languages: the single source of truth for the Settings picker, the
Whisper `language` parameter, and the cleanup prompt's "don't translate" rule.

Codes are the ISO-639-1 codes Whisper accepts. "auto" lets Whisper detect the language
per recording (best for people who switch languages, slightly less reliable on very
short clips than a fixed language).
"""
from __future__ import annotations

import re

AUTO = "auto"

LANGUAGES: list[tuple[str, str]] = [
    (AUTO, "Auto-detect"),
    ("en", "English"),
    ("es", "Spanish"), ("fr", "French"), ("de", "German"), ("it", "Italian"),
    ("pt", "Portuguese"), ("nl", "Dutch"), ("pl", "Polish"), ("ru", "Russian"),
    ("uk", "Ukrainian"), ("tr", "Turkish"), ("ar", "Arabic"), ("he", "Hebrew"),
    ("hi", "Hindi"), ("bn", "Bengali"), ("ur", "Urdu"), ("fa", "Persian"),
    ("ja", "Japanese"), ("ko", "Korean"), ("zh", "Chinese"), ("id", "Indonesian"),
    ("ms", "Malay"), ("vi", "Vietnamese"), ("th", "Thai"), ("sv", "Swedish"),
    ("no", "Norwegian"), ("da", "Danish"), ("fi", "Finnish"), ("cs", "Czech"),
    ("sk", "Slovak"), ("hu", "Hungarian"), ("ro", "Romanian"), ("bg", "Bulgarian"),
    ("el", "Greek"), ("hr", "Croatian"), ("sr", "Serbian"), ("sl", "Slovenian"),
    ("lt", "Lithuanian"), ("lv", "Latvian"), ("et", "Estonian"), ("ca", "Catalan"),
    ("ta", "Tamil"), ("te", "Telugu"), ("sw", "Swahili"), ("af", "Afrikaans"),
    ("cy", "Welsh"),
]

_NAMES = dict(LANGUAGES)


def is_valid(code: str) -> bool:
    return code in _NAMES


def language_name(code: str) -> str:
    return _NAMES.get(code, code)


def language_instruction(code: str) -> str | None:
    """Sentence for the cleanup prompt, or None when English needs no extra rule.

    The cleanup prompt is written in English, so without this a non-English transcript
    can come back translated. The rule is deliberately blunt.
    """
    if code in (None, "", "en"):
        return None
    if code == AUTO:
        return ("The speech may be in any language. Keep the output in the language that was "
                "spoken, using that language's normal spelling and punctuation. Never translate it.")
    name = language_name(code)
    return (f"The speech is in {name}. Keep the output in {name}, using {name} spelling and "
            f"punctuation. Never translate it into English.")


# Scripts written without spaces between words: Han (Chinese), kana (Japanese)
# and Thai. A count of whitespace-separated "words" says nothing about how
# much was said in them (a whole sentence is one "word"), so the checks that
# compare words with seconds of speech must not judge them.
_UNSPACED = re.compile(r"[\u0e00-\u0e7f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def is_unspaced(text: str) -> bool:
    """True when most of the letters in ``text`` are in a script written
    without spaces (Chinese, Japanese, Thai). Works on the text, not on the
    chosen language, so it also holds for Auto-detect."""
    letters = [c for c in str(text or "") if c.isalpha()]
    return bool(letters) and 2 * sum(1 for c in letters if _UNSPACED.match(c)) > len(letters)
