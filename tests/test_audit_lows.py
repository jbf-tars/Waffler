"""Small fixes from the security and correctness audit (L3, L7, L8, L12, L13).

Everything offline: fake clients, fake windows, fake clocks; no key, no
clipboard, no keyboard.
"""
import ast
import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import history_ops  # noqa: E402
import overlay as overlay_mod  # noqa: E402
import privacy_data  # noqa: E402
import style_openai as so  # noqa: E402
import _pipeline_harness as H  # noqa: E402
from test_shared_llm_path import Client, _answer, chat, styler  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


def lifted(name, **ns):
    node = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module([node], []), "<app.py Api>", "exec"), ns)
    return ns[name]


# ── L13: Clear history empties the Undo shelf ────────────────────────────────

def test_clear_history_leaves_nothing_on_the_undo_shelf():
    shelf = history_ops.UndoShelf()
    token = shelf.put(history_ops.DELETED, {"id": 5, "text": "a dictation that was deleted a moment ago"})
    saved = []
    fn = lifted("clear_history", _pipeline=None, _history_lock=threading.Lock(), save_history=saved.append,
                _history_ops=types.SimpleNamespace(SHELF=shelf))
    assert fn(object()) == {"ok": True}
    assert saved == [[]]
    assert shelf.take(token) is None, "an Undo token would put the entry back into the cleared Journal"
    assert len(shelf) == 0


def test_clear_history_still_clears_when_the_shelf_cannot_be():
    class Broken:
        def clear(self):
            raise RuntimeError("shelf gone")
    saved = []
    fn = lifted("clear_history", _pipeline=None, _history_lock=threading.Lock(), save_history=saved.append,
                _history_ops=types.SimpleNamespace(SHELF=Broken()))
    assert fn(object()) == {"ok": True} and saved == [[]]


# ── L12: the export page says the Desktop may be backed up ───────────────────

def test_the_export_row_says_a_backed_up_desktop_takes_the_file_too():
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    row = html[html.index('id="exportTitle"'):]
    row = row[:row.index("</div></div>")]
    assert "iCloud" in row and "OneDrive" in row and "backs up" in row
    assert "—" not in row and "–" not in row


# ── L3: the log keeps what the person named out of it ────────────────────────

class _Sent:
    def __init__(self):
        self.lines = []


def _overlay(logged):
    o = object.__new__(overlay_mod.RecordingOverlay)
    o._log = logged.append
    o._is_alive = lambda: True
    o._send = lambda cmd: None
    return o


def test_an_info_toasts_heading_is_not_logged_but_a_warnings_is():
    logged = []
    o = _overlay(logged)
    o.show_toast("info", "Rewrite for Acme Corp done", "Press Cmd+Z in your app to undo it.")
    o.show_toast("info", "Siobhan's AirPods detected", "Use it for dictation?")
    o.show_toast("warn", "Not pasted", "The text is on your clipboard.")
    o.show_toast("error", "Couldn't hear you", "Try again.")
    text = "\n".join(logged)
    assert "Acme" not in text and "Siobhan" not in text
    assert "heading='Not pasted'" in text and "heading='Couldn't hear you'" in text
    assert "style=info" in text


def test_the_logs_bundle_drops_the_identifiers_of_the_ranked_microphones():
    raw = json.dumps({"mic_ranking": [{"name": "AirPods Pro", "id": "uid:00-1A-7D-DA-71-13"},
                                      {"name": "Yeti", "id": "uid:AppleUSBAudioEngine:Blue:Yeti:2101"},
                                      {"name": "Built-in"}, "junk"],
                      "language": "en"})
    got = json.loads(privacy_data.settings_for_logs(raw))
    assert got["mic_ranking"] == [{"name": "AirPods Pro"}, {"name": "Yeti"}, {"name": "Built-in"}, "junk"]
    assert got["language"] == "en"
    assert "00-1A-7D" not in privacy_data.settings_for_logs(raw) and "AppleUSBAudioEngine" not in \
        privacy_data.settings_for_logs(raw)
    assert json.loads(privacy_data.settings_for_logs(json.dumps({"mic_ranking": "x"})))["mic_ranking"] == "x"


def test_a_provider_failure_is_logged_without_a_traceback_or_a_long_message(tmp_path, monkeypatch):
    monkeypatch.setattr(so, "_data_dir", lambda: tmp_path)
    s = styler()
    secret = "WORDS-THE-PERSON-DICTATED " * 40
    try:
        raise ValueError(f"Error code: 400 - the request was {secret}")
    except ValueError as e:
        s._log_provider_failure("Groq", e, "transform")
    log = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "[transform] Groq FAILED: ValueError: Error code: 400" in log
    assert "Traceback" not in log and 'File "' not in log
    assert log.count("WORDS-THE-PERSON-DICTATED") <= 5, "only the first 120 characters of the message"
    assert "test_audit_lows.py" in log, "where it was raised is kept"


def test_a_provider_failure_with_no_traceback_is_still_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(so, "_data_dir", lambda: tmp_path)
    styler()._log_provider_failure("Cerebras", "model 'x' unavailable - skipping for 10m")
    assert "[styling] Cerebras FAILED: str: model 'x' unavailable" in (tmp_path / "app.log").read_text(encoding="utf-8")


# ── L8: a request error does not switch Cerebras off for the dictations ──────

BAD_REQUEST = RuntimeError("Error code: 400 - {'error': {'message': 'Invalid request: the prompt is longer than "
                           "the context length', 'type': 'invalid_request_error'}}")
BAD_KEY = RuntimeError("Error code: 401 - {'error': {'message': 'Invalid API Key'}}")


def test_a_bad_request_to_cerebras_does_not_start_the_one_hour_auth_cooldown():
    c = Client([BAD_REQUEST])
    s = styler(order=("cerebras",), cerebras=c)
    with pytest.raises(Exception):
        chat(s, purpose="transform")
    assert s._cerebras_skip_until == 0.0, "a prompt over the context length silenced Cerebras for an hour"


def test_a_wrong_key_still_starts_it():
    c = Client([BAD_KEY])
    s = styler(order=("cerebras",), cerebras=c)
    with pytest.raises(Exception):
        chat(s, purpose="transform")
    assert s._cerebras_skip_until > time.monotonic() + 3000


@pytest.mark.parametrize("message", ["Error code: 401 - nope", "Error code: 403 - forbidden",
                                     "Unauthorized", "Incorrect invalid_api_key given"])
def test_each_auth_shape_still_counts(message):
    s = styler(order=("cerebras",), cerebras=Client([RuntimeError(message)]))
    with pytest.raises(Exception):
        chat(s)
    assert s._cerebras_skip_until > time.monotonic() + 3000, message


def test_a_slow_answer_to_a_transform_does_not_start_the_pause_but_a_slow_clean_up_does():
    slow = RuntimeError("Request timeout while waiting for the model")
    for purpose, silenced in (("transform", False), ("command", False), ("styling", True)):
        g, c = Client([slow]), Client([slow])
        s = styler(order=("groq", "cerebras"), groq=g, cerebras=c)
        with pytest.raises(Exception):
            chat(s, purpose=purpose)
        assert (s._groq_skip_until > time.monotonic()) is silenced, purpose
        assert (s._cerebras_skip_until > time.monotonic()) is silenced, purpose


def test_a_connection_that_failed_pauses_the_provider_whatever_the_request_was_for():
    down = RuntimeError("Connection error.")
    for purpose in ("transform", "command", "styling"):
        g, c = Client([down]), Client([down])
        s = styler(order=("groq", "cerebras"), groq=g, cerebras=c)
        with pytest.raises(Exception):
            chat(s, purpose=purpose)
        assert s._groq_skip_until > time.monotonic() and s._cerebras_skip_until > time.monotonic(), purpose


def test_a_rate_limit_is_provider_wide_whatever_the_request_was_for():
    limit = RuntimeError("Error code: 429 - Too many requests, please try again in 20 seconds")
    s = styler(order=("cerebras",), cerebras=Client([limit]))
    with pytest.raises(Exception):
        chat(s, purpose="transform")
    assert s._cerebras_skip_until > time.monotonic()


# ── L7: dictation, Transform and Command claim through one door ──────────────

def write_settings(p, **extra):
    body = {"command_mode": {"enabled": True, "hotkey": ["ctrl", "shift", "y"]},
            "transforms": {"enabled": True, "hotkey": ["ctrl", "shift", "u"]}}
    body.update(extra)
    (p.ns["DATA_DIR"] / "settings.json").write_text(json.dumps(body), encoding="utf-8")


def between_the_look_and_the_claim(runner, attr):
    """Make ``runner``'s look at what else is running pause, as the hook is
    called, until the test lets it go on to claim."""
    paused, go = threading.Event(), threading.Event()
    hooks = runner.h
    real = getattr(hooks, attr)

    def slow(*a, **k):
        answer = real(*a, **k)
        paused.set()
        go.wait(5)
        return answer
    setattr(hooks, attr, slow)
    return paused, go


def settle(runner):
    runner.cancel()
    deadline = time.time() + 5
    while runner.busy() and time.time() < deadline:
        time.sleep(0.02)


def test_a_dictation_pressed_between_a_commands_look_and_claim_does_not_share_the_microphone(tmp_path):
    p = H.make_pipeline(tmp_path)
    write_settings(p)
    paused, go = between_the_look_and_the_claim(p._commands, "busy_elsewhere")
    command = threading.Thread(target=p._commands.down, daemon=True)
    command.start()
    assert paused.wait(5)
    press = threading.Thread(target=p.on_hotkey_press, daemon=True)
    press.start()
    time.sleep(0.2)
    go.set()
    command.join(5)
    press.join(5)
    assert not (p.is_recording and p._commands.busy()), "a dictation and a command both claimed the recorder"
    assert p.is_recording or p._commands.busy()
    settle(p._commands)


def test_a_command_pressed_between_a_dictations_look_and_claim_does_not_share_the_microphone(tmp_path):
    p = H.make_pipeline(tmp_path)
    write_settings(p)
    paused, go = threading.Event(), threading.Event()
    real = p._commands.busy
    looks = []

    def slow_busy():
        answer = real()
        looks.append(1)
        if len(looks) == 2 and not paused.is_set():     # the second look: the one made at the claim
            paused.set()
            go.wait(5)
        return answer
    p._commands.busy = slow_busy                     # the dictation's look at the command pauses here
    press = threading.Thread(target=p.on_hotkey_press, daemon=True)
    press.start()
    assert paused.wait(5)
    command = threading.Thread(target=p._commands.down, daemon=True)
    command.start()
    time.sleep(0.2)
    go.set()
    press.join(5)
    command.join(5)
    assert not (p.is_recording and real()), "a dictation and a command both claimed the recorder"
    assert p.is_recording or real()
    settle(p._commands)


def test_a_transform_and_a_dictation_cannot_both_claim_either():
    import activity_gate
    import transform_action
    assert transform_action.activity_gate is activity_gate
    import command_action
    assert command_action.activity_gate is activity_gate


def test_the_gate_is_reentrant_and_held_only_for_the_claim():
    import activity_gate
    with activity_gate.GATE:
        with activity_gate.GATE:
            pass
    src = ast.unparse(next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline"))
    press = src[src.index("def on_hotkey_press"):src.index("def on_hotkey_release")]
    assert press.index("_activity_gate.GATE") < press.index("_processing_lock")
    assert press.index("_activity_gate.GATE") < press.index("self.audio.start()"), "the claim, not the recording"
    assert "with _activity_gate.GATE" in press and press.count("with _activity_gate.GATE") == 1


def test_a_second_press_while_one_dictation_runs_is_still_ignored(tmp_path):
    p = H.make_pipeline(tmp_path)
    write_settings(p)
    p.on_hotkey_press()
    first = p._current_press_id
    p.on_hotkey_press()
    assert p.is_recording and p._current_press_id == first
