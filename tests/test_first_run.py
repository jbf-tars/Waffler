"""First-run setup's decisions (src/first_run.py, OB3 and OB4).

The practice dictation used to run only the transcription (app.py called
transcribe_sync and stopped), so setup never showed the clean-up and the
Journal was empty afterwards. These tests pin the new behaviour with a fake
styler: the clean-up runs, "You said" and "Waffler wrote" both come back,
and the result is shaped as a Journal entry. Offline; no keys.
"""
import ast
import pytest
import re
import sys
import types
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import first_run as fr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAID = "Send it to John, sorry, James, by Wednesday at three."
WROTE = "Send it to James by Wednesday at three."


# ── the key check lists what the key can do ──────────────────────────────────

def _models(*ids):
    return types.SimpleNamespace(data=[types.SimpleNamespace(id=i) for i in ids])


def test_both_jobs_work_when_groq_lists_both_models():
    ids = fr.model_ids(_models("whisper-large-v3", "openai/gpt-oss-120b", "llama-3.1-8b-instant"))
    assert fr.groq_services(ids) == [
        {"name": "Speech to text", "provider": "Groq", "model": "whisper-large-v3", "status": "ok"},
        {"name": "Clean-up", "provider": "Groq", "model": "gpt-oss-120b", "status": "ok"},
    ]


def test_a_model_missing_from_the_list_is_named():
    services = fr.groq_services(fr.model_ids(_models("whisper-large-v3")))
    assert [s["status"] for s in services] == ["ok", "missing"]


def test_an_empty_list_is_unchecked_not_broken():
    assert [s["status"] for s in fr.groq_services(fr.model_ids(_models()))] == ["unchecked", "unchecked"]
    assert fr.model_ids({"data": [{"id": "whisper-large-v3"}]}) == {"whisper-large-v3"}
    assert fr.model_ids(None) == set()


def test_the_clean_up_model_follows_the_styler(monkeypatch):
    """The same override and default as src/style_openai.py."""
    src = (ROOT / "src" / "style_openai.py").read_text(encoding="utf-8")
    assert f'_os.getenv("GROQ_STYLE_MODEL", "").strip() or "{fr.DEFAULT_CLEANUP_MODEL}"' in src
    assert f'model="{fr.SPEECH_MODEL}"' in (ROOT / "src" / "transcribe_whisper.py").read_text(encoding="utf-8")
    monkeypatch.setenv("GROQ_STYLE_MODEL", "openai/gpt-oss-20b")
    assert fr.groq_services({"openai/gpt-oss-20b"})[1] == {
        "name": "Clean-up", "provider": "Groq", "model": "gpt-oss-20b", "status": "ok"}


# ── the practice dictation runs the clean-up ─────────────────────────────────

def test_the_practice_is_cleaned_up_and_both_versions_come_back():
    calls = []

    def style(t):
        calls.append(t)
        return WROTE, {"input_tokens": 900, "output_tokens": 12, "api_used": True, "provider": "groq"}

    r = fr.finish_practice(SAID, style, fallback=lambda t: "unused")
    assert calls == [SAID]
    assert r["said"] == SAID and r["wrote"] == WROTE
    assert r["cleaned"] is True and r["note"] == ""
    assert r["usage"]["provider"] == "groq"


def test_a_clean_up_that_raises_keeps_the_words_and_says_so():
    def style(t):
        raise ConnectionError("Connection error.")

    r = fr.finish_practice(SAID, style, fallback=lambda t: t.upper())
    assert r["wrote"] == SAID.upper()
    assert r["cleaned"] is False
    assert r["note"] == "The clean-up didn't run this time, so these are your words as you said them."


def test_a_rate_limited_clean_up_says_when_it_comes_back():
    reason = "RATE_LIMIT|tokens per day (TPD)|16m12.5s|Groq: Rate limit reached for model"
    r = fr.finish_practice(SAID, lambda t: (SAID, {"fallback_reason": reason}), fallback=str)
    assert r["cleaned"] is False
    assert r["note"].startswith("Clean-up paused for about 17 minutes.")
    assert "pasted" not in r["note"]          # nothing is pasted during setup


def test_an_empty_clean_up_falls_back_to_the_words():
    r = fr.finish_practice(SAID, lambda t: ("", {}), fallback=str)
    assert r["wrote"] == SAID


def test_the_practice_becomes_a_journal_entry_shaped_like_the_pipelines():
    e = fr.journal_entry(SAID, WROTE, now=datetime(2026, 9, 26, 10, 30, 5))
    assert e == {"timestamp": "2026-09-26T10:30:05", "text": SAID, "styled": WROTE,
                 "word_count": 8, "text_is": "asr_filtered", "source": "setup"}
    # The pipeline writes the same core fields (app.py _process).
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    for field in ('"timestamp":', '"text": transcript', '"styled": styled', '"word_count":', '"text_is": "asr_filtered"'):
        assert field in src


# ── app.py's practice path uses all of this ──────────────────────────────────

def _fn_source(name):
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(src, fn)


def test_the_setup_practice_calls_the_clean_up_and_saves_the_entry():
    body = _fn_source("_wizard_on_release")
    assert "transcribe_sync(" in body
    assert "_wizard_finish_practice(" in body
    finish = _fn_source("_wizard_finish_practice")
    assert "_first_run.finish_practice(" in finish and "styler.style(" in finish and "styler = _wizard_styler" in finish
    assert "journal_entry(" in finish and "append_history_safely(" in finish
    assert "record_usage_safely(" in finish
    # No speech in app.log unless the user opted in.
    assert not re.search(r"_log_to_file\(f?[\"'][^\"']*\{(said|wrote|transcript)\b", finish)


# ── app.py's setup bridge methods, run with fakes ────────────────────────────

def _api_method(name, **ns):
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    fn = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name)
    env = {"os": __import__("os"), "_log_to_file": lambda m: None, **ns}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), env)
    return env[name]


class _Store:
    _SETUP_STEPS = ("connect", "permissions", "try", "anywhere")

    def __init__(self, data=None):
        self.data = dict(data or {})
        self.saves = 0

    def _load_settings_file(self):
        return dict(self.data)

    def _save_settings_file(self, d):
        self.data = dict(d)
        self.saves += 1


def test_the_setup_step_is_remembered_and_only_known_steps_are(monkeypatch):
    save = _api_method("save_setup_step")
    store = _Store()
    assert save(store, "permissions") == {"ok": True}
    assert store.data["setup_step"] == "permissions"
    assert save(store, "permissions") == {"ok": True} and store.saves == 1   # no rewrite
    assert save(store, "../../evil") == {"ok": False}
    assert store.data["setup_step"] == "permissions"


@pytest.mark.parametrize("done, key, stored, resume", [
    (False, "gsk_x", "try", "try"),       # mid-setup: carry on
    (True, "gsk_x", "try", ""),           # set up: nothing to resume, no wizard
    (True, "", "try", "try"),             # key gone: setup again, from where it was
])
def test_onboarding_status_resumes_only_while_setup_is_needed(monkeypatch, done, key, stored, resume):
    status = _api_method("get_onboarding_status", _is_setup_complete=lambda: done)
    monkeypatch.setenv("GROQ_API_KEY", key)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    r = status(_Store({"setup_step": stored}))
    assert r["resume_step"] == resume
    assert r["needs_setup"] is (not done or not key)


def test_allow_buttons_reach_the_matching_prompt():
    calls = []
    fake = types.SimpleNamespace(
        request_microphone=lambda: calls.append("mic") or {"ok": True},
        request_input_monitoring=lambda already_asked=False: calls.append(("im", already_asked)) or {"ok": True},
        request_accessibility=lambda already_asked=False: calls.append(("acc", already_asked)) or {"ok": True},
    )
    req = _api_method("request_permission", _mac_perms=fake)
    api = types.SimpleNamespace()
    req(api, "microphone")
    req(api, "input_monitoring")
    req(api, "input_monitoring")          # a second press may open the pane
    req(api, "accessibility")
    assert calls == ["mic", ("im", False), ("im", True), ("acc", False)]
    assert req(api, "camera") == {"ok": False}
