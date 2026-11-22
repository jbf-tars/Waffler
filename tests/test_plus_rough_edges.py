"""Three rough edges of Waffler Plus, smoothed.

1. After a fallback (signed out, the plan ended, the service unreachable)
   Plus STAYS the chosen way and is used again by itself when it works.
   Unreachable is temporary: that one dictation goes through the key or this
   computer, the small pill says so, and nothing is changed.
2. First-run setup: a signed-in account that has Plus is asked for no key
   and no download. Setup says "Waffler Plus is ready." and goes on. Anyone
   else sees setup exactly as before.
3. Keys and providers does not name a key's provider as the one "In use"
   while Plus is doing the work.

The dictation runs for real (tests/_pipeline_harness.py) against a stand-in
for the account service; the page's decisions are ui/logic.js, run in Node.
"""
import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _pipeline_harness as H  # noqa: E402
import transcribe_whisper as tw  # noqa: E402
import unsent  # noqa: E402
import user_messages as um  # noqa: E402
from src import account  # noqa: E402
from test_plus_dictation import EMAIL, RAW, TEXT, dictate, pipeline, transcriber, wav  # noqa: E402
from test_plus_dictation import plus  # noqa: E402,F401
from test_plus_page import FREE, OLDER, OUT, PLUS, PLUS_UNTIL  # noqa: E402
from test_ui_logic import code_only, js, needs_node, read  # noqa: E402


def chosen(fake) -> bool:
    return json.loads((fake.data / "settings.json").read_text(encoding="utf-8")).get(account.PLUS_SETTING) is True


def again(p, n):
    p._processing_id = n
    worker = H.run_process(p, n)
    worker.join(10)
    assert not worker.is_alive()


# ── 1. Plus stays the chosen way ─────────────────────────────────────────────

FALLBACKS = [("ended", "groq", "Plus ended. Using your key."),
             ("signed_out", "groq", "Signed out. Using your key."),
             ("offline", "groq", "Plus unreachable. Using your key."),
             ("ended", "", "Plus ended. Using this computer."),
             ("signed_out", "", "Signed out. Using this computer."),
             ("offline", "", "Plus unreachable. Using this computer.")]


def gone(fake, why):
    if why == "ended":
        fake.end_plus(EMAIL)
    elif why == "signed_out":
        fake.sessions.clear()
    else:
        fake.offline = True


@pytest.mark.parametrize("why, groq, words", FALLBACKS)
def test_a_fallback_says_so_once_changes_nothing_and_plus_comes_back_by_itself(plus, monkeypatch, why, groq, words):
    plus.model = "ready"
    p = pipeline(plus, monkeypatch, groq=groq)
    gone(plus, why)
    dictate(p)
    said = "From my own key." if groq else "Heard on this computer."
    assert p.clipboard.pastes == [said], "this very dictation is not lost"
    (toast,) = p.overlay.toasts()
    assert toast["heading"] == words and len(words.split()) <= 6
    assert H.as_pill(toast).text == words, "the small pill shows exactly these words"
    assert p.stored == [] and chosen(plus), "nothing is changed: Plus is still the chosen way"
    # The next one falls back too, and is not announced again.
    again(p, 2)
    assert p.clipboard.pastes == [said, said] and len(p.overlay.toasts()) == 1
    assert p.stored == [] and chosen(plus)
    if why == "signed_out":
        return                                            # signing in again is the person's to do
    # It works again: used again, with nothing re-picked.
    if why == "ended":
        plus.give_plus(EMAIL)
        assert account.refresh_plan()["plan"] == "plus"
    else:
        plus.offline = False
    again(p, 3)
    assert p.clipboard.pastes[-1] == TEXT and len(plus.dictations) == 1
    assert len(p.overlay.toasts()) == 1, "coming back is not news"
    # And if it goes again later, that is said again.
    gone(plus, why)
    if why == "ended":
        account.refresh_plan()
    again(p, 4)
    assert p.clipboard.pastes[-1] == said and len(p.overlay.toasts()) == 2


def test_unreachable_is_for_that_one_dictation_and_the_service_is_tried_each_time(plus, monkeypatch):
    t, cloud = transcriber(monkeypatch)
    plus.offline = True
    assert t.transcribe_sync(wav()) == "from my own key"
    assert t.last_plus_gone == "offline" and t.last_plus is None and cloud.calls == 1
    tried = len(plus.requests)
    assert t.transcribe_sync(wav()) == "from my own key" and len(plus.requests) > tried
    plus.offline = False
    assert t.transcribe_sync(wav()) == RAW and t.last_plus_gone == "" and t._backend == "plus"
    assert cloud.calls == 2


def test_unreachable_with_nothing_else_keeps_the_recording_to_send_later(plus, monkeypatch):
    t, _ = transcriber(monkeypatch, groq="")
    plus.offline = True
    with pytest.raises(tw.PlusError) as e:
        t.transcribe_sync(wav())
    assert str(e.value) == account.PLUS_OFFLINE and t.last_plus_gone == "offline"
    assert unsent.classify_reason(str(e.value)) == unsent.REASON_OFFLINE, "sent again when the internet is back"
    p = pipeline(plus, monkeypatch, groq="")
    dictate(p)
    entry = H.history(p)[-1]
    assert entry["failed"] and Path(entry["audio_path"]).exists() and p.stored == [] and chosen(plus)


def test_busy_is_not_unreachable(plus, monkeypatch):
    """The service answered: it is asked again later, and no key stands in."""
    plus.dictate_refusal = (503, "Busy.", "", {"Retry-After": "5"})
    t, cloud = transcriber(monkeypatch)
    with pytest.raises(tw.PlusError) as e:
        t.transcribe_sync(wav())
    assert str(e.value) == account.DICTATE_BUSY and cloud.calls == 0 and t.last_plus_gone == ""


def test_the_pills_words():
    assert um.plus_gone_message("offline", False) == (
        "Plus unreachable. Using your key.",
        "Waffler Plus couldn't be reached, so this dictation used your key. "
        "Your words were pasted as usual.")
    for why in ("ended", "signed_out", "offline"):
        for local in (True, False):
            heading, body = um.plus_gone_message(why, local)
            assert len(heading.split()) <= 6 and body.endswith("Your words were pasted as usual.")


def test_the_app_no_longer_unchooses_plus_and_asks_whether_it_is_back():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    body = app[app.index("    def _plus_gone("):app.index("    def _stt_is_local(")]
    code = "\n".join(line for line in body.split("\n") if not line.strip().startswith("#"))
    code = code[code.index('"""', code.index('"""') + 3):]
    assert "_store_setting" not in code, "the choice is the person's: a fallback never changes it"
    assert "_account_plan_soon()" in code, "the plan is read again, so a Plus that is back is seen"


# ── 2. first-run setup ───────────────────────────────────────────────────────

@needs_node
def test_setup_for_a_signed_in_plus_account_asks_for_nothing():
    assert js("L.SETUP_PLUS") == {"title": "Waffler Plus is ready.", "lede": "No key. No download.",
                                 "go": "Next, try it out."}
    for acct in (PLUS, PLUS_UNTIL):
        assert js(f"L.setupPlusReady({acct}, '')") is True
    assert js(f"L.setupPlusReady({PLUS_UNTIL}, 'local')") is True, "Plus is already the chosen way"
    assert js("L.setupConnectDone('plus', 'plus', false, null)") is True, "Continue is there at once"
    assert js("L.setupStepLabel('connect', 'plus')") == "Waffler Plus"
    assert js("L.setupModelWait(null, 'plus').waiting") is False, "no download is waited for"


@needs_node
@pytest.mark.parametrize("acct", [OUT, FREE, OLDER, "null", "undefined",
                                  PLUS.replace("signed_in: true", "signed_in: false")])
def test_anyone_else_sees_setup_exactly_as_before(acct):
    assert js(f"L.setupPlusReady({acct}, '')") is False
    assert js("L.setupConnectDone('choose', '', false, null)") is False
    assert js("L.setupStepLabel('connect', '')") == "How it runs"


@needs_node
def test_someone_who_already_chose_a_key_or_this_computer_keeps_their_way():
    assert js(f"L.setupPlusReady({PLUS}, 'key')") is False
    assert js(f"L.setupPlusReady({PLUS}, 'local')") is False


def test_setups_plus_screen_is_made_by_the_script_and_chooses_plus():
    assert "Plus" not in read("index.html"), "the page as written still says nothing of Plus"
    app = code_only(read("app.js"))
    show = app[app.index("function wizShowPlus("):]
    show = show[:show.index("\n}\n")]
    assert "WL.SETUP_PLUS.title" in show and "textContent" in show and "innerHTML" not in show
    assert "wizSetState('connect', 'plus')" in show and "_wizWay = 'plus'" in show
    assert "save_settings({ dictation_plus: true })" in show
    # Asked when setup opens, and again when the account's plan is read (it is not known at once).
    assert "if (WL.setupPlusReady(acct, _wizWay)) wizShowPlus();" in app
    assert "window.wizAccountChanged" in app and "window.wizAccountChanged" in code_only(read("account.js"))
    label = app[app.index("function _wizLabelWay("):]
    assert "'plus'" in label[:label.index("\n}\n")]


def test_setups_practice_goes_through_plus_with_no_key():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    start = app[app.index("    def wizard_start_hotkey_test("):app.index("    def wizard_stop_hotkey_test(")]
    refuse = start[start.index("if not openai_key and not groq_key and not _local_stt.mode_is_local():"):]
    assert refuse.index("if not _plus_chosen_here():") < refuse.index("No API key found.")
    assert "_wizard_transcriber.plus_allowed = True" in start
    finish = app[app.index("def _wizard_finish_practice("):app.index("def _push_wizard_js(")]
    assert "last_plus" in finish, "the service's tidy text is what Waffler wrote"


def test_the_practice_shows_what_the_service_tidied(plus, monkeypatch):
    """_wizard_finish_practice's clean-up step, with the practice's own
    transcriber having gone through Plus."""
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(app).body if isinstance(n, ast.FunctionDef) and n.name == "_wizard_plus_words")
    ns = {}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), ns)
    words = ns["_wizard_plus_words"]
    t, _ = transcriber(monkeypatch, groq="")
    assert t.transcribe_sync(wav()) == RAW
    assert words(t) == TEXT
    t.last_plus = {"text": RAW, "cleaned": False, "code": "model_calls_cap"}
    assert words(t) is None, "not tidied: the words as said, like any dictation without the clean-up"
    assert words(None) is None and words(object()) is None


# ── 3. Keys and providers ────────────────────────────────────────────────────

KEYED = "{groq_key_set: true, groq_key_masked: 'gsk_…abcd', api_key_set: true, transcription_backend: 'groq', styling_backend: 'groq'}"
WITH_PLUS = KEYED.replace("{", "{plus_active: true, ", 1)


@needs_node
def test_no_keys_provider_is_in_use_while_plus_does_the_work():
    rows = js(f"L.keyRows(null, {WITH_PLUS})")
    assert [r["status"] for r in rows if r["hasKey"]] == ["Saved", "Saved"], "saved, and not in use"
    assert all(r["status"] != "In use" for r in rows)
    assert js(f"L.keyCardView('groq', null, {WITH_PLUS}, null, false).state") == "Saved"
    assert js(f"L.providerPill(L.keyRows(null, {WITH_PLUS})[0]).pill") == "Standby"
    assert js(f"L.backendsLine({WITH_PLUS})") == "Speech to text: Waffler Plus · Clean-up: Waffler Plus"
    flow = js(f"L.flowView({WITH_PLUS})")
    assert flow["speech"] == {"by": "Waffler Plus", "model": ""} == flow["cleanup"]
    assert js(f"L.usesView({WITH_PLUS})") == {"speech": "Waffler Plus", "cleanup": "Waffler Plus"}
    assert js(f"L.privacyFlow({WITH_PLUS}).name") == "Waffler Plus"
    assert js(f"L.aboutLine('3.16.0', {WITH_PLUS})") == "v3.16.0", "no provider's model is named"
    # With no key at all it is not "No key yet" either.
    assert js("L.backendsLine({plus_active: true, transcription_backend: 'none', styling_backend: 'none'})") \
        == "Speech to text: Waffler Plus · Clean-up: Waffler Plus"


@needs_node
def test_without_plus_the_page_says_what_it_said():
    rows = js(f"L.keyRows(null, {KEYED})")
    assert rows[0]["status"] == "In use" and js(f"L.backendsLine({KEYED})") == "Speech to text: Groq · Clean-up: Groq"
    assert js(f"L.keyRows(null, {KEYED.replace('{', '{plus_active: false, ', 1)})")[0]["status"] == "In use"


def test_the_settings_say_whether_plus_is_doing_the_work(plus):
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    body = app[app.index("    def get_settings(self)"):app.index("    def save_settings(self")]
    assert '"plus_active":' in body and "plus_ready(stored)" in body
    assert account.plus_ready({account.PLUS_SETTING: True}) is True
    plus.end_plus(EMAIL)
    account.refresh_plan()
    assert account.plus_ready({account.PLUS_SETTING: True}) is False, "gone: the key is in use again, and says so"
