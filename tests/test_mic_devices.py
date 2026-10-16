"""Which microphone is which (S12): names, identity, and renumbering.

A microphone is kept by name (and host API or the system's identifier), never
by PortAudio index, because the index is a position in a list that is built
again before every stream. These tests are made of plain lists: nothing opens
a device and PortAudio is never started.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import mic_devices as md  # noqa: E402
from mic_devices import Device  # noqa: E402


def info(name, ch=1, api=0):
    return {"name": name, "max_input_channels": ch, "max_output_channels": 0, "hostapi": api}


MAC_APIS = [{"name": "Core Audio"}]
WIN_APIS = [{"name": "MME"}, {"name": "Windows DirectSound"}, {"name": "Windows WASAPI"}]


# ── names ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("a, b", [
    ("Yeti Stereo Microphone", "yeti  stereo microphone"),
    ("USB Mic (2)", "USB Mic"),
    ("USB Mic", "USB Mic (3)"),
    ("Café Mic", "Café Mic"),                     # one accent, written two ways
    ("Microphone (Realtek High Defini", "Microphone (Realtek High Definition Audio)"),
])
def test_one_microphone_under_other_spellings(a, b):
    assert md.same_name(a, b) and md.same_name(b, a)


@pytest.mark.parametrize("a, b", [
    ("Yeti", "Yeti Nano"),
    ("USB Mic (Front)", "USB Mic (Rear)"),
    ("", "Yeti"), (None, "Yeti"), ("Yeti", None), ("", ""),
    # Only a name cut at exactly the Windows limit is a prefix of another.
    ("Microphone (Realtek", "Microphone (Realtek High Definition Audio)"),
])
def test_two_microphones(a, b):
    assert not md.same_name(a, b)


def test_norm_name_is_safe_on_anything():
    assert md.norm_name(None) == "" and md.norm_name(7) == ""
    assert md.norm_name("  MacBook   Pro Microphone  ") == "macbook pro microphone"


@pytest.mark.parametrize("name", [
    "Microsoft Teams Audio", "BlackHole 2ch", "Soundflower (64ch)", "Loopback Audio",
    "VB-Cable", "CABLE Output (VB-Audio Virtual Cable)", "Aggregate Device",
    "Multi-Output Device", "Stereo Mix (Realtek Audio)", "ZoomAudioDevice", "OBS Virtual Camera",
    "Krisp Microphone", "Voicemeeter Out B1",
])
def test_virtual_devices_are_known_by_name(name):
    assert md.is_virtual(name) is True


@pytest.mark.parametrize("name", [
    "MacBook Pro Microphone", "Yeti Stereo Microphone", "iPhone Microphone", "AirPods Pro",
    "Microphone (Realtek High Definition Audio)", "USB Audio Device", "Scarlett 2i2 USB",
    "Jabra Evolve2 65", "External Microphone",
])
def test_real_microphones_are_not_called_virtual(name):
    assert md.is_virtual(name) is False


def test_builtin_and_bluetooth_by_name():
    assert md.is_builtin("MacBook Pro Microphone") and md.is_builtin("Built-in Microphone")
    assert not md.is_builtin("Yeti")
    assert md.is_bluetooth("AirPods Pro") and md.is_bluetooth("Jabra Evolve2 65")
    assert not md.is_bluetooth("MacBook Pro Microphone")


def test_the_system_can_say_it_in_a_way_a_name_cannot():
    plain = Device("Gadget", transport="virt")
    assert plain.virtual
    assert Device("Gadget", transport="blue").bluetooth
    assert Device("Gadget", transport="bltn").builtin
    assert not Device("Gadget", transport="usb ").virtual


# ── turning PortAudio's answers into devices ─────────────────────────────

def test_only_inputs_with_a_name_are_listed_and_the_index_is_the_position():
    infos = [info("Speakers", ch=0), info("MacBook Pro Microphone"), info("   "), info("Yeti", ch=2)]
    got = md.devices_from_portaudio(infos, MAC_APIS, default_index=1)
    assert [(d.name, d.index, d.channels, d.is_default) for d in got] == [
        ("MacBook Pro Microphone", 1, 1, True), ("Yeti", 3, 2, False)]
    assert all(d.host_api == "Core Audio" for d in got)


def test_garbage_in_the_list_is_skipped_not_raised():
    got = md.devices_from_portaudio([None, 7, {"name": 5, "max_input_channels": 1},
                                     {"name": "Ok", "max_input_channels": "x"}, info("Fine")],
                                    [], None)
    assert [d.name for d in got] == ["Fine"]
    assert md.devices_from_portaudio(None) == []


def test_the_default_host_api_is_marked_primary():
    infos = [info("Mic", api=0), info("Mic", api=2)]
    got = md.devices_from_portaudio(infos, WIN_APIS, primary_api=2)
    assert [d.primary_api for d in got] == [False, True]


# ── renumbering: the point of all of it ──────────────────────────────────

def test_a_saved_microphone_is_found_by_name_after_the_indexes_move():
    entry = {"name": "Yeti Stereo Microphone", "id": "api:Core Audio"}
    monday = md.devices_from_portaudio(
        [info("MacBook Pro Microphone"), info("Yeti Stereo Microphone")], MAC_APIS)
    tuesday = md.devices_from_portaudio(           # a headset was plugged in; Yeti is device 2 now
        [info("MacBook Pro Microphone"), info("USB Headset"), info("Yeti Stereo Microphone")],
        MAC_APIS)
    assert md.find(entry, monday).index == 1
    assert md.find(entry, tuesday).index == 2


def test_the_old_index_would_have_been_the_wrong_microphone_on_tuesday():
    """What the name instead of the number protects against."""
    tuesday = md.devices_from_portaudio(
        [info("MacBook Pro Microphone"), info("USB Headset"), info("Yeti Stereo Microphone")],
        MAC_APIS)
    assert [d for d in tuesday if d.index == 1][0].name == "USB Headset"
    assert md.find({"name": "Yeti Stereo Microphone"}, tuesday).name == "Yeti Stereo Microphone"


def test_a_microphone_that_is_not_plugged_in_is_not_found():
    here = md.devices_from_portaudio([info("MacBook Pro Microphone")], MAC_APIS)
    assert md.find({"name": "Yeti Stereo Microphone"}, here) is None
    assert md.find({"name": "Yeti"}, []) is None
    assert md.find(None, here) is None and md.find("Yeti", here) is None


def test_between_two_of_one_name_the_same_host_api_wins_then_the_primary_one():
    devs = md.devices_from_portaudio(
        [info("Microphone (USB)", api=0), info("Microphone (USB)", api=1), info("Microphone (USB)", api=2)],
        WIN_APIS, primary_api=1)
    assert md.find({"name": "Microphone (USB)", "id": "api:Windows WASAPI"}, devs).index == 2
    assert md.find({"name": "Microphone (USB)", "id": "api:Gone"}, devs).index == 1     # primary
    assert md.find({"name": "Microphone (USB)"}, devs).index == 1


def test_the_systems_own_identifier_finds_a_renamed_device():
    devs = [Device("Studio Mic", uid="AppleUSBAudioEngine:Yeti:1", host_api="Core Audio")]
    entry = {"name": "Yeti Stereo Microphone", "id": "uid:AppleUSBAudioEngine:Yeti:1"}
    assert md.find(entry, devs).name == "Studio Mic"


def test_two_devices_of_one_name_are_told_apart_by_identifier():
    devs = [Device("USB Audio Device", uid="A", host_api="Core Audio"),
            Device("USB Audio Device (2)", uid="B", host_api="Core Audio")]
    assert md.find({"name": "USB Audio Device", "id": "uid:B"}, devs).uid == "B"


def test_an_entry_is_made_of_the_name_and_the_best_identity_there_is():
    assert Device("Yeti", uid="U1", host_api="Core Audio").entry() == {"name": "Yeti", "id": "uid:U1"}
    assert Device("Yeti", host_api="Core Audio").entry() == {"name": "Yeti", "id": "api:Core Audio"}
    assert Device("Yeti").entry() == {"name": "Yeti"}


def test_the_first_ranked_microphone_that_is_here_wins():
    here = md.devices_from_portaudio([info("MacBook Pro Microphone"), info("Scarlett 2i2")], MAC_APIS)
    ranking = [{"name": "Yeti"}, {"name": "Scarlett 2i2"}, {"name": "MacBook Pro Microphone"}]
    assert md.first_present(ranking, here).name == "Scarlett 2i2"
    assert md.first_present([{"name": "Yeti"}], here) is None
    assert md.first_present([], here) is None and md.first_present(None, here) is None


# ── one entry for each microphone ────────────────────────────────────────

def test_windows_lists_a_device_once_for_each_host_api_and_it_is_shown_once():
    infos = [info("Microphone (Realtek High Defini", api=0), info("Microphone (Realtek High Definition Audio)", api=2),
             info("Headset Microphone (USB)", api=0), info("Headset Microphone (USB)", api=2)]
    devs = md.devices_from_portaudio(infos, WIN_APIS, default_index=1, primary_api=2)
    one = md.distinct(devs)
    assert [d.name for d in one] == ["Microphone (Realtek High Definition Audio)", "Headset Microphone (USB)"]
    assert one[0].is_default and not one[1].is_default
    assert [d.index for d in one] == [1, 3]


def test_distinct_keeps_the_order_the_list_came_in():
    devs = [Device("B", index=0), Device("A", index=1), Device("C", index=2)]
    assert [d.name for d in md.distinct(devs)] == ["B", "A", "C"]


# ── PortAudio's index, only when a stream is made ────────────────────────

def test_an_index_is_worked_out_from_a_list_made_just_now():
    live = [Device("Yeti", uid="U", host_api="Core Audio")]           # the system's list has no PortAudio index
    pa = md.devices_from_portaudio([info("MacBook Pro Microphone"), info("USB Headset"), info("Yeti")], MAC_APIS)
    assert md.index_of(live[0], pa) == 2
    assert md.index_of(Device("Not Listed", uid="X"), pa) is None
    assert md.index_of(None, pa) is None
    assert md.index_of(pa[1], pa) == 1          # a device from PortAudio's own list keeps its index


def test_the_default_is_the_one_marked():
    pa = md.devices_from_portaudio([info("A"), info("B")], MAC_APIS, default_index=1)
    assert md.default_of(pa).name == "B"
    assert md.default_of([Device("A")]) is None


# ── the readers never raise ──────────────────────────────────────────────

def test_the_readers_give_nothing_rather_than_an_exception(monkeypatch):
    import audio
    monkeypatch.setattr(audio, "snapshot_devices", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert md.pa_inputs() == []
    monkeypatch.setattr(audio, "snapshot_devices", lambda *a, **k: None)
    assert md.pa_inputs() == []


def test_pa_inputs_reads_the_snapshot(monkeypatch):
    import audio
    snap = ([info("Speakers", ch=0), info("MacBook Pro Microphone")], MAC_APIS, 1, 0)
    monkeypatch.setattr(audio, "snapshot_devices", lambda *a, **k: snap)
    got = md.pa_inputs()
    assert [(d.name, d.index, d.is_default) for d in got] == [("MacBook Pro Microphone", 1, True)]


def test_current_inputs_prefers_the_systems_own_list(monkeypatch):
    monkeypatch.setattr(md, "live_inputs", lambda: [Device("Live")])
    monkeypatch.setattr(md, "pa_inputs", lambda: [Device("Cached")])
    assert [d.name for d in md.current_inputs()] == ["Live"]
    monkeypatch.setattr(md, "live_inputs", lambda: None)
    assert [d.name for d in md.current_inputs()] == ["Cached"]
