"""Dictation languages: the single source of truth for the Settings picker, the
Whisper `language` parameter, and the cleanup prompt's "don't translate" rule.

Codes are the ISO-639-1 codes Whisper accepts. "auto" lets Whisper detect the language
per recording (best for people who switch languages, slightly less reliable on very
short clips than a fixed language).
"""
from __future__ import annotations

import re

AUTO = "auto"
DEFAULT = "en"       # when the person has not picked and the computer cannot say

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


# ── the computer's own language ────────────────────────────────────────────
# A speech request that goes out with no language lets Whisper guess, and over
# silence or a cough it guesses Norwegian or Welsh and writes it down. So when
# the person has not picked a language (unset, empty), the computer's own is
# sent. A pick always wins, and "auto" (Auto-detect in Settings, "Detect
# automatically" on the Meetings page) is a pick: it is kept as the person's
# deliberate choice and sends no language.

# Codes a locale uses that Whisper writes another way.
_LOCALE_ALIASES = {"nb": "no", "nn": "no", "iw": "he", "in": "id", "fil": "tl", "ji": "yi"}


def _locale_names() -> list[str]:
    """Locale names the OS offers, most specific first, e.g. ["en_GB.UTF-8"]."""
    import os
    import sys
    names: list[str] = []
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        v = os.environ.get(var)
        if v:
            names.extend(p for p in v.split(":") if p)
    try:
        if sys.platform == "win32":
            import ctypes
            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
                names.append(buf.value)
        elif sys.platform == "darwin" and not names:
            import subprocess
            out = subprocess.run(["defaults", "read", "-g", "AppleLocale"], capture_output=True,
                                 text=True, timeout=3).stdout.strip()
            if out:
                names.append(out)
    except Exception:           # noqa: BLE001 - a missing answer is only "no language"
        pass
    try:
        import locale
        got = locale.getlocale()[0]
        if got:
            names.append(got)
    except Exception:           # noqa: BLE001
        pass
    return names


def system_language() -> str | None:
    """The computer's language as a Whisper code ("en" for en_GB), or None when
    it cannot be told or is one Whisper has no code for. Never raises."""
    try:
        for name in _locale_names():
            code = re.split(r"[_\-.@]", name.strip(), maxsplit=1)[0].lower()
            if code in ("c", "posix", ""):
                continue
            code = _LOCALE_ALIASES.get(code, code)
            if is_valid(code) and code != AUTO:
                return code
    except Exception:           # noqa: BLE001
        pass
    return None


def resolve(setting) -> str | None:
    """The language to send with a speech request, for what Settings holds:
    a picked language as it is; "auto" (picked on purpose) None, so the model
    detects; unset, empty or unreadable the computer's own language, or
    English (the app's own default) when that cannot be told: nobody is left
    to a guess they did not ask for."""
    if isinstance(setting, str):
        s = setting.strip()
        if s == AUTO:
            return None
        if s and is_valid(s):
            return s
        if s and re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z]{2,4})?", s):
            return s            # a code this list does not know: the person's own, as before
    return system_language() or DEFAULT


def effective(setting) -> str:
    """`resolve`, but as a code for the clean-up rules: "auto" when the person
    picked Auto-detect (the clean-up then keeps whatever language was spoken)."""
    return resolve(setting) or AUTO
