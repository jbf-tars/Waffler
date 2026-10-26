"""The app a dictation is for is the one in front when the key went down (S5).

The real pipeline methods from app.py run against the fakes in
tests/_pipeline_harness.py: nothing reads a real window, Accessibility or the
clipboard. What is pinned:

* the app is captured at key-down and carried through the run, so what is
  in front when processing ends (or when the next press begins) never replaces it;
* the clean-up is handed it as a plain argument (S6 will use it);
* the Journal entry keeps its name, identifier and kind, every one optional;
* Try again and Redo give the clean-up the same app again, from the entry;
* the privacy copy says what is kept, once, on this computer only.
"""
import ast
import json
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import app_categories  # noqa: E402
import history_meta  # noqa: E402
import pipeline_watchdog as pw  # noqa: E402
import text_context  # noqa: E402
import unsent  # noqa: E402
from text_context import AppContext, UNKNOWN  # noqa: E402
from _pipeline_harness import (  # noqa: E402
    FakeTranscriber, Hang, fast_limits, history, make_pipeline, run_process, speech_wav,
)

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))

MAIL = AppContext("com.apple.mail", "Mail", "", "email")
SLACK = AppContext("com.tinyspeck.slackmacgap", "Slack", "", "work")
NOTES = AppContext("com.apple.Notes", "Notes", "", "other")


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    fast_limits(monkeypatch, TRANSCRIBE_DEADLINE_MIN_S=3.0, TRANSCRIBE_DEADLINE_BASE_S=3.0,
                TRANSCRIBE_DEADLINE_MAX_S=3.0, STYLE_DEADLINE_S=3.0)


@pytest.fixture
def in_front(monkeypatch):
    """What NSWorkspace would say is in front, changeable mid-test."""
    box = {"app": MAIL, "asked": 0}

    def frontmost_app(read_title=None):
        box["asked"] += 1
        return box["app"]
    monkeypatch.setattr(text_context, "frontmost_app", frontmost_app)
    return box


def _dictate(p, generation=None):
    worker = run_process(p, generation or p._current_press_id)
    worker.join(5)
    assert not worker.is_alive()
    p.is_recording = False          # the key came up
    return history(p)[-1]


def _press(p):
    p.on_hotkey_press()
    # A real take is held for a while; without this the tap check (shorter
    # than half a second) would throw it away.
    p._recording_start_time = time.time() - 3.0
    return p._current_press_id


def api_of(p, *names):
    api = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    nodes = [n for n in api.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    p.ns["_pipeline"] = p
    exec(compile(ast.Module(nodes, []), "<app.py Api>", "exec"), p.ns)
    return types.SimpleNamespace(**{n: (lambda f: lambda *a, **k: f(None, *a, **k))(p.ns[n]) for n in names})


# ── captured at key-down, not at processing time ─────────────────────────────

def test_the_app_is_the_one_in_front_when_the_key_went_down(tmp_path, in_front):
    p = make_pipeline(tmp_path)
    press = _press(p)
    assert p._press_context == (press, MAIL) and in_front["asked"] == 1
    in_front["app"] = SLACK                  # the user switched while the words were being made
    entry = _dictate(p, press)
    assert (entry["app"], entry["app_bundle"], entry["category"]) == ("Mail", "com.apple.mail", "email")
    assert in_front["asked"] == 1, "processing never asks again"
    assert p.styler.contexts == [MAIL], "the clean-up is told the same app"


def test_a_newer_press_while_a_dictation_is_processing_does_not_rename_it(tmp_path, in_front):
    hang = Hang(10)
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(hang=hang))
    press = _press(p)
    worker = run_process(p, press)
    assert hang.entered.wait(5)                       # transcribing
    in_front["app"] = SLACK
    p._press_context = (press + 1, SLACK)             # the next dictation began in Slack
    hang.release.set()
    worker.join(5)
    entry = history(p)[-1]
    assert (entry["app"], entry["category"]) == ("Mail", "email")
    assert p.styler.contexts == [MAIL]


def test_a_record_that_belongs_to_another_press_is_not_claimed(tmp_path):
    p = make_pipeline(tmp_path)
    p._press_context = (99, SLACK)                    # a newer press than the one processed
    entry = _dictate(p)
    assert "app" not in entry and "category" not in entry
    assert p.styler.contexts == [UNKNOWN]


def test_the_record_is_kept_for_the_press_it_belongs_to(tmp_path):
    p = make_pipeline(tmp_path)
    p._press_context = (5, MAIL)
    assert p._press_target(5) is MAIL
    assert p._press_target(6) == UNKNOWN and p._press_target(4) == UNKNOWN


def test_the_name_only_shape_from_before_is_still_understood(tmp_path):
    p = make_pipeline(tmp_path)
    p._press_app = (3, "Notes")
    ctx = p._press_target(3)
    assert (ctx.name, ctx.bundle_id, ctx.category) == ("Notes", "", "other")
    p._press_app = (3, "Mail")
    assert p._press_target(3).category == "email"
    assert p._press_target(2) == UNKNOWN
    p._press_app = (3, "")
    assert p._press_target(3) == UNKNOWN


def test_the_context_wins_over_the_older_name_record(tmp_path):
    p = make_pipeline(tmp_path)
    p._press_app = (3, "Notes")
    p._press_context = (3, SLACK)
    assert p._press_target(3) is SLACK


def test_a_lookup_that_fails_at_key_down_never_stops_the_recording(tmp_path, monkeypatch):
    def boom(read_title=None):
        raise RuntimeError("no window server")
    monkeypatch.setattr(text_context, "frontmost_app", boom)
    p = make_pipeline(tmp_path)
    press = _press(p)
    assert p.is_recording and p._press_context == (press, UNKNOWN)
    assert p.audio.starts == 1, "the recording started anyway"
    assert "app" not in _dictate(p, press)


def test_the_app_is_read_before_the_overlay_can_take_focus():
    klass = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    press = ast.unparse(next(n for n in klass.body
                             if isinstance(n, ast.FunctionDef) and n.name == "on_hotkey_press"))
    assert press.index("_text_context.frontmost_app()") < press.index("self._start_audio(")
    assert press.index("_text_context.frontmost_app()") < press.index("self._show_recording_pill")


# ── handed to the clean-up as a plain argument ───────────────────────────────

def test_the_clean_up_is_handed_the_app_as_a_keyword_argument(tmp_path, in_front):
    p = make_pipeline(tmp_path)
    _dictate(p, _press(p))
    assert p.styler.contexts == [MAIL]


def test_with_no_app_known_the_clean_up_is_handed_unknown_not_nothing(tmp_path):
    p = make_pipeline(tmp_path)
    _dictate(p)
    assert p.styler.contexts == [UNKNOWN]


def test_the_run_carries_the_app_for_the_stuck_handler(tmp_path, in_front):
    p = make_pipeline(tmp_path)
    seen = []
    real_begin = p._watchdog.begin

    def begin(gen):
        run = real_begin(gen)
        seen.append(run)
        return run
    p._watchdog.begin = begin
    _dictate(p, _press(p))
    assert seen[0].app_context == MAIL
    assert pw.DictationRun("x").app_context is None


def test_the_styler_accepts_the_app_and_does_not_change_its_answer_for_it():
    import inspect
    import style_openai
    sig = inspect.signature(style_openai.OpenAIStyler.style)
    assert list(sig.parameters) == ["self", "transcript", "app_context"]
    assert sig.parameters["app_context"].default is None


# ── the Journal entry ────────────────────────────────────────────────────────

def test_the_entry_keeps_name_identifier_and_kind(tmp_path, in_front):
    p = make_pipeline(tmp_path)
    in_front["app"] = SLACK
    entry = _dictate(p, _press(p))
    assert (entry["app"], entry["app_bundle"], entry["category"]) == (
        "Slack", "com.tinyspeck.slackmacgap", "work")
    assert "title" not in entry and not any("title" in k for k in entry)


def test_a_browser_tabs_title_helped_decide_the_kind_but_is_never_kept(tmp_path, in_front):
    p = make_pipeline(tmp_path)
    in_front["app"] = AppContext("com.apple.Safari", "Safari", "Re: my salary - me@example.com - Gmail", "email")
    entry = _dictate(p, _press(p))
    assert entry["app"] == "Safari" and entry["category"] == "email"
    assert "salary" not in json.dumps(history(p))
    assert "salary" not in repr(in_front["app"])


def test_every_new_field_is_optional(tmp_path):
    p = make_pipeline(tmp_path)
    entry = _dictate(p)
    for key in ("app", "app_bundle", "category"):
        assert key not in entry, key
    assert entry["styled"] and entry["id"]                  # and nothing else is missing


def test_windows_gives_a_name_and_a_kind_without_a_bundle_id(tmp_path):
    p = make_pipeline(tmp_path)
    p._press_context = (1, text_context.make_context("", "Slack"))
    entry = _dictate(p, 1)
    assert entry["app"] == "Slack" and entry["category"] == "work" and "app_bundle" not in entry


def test_take_fields_validates_what_it_keeps():
    f = history_meta.take_fields
    assert f(app="Mail", app_bundle="com.apple.mail", category="email") == {
        "app": "Mail", "app_bundle": "com.apple.mail", "category": "email"}
    assert f(app="Mail", category="nonsense") == {"app": "Mail"}, "an unknown kind is not stored"
    assert f(category="email") == {}, "a kind with no app means nothing"
    assert f(app_bundle="x" * 500)["app_bundle"] == "x" * history_meta.APP_BUNDLE_MAX
    assert f(app=5, app_bundle=["x"], category=["email"]) == {}
    assert f(app="  Mail \n", app_bundle=" com.apple.mail ") == {"app": "Mail", "app_bundle": "com.apple.mail"}
    for kind in app_categories.CATEGORIES:
        assert f(app="A", category=kind)["category"] == kind


def test_context_of_rebuilds_the_app_from_an_entry():
    ctx = history_meta.context_of({"app": "Mail", "app_bundle": "com.apple.mail", "category": "email"})
    assert ctx == AppContext("com.apple.mail", "Mail", "", "email")
    assert history_meta.context_of({"app": "Slack"}).category == "work", "derived when not stored"
    assert history_meta.context_of({"app": "Mail", "category": "bogus"}).category == "email"
    for entry in ({}, {"text": "x"}, {"app": ""}, None, "junk", 5, {"app": 5}):
        assert history_meta.context_of(entry) is None, entry


def test_redo_keeps_the_app_it_was_dictated_in():
    old = {"id": 4, "timestamp": "t", "audio": "rec-1.wav", "app": "Mail", "app_bundle": "com.apple.mail",
           "category": "email", "duration_s": 3.0, "quality": {"level": "check"}, "paste": "failed"}
    new = history_meta.redone_entry(old, transcript="a", styled="A.")
    for key in ("app", "app_bundle", "category", "id", "audio"):
        assert new[key] == old[key], key
    assert "quality" not in new and "paste" not in new


# ── Not sent cards, Try again and Redo ───────────────────────────────────────

def _failing(tmp_path, in_front):
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(error=ConnectionError("Connection error.")))
    in_front["app"] = SLACK
    card = _dictate(p, _press(p))
    return p, card


def test_a_not_sent_card_says_which_app_the_take_was_for(tmp_path, in_front):
    p, card = _failing(tmp_path, in_front)
    assert card["failed"] and (card["app"], card["app_bundle"], card["category"]) == (
        "Slack", "com.tinyspeck.slackmacgap", "work")


def test_try_again_gives_the_clean_up_the_same_app_and_keeps_it_in_the_entry(tmp_path, in_front):
    p, card = _failing(tmp_path, in_front)
    in_front["app"] = NOTES                                  # the user is elsewhere by now
    p.transcriber.error = None
    result = p.resend_unsent(card["unsent_id"])
    assert result["ok"]
    assert p.styler.contexts == [SLACK]
    item = result["item"]
    assert item["app"] == "Slack" and item["category"] == "work" and "failed" not in item


def test_a_card_from_before_this_has_no_app_and_still_resends(tmp_path):
    p = make_pipeline(tmp_path, transcriber=FakeTranscriber(error=ConnectionError("Connection error.")))
    card = _dictate(p)
    for key in ("app", "app_bundle", "category"):
        card.pop(key, None)
    (tmp_path / "history.json").write_text(json.dumps([card]), encoding="utf-8")
    p.transcriber.error = None
    assert p.resend_unsent(card["unsent_id"])["ok"]
    assert p.styler.contexts == [None], "no app recorded, so none is claimed"


def test_the_stuck_handler_puts_the_app_on_the_card(tmp_path):
    p = make_pipeline(tmp_path)
    run = types.SimpleNamespace(audio_bytes=speech_wav(1.0), transcript=None, saved_as_unsent=False,
                                audio_name="", app_context=MAIL)
    p._run_is_current = lambda r: False
    p._ui_stuck(run)
    [card] = history(p)
    assert card["not_sent_reason"] == unsent.REASON_STUCK and card["app"] == "Mail"
    assert card["category"] == "email"


def test_redo_gives_the_clean_up_the_entrys_app(tmp_path, in_front, monkeypatch):
    import history_ops
    monkeypatch.setattr(history_ops, "SHELF", history_ops.UndoShelf(window_s=60, timer=False))
    p = make_pipeline(tmp_path)
    in_front["app"] = SLACK
    entry = _dictate(p, _press(p))
    in_front["app"] = NOTES
    r = api_of(p, "redo_history_item").redo_history_item(entry["id"])
    assert r["ok"], r
    assert p.styler.contexts == [SLACK, SLACK]
    assert r["item"]["app"] == "Slack" and r["item"]["app_bundle"] == "com.tinyspeck.slackmacgap"
    assert r["item"]["category"] == "work"


# ── privacy copy ─────────────────────────────────────────────────────────────

def _flat(path):
    return " ".join((ROOT / path).read_text(encoding="utf-8").split())


def test_the_privacy_page_says_the_app_and_its_kind_are_kept_here():
    sentence = ("Each notes the app you were in, the language and which services handled it. "
                "The app is kept with its kind (email, work chat, personal chat or other), on this computer only.")
    assert sentence in _flat("ui/index.html")
    assert sentence in _flat("ui/app.js")


def test_the_new_privacy_sentences_have_no_dashes_and_no_jargon():
    for text in (_flat("ui/index.html"), _flat("ui/app.js")):
        start = text.find("The app is kept with its kind")
        if start >= 0:
            sentence = text[start:start + 120]
            assert "\u2014" not in sentence and "\u2013" not in sentence
            for jargon in ("bundle", "AX", "category"):
                assert jargon not in sentence
