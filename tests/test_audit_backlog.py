"""The small cleanup backlog that came with the audit.

1. The setup practice dictation honours the clean-up Off switch.
2. An unsent recording's id with a newline after it is not an id.
3. A result that was only copied is not what "Paste last" returns.
4. A refused save shows the app's own plain sentence.
6. A long take's edit fits in the limit.
7. A future-dated entry is not this month's words.
8. The Vocabulary page asks for its one switch, not for every setting.
9. The Hands-free blurb is true with double-tap on and off.
10. The recent recordings are held to a total size as well as a count.
11. The docs describe the real chain.
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import threading
import types
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import first_run  # noqa: E402
import style_layer  # noqa: E402
import history_edit  # noqa: E402
import journal_data  # noqa: E402
import recent_audio  # noqa: E402
import shortcut_settings as ss  # noqa: E402
import unsent  # noqa: E402
import user_messages as um  # noqa: E402

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")
_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


def js(expr):
    code = (f"const L = require({json.dumps(str(ROOT / 'ui' / 'logic.js'))});\n"
            f"Promise.resolve({expr}).then((v) => process.stdout.write(JSON.stringify(v === undefined ? null : v)));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── 1. the setup practice and the Off switch ─────────────────────────────────

class Styler:
    def __init__(self):
        self.calls = []

    def style(self, text):
        self.calls.append(text)
        return text.capitalize() + ".", {"input_tokens": 12, "output_tokens": 9, "api_used": True,
                                         "provider": "groq"}


def run_practice(settings, styler, said="  um so this is the practice  "):
    """app.py's own ``_wizard_finish_practice``, run with fakes: what it shows
    the setup page, what it saves, what it records."""
    node = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_wizard_finish_practice")
    pushed, saved, usage = [], [], []
    ns = {"_wizard_styler": styler, "_read_settings_now": lambda: settings, "_first_run": first_run,
          "_style_layer": style_layer, "_push_wizard_js": lambda fn, payload: pushed.append((fn, payload)),
          "record_usage_safely": lambda *a, **k: usage.append((a, k)),
          "append_history_safely": lambda e: saved.append(e) or True, "_log_to_file": lambda m: None,
          "os": os, "json": json}
    exec(compile(ast.Module([node], []), "<app.py>", "exec"), ns)
    ns["_wizard_finish_practice"](said, 3.0)
    result = dict(pushed)["wizOnPracticeResult"]
    return result, saved, usage


def test_with_cleanup_off_the_practice_asks_no_model_and_shows_the_words_as_said():
    styler = Styler()
    result, saved, usage = run_practice({"cleanup": "off"}, styler)
    assert styler.calls == [], "clean-up is off: no model is asked"
    assert result["wrote"] == result["said"] == "um so this is the practice"
    assert result["cleaned"] is True and result["note"] == "", "off is a choice, not a failure"
    assert saved[0]["styled"] == "um so this is the practice" and saved[0]["source"] == "setup"
    assert [a[0][0] for a in usage] == ["whisper"], "speech is counted; no clean-up tokens were used"


def test_with_cleanup_on_the_practice_is_cleaned_as_before():
    styler = Styler()
    result, saved, usage = run_practice({}, styler, "hello there")
    assert styler.calls == ["hello there"] and result["wrote"] == "Hello there."
    assert [a[0][0] for a in usage] == ["whisper", "gpt"]
    assert run_practice({"cleanup": "on"}, Styler(), "x")[0]["wrote"] == "X."
    assert run_practice({"cleanup": "OFF"}, Styler(), "x")[0]["wrote"] == "X.", "exactly 'off', as for dictations"


def test_with_no_provider_and_cleanup_on_the_practice_falls_back_to_the_plain_tidy():
    result, _saved, _usage = run_practice({}, None, "hello there")
    assert result["wrote"] == "hello there" and result["cleaned"] is False and result["note"]


def test_with_no_provider_and_cleanup_off_there_is_nothing_to_fail():
    result, _saved, _usage = run_practice({"cleanup": "off"}, None, "hello there")
    assert result["wrote"] == "hello there" and result["cleaned"] is True and result["note"] == ""


def test_a_settings_file_that_cannot_be_read_leaves_the_clean_up_on():
    styler = Styler()
    node = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_wizard_finish_practice")
    pushed = []

    def broken():
        raise OSError("settings.json gone")
    ns = {"_wizard_styler": styler, "_read_settings_now": broken, "_first_run": first_run, "_style_layer": style_layer,
          "_push_wizard_js": lambda fn, payload: pushed.append((fn, payload)),
          "record_usage_safely": lambda *a, **k: None, "append_history_safely": lambda e: True,
          "_log_to_file": lambda m: None, "os": os, "json": json}
    exec(compile(ast.Module([node], []), "<app.py>", "exec"), ns)
    ns["_wizard_finish_practice"]("hello there", 3.0)
    assert styler.calls == ["hello there"]


# ── 2. the unsent id ─────────────────────────────────────────────────────────

GOOD_ID = "recording-2026-10-16T09-30-12.wav"


def test_an_unsent_id_is_exactly_the_shape_and_nothing_after_it():
    assert unsent.is_valid_id(GOOD_ID) and unsent.is_valid_id("recording-2026-10-16T09-30-12-3.wav")
    for bad in (GOOD_ID + "\n", GOOD_ID + "\r\n", "\n" + GOOD_ID, GOOD_ID + " ", GOOD_ID + "x",
                "../" + GOOD_ID, "", None, 5):
        assert unsent.is_valid_id(bad) is False, repr(bad)


# ── 3. what "Paste last" can return ──────────────────────────────────────────

def test_a_result_that_was_only_copied_is_not_remembered_but_a_failed_paste_is():
    """The words of a failed paste are what the failed-paste message's Copy
    again button copies, so they are remembered; text that was put on the
    clipboard for the person to paste is already there, and Paste last stays
    the last dictation."""
    import test_command_action as tca
    import test_transform_action as tta
    # Command mode: a draft over a selection, and the person moving on.
    rig = tca.Rig()
    rig.chat_text = "ACTION: draft\nYes, Thursday works for me."
    rig.spoken = "reply saying yes"
    rig.go()
    assert rig.copies and rig.remembered == []
    rig = tca.Rig()
    block = threading.Event()
    rig.chat_block = block
    rig.press()
    assert tca.wait_for(lambda: "begin" in rig.ui)
    rig.let_go()
    assert tca.wait_for(lambda: rig.chats)
    rig.front = tca.MAIL                              # the person moved on while the model worked
    block.set()
    assert tca.wait_for(rig.finished)
    assert rig.saved and rig.saved[0]["paste"] == "copied" and rig.remembered == []
    rig = tca.Rig(settings={"auto_paste": False})
    rig.go()
    assert rig.copies and rig.remembered == []
    # Transforms: the same three.
    rig = tta.Rig(settings={"auto_paste": False, "transforms": {"enabled": True}})
    rig.go()
    assert rig.copies and rig.remembered == []
    rig = tta.Rig()

    def chat(system, user, **kw):
        rig.chats.append(kw)
        rig.front = tta.MAIL                          # the person switched while the model thought
        return rig.chat_result
    rig.runner.h.chat = chat
    rig.go()
    assert rig.saved[0]["paste"] == "copied" and rig.remembered == []


def test_a_failed_paste_is_still_remembered_for_copy_again():
    import pasteboard
    import test_command_action as tca
    rig = tca.Rig()
    rig.paste_outcome = pasteboard.PasteOutcome(pasteboard.FAILED, "keystroke_error", on_clipboard=True)
    rig.go()
    assert rig.remembered == [tca.SHORT]


# ── 4. a refused save says why ───────────────────────────────────────────────

@needs_node
def test_the_apps_own_sentence_is_shown_for_a_refused_save():
    for sentence in ("Keep it to 3 words or fewer.", "Choose a key that isn't already a shortcut.",
                     "A trigger can be up to 60 characters.", "Pick JSON, CSV or Plain text."):
        assert js(f"L.saveFailedMessage({{ok: false, error: {json.dumps(sentence)}}})") == sentence


@needs_node
@pytest.mark.parametrize("raw", [
    "invalid literal for int() with base 10: 'f²'", "ValueError: nope.", "'NoneType' object has no attribute 'get'",
    "", "   ", "x" * 300 + ".", "Traceback (most recent call last):", "line one.\nline two.", "{'a': 1}.",
    "lower case start.", "no full stop at the end"])
def test_anything_that_is_not_a_plain_sentence_gets_the_generic_line(raw):
    assert js(f"L.saveFailedMessage({{ok: false, error: {json.dumps(raw)}}})") == "Couldn't save that. Try again."


@needs_node
def test_no_error_at_all_gets_the_generic_line():
    for given in ("null", "undefined", "{ok: false}", "{error: 5}", "{error: ['a.']}"):
        assert js(f"L.saveFailedMessage({given})") == js("L.SAVE_FAILED") == "Couldn't save that. Try again."


def test_save_setting_shows_the_sentence_the_app_gave():
    src = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    body = src[src.index("async function saveSetting(key, value)"):]
    body = body[:body.index("\n}\n")]
    assert "WL.saveFailedMessage(r)" in body and body.count("Couldn't save that") == 0
    assert "WL.SAVE_FAILED" in body, "an exception still says the generic line"


# ── 6. the longest take can be edited ────────────────────────────────────────

def test_an_edit_of_a_fast_twenty_minute_take_fits():
    fast_take = ("word " * 5000).strip()                 # about 25,000 characters
    assert len(fast_take) > 20000
    text, reason = history_edit.clean(fast_take + " and a correction")
    assert reason == "" and text.endswith("a correction")
    assert history_edit.clean("x" * history_edit.MAX_EDIT_CHARS)[1] == ""
    assert history_edit.clean("x" * (history_edit.MAX_EDIT_CHARS + 1)) == ("", "too_long")
    assert history_edit.MAX_EDIT_CHARS == 60000
    assert "60,000" in um.EDIT_TOO_LONG and "20,000" not in um.EDIT_TOO_LONG


# ── 7. a future date is not this month's words ───────────────────────────────

def entry(day, words=10, **kw):
    e = {"timestamp": f"{day}T10:00:00", "text": " ".join(["w"] * words), "styled": " ".join(["w"] * words),
         "word_count": words}
    e.update(kw)
    return e


def test_an_entry_dated_after_today_is_not_in_the_months_words_or_count():
    today = date(2026, 10, 16)
    stats = journal_data.compute_stats([entry("2026-10-03", 10), entry("2026-10-16", 5), entry("2026-10-20", 400),
                                        entry("2026-10-31", 70)], today)
    assert stats["month_words"] == 15 and stats["month_count"] == 2
    assert stats["words_this_month"] == 15
    assert stats["total_words"] == 485, "the Journal still holds them all"


def test_the_month_still_counts_every_day_up_to_today():
    today = date(2026, 10, 16)
    stats = journal_data.compute_stats([entry("2026-10-01", 1), entry("2026-10-15", 2), entry("2026-10-16", 4),
                                        entry("2026-09-30", 8)], today)
    assert stats["month_words"] == 7 and stats["month_count"] == 3


# ── 8. the Vocabulary page's one switch ──────────────────────────────────────

def test_the_vocabulary_page_asks_for_its_switch_not_for_every_setting():
    src = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    body = src[src.index("async function loadLearnSwitch()"):]
    body = body[:body.index("\n}\n")]
    assert "get_learn_from_edits()" in body and "get_settings" not in body


@pytest.mark.parametrize("stored,want", [({}, True), ({"learn_from_edits": True}, True),
                                         ({"learn_from_edits": False}, False), ({"learn_from_edits": "false"}, True),
                                         ({"learn_from_edits": 0}, True), ({"learn_from_edits": None}, True)])
def test_the_light_call_reads_the_switch_as_get_settings_does(stored, want):
    node = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == "get_learn_from_edits")
    ns = {}
    exec(compile(ast.Module([node], []), "<app.py Api>", "exec"), ns)

    class Api:
        def _load_settings_file(self):
            return stored
    assert ns["get_learn_from_edits"](Api()) == {"ok": True, "on": want}


def test_the_light_call_never_raises():
    node = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == "get_learn_from_edits")
    ns = {}
    exec(compile(ast.Module([node], []), "<app.py Api>", "exec"), ns)

    class Api:
        def _load_settings_file(self):
            raise OSError("disk")
    assert ns["get_learn_from_edits"](Api()) == {"ok": True, "on": True}


# ── 9. the Hands-free blurb ──────────────────────────────────────────────────

def test_the_hands_free_blurb_does_not_promise_a_bare_press():
    blurb = ss._HANDS_FREE_BLURB
    assert "press the hotkey and Space together" in blurb
    assert "Tap the hotkey again" not in blurb
    assert "—" not in blurb and "–" not in blurb and blurb.endswith(".")
    for platform in (ss.ah.MAC, ss.ah.WINDOWS):
        row = next(r for r in ss.rows({}, platform) if r["id"] == "hands_free")
        assert row["description"] == blurb


# ── 10. recent recordings: a size as well as a count ─────────────────────────

def put(folder, name, size, mtime):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(b"\0" * size)
    os.utime(p, (mtime, mtime))
    return p


def test_ten_ordinary_takes_are_all_kept(tmp_path):
    for i in range(12):
        recent_audio.keep(tmp_path, b"\1" * 200_000, {}, now=datetime(2026, 10, 16, 9, 0, i))
    assert len(recent_audio.files(tmp_path)) == recent_audio.KEEP


def test_the_newest_takes_that_fit_are_kept_when_they_are_long(tmp_path):
    folder = recent_audio.folder(tmp_path)
    for i in range(9):
        put(folder, f"rec-20261016-0900{i:02d}.wav", 40_000_000, 1000 + i)
    recent_audio.keep(tmp_path, b"\2" * 40_000_000, {}, now=datetime(2026, 10, 16, 9, 30, 0))
    kept = recent_audio.files(tmp_path)
    total = sum(p.stat().st_size for p in kept)
    assert total <= recent_audio.MAX_TOTAL_BYTES
    assert len(kept) == 7, "seven 40 MB takes fit in 300 MB"
    assert kept[-1].name == "rec-20261016-093000.wav", "the latest take is the one just kept"
    assert [p.name for p in kept[:-1]] == [f"rec-20261016-0900{i:02d}.wav" for i in range(3, 9)], \
        "the oldest went first"


def test_the_latest_take_is_kept_even_when_it_is_over_the_size_alone(tmp_path):
    folder = recent_audio.folder(tmp_path)
    put(folder, "rec-20261016-090000.wav", 1000, 1000)
    path = recent_audio.keep(tmp_path, b"\3" * 5000, {}, now=datetime(2026, 10, 16, 9, 1, 0), max_bytes=2000)
    assert [p.name for p in recent_audio.files(tmp_path)] == [path.name]


def test_pruning_by_size_does_not_touch_other_files_or_fail_on_a_vanished_one(tmp_path):
    folder = recent_audio.folder(tmp_path)
    put(folder, "rec-20261016-090000.wav", 1000, 1000)
    (folder / "notes.txt").write_text("keep me", encoding="utf-8")
    recent_audio.keep(tmp_path, b"\4" * 1500, {}, now=datetime(2026, 10, 16, 9, 1, 0), max_bytes=2000)
    assert (folder / "notes.txt").exists()
    assert len(recent_audio.files(tmp_path)) == 1


def test_switched_off_nothing_is_kept_or_pruned(tmp_path):
    assert recent_audio.keep(tmp_path, b"x" * 100, {"keep_recent_audio": False}) is None
    assert not recent_audio.folder(tmp_path).exists()


def test_the_promise_in_settings_mentions_the_size():
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    js_src = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    assert "fewer if they are very long" in html and "300 MB" in html
    assert "fewer if they are very long" in js_src
    assert recent_audio.MAX_TOTAL_BYTES == 300 * 1024 * 1024
    assert recent_audio.summary(Path("/nonexistent"), {})["keep"] == 10


# ── 11. the docs describe the real chain ─────────────────────────────────────

def test_the_styler_docstring_describes_the_real_chain():
    import style_openai
    doc = style_openai.__doc__
    assert "Llama" not in doc.replace("ended when Groq retired", "") or "retired" in doc
    assert "gpt-oss-120b" in doc and "gpt-4.1-mini" in doc and "chose in Settings" in doc
    assert doc.index("Groq openai/gpt-oss-120b") < doc.index("OpenAI gpt-4.1-mini") < doc.index("Cerebras gpt-oss-120b")
    assert "Cerebras → Groq" not in doc and "free allowance" not in doc   # doc-drift-ok


def test_the_prompts_readme_describes_the_real_chain_and_the_real_corpus():
    text = (ROOT / "prompts" / "README.md").read_text(encoding="utf-8")
    assert "Groq → OpenAI → Cerebras" in text and "Groq → Cerebras → OpenAI" not in text   # doc-drift-ok
    assert "107 cases" in text and "101 cases" not in text
    assert "{dialect_instruction}" in text
    assert "100k tokens/day" not in text
    import importlib.util
    spec = importlib.util.spec_from_file_location("auto_test_corpus", ROOT / "scripts" / "auto_test_corpus.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert len(m.CORPUS) == 107, "the README names the real size of the corpus"


def test_nothing_under_prompts_but_the_readme_changed():
    import hashlib
    # The two prompts a model reads are exactly what the Style section's test pins.
    assert hashlib.sha256((ROOT / "prompts" / "normal.txt").read_bytes()).hexdigest() == \
        "14e2868af8da6836f08c9c4a308cd1a7d8ea08505c681883f7b4926d4d91b84c"
    assert hashlib.sha256((ROOT / "prompts" / "email.txt").read_bytes()).hexdigest() == \
        "f4619da2d78b5760b68d3dfc40e7b8dc89c51bd90147e91432f286268e1bf248"
