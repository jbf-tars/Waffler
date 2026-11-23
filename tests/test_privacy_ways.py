"""The privacy sentences say where speech and text go for the way in use:
on this computer, on your own key, or Waffler Plus (ui/logic.js privacyText).
The page as written (index.html) holds the own-key wording."""
import json
import re

from test_ui_logic import js, needs_node, read

pytestmark = needs_node

PLUS = {"ok": True, "available": True, "signed_in": True, "plan": "plus", "plus_chosen": True, "plus_state": "ready"}
KEY = {"groq_key_set": True}
LLM_READY = {"ok": True, "available": True, "state": "ready"}


def say(job, acct=None, settings=None, llm=None, mac=True):
    st = {"acct": acct, "settings": settings or {}, "llm": llm}
    return js(f"L.privacyText({json.dumps(job)}, {json.dumps(st)}, {json.dumps(mac)})")


def flat(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_own_key_wording_is_what_the_page_says():
    page = flat(read("index.html"))
    for job in ("dictation", "transforms", "selected", "cursor", "command", "transformsHow",
                "commandHow", "about"):
        assert say(job, settings=KEY) in page, job
        assert say(job) == say(job, settings=KEY), job


def test_plus_wording():
    assert say("dictation", PLUS).endswith("That goes to Waffler Plus, which keeps none of it. "
                                          "Up to 200 words of your Vocabulary go along as a spelling hint.")
    assert "sends it to Waffler Plus, which keeps none of it." in say("transforms", PLUS)
    assert "sends it to Waffler Plus, which keeps none of it." in say("selected", PLUS)
    assert "sends them to Waffler Plus, which keeps none of it." in say("cursor", PLUS)
    assert "to Waffler Plus, which keeps none of it." in say("command", PLUS)
    assert "sent to Waffler Plus" in say("transformsHow", PLUS)
    assert "Waffler service" in say("about", PLUS)
    for job in ("dictation", "transforms", "selected", "cursor", "command", "transformsHow", "commandHow", "about"):
        assert "your own key" not in say(job, PLUS), job


def test_plus_that_is_not_chosen_or_has_gone_is_not_used():
    for acct in ({**PLUS, "plus_chosen": False}, {**PLUS, "plus_state": "ended"},
                 {**PLUS, "plus_state": "signed_out"}, {**PLUS, "plan": "free"}, {**PLUS, "signed_in": False}):
        assert say("dictation", acct, KEY) == say("dictation", settings=KEY), acct


def test_free_model_wording():
    local = {"transcription_mode": "local"}
    # No key: nothing leaves.
    assert say("dictation", settings=local) == "Everything stays on this computer. Nothing you say leaves this computer."
    # A key and the model here to tidy: nothing leaves.
    assert say("dictation", settings={**local, **KEY, "writing_mode": "local"}, llm=LLM_READY) \
        == say("dictation", settings=local)
    # A key tidies the text: only the text goes.
    mixed = say("dictation", settings={**local, **KEY})
    assert "Your voice is turned into text here" in mixed and "on your own key" in mixed
    assert "Nothing you say leaves" not in mixed
    # Clean-up off: nothing goes.
    assert say("dictation", settings={**local, **KEY, "cleanup": "off"}) == say("dictation", settings=local)
    assert "on this computer" in say("command", settings={**local, **KEY})
    assert "No Waffler server" not in say("about", settings=local)


def test_transforms_never_follow_the_free_model():
    local = {"transcription_mode": "local", **KEY, "writing_mode": "local"}
    assert say("transforms", settings=local, llm=LLM_READY) == say("transforms", settings=KEY)


def test_plus_wins_over_the_free_model_and_windows_has_no_accessibility():
    assert "Waffler Plus" in say("dictation", PLUS, {"transcription_mode": "local"})
    win = say("transforms", PLUS, mac=False)
    assert "Accessibility" not in win and "by copying it" in win and "—" not in win


def test_nothing_else_says_where_it_goes():
    for job in ("dictation", "transforms", "selected", "cursor", "command", "transformsHow", "commandHow", "about"):
        for acct, settings in ((PLUS, {}), (None, {"transcription_mode": "local"}), (None, KEY)):
            text = say(job, acct, settings)
            assert "—" not in text and "  " not in text and " - " not in text, text


def test_app_applies_it_and_the_ids_exist():
    app, html = read("app.js"), read("index.html")
    for el in ("privHowDictation", "privTransformsDesc", "privSelectedDesc", "privCursorDesc", "privCommandDesc",
               "transformsHowReads", "commandHowSends", "aboutHowVoice"):
        assert f"'{el}'" in app and f'id="{el}"' in html, el
    assert "window.refreshPrivacyWays" in app and "refreshPrivacyWays" in read("howitruns.js")
