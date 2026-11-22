"""Plus use shown quietly in Settings, Account; an import is titled by its file;
"Transcribe again" with nothing set up is the calm state."""
import base64
import io
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from _account_fake import FakeAccountServer  # noqa: E402
from meetings import transcribe as mt  # noqa: E402
from meetings.store import wait_until  # noqa: E402
from src import account  # noqa: E402
from test_free_meetings import _speech, call, ctl, machine  # noqa: E402,F401
from test_ui_logic import js, needs_node, read  # noqa: E402

SERVER = "https://notes.example.test"
EMAIL = "ada@example.com"
HOUR, MIN = 3600, 60


@pytest.fixture
def server(monkeypatch, _isolated_data_dir):
    fake = FakeAccountServer(contract="new")
    monkeypatch.setenv(account.SERVER_ENV, SERVER)
    monkeypatch.setattr(account, "_transport", fake.transport())
    monkeypatch.setattr(account, "_notice", "")
    monkeypatch.setattr(account, "_plan", {})
    fake.data = _isolated_data_dir
    return fake


def _in(fake, plus=True):
    assert account.start(EMAIL)["ok"]
    assert account.verify(EMAIL, fake.mailbox[EMAIL], "Ada's Mac")["ok"]
    if plus:
        fake.give_plus(EMAIL)
    assert account.refresh_plan()["ok"]
    return account.status()


# ── 1. the numbers reach the page, from the one request that already asks ───

def test_usage_rides_the_plan_request_and_asks_for_nothing_more(server):
    server.month_usage = {"transcribed_seconds": 3.5 * HOUR, "model_chars_month": 1000}
    server.month_limits = {"monthly_minutes": 1200, "model_chars_per_month": 10000}
    s = _in(server)
    assert s["plus_usage"] == {"seconds": 3.5 * HOUR, "minutes_limit": 1200,
                               "chars": 1000, "chars_limit": 10000}
    n = len(server.requests)
    account.status(); account.status()
    assert len(server.requests) == n, "status reads memory"
    assert len(server.calls("GET", "/account")) == 1


def test_no_limit_or_no_numbers_is_nothing(server):
    server.month_usage = {"transcribed_seconds": 5, "model_chars_month": 5}
    server.month_limits = {"monthly_minutes": 0, "model_chars_per_month": 0}
    assert _in(server)["plus_usage"] is None, "a limit of 0 is no limit"
    server.month_usage, server.month_limits = {}, {}
    account.refresh_plan()
    assert account.status()["plus_usage"] is None, "an older server"
    server.month_usage = {"transcribed_seconds": "lots", "model_chars_month": True}
    server.month_limits = {"monthly_minutes": 1200, "model_chars_per_month": 100}
    account.refresh_plan()
    assert account.status()["plus_usage"] == {"seconds": None, "minutes_limit": 1200,
                                              "chars": None, "chars_limit": 100}


def test_a_free_account_carries_no_usage(server):
    server.month_usage = {"transcribed_seconds": 5}
    server.month_limits = {"monthly_minutes": 1200}
    assert _in(server, plus=False)["plus_usage"] is None


# ── the lines ───────────────────────────────────────────────────────────────

def lines(usage, plan="plus"):
    return js("L.accountUsageLines({ok: true, available: true, signed_in: true, email: 'a@b.c', plan: '%s', "
              "plus_until: null, plus_usage: %s})" % (plan, usage))


def u(seconds=None, minutes=1200, chars=None, cap=None):
    f = lambda v: "null" if v is None else str(v)  # noqa: E731
    return "{seconds: %s, minutes_limit: %s, chars: %s, chars_limit: %s}" % (f(seconds), f(minutes), f(chars), f(cap))


@needs_node
def test_the_use_line_and_its_exceptions():
    assert lines(u(3 * HOUR + 40 * MIN)) == ["3 of 20 hours used this month."]
    assert lines(u(20 * MIN)) == ["Under 1 of 20 hours used this month."]
    assert lines(u(0)) == ["Under 1 of 20 hours used this month."]
    assert lines(u(19 * HOUR + 50 * MIN)) == ["19 of 20 hours used this month."], "never full before it is"
    assert lines(u(3 * HOUR, chars=7999, cap=10000)) == ["3 of 20 hours used this month."]
    assert lines(u(3 * HOUR, chars=8000, cap=10000)) == [
        "3 of 20 hours used this month.", "Writing is nearly used up."]
    assert lines(u(20 * HOUR)) == ["This month\u2019s Waffler Plus is used up."]
    assert lines(u(3 * HOUR, chars=10000, cap=10000)) == ["This month\u2019s Waffler Plus is used up."]


@needs_node
def test_nothing_is_shown_that_was_not_sent():
    assert lines("null") == [] and lines("{}") == []
    assert lines(u(5 * HOUR, minutes=None)) == [], "no limit: nothing"
    assert lines(u(seconds=None)) == [], "no usage number: nothing"
    assert lines(u(5 * HOUR, minutes=0)) == []
    assert lines("'x'") == []
    assert lines(u(3 * HOUR), plan="free") == []
    assert lines(u(chars=9000, cap=10000, seconds=None, minutes=None)) == ["Writing is nearly used up."]


@needs_node
def test_the_words_are_few_and_nothing_sells_plus():
    for t in lines(u(3 * HOUR, chars=9000, cap=10000)) + lines(u(20 * HOUR)):
        assert len(t.split()) <= 9 and "\u2014" not in t and "\u2013" not in t and " - " not in t
        assert not any(w in t.lower() for w in ("upgrade", "buy", "price", "$"))


def test_the_page_draws_them_under_the_plan_as_text():
    page = read("account.js")
    draw = page[page.index("function drawPlan("):]
    draw = draw[:draw.index("\n  }\n")]
    assert "L.accountUsageLines(s)" in draw and "d.textContent = t" in draw and "innerHTML" not in draw
    assert page.count("call('account_status')") == 1, "the page asks for the account once, as before"


# ── 2. an imported file is titled by its name ───────────────────────────────

def _wav_b64(seconds=6.0):
    pcm = (_speech(seconds) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
    return base64.b64encode(buf.getvalue()).decode()


def _import(ctl, name):
    r = call(ctl, "POST", "/import", {"file_name": name, "file_base64": _wav_b64()})
    assert r["status"] == 200, r
    nid = r["body"]["id"]
    store = ctl._svc().store
    assert wait_until(lambda: store.get_note(nid)["status"] == "ready", timeout=20)
    return nid, store


def test_a_failed_import_is_titled_by_its_file_name(machine, ctl):
    a, store = _import(ctl, "Kitchen call.m4a")
    b, _ = _import(ctl, "C:\\Users\\me\\Voice memo 3.wav")
    assert store.get_note(a)["title"] == "Kitchen call"
    assert store.get_note(b)["title"] == "Voice memo 3"
    assert store.get_note(a)["title_locked"] == 0, "notes may still write a better one"
    assert store.get_note(a)["error"] == mt.SPEECH_NEEDS_SETUP
    assert call(ctl, "POST", "/import", {"file_name": ".m4a", "file_base64": _wav_b64()})["status"] == 200


def test_notes_still_replace_the_file_name_title(machine, ctl):
    machine.speech_model = True
    machine.writing_model = True
    machine.choose(transcription_mode="local")
    nid, store = _import(ctl, "Kitchen call.m4a")
    assert wait_until(lambda: store.get_note(nid)["summary"] != "", timeout=20)
    assert store.get_note(nid)["title_locked"] == 0


# ── 3. transcribe again with nothing set up ─────────────────────────────────

def test_transcribe_again_with_nothing_set_up_is_the_calm_state(machine, ctl):
    nid, store = _import(ctl, "Kitchen call.m4a")
    assert call(ctl, "POST", f"/notes/{nid}/retranscribe", {})["status"] == 200
    assert wait_until(lambda: store.get_note(nid)["status"] == "ready" and store.get_note(nid)["error"], timeout=20)
    assert store.get_note(nid)["error"] == mt.SPEECH_NEEDS_SETUP
    shown = call(ctl, "GET", f"/notes/{nid}")["body"]
    assert shown["error"] == "", "the bridge hands the page no problem; it draws Your audio is kept."
    assert shown["has_audio"] is True
