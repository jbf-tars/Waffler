"""Ask across everything: which dictations are handed to the model (src/meetings_ctl.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.meetings_ctl import dictations_material  # noqa: E402


def entry(i, text, **kw):
    return {"id": f"h{i}", "timestamp": f"2026-11-{10 + i:02d}T09:00:00", "text": text, **kw}


def test_nothing_to_give_is_an_empty_string():
    assert dictations_material([], "anything") == ""
    assert dictations_material(None, "anything") == ""
    assert dictations_material([entry(1, "")], "anything") == ""


def test_matching_entries_come_first_then_recent_and_none_twice():
    hist = [entry(1, "The supplier quote is due on Tuesday."), entry(2, "Lunch at one."),
            entry(3, "Remind me about the dentist.")]
    out = dictations_material(hist, "When is the supplier quote due?")
    lines = out.splitlines()[1:]
    assert "supplier quote" in lines[0]                       # the match leads, though it is the oldest
    assert [l for l in lines if "dentist" in l] and [l for l in lines if "Lunch" in l]
    assert len(lines) == 3                                    # the match is not repeated among the recent


def test_the_persons_own_edit_wins_and_the_app_is_named():
    out = dictations_material([entry(1, "raw words", styled="Styled words.", edited_text="My words.", app="Mail")], "x")
    assert "My words." in out and "raw words" not in out and " in Mail:" in out


def test_unsent_takes_transforms_and_commands_are_left_out():
    hist = [entry(1, "never sent", failed=True), entry(2, "a transform", kind="transform"),
            entry(3, "a command", kind="command"), entry(4, "A real dictation.")]
    out = dictations_material(hist, "x")
    assert "A real dictation." in out and "never sent" not in out and "transform" not in out and "command" not in out


def test_it_stops_at_the_size_limit():
    hist = [entry(i % 20, "word " * 200, id=f"k{i}") for i in range(100)]
    for h, i in zip(hist, range(100)):
        h["id"] = f"k{i}"
    out = dictations_material(hist, "x", limit_chars=5000)
    assert 0 < len(out) < 5400


# ── Ask answers quickly, and the dictations keep their share ─────────────────

from src.meetings import llm  # noqa: E402


class _FakeOpenAI:
    """Stands in for the OpenAI client: keeps what each request asked for."""

    def __init__(self, refuse_effort=False):
        self.calls, self.refuse_effort = [], refuse_effort
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.refuse_effort and "reasoning_effort" in kwargs:
            err = RuntimeError("unsupported parameter")
            err.status_code = 400
            raise err
        if kwargs.get("stream"):
            def piece(text):
                class _Delta:
                    content = text

                class _Choice:
                    delta = _Delta()

                class _Chunk:
                    choices = [_Choice()]
                return _Chunk()
            return iter([piece("The "), piece(None), piece("answer.")])

        class _Msg:
            content = "The answer."

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]
        return _Resp()


def _on_openai(monkeypatch, client):
    monkeypatch.setattr(llm, "resolve_provider", lambda settings: "openai")
    monkeypatch.setattr(llm, "resolve_model", lambda provider, settings: "gpt-5-mini")
    monkeypatch.setattr(llm, "_openai_client", lambda provider, settings: client)


def test_ask_tells_a_thinking_model_to_answer_quickly(monkeypatch):
    client = _FakeOpenAI()
    _on_openai(monkeypatch, client)
    assert llm.ask("When is the quote due?", "material", [], {}) == "The answer."
    assert client.calls[0]["reasoning_effort"] == "low"


def test_a_model_that_refuses_the_quick_setting_is_asked_again_without_it(monkeypatch):
    client = _FakeOpenAI(refuse_effort=True)
    _on_openai(monkeypatch, client)
    assert llm.ask("When is the quote due?", "material", [], {}) == "The answer."
    assert len(client.calls) == 2 and "reasoning_effort" not in client.calls[1]


def test_writing_notes_is_not_hurried(monkeypatch):
    client = _FakeOpenAI()
    _on_openai(monkeypatch, client)
    llm.complete("system", [{"role": "user", "content": "write"}], {})
    assert "reasoning_effort" not in client.calls[0]


def test_the_dictations_are_not_cut_off_by_long_meeting_notes():
    seen = {}

    def backend(system, messages):
        seen["material"] = messages[0]["content"]
        return "ok"
    notes = "N" * 80000
    said = "## Dictations (things the owner said and had typed, newest first)\n- " + "D" * 9000
    llm.ask("x", notes, [], {}, backend=backend, extra=said)
    assert "## Dictations" in seen["material"] and "D" * 9000 in seen["material"]
    assert "N" * 1000 in seen["material"]           # the notes are still there too
    assert len(seen["material"]) < 40000            # and the whole is still a size a model reads


def test_ask_with_no_dictations_sends_the_notes_as_before():
    seen = {}

    def backend(system, messages):
        seen["material"] = messages[0]["content"]
        return "ok"
    llm.ask("x", "Some notes.", [], {}, backend=backend)
    assert "Some notes." in seen["material"] and "Dictations" not in seen["material"]


def test_ask_hands_over_the_words_as_they_are_written(monkeypatch):
    client = _FakeOpenAI()
    _on_openai(monkeypatch, client)
    seen = []
    assert llm.ask("When is the quote due?", "material", [], {}, on_text=seen.append) == "The answer."
    assert seen == ["The ", "The answer."]
    assert client.calls[0]["stream"] is True


def test_ask_without_a_listener_is_one_plain_request(monkeypatch):
    client = _FakeOpenAI()
    _on_openai(monkeypatch, client)
    llm.ask("When is the quote due?", "material", [], {})
    assert "stream" not in client.calls[0]


def test_the_page_and_the_app_are_wired_for_an_answer_that_arrives_in_pieces():
    root = Path(__file__).resolve().parent.parent
    app = (root / "app.py").read_text(encoding="utf-8")
    page = (root / "ui" / "meetings.js").read_text(encoding="utf-8")
    assert "waffler_ask_text" in app and "window.waffler_ask_text" in page
    assert "def meetings_ask(self, question: str, note_id: str = \"\", everything: bool = False,\n" in app


def test_home_shows_what_a_meeting_was_about_without_its_headings():
    import shutil
    import subprocess
    if not shutil.which("node"):
        import pytest
        pytest.skip("node is not here")
    page = Path(__file__).resolve().parent.parent / "ui" / "meetings.js"
    script = (
        "const src=require('fs').readFileSync(process.argv[1],'utf8');"
        "const gist=eval('('+/function gist\\(preview\\) \\{[\\s\\S]*?\\n  \\}/.exec(src)[0]+')');"
        "console.log(JSON.stringify(["
        "gist('## Opening greeting\\n- Hello there.\\n\\n## Next steps\\n- No actions mentioned'),"
        "gist('## Launch\\n- Moves to the 2nd.\\n- **Priya** owns pricing.\\n## Next steps\\n- [ ] Tom chases the quote.'),"
        "gist('No one objected, and the date holds.')]))")
    out = subprocess.run(["node", "-e", script, str(page)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    import json
    assert json.loads(out.stdout) == ["Hello there.",
                                      "Moves to the 2nd. Priya owns pricing. Tom chases the quote.",
                                      "No one objected, and the date holds."]


# ── on Groq, a model that reasons is told not to, when the job is a quick one ──

import pytest  # noqa: E402

def _on_groq(monkeypatch, client, model):
    monkeypatch.setattr(llm, "resolve_provider", lambda settings: "groq")
    monkeypatch.setattr(llm, "resolve_model", lambda provider, settings: model)
    monkeypatch.setattr(llm, "_openai_client", lambda provider, settings: client)


@pytest.mark.parametrize("model, sent", [
    ("openai/gpt-oss-120b", {"reasoning_effort": "low"}),
    ("qwen/qwen3-32b", {"reasoning_effort": "none"}),
    ("llama-3.3-70b-versatile", {}),
])
def test_a_quick_job_on_groq_tells_a_reasoning_model_how_little_to_think(monkeypatch, model, sent):
    client = _FakeOpenAI()
    _on_groq(monkeypatch, client, model)
    llm.complete("system", [{"role": "user", "content": "punctuate"}], {}, effort="low", temperature=0.0)
    first = client.calls[0]
    assert {k: first[k] for k in first if k == "reasoning_effort"} == sent
    assert first["temperature"] == 0.0


def test_groq_notes_are_not_hurried(monkeypatch):
    client = _FakeOpenAI()
    _on_groq(monkeypatch, client, "openai/gpt-oss-120b")
    llm.complete("system", [{"role": "user", "content": "write"}], {})
    assert "reasoning_effort" not in client.calls[0]


def test_a_model_that_refuses_the_effort_still_gets_its_temperature(monkeypatch):
    client = _FakeOpenAI(refuse_effort=True)
    _on_groq(monkeypatch, client, "openai/gpt-oss-120b")
    assert llm.complete("system", [{"role": "user", "content": "x"}], {}, effort="low", temperature=0.0) == "The answer."
    assert "reasoning_effort" not in client.calls[-1] and client.calls[-1]["temperature"] == 0.0
