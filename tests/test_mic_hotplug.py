"""A microphone that appears (S12, item 3): the decision table, what is new,
and the memory of what was decided. No device is opened.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import mic_devices as md  # noqa: E402
import mic_hotplug as hp  # noqa: E402
from mic_devices import Device  # noqa: E402

YETI = Device("Yeti Stereo Microphone", host_api="Core Audio")
AIRPODS = Device("AirPods Pro", host_api="Core Audio")
TEAMS = Device("Microsoft Teams Audio", host_api="Core Audio")
BUILTIN = Device("MacBook Pro Microphone", host_api="Core Audio", is_default=True)


# ── the decision table ───────────────────────────────────────────────────

@pytest.mark.parametrize("device, ranked, decided, recording, want", [
    # a new, ordinary microphone, nothing else going on: ask
    (YETI, False, None, False, hp.PROMPT),
    # ...while a take is recorded: wait until it is over
    (YETI, False, None, True, hp.WAIT),
    # already in the list: nothing to ask, even mid-take
    (YETI, True, None, False, hp.RANKED),
    (YETI, True, None, True, hp.RANKED),
    # asked before, whatever the answer or whether it was answered at all
    (YETI, False, hp.NOT_NOW, False, hp.DECIDED),
    (YETI, False, hp.USE, False, hp.DECIDED),
    (YETI, False, hp.SEEN, False, hp.DECIDED),
    (YETI, False, hp.NOT_NOW, True, hp.DECIDED),
    # a Bluetooth headset Waffler avoids: say nothing, in every state
    (AIRPODS, False, None, False, hp.BLUETOOTH),
    (AIRPODS, False, None, True, hp.BLUETOOTH),
    (AIRPODS, True, None, False, hp.BLUETOOTH),
    # software devices
    (TEAMS, False, None, False, hp.VIRTUAL),
    (Device("Gadget", transport="virt"), False, None, False, hp.VIRTUAL),
    (Device("Gadget", transport="blue"), False, None, False, hp.BLUETOOTH),
])
def test_the_decision_table(device, ranked, decided, recording, want):
    assert hp.decide(device, ranked=ranked, decided=decided, recording=recording) == want


def test_an_unknown_decision_in_a_damaged_file_is_not_a_decision():
    assert hp.decide(YETI, ranked=False, decided="maybe", recording=False) == hp.PROMPT


# ── what is new ──────────────────────────────────────────────────────────

def test_the_first_list_is_the_baseline_and_nothing_in_it_is_new():
    t = hp.HotplugTracker()
    assert t.update([BUILTIN, YETI]) == []
    assert t.update([BUILTIN, YETI]) == []


def test_a_device_that_appears_is_new_once_and_stays_pending_until_dealt_with():
    t = hp.HotplugTracker()
    t.update([BUILTIN])
    assert [d.name for d in t.update([BUILTIN, YETI])] == ["Yeti Stereo Microphone"]
    assert [d.name for d in t.update([BUILTIN, YETI])] == ["Yeti Stereo Microphone"]     # still waiting
    t.resolve(YETI)
    assert t.update([BUILTIN, YETI]) == []


def test_a_pending_device_that_is_unplugged_again_is_forgotten():
    t = hp.HotplugTracker()
    t.update([BUILTIN])
    t.update([BUILTIN, YETI])
    assert t.update([BUILTIN]) == []
    assert t.pending() == []


def test_a_device_plugged_in_again_is_new_again():
    t = hp.HotplugTracker()
    t.update([BUILTIN, YETI])
    t.update([BUILTIN])
    assert [d.name for d in t.update([BUILTIN, YETI])] == ["Yeti Stereo Microphone"]


def test_the_system_numbering_a_second_device_of_the_same_name_is_not_a_new_one():
    t = hp.HotplugTracker()
    t.update([Device("USB Mic")])
    assert t.update([Device("USB Mic (2)")]) == []


def test_windows_listing_a_device_under_each_host_api_is_one_device():
    t = hp.HotplugTracker()
    t.update([Device("Headset (USB)", index=0, host_api="MME")])
    new = t.update([Device("Headset (USB)", index=0, host_api="MME"),
                    Device("Mic (Realtek High Definition Audio)", index=1, host_api="MME"),
                    Device("Mic (Realtek High Definition Audio)", index=4, host_api="WASAPI")])
    assert len(new) == 1


def test_a_reset_starts_a_new_baseline():
    t = hp.HotplugTracker()
    t.update([BUILTIN])
    t.reset()
    assert t.update([BUILTIN, YETI]) == []


# ── what was decided, kept ───────────────────────────────────────────────

def memory(tmp_path, **kw):
    return hp.DeviceMemory(path=tmp_path / "mic_devices.json", **kw)


def test_a_decision_is_kept_across_restarts_by_name(tmp_path):
    m = memory(tmp_path)
    assert m.decision(YETI) is None
    m.record(YETI, hp.NOT_NOW)
    again = memory(tmp_path)
    assert again.decision(YETI) == hp.NOT_NOW
    assert again.decision("yeti stereo microphone (2)") == hp.NOT_NOW


def test_the_file_holds_names_and_decisions_and_no_one_elses_words(tmp_path):
    m = memory(tmp_path, clock=lambda: 1700000000.0)
    m.record(YETI, hp.USE)
    data = json.loads((tmp_path / "mic_devices.json").read_text(encoding="utf-8"))
    assert data == {"version": 1, "devices": {"yeti stereo microphone": {"decision": "use", "at": 1700000000.0}}}


def test_seen_never_replaces_a_real_answer_but_an_answer_replaces_seen(tmp_path):
    m = memory(tmp_path)
    m.record(YETI, hp.SEEN)
    m.record(YETI, hp.NOT_NOW)
    m.record(YETI, hp.SEEN)
    assert m.decision(YETI) == hp.NOT_NOW


def test_nonsense_is_not_recorded(tmp_path):
    m = memory(tmp_path)
    m.record(YETI, "maybe")
    m.record("", hp.USE)
    m.record(None, hp.USE)
    assert m.count() == 0 and not (tmp_path / "mic_devices.json").exists()


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"devices": []}', '{"devices": {"a": 5}}',
                                     '{"devices": {"a": {"decision": "bogus"}}}', "﻿{}"])
def test_a_damaged_file_reads_as_nothing_decided(tmp_path, content):
    (tmp_path / "mic_devices.json").write_text(content, encoding="utf-8")
    assert memory(tmp_path).decision("a") is None
    m = memory(tmp_path)
    m.record(YETI, hp.USE)                      # and it is written afresh
    assert memory(tmp_path).decision(YETI) == hp.USE


def test_a_file_that_cannot_be_written_costs_only_the_memory(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the folder should be", encoding="utf-8")
    m = hp.DeviceMemory(path=blocked / "mic_devices.json")
    m.record(YETI, hp.NOT_NOW)                  # must not raise
    assert m.decision(YETI) == hp.NOT_NOW       # remembered for this run


def test_forget_deletes_the_file_and_what_was_in_memory(tmp_path):
    m = memory(tmp_path)
    m.record(YETI, hp.USE)
    m.forget()
    assert not (tmp_path / "mic_devices.json").exists()
    assert m.decision(YETI) is None
    m.forget()                                   # nothing to delete is fine


def test_the_oldest_are_forgotten_past_the_limit(tmp_path):
    t = [0.0]

    def clock():
        t[0] += 1
        return t[0]
    m = memory(tmp_path, clock=clock)
    for i in range(hp.MAX_REMEMBERED + 5):
        m.record(f"Mic {i}", hp.NOT_NOW)
    assert m.count() == hp.MAX_REMEMBERED
    assert m.decision("Mic 0") is None and m.decision(f"Mic {hp.MAX_REMEMBERED + 4}") == hp.NOT_NOW


def test_the_data_folder_is_asked_at_use_not_at_import(tmp_path, monkeypatch):
    monkeypatch.setenv("WAFFLER_DATA_DIR", str(tmp_path / "elsewhere"))
    m = hp.DeviceMemory()
    m.record(YETI, hp.USE)
    assert (tmp_path / "elsewhere" / "mic_devices.json").exists()


def test_the_file_name_is_the_one_the_privacy_lists_name():
    import privacy_data
    assert hp.STATE_FILE == "mic_devices.json"
    assert hp.STATE_FILE in privacy_data.DATA_FILES
    assert hp.STATE_FILE not in privacy_data.KEPT_FILES
