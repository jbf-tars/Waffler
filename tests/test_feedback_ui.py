"""The window's side of sounds and tips: two switches under Settings, General,
"Feedback", and a line on Privacy and data about the tip memory.

The pure parts live in ui/logic.js and run in Node; the rest is checked by
source, as the other window tests do.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from test_ui_logic import code_only, js, needs_node, read  # noqa: E402


# ── the pure part ────────────────────────────────────────────────────────

@needs_node
def test_sounds_are_off_and_tips_are_on_until_the_settings_say_otherwise():
    assert js("L.feedbackView(undefined)") == {"sounds": False, "tips": True}
    assert js("L.feedbackView({})") == {"sounds": False, "tips": True}
    assert js("L.feedbackView({sounds: true, tips: false})") == {"sounds": True, "tips": False}


@needs_node
def test_a_stray_value_means_the_default_as_the_engine_reads_it():
    for stray in ("'true'", "'false'", "1", "0", "null"):
        v = js(f"L.feedbackView({{sounds: {stray}, tips: {stray}}})")
        assert v == {"sounds": False, "tips": True}, stray


@needs_node
def test_the_toast_after_changing_one_is_a_plain_sentence():
    assert js("L.feedbackSaved('sounds', true)") == "Waffler will play soft tones."
    assert js("L.feedbackSaved('sounds', false)") == "Sounds are off."
    assert js("L.feedbackSaved('tips', true)") == "Waffler will show an occasional tip."
    assert js("L.feedbackSaved('tips', false)") == "Tips are off."
    assert js("L.feedbackSaved('other', true)") == ""


def test_the_words_have_no_em_dash_and_no_jargon():
    src = read("logic.js")
    block = src[src.index("function feedbackView"):src.index("// General: the microphone meter")]
    assert "—" not in block and "–" not in block
    for jargon in ("NSSound", "winsound", "notifier", "deafen", "pre-roll"):
        assert jargon not in block


# ── Settings, General ────────────────────────────────────────────────────

def _general():
    html = read("index.html")
    return html[html.index('id="secGeneral"'):html.index('id="secKeys"')]


def test_the_two_rows_sit_in_their_own_group_between_dictation_and_startup():
    general = _general()
    assert general.index('<h2 class="sh2">Dictation</h2>') < general.index('<h2 class="sh2">Feedback</h2>') \
        < general.index('<h2 class="sh2">Startup</h2>')
    group = general[general.index('<h2 class="sh2">Feedback</h2>'):general.index('<h2 class="sh2">Startup</h2>')]
    assert group.index('id="soundsRow"') < group.index('id="tipsRow"')


def test_each_row_is_a_labelled_switch_with_a_plain_description():
    group = _general()
    group = group[group.index('<h2 class="sh2">Feedback</h2>'):group.index('<h2 class="sh2">Startup</h2>')]
    assert '<label for="soundsToggle">Sounds</label>' in group
    assert '<label for="tipsToggle">Show tips</label>' in group
    for ident, handler in (("soundsToggle", "sounds"), ("tipsToggle", "tips")):
        assert re.search(r'<input type="checkbox" class="tog" role="switch" id="%s" '
                         r'onchange="setFeedback\(\'%s\', this.checked\)">' % (ident, handler), group), ident
    assert ("Soft tones when recording starts and stops, and when hands-free locks. "
            "The microphone ignores a fraction of a second around each one.") in group
    assert ("An occasional hint, such as how to hold the key. "
            "Each one can be hidden for good.") in group
    assert "—" not in group and "–" not in group


def test_sentence_case_labels():
    group = _general()
    group = group[group.index('<h2 class="sh2">Feedback</h2>'):group.index('<h2 class="sh2">Startup</h2>')]
    for label in re.findall(r'<div class="row-t"><label[^>]*>([^<]+)</label>', group):
        assert label[0].isupper() and label[1:] == label[1:].lower() or label == "Show tips", label


def test_the_window_reads_and_saves_both_switches():
    app = code_only(read("app.js"))
    assert "function renderFeedbackRows()" in app
    load = app[app.index("async function loadSettings()"):app.index("function renderClipboardRow()")]
    assert "renderFeedbackRows();" in load
    assert "WL.feedbackView(_lastSettings)" in app
    setter = app[app.index("async function setFeedback(key, on)"):]
    setter = setter[:setter.index("\n}\n")]
    assert "saveSetting(key, !!on)" in setter
    assert "_lastSettings[key] = !!on;" in setter
    assert "renderFeedbackRows();" in setter, "a refused save puts the switch back"
    save = app[app.index("async function saveSetting(key, value)"):]
    save = save[:save.index("\n}\n")]
    assert "key === 'sounds' || key === 'tips'" in save and "WL.feedbackSaved(key, value)" in save


# ── Privacy and data ─────────────────────────────────────────────────────

def _privacy():
    html = read("index.html")
    sec = html[html.index('id="secPrivacy"'):]
    return sec[:sec.index("</section>")]


def test_privacy_says_what_the_tip_memory_is():
    sec = _privacy()
    row = sec[sec.index('id="tipsMemoryRow"'):]
    row = row[:row.index("</div>\n            </div>")]
    assert "Tips seen" in row
    assert "Which tips Waffler has shown, and which you hid, so it doesn't repeat them. Counts and times only." in row
    assert "—" not in row and "–" not in row


def test_delete_all_my_data_says_it_takes_the_tip_memory_too():
    sec = _privacy()
    assert "History, usage, recordings, logs and which tips you've seen. Your keys, words, snippets and settings stay." in sec


def test_the_policy_and_readme_say_the_same_thing():
    policy = (ROOT / "docs" / "CODE_SIGNING_POLICY.md").read_text(encoding="utf-8")
    assert "notifications.json" in policy
    assert "which tips" in policy.replace("\n", " ")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Optional sounds" in readme and "Don't tap, hold" in readme
