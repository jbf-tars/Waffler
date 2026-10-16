"""Every toast in the app goes through the notifier, and still reads the same.

app.py used to call the overlay from about twenty places. Each now asks
`self._toast_allowed("<kind>")` first (src/notifier.py decides), and nothing
else about them changed: the text, the buttons, the time they stay and when
they appear are what the pinned tests (test_toast_protocol, the pipeline and
watchdog tests, test_plain_error_messages) already check. These tests make
sure no site slips past the governor, that every kind is registered and has
exactly the behaviour the old code had, and that the glue does what it says.
"""
import ast
import json
import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _pipeline_harness as H  # noqa: E402
import notifier as N  # noqa: E402

APP = ROOT / "app.py"
_TREE = ast.parse(APP.read_text(encoding="utf-8"))


def _parents(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _is_toast_allowed(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_toast_allowed")


def _allowed_ids(tree):
    return [n.args[0].value for n in ast.walk(tree)
            if _is_toast_allowed(n) and n.args and isinstance(n.args[0], ast.Constant)]


# ── no toast without the governor ────────────────────────────────────────

def test_every_overlay_toast_in_app_py_is_behind_the_governor():
    parents = _parents(_TREE)
    # Display-only helper: its callers decided already (the permission check
    # and the failed-paste message claim their kinds in src/).
    exempt = {"_show_toast_safe"}
    bare = []
    seen = 0
    for call in ast.walk(_TREE):
        if not (isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "show_toast"):
            continue
        seen += 1
        node, ok, func = call, False, None
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.If) and any(_is_toast_allowed(n) for n in ast.walk(node.test)):
                ok = True
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func = node
                break
        if func is not None and func.name in exempt:
            continue
        if not ok and func is not None:
            # "if not self._toast_allowed(...): return" earlier in the same function.
            for stmt in ast.walk(func):
                if (isinstance(stmt, ast.If) and stmt.lineno < call.lineno
                        and any(_is_toast_allowed(n) for n in ast.walk(stmt.test))
                        and any(isinstance(b, ast.Return) for b in stmt.body)):
                    ok = True
        if not ok:
            bare.append(f"app.py:{call.lineno} in {getattr(func, 'name', '?')}")
    assert seen >= 18, "the scan found the toast sites"
    assert not bare, f"toasts that do not ask the notifier: {bare}"


def test_the_overlay_is_only_called_for_toasts_through_those_sites():
    """show_toast is the one overlay call that puts a message up; hide_toast and
    the pill calls are not messages."""
    src = APP.read_text(encoding="utf-8")
    assert "self.overlay.show_toast(" in src
    assert len(re.findall(r"\.show_toast\(", src)) == len(
        [n for n in ast.walk(_TREE) if isinstance(n, ast.Call)
         and getattr(n.func, "attr", "") == "show_toast"])


def test_every_kind_the_app_asks_for_is_registered():
    ids = _allowed_ids(_TREE)
    assert len(ids) >= 18
    assert set(ids) <= set(N.KINDS), set(ids) - set(N.KINDS)


def test_every_registered_kind_is_raised_somewhere():
    """No kind is left over from a message that no longer exists."""
    named = set()
    for path in [APP, *sorted((ROOT / "src").glob("*.py"))]:
        if path.name == "notifier.py":
            continue
        named |= {n.value for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    # Built as f"permission_{problem.permission}" in mac_permissions.
    composed = {"permission_microphone", "permission_accessibility", "permission_keyboard"}
    assert set(N.KINDS) - named - composed == set()


def test_errors_and_info_keep_the_behaviour_the_old_code_had():
    """The migration changed nothing about when a message appears: every kind
    the app asks for has no cap, and only the two that were already limited
    (the failed-paste message, the permission messages) have a gap."""
    limited = {"paste_failed", "permission_microphone", "permission_accessibility",
               "permission_accessibility_idle", "permission_keyboard",
               # Transforms (S7): a refusal while a dictation runs is shown once
               # every 8 seconds, so mashing the shortcut does not stack messages.
               "transform_busy",
               # Command mode (S8): the same, for a command refused while busy.
               "command_busy",
               # Paste last and Copy last as shortcuts (S11a): a notice at most every 3 seconds.
               "last_result_notice"}
    for kid, k in N.KINDS.items():
        if k.severity == N.TIP:
            continue
        assert k.max_times is None, kid
        assert (k.min_gap_s > 0) == (kid in limited), kid


def test_only_the_hint_is_a_tip():
    assert [k.id for k in N.KINDS.values() if k.severity == N.TIP] == ["hold_hint"]


# ── the glue ─────────────────────────────────────────────────────────────

def pipeline(tmp_path, **kw):
    p = H.make_pipeline(tmp_path, **kw)
    return p


def test_an_error_is_counted_and_remembered_in_the_state_file(tmp_path):
    p = pipeline(tmp_path)
    p._notifier = N.Notifier(store=N.FileStore(tmp_path / N.STATE_FILE), context=p._notifier_context)
    assert p._toast_allowed("pipeline_error") is True
    assert p._toast_allowed("pipeline_error") is True, "no cap, no gap: as before"
    data = json.loads((tmp_path / N.STATE_FILE).read_text(encoding="utf-8"))
    assert data["kinds"]["pipeline_error"]["shown"] == 2
    assert "text" not in json.dumps(data).lower()


def test_a_message_that_is_dropped_says_why_in_the_log_without_text(tmp_path):
    p = pipeline(tmp_path)
    assert p._toast_allowed("paste_failed") is True
    assert p._toast_allowed("paste_failed") is False
    assert any("paste_failed not shown (gap)" in line for line in p.logged)


def test_if_the_notifier_breaks_the_message_is_shown_anyway(tmp_path):
    p = pipeline(tmp_path)

    def boom(kind_id):
        raise RuntimeError("state file locked")
    p._notifier.request = boom
    assert p._toast_allowed("pipeline_error") is True
    assert any("showing pipeline_error" in line for line in p.logged)


def test_a_kind_nobody_registered_is_shown_not_swallowed(tmp_path):
    p = pipeline(tmp_path)
    assert p._toast_allowed("brand_new_message") is True


def test_a_tip_waits_while_an_error_is_up_and_an_error_never_waits(tmp_path):
    p = pipeline(tmp_path)
    assert p._toast_allowed("access_denied")
    assert p._toast_allowed("hold_hint") is False
    assert p._toast_allowed("access_denied") is True


@pytest.mark.parametrize("action", ["dismiss", "open_journal", "copy_last", "keep_waiting",
                                    "never_heard_of_it", "tip_dismiss:hold_hint"])
def test_any_button_click_frees_the_slot(tmp_path, action):
    p = pipeline(tmp_path)
    p._show_journal = lambda: None
    p.copy_last = lambda: None
    assert p._toast_allowed("pipeline_error")
    assert p._notifier.request("hold_hint").reason == "slot_busy"
    p._on_toast_action(action)
    assert p._notifier.request("hold_hint").reason != "slot_busy"


def test_the_still_working_offer_holds_the_slot_until_it_is_withdrawn(tmp_path):
    p = pipeline(tmp_path)
    assert p._toast_allowed("still_working")
    assert p._notifier.request("hold_hint").reason == "slot_busy"
    run = type("R", (), {})()
    p._run_is_current = lambda r: True
    p._set_listener_processing = lambda on: None
    p._ui_withdraw_offer(run)
    assert p._notifier.request("hold_hint").reason != "slot_busy"
    assert "hide_toast" in p.overlay.names()


# ── the old limiter is gone ──────────────────────────────────────────────

def test_the_temporary_rate_limiter_is_retired():
    """The module is gone and nothing imports it or names its class (a comment
    may still say what it used to be)."""
    assert not (ROOT / "src" / "rate_limit.py").exists()
    for path in [APP, *sorted((ROOT / "src").glob("*.py")), *sorted((ROOT / "tests").glob("*.py"))]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[-1] != "rate_limit", path.name
            elif isinstance(node, ast.Import):
                assert all(a.name.split(".")[-1] != "rate_limit" for a in node.names), path.name
            elif isinstance(node, ast.Name):
                assert node.id != "RateLimiter", path.name
            elif isinstance(node, ast.Attribute):
                assert node.attr != "RateLimiter", path.name


def test_the_permission_check_and_the_failed_paste_message_use_the_same_notifier():
    src = APP.read_text(encoding="utf-8")
    assert "PermissionPrecheck(notifier=self._notifier)" in src
    assert "report_paste_failure(outcome, self._notifier, platform)" in src


# ── the messages read exactly as they did ────────────────────────────────

@pytest.fixture
def limits(monkeypatch):
    H.fast_limits(monkeypatch)


def run_to_the_end(p, generation=1):
    t = H.run_process(p, generation)
    t.join(8)
    assert not t.is_alive()
    return p


def only_toast(p, timeout=3):
    assert H.wait_for(lambda: p.overlay.toasts(), timeout=timeout)
    toasts = p.overlay.toasts()
    assert len(toasts) == 1, toasts
    return toasts[0]


@pytest.mark.parametrize("error,heading,body", [
    ("Connection error talking to api.groq.com", "Connection failed",
     "Couldn't reach the server. Check your internet or VPN."),
    ("403 Access denied", "Access denied",
     "Your VPN server's IP is blocked by Groq. Switch to a different VPN server/location and try again."),
    ("RATE_LIMIT|tokens per day (TPD)|16m12s|Groq: Rate limit reached", "Limit reached",
     "Groq says you've reached your limit for now. Try again in about 17 minutes."),
    ("something odd", "Something went wrong", None),
])
def test_an_error_in_the_pipeline_still_ends_in_the_same_message(tmp_path, limits, monkeypatch,
                                                                   error, heading, body):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("openai_api_key", raising=False)
    p = H.make_pipeline(tmp_path)
    p._apply_snippets = lambda text: (_ for _ in ()).throw(ValueError(error))
    run_to_the_end(p)
    toast = only_toast(p)
    assert set(toast) == {"style", "heading", "body"}, "no new keys: it is the message it always was"
    assert toast["style"] == "warn" and toast["heading"] == heading
    if body is not None:
        assert toast["body"] == body
    assert p._notifier.snapshot()[{"Connection failed": "connection_failed",
                                   "Access denied": "access_denied",
                                   "Limit reached": "limit_reached",
                                   "Something went wrong": "pipeline_error"}[heading]]["shown"] == 1


def test_a_dead_microphone_still_says_mic_reset(tmp_path, limits):
    p = H.make_pipeline(tmp_path, wav=H.silence_wav(1.5))
    p._recording_start_time = __import__("time").time() - 1.5
    run_to_the_end(p)
    toast = only_toast(p)
    assert toast == {"style": "warn", "heading": "Mic reset",
                     "body": "Your mic stopped responding after sleep or a device change. "
                             "It has been reset: press and speak again."}


def test_a_quiet_take_still_says_we_couldnt_hear_you(tmp_path, limits):
    import struct
    import io
    import wave
    import random
    rng = random.Random(4)
    frames = b"".join(struct.pack("<h", int(rng.gauss(0, 3))) for _ in range(int(1.5 * 16000)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(frames)
    p = H.make_pipeline(tmp_path, wav=buf.getvalue())
    p._recording_start_time = __import__("time").time() - 1.5
    run_to_the_end(p)
    toast = only_toast(p)
    assert toast == {"style": "error", "heading": "We couldn't hear you",
                     "body": "Check your mic is connected and not muted."}


def test_a_history_that_could_not_be_saved_still_says_so(tmp_path, limits):
    p = H.make_pipeline(tmp_path)
    p.ns["append_history_safely"] = lambda item: False
    run_to_the_end(p)
    toast = only_toast(p)
    assert toast == {"style": "warn", "heading": "Not saved to History",
                     "body": "Your text was pasted, but Waffler couldn't save it to History."}


def test_two_errors_in_a_row_both_appear_as_before(tmp_path, limits):
    p = H.make_pipeline(tmp_path)
    p._apply_snippets = lambda text: (_ for _ in ()).throw(ValueError("something odd"))
    run_to_the_end(p, 1)
    p._processing_id = p._current_press_id = 2         # the next press
    p._recording_start_time = __import__("time").time() - 3.0
    run_to_the_end(p, 2)
    assert H.wait_for(lambda: len(p.overlay.toasts()) == 2, timeout=3)
    assert p._notifier.snapshot()["pipeline_error"]["shown"] == 2


def test_the_notifier_is_made_before_the_things_that_use_it():
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    lines = {}
    for node in ast.walk(init):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Attribute):
            lines[node.targets[0].attr] = node.lineno
    assert lines["_notifier"] < lines["_precheck"] < lines["_cues"]
    assert "_hands_free_press_id" in lines
