"""Looking after the microphone while Waffler runs (S12): the prompt for a new
microphone, the early silence watchdog's actions, salvage when a microphone
leaves, and the closed-lid tip. The supervisor is run with fake hooks and an
injected clock; no device is opened and no overlay exists.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import mic_devices as md  # noqa: E402
import mic_hotplug as hp  # noqa: E402
import mic_supervisor as ms  # noqa: E402
import mic_watch as mw  # noqa: E402
import user_messages as um  # noqa: E402
from mic_devices import Device  # noqa: E402

BUILTIN = Device("MacBook Pro Microphone", host_api="Core Audio", is_default=True)
YETI = Device("Yeti Stereo Microphone", host_api="Core Audio", uid="UY")
SCARLETT = Device("Scarlett 2i2 USB", host_api="Core Audio")
AIRPODS = Device("AirPods Pro", host_api="Core Audio")
TEAMS = Device("Microsoft Teams Audio", host_api="Core Audio", transport="virt")


class World:
    """The app, as far as the supervisor can see it."""

    def __init__(self, tmp_path, settings=None, ranking=None, platform="darwin"):
        self.settings = {} if settings is None else settings
        self.ranking = [] if ranking is None else ranking
        self.recording = False
        self.busy = False
        self.allowed = True                 # the notifier's answer
        self.allowed_calls = []
        self.toasts = []
        self.hidden = []
        self.released = []
        self.promoted = []
        self.changed = 0
        self.rebuilt = 0
        self.stops = 0
        self.reopens = []
        self.health_value = {"recording": True, "reopening": False, "stream_ok": True,
                             "quiet_s": 0.0, "stall_s": 0.0, "device": "MacBook Pro Microphone"}
        self.opened = "MacBook Pro Microphone"
        self.lid = None
        self.now = 0.0
        self.slept = []
        self.logged = []
        self.memory = hp.DeviceMemory(path=tmp_path / "mic_devices.json")
        self.platform = platform

    def hooks(self):
        def toast_allowed(kind):
            self.allowed_calls.append(kind)
            return self.allowed

        def show(style, heading, body, buttons=None, timeout=None):
            self.toasts.append({"style": style, "heading": heading, "body": body,
                                "buttons": buttons, "timeout": timeout})

        def reopen():
            self.reopens.append(self.now)
            return True

        def stop():
            self.stops += 1

        def sleep(s):
            self.slept.append(s)

        def promote(entry):
            self.promoted.append(entry)

        return ms.Hooks(
            settings=lambda: self.settings, recording=lambda: self.recording,
            busy=lambda: self.busy, toast_allowed=toast_allowed, show_toast=show,
            health=lambda: dict(self.health_value), reopen=reopen,
            opened_device=lambda: self.opened, stop_take=stop,
            ranking=lambda: list(self.ranking), promote=promote,
            note_changed=lambda: setattr(self, "changed", self.changed + 1),
            memory=self.memory, hide_toast=lambda style=None: self.hidden.append(style),
            release_toast=self.released.append,
            rebuild_idle=lambda: setattr(self, "rebuilt", self.rebuilt + 1),
            lid_closed=lambda: self.lid, log=self.logged.append,
            clock=lambda: self.now, sleep=sleep, spawn=lambda fn: fn(), platform=self.platform)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def supervisor(world):
    return ms.MicSupervisor(world.hooks())


# ── a new microphone: asked once ─────────────────────────────────────────

def test_a_new_microphone_is_asked_about_with_use_it_and_not_now(world):
    s = supervisor(world)
    s.on_devices([BUILTIN])
    assert world.toasts == []                               # what was here at the start is not new
    s.on_devices([BUILTIN, YETI])
    assert len(world.toasts) == 1
    t = world.toasts[0]
    assert (t["heading"], t["body"]) == ("Yeti Stereo Microphone detected", "Use it for dictation?")
    assert [b["label"] for b in t["buttons"]] == ["Use it", "Not now"]
    assert [b["kind"] for b in t["buttons"]] == ["primary", "secondary"]
    assert all(b["action"].startswith(("mic_use:", "mic_later:")) for b in t["buttons"])
    assert world.allowed_calls == ["mic_detected"] and t["timeout"] == ms.PROMPT_S


def test_it_is_asked_once_however_often_the_list_is_looked_at(world):
    s = supervisor(world)
    s.on_devices([BUILTIN])
    for _ in range(5):
        s.on_devices([BUILTIN, YETI])
    assert len(world.toasts) == 1


def test_it_is_asked_once_for_the_device_even_if_it_is_unplugged_and_back_unanswered(world):
    s = supervisor(world)
    s.on_devices([BUILTIN])
    s.on_devices([BUILTIN, YETI])
    s.on_devices([BUILTIN])
    s.on_devices([BUILTIN, YETI])
    assert len(world.toasts) == 1
    assert world.memory.decision(YETI) == hp.SEEN


def test_use_it_puts_the_microphone_first_and_the_next_dictation_uses_it(world):
    s = supervisor(world)
    s.on_devices([BUILTIN])
    s.on_devices([BUILTIN, YETI])
    action = world.toasts[0]["buttons"][0]["action"]
    assert s.answer(action) is True
    assert world.promoted == [{"name": "Yeti Stereo Microphone", "id": "uid:UY"}]
    assert world.memory.decision(YETI) == hp.USE
    assert world.changed >= 1 and world.rebuilt >= 1


def test_not_now_is_remembered_for_that_device_for_good(world, tmp_path):
    s = supervisor(world)
    s.on_devices([BUILTIN])
    s.on_devices([BUILTIN, YETI])
    s.answer(world.toasts[0]["buttons"][1]["action"])
    assert world.promoted == [] and world.memory.decision(YETI) == hp.NOT_NOW
    s.on_devices([BUILTIN])
    s.on_devices([BUILTIN, YETI])
    assert len(world.toasts) == 1
    # And after a restart: a new supervisor reading the same file.
    again = ms.MicSupervisor(world.hooks())
    again.on_devices([BUILTIN])
    again.on_devices([BUILTIN, YETI])
    assert len(world.toasts) == 1


def test_a_different_new_microphone_is_asked_about_separately(world):
    s = supervisor(world)
    s.on_devices([BUILTIN])
    s.on_devices([BUILTIN, YETI])
    s.on_devices([BUILTIN, YETI, SCARLETT])
    assert [t["heading"] for t in world.toasts] == [
        "Yeti Stereo Microphone detected", "Scarlett 2i2 USB detected"]


def test_a_bluetooth_headset_waffler_avoids_is_not_asked_about(world):
    s = supervisor(world)
    s.on_devices([BUILTIN])
    s.on_devices([BUILTIN, AIRPODS])
    assert world.toasts == [] and world.allowed_calls == []
    assert world.memory.decision(AIRPODS) is None


def test_a_virtual_device_is_not_asked_about(world):
    s = supervisor(world)
    s.on_devices([BUILTIN])
    s.on_devices([BUILTIN, TEAMS])
    assert world.toasts == [] and world.allowed_calls == []


def test_a_microphone_already_ranked_is_not_asked_about(world):
    world.ranking = [{"name": "Yeti Stereo Microphone"}]
    s = supervisor(world)
    s.on_devices([BUILTIN])
    s.on_devices([BUILTIN, YETI])
    assert world.toasts == []


def test_nothing_is_asked_during_a_take_and_it_is_asked_when_the_take_is_over(world):
    s = supervisor(world)
    s.on_devices([BUILTIN])
    world.recording = True
    s.on_devices([BUILTIN, YETI])
    s.on_devices([BUILTIN, YETI])
    assert world.toasts == [] and world.allowed_calls == []
    world.recording = False
    world.busy = True                                       # still being turned into text
    s.on_devices([BUILTIN, YETI])
    assert world.toasts == []
    world.busy = False
    s.on_devices([BUILTIN, YETI])
    assert len(world.toasts) == 1


def test_with_tips_off_it_says_nothing_and_remembers_nothing(world):
    world.settings = {"tips": False}
    s = supervisor(world)
    s.on_devices([BUILTIN])
    s.on_devices([BUILTIN, YETI])
    assert world.toasts == [] and world.allowed_calls == []
    assert world.memory.decision(YETI) is None
    world.settings = {}                                    # turned back on later
    s.on_devices([BUILTIN, YETI])
    assert world.toasts == []                              # not asked about retroactively...
    s.on_devices([BUILTIN])
    s.on_devices([BUILTIN, YETI])
    assert len(world.toasts) == 1                          # ...but asked when it is plugged in again


def test_when_the_notifier_says_not_now_it_is_asked_again_on_the_next_look(world):
    s = supervisor(world)
    s.on_devices([BUILTIN])
    world.allowed = False                                  # another message holds the slot
    s.on_devices([BUILTIN, YETI])
    assert world.toasts == [] and world.memory.decision(YETI) is None
    world.allowed = True
    s.on_devices([BUILTIN, YETI])
    assert len(world.toasts) == 1


def test_a_microphone_that_never_finds_a_quiet_moment_is_given_up_on(world):
    s = supervisor(world)
    s.on_devices([BUILTIN])
    world.allowed = False
    for _ in range(ms.ASK_AGAIN_LIMIT + 2):
        s.on_devices([BUILTIN, YETI])
    world.allowed = True
    s.on_devices([BUILTIN, YETI])
    assert world.toasts == []                              # given up on, quietly


def test_a_stale_or_foreign_action_does_nothing(world):
    s = supervisor(world)
    assert s.answer("mic_use:feedface") is True and world.promoted == []
    assert s.answer("dismiss") is False and s.answer("tip_mute:hold_hint") is False
    assert s.answer(None) is False and s.answer("mic_use") is False


def test_it_never_raises_into_the_monitor(world):
    s = supervisor(world)
    s.h.ranking = lambda: (_ for _ in ()).throw(RuntimeError("settings"))
    s.on_devices([BUILTIN])                                # must not raise
    s.on_devices(None)
    assert any("skipped" in line for line in world.logged)


def test_the_log_never_names_a_device(world):
    s = supervisor(world)
    s.on_devices([BUILTIN])
    s.on_devices([BUILTIN, YETI])
    s.answer(world.toasts[0]["buttons"][0]["action"])
    assert not any("Yeti" in line for line in world.logged)


# ── which ranked microphone is best changes ──────────────────────────────

def test_a_higher_ranked_microphone_coming_back_makes_the_next_dictation_use_it(world):
    world.ranking = [{"name": "Yeti Stereo Microphone"}, {"name": "MacBook Pro Microphone"}]
    s = supervisor(world)
    s.on_devices([BUILTIN])
    assert world.changed == 0                              # the first look only learns
    s.on_devices([BUILTIN, YETI])
    assert world.changed == 1 and world.rebuilt == 1
    s.on_devices([BUILTIN, YETI])
    assert world.changed == 1                              # nothing new
    s.on_devices([BUILTIN])                                # unplugged: the best is the built-in again
    assert world.changed == 2


def test_during_a_take_the_stream_is_marked_stale_but_not_touched(world):
    world.ranking = [{"name": "Yeti Stereo Microphone"}, {"name": "MacBook Pro Microphone"}]
    s = supervisor(world)
    s.on_devices([BUILTIN])
    world.recording = True
    s.on_devices([BUILTIN, YETI])
    assert world.changed == 1 and world.rebuilt == 0


def test_saving_the_ranking_applies_it_now(world):
    s = supervisor(world)
    s.preferences_changed()
    assert world.changed == 1 and world.rebuilt == 1


# ── a take: the silence watchdog's actions ───────────────────────────────

def go_quiet(world, seconds, **extra):
    world.health_value.update(quiet_s=seconds, stall_s=extra.pop("stall", 0.0), **extra)


def test_five_seconds_of_silence_opens_the_stream_again_once(world):
    s = supervisor(world)
    s.take_started(7)
    go_quiet(world, 4.9)
    assert s.tick(7) is None
    go_quiet(world, 5.0)
    assert s.tick(7) == mw.REOPEN
    assert len(world.reopens) == 1
    assert s.tick(7) is None and len(world.reopens) == 1


def test_fifteen_seconds_says_so_with_a_button_to_choose_another_microphone(world):
    s = supervisor(world)
    s.take_started(7)
    go_quiet(world, 5.0)
    s.tick(7)
    world.now += 3.0                                        # the re-open finished long ago
    go_quiet(world, 15.0)
    assert s.tick(7) == mw.NOTIFY
    t = world.toasts[-1]
    assert (t["heading"], t["style"]) == ("Waffler can't hear your microphone", "error")
    assert t["buttons"][0] == {"label": "Choose microphone", "action": "choose_microphone",
                               "kind": "primary"}
    assert world.allowed_calls == ["mic_cant_hear"]
    assert s.told_cant_hear(7) and not s.told_cant_hear(8)
    assert s.tick(7) is None                                # once in a take


def test_the_message_is_withdrawn_when_sound_comes_back(world):
    s = supervisor(world)
    s.take_started(7)
    go_quiet(world, 5.0)
    s.tick(7)
    world.now += 3.0
    go_quiet(world, 15.0)
    s.tick(7)
    go_quiet(world, 0.1)
    assert s.tick(7) == mw.RECOVERED
    assert world.hidden == ["error"] and world.released == ["mic_cant_hear"]


def test_a_healthy_take_does_nothing_at_all(world):
    s = supervisor(world)
    s.take_started(7)
    for _ in range(1000):
        assert s.tick(7) is None
    assert world.reopens == [] and world.toasts == [] and world.stops == 0


def test_each_take_has_its_own_single_reopen(world):
    s = supervisor(world)
    s.take_started(7)
    go_quiet(world, 5.0)
    s.tick(7)
    s.take_started(8)
    go_quiet(world, 5.0)
    s.tick(8)
    assert len(world.reopens) == 2


def test_a_tick_for_a_take_that_is_not_the_current_one_is_ignored(world):
    s = supervisor(world)
    s.take_started(8)
    go_quiet(world, 30.0)
    assert s.tick(7) is None and world.reopens == []


def test_the_notice_is_governed_by_the_notifier(world):
    s = supervisor(world)
    s.take_started(7)
    go_quiet(world, 5.0)
    s.tick(7)
    world.now += 3.0
    world.allowed = False
    go_quiet(world, 15.0)
    s.tick(7)
    assert world.toasts == [] and not s.told_cant_hear(7)


def test_a_failing_reopen_still_lets_the_watch_go_on_to_the_message(world):
    s = supervisor(world)
    s.h.reopen = lambda: (_ for _ in ()).throw(RuntimeError("no such device"))
    s.take_started(7)
    go_quiet(world, 5.0)
    assert s.tick(7) == mw.REOPEN
    world.now += 3.0
    go_quiet(world, 15.0)
    assert s.tick(7) == mw.NOTIFY


def test_a_broken_health_check_never_raises_into_the_level_loop(world):
    s = supervisor(world)
    s.take_started(7)
    s.h.health = lambda: (_ for _ in ()).throw(RuntimeError("recorder gone"))
    assert s.tick(7) is None


# ── a take: the microphone leaves ────────────────────────────────────────

def test_a_stream_that_stopped_ends_the_take_once_and_the_notice_comes_after_the_words(world):
    s = supervisor(world)
    s.take_started(7)
    world.health_value.update(stream_ok=False)
    assert s.tick(7) is None                               # a blink is not a loss
    world.now += mw.STREAM_GONE_S
    assert s.tick(7) == mw.LOST
    assert world.stops == 1 and s.lost_in(7)
    assert world.toasts == []                              # not over the pill, not yet
    assert s.tick(7) is None and world.stops == 1          # and not again
    assert s.announce_lost(7) is True
    t = world.toasts[-1]
    assert (t["heading"], t["body"]) == ("Your microphone was disconnected",
                                         "Waffler used what it had recorded.")
    assert world.allowed_calls == ["mic_disconnected"]
    assert s.announce_lost(7) is False                     # said once


def test_a_device_the_system_stopped_listing_while_nothing_arrives_is_lost_fast(world):
    s = supervisor(world)
    s.on_devices([BUILTIN, YETI])
    world.opened = "Yeti Stereo Microphone"
    world.health_value.update(device="Yeti Stereo Microphone", stall_s=0.0)
    s.take_started(7)
    s.on_devices([BUILTIN])                                # Yeti unplugged
    assert s.tick(7) is None                               # the stream may just be between blocks
    world.health_value.update(stall_s=0.8)
    assert s.tick(7) == mw.LOST and world.stops == 1


def test_a_device_missing_from_the_list_but_still_delivering_is_not_lost(world):
    s = supervisor(world)
    s.on_devices([BUILTIN])                                # the list does not name the stream's device
    world.health_value.update(device="Some Name The List Lacks", stall_s=0.0, quiet_s=0.0)
    s.take_started(7)
    for _ in range(100):
        assert s.tick(7) is None
    assert world.stops == 0


def test_the_notice_is_for_that_dictation_only(world):
    s = supervisor(world)
    s.take_started(7)
    world.health_value.update(stream_ok=False)
    s.tick(7)
    world.now += 1.0
    s.tick(7)
    assert s.announce_lost(8) is False and world.toasts == []
    assert s.announce_lost(7) is True


def test_a_take_that_produced_nothing_is_not_told_it_was_used(world):
    s = supervisor(world)
    s.take_started(7)
    world.health_value.update(stream_ok=False)
    s.tick(7)
    world.now += 1.0
    s.tick(7)
    assert s.announce_lost(7, produced_text=False) is False
    assert world.toasts == [] and world.allowed_calls == []
    assert s.announce_lost(7) is False                      # and it is not kept for later


def test_a_notice_the_notifier_refuses_is_not_forced(world):
    s = supervisor(world)
    s.take_started(7)
    world.health_value.update(stream_ok=False)
    s.tick(7)
    world.now += 1.0
    s.tick(7)
    world.allowed = False
    assert s.announce_lost(7) is False and world.toasts == []


# ── the lid ──────────────────────────────────────────────────────────────

def test_a_closed_lid_on_the_builtin_microphone_gets_a_tip_once_the_dictation_is_over(world):
    world.lid = True
    s = supervisor(world)
    world.busy = True
    calls = []
    s.h.sleep = lambda secs: (calls.append(secs), setattr(world, "busy", len(calls) < 3))
    s.take_started(7)
    assert len(world.toasts) == 1
    t = world.toasts[0]
    assert (t["heading"], t["body"]) == ("Your Mac's lid is closed",
                                         "The built-in microphone sounds muffled. Choose another microphone.")
    assert [b["label"] for b in t["buttons"]] == ["Choose microphone", "Got it", "Don't show again"]
    assert t["buttons"][0]["action"] == "choose_microphone"
    assert t["buttons"][1]["action"] == "tip_dismiss:lid_closed" and t["buttons"][2]["action"] == "tip_mute:lid_closed"
    assert world.allowed_calls == ["lid_closed"]


@pytest.mark.parametrize("lid, device, platform", [
    (False, "MacBook Pro Microphone", "darwin"),            # open
    (None, "MacBook Pro Microphone", "darwin"),             # a Mac without a lid
    (True, "Yeti Stereo Microphone", "darwin"),             # another microphone is in use
    (True, "MacBook Pro Microphone", "win32"),              # not a Mac
])
def test_no_lid_tip_when_it_does_not_apply(tmp_path, lid, device, platform):
    w = World(tmp_path, platform=platform)
    w.lid, w.opened = lid, device
    s = ms.MicSupervisor(w.hooks())
    s.take_started(1)
    assert w.toasts == [] and w.allowed_calls == []


def test_the_lid_tip_gives_up_if_the_dictation_never_ends(world):
    world.lid = True
    world.busy = True
    s = supervisor(world)
    s.take_started(1)
    assert world.toasts == [] and sum(world.slept) >= ms.LID_WAIT_S


def test_the_lid_tip_waits_for_the_stream_to_have_a_name(world):
    world.lid = True
    names = iter([None, None, "MacBook Pro Microphone"])
    s = supervisor(world)
    s.h.opened_device = lambda: next(names, "MacBook Pro Microphone")
    s.take_started(1)
    assert len(world.toasts) == 1


def test_a_lid_reader_that_fails_is_no_tip(world):
    s = supervisor(world)
    s.h.lid_closed = lambda: (_ for _ in ()).throw(OSError("IOKit"))
    s.take_started(1)
    assert world.toasts == []


# ── delete my data ───────────────────────────────────────────────────────

def test_forget_clears_what_was_decided_and_what_waits(world):
    s = supervisor(world)
    s.on_devices([BUILTIN])
    s.on_devices([BUILTIN, YETI])
    s.forget()
    assert world.memory.decision(YETI) is None and not (world.memory.path()).exists()
    assert s.answer(world.toasts[0]["buttons"][0]["action"]) is True and world.promoted == []
    s.on_devices([BUILTIN])                                # a new baseline
    s.on_devices([BUILTIN, YETI])
    assert len(world.toasts) == 2


def test_the_messages_have_no_dashes_and_no_jargon():
    texts = [um.MIC_DISCONNECTED, um.MIC_CANT_HEAR, um.MIC_LID_CLOSED, um.mic_detected_message("Yeti"),
             (um.MIC_RANKING_UNKNOWN, "")]
    for heading, body in texts:
        for t in (heading, body):
            assert "—" not in t and "–" not in t
            for jargon in ("PortAudio", "CoreAudio", "stream", "index", "device id"):
                assert jargon not in t


def test_a_long_device_name_is_shortened_in_the_heading():
    heading, _ = um.mic_detected_message("A" * 120)
    assert len(heading) <= 50 and heading.endswith("\u2026 detected")
