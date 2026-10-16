"""Settings, General, Microphone (S12): the ranked list in the window.

The decisions are pure functions in ui/logic.js, run in Node; the markup, the
wiring and the words are source checks. The page itself was driven in the
Browser pane with a stubbed pywebview.api (see the work package's notes).
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from test_ui_logic import code_only, js, needs_node, read  # noqa: E402

VIEW = {
    "ok": True, "listed": True, "max": 8, "full": False, "system": "MacBook Pro Microphone",
    "system_avoided": False, "following_system": False,
    "ranking": [
        {"name": "Scarlett 2i2 USB", "id": "uid:S", "present": False, "active": False, "bluetooth": False},
        {"name": "Yeti Stereo Microphone", "id": "uid:Y", "present": True, "active": True, "bluetooth": False},
        {"name": "AirPods Pro", "id": "uid:A", "present": True, "active": False, "bluetooth": True},
    ],
    "add": [{"name": "MacBook Pro Microphone", "id": "uid:M", "bluetooth": False, "default": True},
            {"name": "iPhone Microphone", "id": "uid:I", "bluetooth": False, "default": False}],
    "other": [{"name": "Microsoft Teams Audio", "id": "uid:T", "bluetooth": False, "default": False}],
}


def L(expr):
    return js(expr, setup=f"const V = {__import__('json').dumps(VIEW)};")


# ── editing the list: pure ───────────────────────────────────────────────

@needs_node
def test_a_move_swaps_neighbours_and_stops_at_the_ends():
    assert L("L.micMove(L.micList(V), 1, -1).map((e) => e.name)") == [
        "Yeti Stereo Microphone", "Scarlett 2i2 USB", "AirPods Pro"]
    assert L("L.micMove(L.micList(V), 1, 1).map((e) => e.name)") == [
        "Scarlett 2i2 USB", "AirPods Pro", "Yeti Stereo Microphone"]
    assert L("L.micMove(L.micList(V), 0, -1).map((e) => e.name)")[0] == "Scarlett 2i2 USB"
    assert L("L.micMove(L.micList(V), 2, 1).map((e) => e.name)")[2] == "AirPods Pro"
    assert L("L.micMove(L.micList(V), 9, 1).length") == 3


@needs_node
def test_a_move_does_not_change_the_list_it_was_given():
    assert L("(() => { const l = L.micList(V); L.micMove(l, 0, 1); return l.map((e) => e.name); })()") == [
        "Scarlett 2i2 USB", "Yeti Stereo Microphone", "AirPods Pro"]


@needs_node
def test_remove_drops_one_and_the_ids_travel_with_the_names():
    got = L("L.micRemove(L.micList(V), 0)")
    assert got == [{"name": "Yeti Stereo Microphone", "id": "uid:Y"}, {"name": "AirPods Pro", "id": "uid:A"}]
    assert L("L.micRemove(L.micList(V), 7).length") == 3


@needs_node
def test_add_goes_to_the_end_once_and_not_past_eight():
    got = L("L.micAdd(L.micList(V), V.add[1], V.max)")
    assert got[-1] == {"name": "iPhone Microphone", "id": "uid:I"} and len(got) == 4
    # already there (even as the system numbers a second one): the list is unchanged
    assert L("L.micAdd(L.micList(V), {name: 'yeti stereo microphone (2)'}, 8).length") == 3
    assert L("L.micAdd(L.micList(V), {name: ''}, 8).length") == 3
    full = "Array.from({length: 8}, (_, i) => ({name: 'Mic ' + i}))"
    assert L(f"L.micAdd({full}, {{name: 'Extra'}}, 8).length") == 8


@needs_node
def test_an_entry_is_only_a_name_and_an_id():
    assert L("L.micEntry({name: '  Yeti  ', id: 'uid:Y', present: true, active: true})") == {"name": "Yeti", "id": "uid:Y"}
    assert L("L.micEntry({name: 'Yeti', id: 5})") == {"name": "Yeti"}


# ── what the rows say ────────────────────────────────────────────────────

@needs_node
def test_each_row_says_whether_it_is_in_use_or_not_plugged_in_or_bluetooth():
    rows = L("L.micRows(V)")
    assert [r["name"] for r in rows] == ["Scarlett 2i2 USB", "Yeti Stereo Microphone", "AirPods Pro"]
    assert [[c["text"] for c in r["chips"]] for r in rows] == [["Not plugged in"], ["In use"], ["Bluetooth"]]
    assert [r["dim"] for r in rows] == [True, False, False]
    assert rows[1]["chips"][0]["cls"] == "chip-ok"
    assert rows[2]["note"].startswith("A Bluetooth headset switches your music")
    assert rows[0]["note"] == "" and rows[1]["note"] == ""


@needs_node
def test_the_buttons_are_named_for_the_microphone_and_the_ends_cannot_move_out():
    rows = L("L.micRows(V)")
    assert rows[0]["canUp"] is False and rows[0]["canDown"] is True
    assert rows[2]["canUp"] is True and rows[2]["canDown"] is False
    assert rows[1]["upLabel"] == "Move Yeti Stereo Microphone up"
    assert rows[1]["downLabel"] == "Move Yeti Stereo Microphone down"
    assert rows[1]["removeLabel"] == "Remove Yeti Stereo Microphone from the list"
    assert [r["rank"] for r in rows] == [1, 2, 3] and rows[0]["total"] == 3


@needs_node
def test_the_top_line_names_the_microphone_in_use():
    s = L("L.micStatus(V)")
    assert s == {"title": "Using Yeti Stereo Microphone", "following": False,
                 "desc": "The first microphone in your list that is plugged in."}


@needs_node
def test_the_empty_state_is_follow_the_systems_microphone():
    s = L("L.micStatus({ok: true, ranking: [], system: 'MacBook Pro Microphone'})")
    assert s["title"] == "Follow the system's microphone" and s["following"] is True
    assert s["desc"] == ("Waffler uses your computer's microphone, MacBook Pro Microphone. "
                         "Add one below to use it whenever it is plugged in.")


@needs_node
def test_following_the_system_with_a_list_where_nothing_is_plugged_in_says_so():
    s = L("L.micStatus({ok: true, ranking: [{name: 'Yeti', present: false, active: false}], system: 'MacBook Pro Microphone'})")
    assert s["following"] is True and s["desc"].endswith("None of the microphones in your list is plugged in.")


@needs_node
def test_a_system_microphone_waffler_avoids_is_explained():
    s = L("L.micStatus({ok: true, ranking: [], system: 'AirPods Pro', system_avoided: true})")
    assert ("It looks like a Bluetooth headset, so Waffler listens through another microphone "
            "and your music stays in high quality.") in s["desc"]


@needs_node
def test_when_the_list_cannot_be_read_it_says_so_in_plain_words():
    assert L("L.micStatus({ok: false})")["desc"] == "Couldn't list your microphones."
    assert L("L.micStatus(null)")["desc"] == "Couldn't list your microphones."


# ── the Add menu ─────────────────────────────────────────────────────────

@needs_node
def test_the_add_menu_has_a_prompt_and_what_is_plugged_in_and_not_listed():
    m = L("L.micAddOptions(V, false)")
    assert [o["label"] for o in m["options"]] == [
        "Add a microphone", "MacBook Pro Microphone (system default)", "iPhone Microphone"]
    assert [o["value"] for o in m["options"]] == ["", "add:0", "add:1"]
    assert m["disabled"] is False and m["otherCount"] == 1 and m["otherLabel"] == "Show other devices (1)"


@needs_node
def test_virtual_devices_come_only_under_show_other_devices():
    m = L("L.micAddOptions(V, true)")
    assert [o["label"] for o in m["options"]][-1] == "Microsoft Teams Audio"
    assert m["options"][-1]["group"] == "Other devices" and m["options"][-1]["value"] == "other:0"
    assert m["otherLabel"] == "Hide other devices"
    assert all(o["group"] == "" for o in L("L.micAddOptions(V, false)")["options"])


@needs_node
def test_a_bluetooth_device_in_the_menu_is_marked():
    m = L("L.micAddOptions({ranking: [], add: [{name: 'AirPods Pro', bluetooth: true, default: false}], other: []}, false)")
    assert m["options"][1]["label"] == "AirPods Pro (Bluetooth)"


@needs_node
def test_nothing_to_add_and_a_full_list_are_said_and_the_menu_is_off():
    none = L("L.micAddOptions({ranking: [], add: [], other: []}, false)")
    assert none["options"][0]["label"] == "No other microphone found" and none["disabled"] is True
    full = L("L.micAddOptions({max: 8, ranking: Array.from({length: 8}, () => ({name: 'x'})), add: [{name: 'Y'}], other: []}, false)")
    assert full["options"][0]["label"] == "Your list is full (8)" and full["disabled"] is True


@needs_node
def test_a_choice_in_the_menu_finds_its_device():
    assert L("L.micPick(V, 'add:1')")["name"] == "iPhone Microphone"
    assert L("L.micPick(V, 'other:0')")["name"] == "Microsoft Teams Audio"
    for bad in ("", "add:9", "other:1", "x", "add:", "add:-1", None):
        assert L(f"L.micPick(V, {__import__('json').dumps(bad)})") is None


@needs_node
def test_what_a_change_says():
    assert L("L.micSaved('add', 'Yeti')") == "Yeti added to your list."
    assert L("L.micSaved('remove', 'Yeti')") == "Yeti removed."
    assert L("L.micSaved('move', 'Yeti', 1, 3)") == "Yeti is now number 1 of 3."
    assert L("L.micSaved('nonsense', 'Yeti')") == ""


@needs_node
def test_the_words_have_no_dashes_and_no_jargon():
    texts = L("""[L.micRows(V).map((r) => [r.note, r.upLabel, r.downLabel, r.removeLabel, ...r.chips.map((c) => c.text)]),
                   L.micStatus(V), L.micStatus({ok: true, ranking: [], system: 'AirPods Pro', system_avoided: true}),
                   L.micAddOptions(V, true).options.map((o) => o.label), L.micAddOptions(V, true).otherLabel,
                   L.micSaved('add', 'a'), L.micSaved('remove', 'a'), L.micSaved('move', 'a', 1, 2)]""")
    flat = re.findall(r'"([^"]*)"', __import__("json").dumps(texts))
    for t in flat:
        assert "—" not in t and "–" not in t and "--" not in t, t
        for jargon in ("PortAudio", "CoreAudio", "index", "uid:", "api:", "stream"):
            assert jargon not in t, t


# ── the markup and the wiring ────────────────────────────────────────────

def test_the_microphone_sheet_is_in_general_with_its_controls():
    html = read("index.html")
    general = html[html.index('id="secGeneral"'):html.index('id="secStyle"')]
    for part in ('<h2 class="sh2" id="micHeading" tabindex="-1">Microphone</h2>', 'id="micSheet"',
                 'id="micNowTitle"', 'id="micNowDesc"', 'id="micLevel"', 'id="micMeter"',
                 '<ol class="mic-rank" id="micRank" aria-label="Your microphones, best first"></ol>',
                 '<label for="micSelect">Add a microphone</label>', 'id="micOtherToggle"',
                 'id="micLive" role="status" aria-live="polite"'):
        assert part in general, part
    assert general.index('id="clipboardAfterPasteRow"') < general.index('id="micHeading"') < general.index('id="micSelect"')
    assert general.index('id="micSelect"') < general.index('<h2 class="sh2">Feedback</h2>')
    assert "the half-second before you press" in general
    # Still nine sections: the microphone is a sheet of General, not a tenth page.
    assert html.count('class="snav-item"') == 9


def test_the_toggle_for_other_devices_says_what_it_controls():
    html = read("index.html")
    tag = html[html.index('<button type="button" class="link-btn" id="micOtherToggle"'):]
    tag = tag[:tag.index(">")]
    assert 'aria-expanded="false"' in tag and 'aria-controls="micSelect"' in tag and "hidden" in tag


def test_the_window_draws_the_list_from_the_bridge_and_saves_the_whole_list():
    app = code_only(read("app.js"))
    for call in ("pywebview.api.get_mic_setup()", "pywebview.api.set_mic_ranking(list)",
                 "WL.micStatus(", "WL.micRows(", "WL.micAddOptions(", "WL.micPick(", "WL.micAdd(",
                 "WL.micMove(", "WL.micRemove(", "WL.micSaved("):
        assert call in app, call
    assert "window.waffler_show_microphone = function" in app
    assert "async function loadAudioDevices() { await loadMicrophones(true); }" in app
    assert "onMicChange" not in app
    # The buttons are real buttons with names, in a list.
    row = app[app.index("function renderMicrophones"):app.index("function _focusMic")]
    for act in ("up", "down", "remove"):
        assert f'data-act="{act}"' in row
    assert row.count("aria-label=") >= 3 and 'type="button"' in row


def test_focus_goes_somewhere_sensible_after_every_edit():
    app = code_only(read("app.js"))
    fn = app[app.index("function _focusMic"):app.index("async function saveMicRanking")]
    assert "button[data-act=" in fn and "micSelect" in fn and "micOtherToggle" in fn


def test_the_list_is_read_when_general_opens_so_it_is_never_hours_old():
    app = code_only(read("app.js"))
    show = app[app.index("function showSettingsSection("):app.index("function _micMeterWanted")]
    assert "if (sec === 'general') loadMicrophones(false);" in show


def test_the_list_is_looked_at_again_while_the_page_is_open_but_not_under_an_open_menu():
    app = code_only(read("app.js"))
    assert "if (++_micTicks % 25 === 0) loadMicrophones(false);" in app
    load = app[app.index("async function loadMicrophones"):app.index("async function loadAudioDevices")]
    assert "document.activeElement === sel" in load and "_micSaving" in load


def test_the_setup_practice_does_not_pin_the_computers_microphone():
    app = code_only(read("app.js"))
    start = app[app.index("async function wizStartPractice"):app.index("function wizStopPractice")]
    assert "set_audio_device" not in start
    assert "_wizMicPicked && sel" in start
    pick = app[app.index("async function wizMicChanged"):app.index("async function wizCheckFnKey")]
    assert "_wizMicPicked = true" in pick and "set_audio_device(Number(value))" in pick


def test_the_styles_use_tokens_only_and_are_still_in_the_narrow_layout():
    css = read("style.css")
    block = css[css.index("/* Settings, General, Microphone (S12)"):css.index("/* Settings, Transforms (beta)")]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", block), "colours come from tokens"
    assert "--faint" in block and block.count("--faint") == 1       # the decorative rank number only
    assert "animation" not in block and "transition" not in block
    assert ".mic-item .row-t { flex-wrap: wrap;" in block           # names and chips wrap when narrow


def test_the_section_keeps_the_meter_where_it_was():
    app = code_only(read("app.js"))
    meter = app[app.index("function _micMeterWanted"):app.index("let _micTicks")]
    assert "_settingsSection === 'general'" in meter
    html = read("index.html")
    assert html.index('id="micNow"') < html.index('id="micLevel"') < html.index('id="micRank"')
