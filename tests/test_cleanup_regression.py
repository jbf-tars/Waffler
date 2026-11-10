"""The clean-up regression set (scripts/cleanup_regression.py) and its
fixture, checked offline: the script itself talks to the provider, so it is
not part of the suite, but its cases and its checker are."""
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "cleanup_regression", ROOT / "scripts" / "cleanup_regression.py")
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)

CASES = cr.load_cases()


def test_the_set_is_big_enough_and_covers_every_kind_of_failure():
    assert len(CASES) >= 40
    kinds = {c["cat"] for c in CASES}
    assert {"unpunctuated", "first-letter", "obeys-dictation", "filler", "self-correction",
            "numbers", "lists", "names-jargon", "british", "email", "short", "long",
            "questions"} <= kinds


def test_every_case_is_well_formed():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    for c in CASES:
        assert set(c) <= cr.CASE_KEYS, c["id"]
        assert c["text"].strip() and c["cat"], c["id"]
        assert set(c) & cr.CHECK_KEYS, f"{c['id']} checks nothing"
        for key in ("re", "not_re"):
            for pattern in c.get(key, ()):
                re.compile(pattern)
        if "lines_re_count" in c:
            re.compile(c["lines_re_count"][0])


def test_the_fixture_says_its_text_is_invented():
    about = json.loads(cr.FIXTURE.read_text(encoding="utf-8"))["_about"]
    assert "No real dictation text" in about


CASE = {"id": "X", "cat": "x", "text": "so the report is late and i need it by friday"}


@pytest.mark.parametrize("rules, output, wanted", [
    ({"has": ["friday"]}, "The report is late and I need it by Friday.", []),
    ({"has": ["monday"]}, "The report is late.", ["missing 'monday'"]),
    ({"lacks": ["so the"]}, "So the report is late.", ["has 'so the'"]),
    ({"has_cs": ["Friday"]}, "i need it by friday", ["missing (exact) 'Friday'"]),
    ({"lacks_cs": ["friday"]}, "I need it by Friday.", []),
    ({"re": [r"\AThe "]}, "The report.", []),
    ({"not_re": [r"^- "]}, "Intro:\n\n- one", ["matches /^- /"]),
    ({"starts_upper": True}, "the report is late.", ["starts with a lower-case letter"]),
    ({"starts_upper": True}, "575 grams of rice.", []),
    ({"starts_upper": True}, "\"The report\" is late.", []),
    ({"ends_with": ".?!"}, "The report is late", ["ends with 'e', not one of '.?!'"]),
    ({"max_paras": 1}, "One.\n\nTwo.", ["2 paragraphs (> 1)"]),
    ({"lines_re_count": [r"^\d\. ", 2]}, "1. One\n2. Two", []),
    ({"lines_re_count": [r"^\d\. ", 2]}, "One and two.", [r"0 lines match /^\d\. /, not 2"]),
])
def test_check(rules, output, wanted):
    assert cr.check({**CASE, **rules}, output) == wanted


def test_check_measures_length_and_punctuation():
    text = CASE["text"]
    assert cr.check({**CASE, "min_ratio": 0.8}, "The report is late.")[0].startswith("kept 0.36")
    assert cr.check({**CASE, "max_ratio": 1.1}, text + " " + text)[0].startswith("grew to 2.00")
    assert cr.check({**CASE, "min_punct": 0.05}, text)[0].startswith("0.000 punctuation")
    assert cr.check({**CASE, "min_punct": 0.05}, "The report is late, and I need it by Friday.") == []


def test_the_script_never_uses_the_real_data_folder():
    src = (ROOT / "scripts" / "cleanup_regression.py").read_text(encoding="utf-8")
    assert src.index('os.environ["WAFFLER_DATA_DIR"]') < src.index("import style_openai")
