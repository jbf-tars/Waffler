"""The ranked list of microphones (S12, item 2): the setting, the choice, the
fallback, the migration of the single saved microphone, and what Settings
shows. Plain lists in, plain lists out: no device is opened.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import mic_devices as md  # noqa: E402
import mic_ranking as mr  # noqa: E402
import user_messages as um  # noqa: E402
from mic_devices import Device  # noqa: E402


def info(name, ch=1, api=0):
    return {"name": name, "max_input_channels": ch, "max_output_channels": 0, "hostapi": api}


MAC_APIS = [{"name": "Core Audio"}]


def pa(*names, default=0):
    return md.devices_from_portaudio([info(n) for n in names], MAC_APIS, default_index=default)


# ── the setting: reading ─────────────────────────────────────────────────

def test_no_setting_is_an_empty_list_that_follows_the_system():
    assert mr.ranking_of({}) == [] and mr.ranking_of(None) == [] and mr.ranking_of("x") == []
    assert not mr.has_ranking({})


def test_an_empty_list_is_a_decision_and_an_absent_one_is_not():
    assert mr.has_ranking({"mic_ranking": []}) and not mr.has_ranking({"mic_ranking": None})
    assert not mr.has_ranking({"mic_ranking": "Yeti"})


def test_a_damaged_list_reads_as_a_shorter_one_never_an_error():
    stored = {"mic_ranking": [{"name": "Yeti"}, 7, None, {"name": ""}, {"nope": 1},
                              {"name": "Yeti (2)"}, {"name": "Scarlett", "id": 5}]}
    assert mr.ranking_of(stored) == [{"name": "Yeti"}, {"name": "Scarlett"}]


def test_reading_never_keeps_more_than_eight():
    stored = {"mic_ranking": [{"name": f"Mic {i}"} for i in range(20)]}
    assert [e["name"] for e in mr.ranking_of(stored)] == [f"Mic {i}" for i in range(8)]


# ── the setting: validation of a save ────────────────────────────────────

GOOD = [{"name": "Yeti Stereo Microphone", "id": "uid:U1"}, {"name": "MacBook Pro Microphone"}]


def test_a_good_list_is_accepted_and_stored_as_it_came():
    assert mr.settings_problem({"mic_ranking": GOOD}) is None
    assert mr.settings_problem({"mic_ranking": []}) is None
    assert mr.merged({"mic_ranking": GOOD}) == GOOD


def test_a_save_that_does_not_name_it_is_not_checked():
    assert mr.settings_problem({"auto_paste": True}) is None
    assert not mr.touches({"auto_paste": True}) and mr.touches({"mic_ranking": []})


@pytest.mark.parametrize("bad", [
    "Yeti", None, 7, {"name": "Yeti"},
    [{"name": "Yeti"}, {"name": "Yeti"}],                          # twice
    [{"name": "Yeti"}, {"name": "yeti (2)"}],                      # twice, as the system numbers it
    [{"name": ""}], [{"name": "   "}], [{"name": 5}], [{}], ["Yeti"], [None], [["Yeti"]],
    [{"name": "Yeti", "id": 5}], [{"name": "Yeti", "id": ["a"]}],
    [{"name": "x" * 201}],
    [{"name": f"Mic {i}"} for i in range(9)],                      # more than 8
])
def test_a_bad_list_is_refused_with_a_plain_sentence(bad):
    problem = mr.settings_problem({"mic_ranking": bad})
    assert problem == um.MIC_RANKING_UNKNOWN
    assert "—" not in problem and "–" not in problem


def test_eight_is_the_most():
    assert mr.settings_problem({"mic_ranking": [{"name": f"Mic {i}"} for i in range(8)]}) is None


# ── editing ──────────────────────────────────────────────────────────────

def test_promote_puts_a_microphone_first_and_does_not_list_it_twice():
    ranking = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
    assert [e["name"] for e in mr.promote(ranking, {"name": "C", "id": "api:x"})] == ["C", "A", "B"]
    assert [e["name"] for e in mr.promote(ranking, {"name": "b"})] == ["b", "A", "C"]
    assert [e["name"] for e in mr.promote([], {"name": "New"})] == ["New"]


def test_promote_into_a_full_list_drops_the_lowest():
    full = [{"name": f"Mic {i}"} for i in range(8)]
    got = mr.promote(full, {"name": "New"})
    assert len(got) == 8 and got[0]["name"] == "New" and got[-1]["name"] == "Mic 6"


def test_promote_ignores_a_bad_entry():
    assert mr.promote([{"name": "A"}], {"name": ""}) == [{"name": "A"}]
    assert mr.promote([{"name": "A"}], None) == [{"name": "A"}]


def test_without_removes_by_name():
    assert mr.without([{"name": "A"}, {"name": "B (2)"}], "b") == [{"name": "A"}]


# ── choosing: the highest-ranked microphone that is here ─────────────────

def resolver(stored, devices, legacy=None, live=None):
    return mr.open_resolver(lambda: stored, lambda: legacy, lambda: devices, lambda: live)


def test_the_highest_ranked_present_microphone_is_chosen_by_index():
    here = pa("MacBook Pro Microphone", "USB Headset", "Yeti")
    stored = {"mic_ranking": [{"name": "Scarlett"}, {"name": "Yeti"}, {"name": "USB Headset"}]}
    assert resolver(stored, here)() == 2                       # Scarlett is not here; Yeti is


def test_rank_one_wins_when_it_is_here():
    here = pa("MacBook Pro Microphone", "USB Headset", "Yeti")
    stored = {"mic_ranking": [{"name": "USB Headset"}, {"name": "Yeti"}]}
    assert resolver(stored, here)() == 1


def test_when_none_of_them_is_here_it_follows_the_system():
    here = pa("MacBook Pro Microphone", "AirPods Pro", default=1)
    stored = {"mic_ranking": [{"name": "Yeti"}, {"name": "Scarlett"}]}
    assert resolver(stored, here)() is None


def test_an_empty_list_follows_the_system():
    here = pa("MacBook Pro Microphone", "Yeti")
    assert resolver({"mic_ranking": []}, here)() is None
    assert resolver({}, here)() is None


def test_the_choice_is_made_again_every_time_so_a_device_that_comes_back_is_used():
    stored = {"mic_ranking": [{"name": "Yeti"}, {"name": "MacBook Pro Microphone"}]}
    devices = pa("MacBook Pro Microphone")
    pick = mr.open_resolver(lambda: stored, None, lambda: devices, lambda: None)
    assert pick() == 0                                          # Yeti is unplugged
    devices[:] = pa("MacBook Pro Microphone", "USB Headset", "Yeti")
    assert pick() == 2                                          # plugged back in, at another number


def test_the_systems_list_decides_who_is_here_and_portaudio_gives_the_index():
    live = [Device("Yeti", uid="U1", host_api="Core Audio")]
    stored = {"mic_ranking": [{"name": "Yeti Studio", "id": "uid:U1"}]}      # renamed since it was ranked
    here = pa("MacBook Pro Microphone", "Yeti")
    assert resolver(stored, here, live=live)() == 1
    # Listed by the system but not (yet) by PortAudio: follow the system rather than guess.
    assert resolver(stored, pa("MacBook Pro Microphone"), live=live)() is None


def test_a_failure_anywhere_is_follow_the_system():
    def broken():
        raise RuntimeError("PortAudio is not up")
    assert mr.open_resolver(lambda: {"mic_ranking": [{"name": "A"}]}, None, broken, lambda: None)() is None
    assert mr.open_resolver(broken, None, lambda: [], lambda: None)() is None


# ── the single microphone of an older version ────────────────────────────

def test_the_old_index_becomes_a_ranking_of_one():
    here = pa("MacBook Pro Microphone", "USB Headset", "Yeti")
    assert mr.migrate(2, here) == [{"name": "Yeti", "id": "api:Core Audio"}]


def test_with_the_systems_list_the_entry_carries_its_identifier():
    here = pa("MacBook Pro Microphone", "Yeti")
    live = [Device("Yeti", uid="U1", host_api="Core Audio")]
    assert mr.migrate(1, here, live) == [{"name": "Yeti", "id": "uid:U1"}]


@pytest.mark.parametrize("legacy", [9, -1, None, "2", True, 1.5])
def test_an_index_that_is_not_an_input_now_is_not_guessed(legacy):
    assert mr.migrate(legacy, pa("MacBook Pro Microphone", "Yeti")) is None


def test_while_nothing_is_decided_the_old_index_is_what_is_used():
    here = pa("MacBook Pro Microphone", "USB Headset", "Yeti")
    assert resolver({}, here, legacy=2)() == 2                  # the person's choice, as before
    assert resolver({}, here, legacy=None)() is None
    assert resolver({}, here, legacy=7)() is None               # not an input any more: follow the system


def test_a_saved_ranking_ends_the_old_indexs_say_even_when_empty():
    here = pa("MacBook Pro Microphone", "USB Headset", "Yeti")
    assert resolver({"mic_ranking": []}, here, legacy=2)() is None
    assert resolver({"mic_ranking": [{"name": "USB Headset"}]}, here, legacy=2)() == 1


def test_effective_reads_the_saved_ranking_or_the_migrated_one():
    here = pa("MacBook Pro Microphone", "Yeti")
    assert mr.effective({"mic_ranking": [{"name": "A"}]}, 1, here) == [{"name": "A"}]
    assert mr.effective({}, 1, here) == [{"name": "Yeti", "id": "api:Core Audio"}]
    assert mr.effective({}, None, here) == []


# ── what Settings shows ──────────────────────────────────────────────────

def devices_with_virtual():
    return md.devices_from_portaudio(
        [info("MacBook Pro Microphone"), info("iPhone Microphone"), info("Yeti"),
         info("Microsoft Teams Audio"), info("AirPods Pro")], MAC_APIS, default_index=0)


def test_the_view_marks_what_is_here_and_what_is_in_use():
    v = mr.setup_view([{"name": "Scarlett"}, {"name": "Yeti"}, {"name": "MacBook Pro Microphone"}],
                      devices_with_virtual())
    assert [(r["name"], r["present"], r["active"]) for r in v["ranking"]] == [
        ("Scarlett", False, False), ("Yeti", True, True), ("MacBook Pro Microphone", True, False)]
    assert v["following_system"] is False
    assert v["system"] == "MacBook Pro Microphone"


def test_an_empty_list_follows_the_system_and_says_which():
    v = mr.setup_view([], devices_with_virtual())
    assert v["ranking"] == [] and v["following_system"] is True
    assert v["system"] == "MacBook Pro Microphone" and v["system_avoided"] is False


def test_a_system_microphone_waffler_avoids_is_flagged_so_the_page_can_explain_it():
    devices = md.devices_from_portaudio([info("MacBook Pro Microphone"), info("AirPods Pro")],
                                        MAC_APIS, default_index=1)
    v = mr.setup_view([], devices)
    assert v["system"] == "AirPods Pro" and v["system_avoided"] is True


def test_a_wired_headset_is_avoided_as_a_default_but_is_not_called_bluetooth_in_the_window():
    """audio.py avoids anything named "headset" when it is the default (it
    cannot tell a wired one); the window only says Bluetooth when it knows."""
    devices = md.devices_from_portaudio([info("MacBook Pro Microphone"), info("Logitech USB Headset"),
                                         info("AirPods Pro")], MAC_APIS, default_index=1)
    v = mr.setup_view([{"name": "Logitech USB Headset"}], devices)
    assert v["system_avoided"] is True
    assert v["ranking"][0]["bluetooth"] is False
    assert {a["name"]: a["bluetooth"] for a in v["add"]} == {"MacBook Pro Microphone": False, "AirPods Pro": True}


def test_the_system_says_how_a_device_is_connected_and_that_wins_over_its_name():
    assert md.Device("Gadget", transport="blue").known_bluetooth
    assert not md.Device("My AirPods", transport="usb ").known_bluetooth      # a USB dongle named so
    assert md.Device("AirPods Pro").known_bluetooth and not md.Device("Jabra Evolve2 65").known_bluetooth
    assert md.Device("Jabra Evolve2 65").bluetooth                            # still avoided as a default


def test_a_list_with_nothing_here_follows_the_system_too():
    v = mr.setup_view([{"name": "Scarlett"}], devices_with_virtual())
    assert v["following_system"] is True and v["ranking"][0]["present"] is False


def test_what_can_be_added_leaves_out_the_ranked_and_sets_the_virtual_apart():
    v = mr.setup_view([{"name": "Yeti"}], devices_with_virtual())
    assert [a["name"] for a in v["add"]] == ["MacBook Pro Microphone", "iPhone Microphone", "AirPods Pro"]
    assert [a["name"] for a in v["other"]] == ["Microsoft Teams Audio"]
    assert [a["name"] for a in v["add"] if a["bluetooth"]] == ["AirPods Pro"]
    assert [a["name"] for a in v["add"] if a["default"]] == ["MacBook Pro Microphone"]
    assert v["add"][0]["id"] == "api:Core Audio"


def test_a_full_list_says_so():
    v = mr.setup_view([{"name": f"Mic {i}"} for i in range(8)], [])
    assert v["full"] is True and v["max"] == 8
    assert mr.setup_view([], [])["full"] is False
