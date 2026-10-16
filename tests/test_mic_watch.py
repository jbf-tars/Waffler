"""The early silence watchdog's timeline (S12, item 5), with an injected clock.

``MicWatch.check`` is given the recorder's account of its input once a tick;
these tests play a take second by second against a fake input that has sound
when told to, and read off what the watch asks for and when. Nothing here
opens a microphone.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import mic_watch as W  # noqa: E402

TICK = 1 / 30


class Take:
    """A take against a fake input. ``sound(t)`` says whether a chunk with any
    sample but zero arrives at time ``t``; ``arrives(t)`` whether any chunk
    does (default: always, as a stream that delivers blocks of zeros)."""

    def __init__(self, sound, arrives=lambda t: True, stream_ok=lambda t: True, gated=lambda t: False):
        self.t = 0.0
        self.gated = gated
        self.sound, self.arrives, self.stream_ok = sound, arrives, stream_ok
        self.last_signal = 0.0
        self.last_chunk = 0.0
        self.watch = W.MicWatch(clock=lambda: self.t)
        self.events = []
        self.present = None
        self.reopen_takes = 0.4       # how long the fake re-open runs
        self._reopen_ends = None
        self.on_reopen = lambda take: None

    def health(self):
        return {"recording": True, "reopening": self._reopen_ends is not None,
                "stream_ok": self.stream_ok(self.t), "gated": self.gated(self.t),
                "quiet_s": self.t - self.last_signal, "stall_s": self.t - self.last_chunk,
                "device": "Fake Mic"}

    def step(self):
        self.t += TICK
        if self._reopen_ends is not None and self.t >= self._reopen_ends:
            self._reopen_ends = None
            self.watch.reopen_finished()
            self.on_reopen(self)
        if self._reopen_ends is None:
            if self.arrives(self.t):
                self.last_chunk = self.t
            if self.sound(self.t):
                self.last_signal = self.t
        event = self.watch.check(self.health(), self.present)
        if event == W.REOPEN:
            self._reopen_ends = self.t + self.reopen_takes
        if event:
            self.events.append((round(self.t, 2), event))
        return event

    def run(self, seconds):
        end = self.t + seconds
        while self.t < end:
            self.step()
        return self.events


def at(events, name):
    return [t for t, e in events if e == name]


# ── a healthy take, however long, says nothing ───────────────────────────

def test_a_twenty_minute_hands_free_take_with_a_voice_throughout_is_never_flagged():
    take = Take(sound=lambda t: True)
    assert take.run(1200) == []


def test_a_long_hands_free_take_with_two_dead_stretches_is_reopened_once_and_never_reported():
    """20 minutes: sound throughout except eight dead seconds at minute 7 and
    again at minute 14. The first stretch gets the one re-open (the stream
    comes back, so there is no message); the second is not re-opened again."""
    def sound(t):
        return not (420 <= t < 428 or 840 <= t < 848)
    take = Take(sound=sound)
    take.on_reopen = lambda tk: None
    events = take.run(1200)
    assert [e for _, e in events] == [W.REOPEN]
    assert 424.9 <= at(events, W.REOPEN)[0] < 425.1       # five seconds after the last sound


def test_quiet_but_real_speech_never_trips_it():
    """The input is quiet (3.15.1 lifts it) but never a block of exact zeros:
    the watch is told about zeros, not about loudness, so there is nothing to
    trip. A voice at the edge of the noise floor looks like any other sound."""
    take = Take(sound=lambda t: True)
    assert take.run(60) == []


def test_pauses_of_digital_silence_under_five_seconds_do_nothing():
    # Speech for 3 s, a gated pause of 4.8 s, again, many times over.
    cycle = 7.8
    take = Take(sound=lambda t: (t % cycle) < 3.0)
    assert take.run(600) == []


# ── the timeline ─────────────────────────────────────────────────────────

def test_a_dead_input_is_reopened_at_five_seconds_and_reported_at_fifteen():
    take = Take(sound=lambda t: False)
    events = take.run(30)
    assert [e for _, e in events] == [W.REOPEN, W.NOTIFY]
    assert 5.0 <= at(events, W.REOPEN)[0] < 5.1
    assert 15.0 <= at(events, W.NOTIFY)[0] < 15.1


def test_no_callbacks_at_all_is_the_same_as_silence():
    take = Take(sound=lambda t: False, arrives=lambda t: False)
    events = take.run(20)
    assert [e for _, e in events] == [W.REOPEN, W.NOTIFY]


def test_speech_first_and_then_a_dead_stream_counts_from_the_last_sound():
    take = Take(sound=lambda t: t < 12.0)
    events = take.run(40)
    assert 17.0 <= at(events, W.REOPEN)[0] < 17.1
    assert 27.0 <= at(events, W.NOTIFY)[0] < 27.1


def test_the_reopen_happens_once_in_a_take_however_often_it_goes_quiet():
    # Sound for 1 s, then 6 s of nothing, over and over; the re-open is spent after the first.
    take = Take(sound=lambda t: (t % 7.0) < 1.0)
    events = take.run(120)
    assert len(at(events, W.REOPEN)) == 1
    assert len(at(events, W.NOTIFY)) == 0      # never 15 s of nothing in a row


def test_if_the_reopen_brings_sound_back_there_is_no_message():
    take = Take(sound=lambda t: t >= 5.6 or t < 0)      # the new stream hears again
    events = take.run(60)
    assert [e for _, e in events] == [W.REOPEN]


def test_a_second_silence_after_the_reopen_is_not_reopened_again():
    take = Take(sound=lambda t: t < 1.0 or 7.0 <= t < 9.0)
    events = take.run(60)
    assert len(at(events, W.REOPEN)) == 1          # silence 1 to 7 s, then again from 9 s on
    assert 6.0 <= at(events, W.REOPEN)[0] < 6.1


def test_the_message_is_withdrawn_once_when_sound_returns_and_never_repeated():
    take = Take(sound=lambda t: t >= 20.0 and t < 25.0)
    events = take.run(90)
    names = [e for _, e in events]
    assert names == [W.REOPEN, W.NOTIFY, W.RECOVERED]
    assert 20.0 <= at(events, W.RECOVERED)[0] < 20.2


def test_the_message_waits_for_the_reopen_to_be_done_even_after_a_long_gap():
    """A clock that jumps (the computer slept for a minute inside the take)
    asks for the re-open first, and the message only after it is done."""
    take = Take(sound=lambda t: False)
    take.reopen_takes = 0.5
    take.t = 60.0
    take.last_signal = take.last_chunk = 0.0
    names = [take.step() for _ in range(3)]
    assert names[0] == W.REOPEN and names[1:] == [None, None]
    take.run(2.0)
    assert [e for _, e in take.events] == [W.REOPEN, W.NOTIFY]
    assert take.events[1][0] - 60.0 >= 0.5 + W.SETTLE_AFTER_REOPEN_S - 0.1


def test_nothing_is_said_while_the_stream_is_being_reopened():
    watch = W.MicWatch(clock=lambda: 100.0)
    h = {"recording": True, "reopening": True, "stream_ok": False, "quiet_s": 99.0, "stall_s": 99.0}
    assert watch.check(h, device_present=False) is None


# ── the microphone left ──────────────────────────────────────────────────

def test_a_stream_that_stopped_is_lost_after_a_moment_and_only_once():
    take = Take(sound=lambda t: t < 2.0, arrives=lambda t: t < 2.0, stream_ok=lambda t: t < 2.0)
    events = take.run(10)
    assert [e for _, e in events] == [W.LOST]
    assert 2.0 + W.STREAM_GONE_S <= events[0][0] < 2.0 + W.STREAM_GONE_S + 0.1


def test_a_stream_that_blinks_off_for_less_than_the_grace_is_not_lost():
    take = Take(sound=lambda t: True, stream_ok=lambda t: not (3.0 <= t < 3.3))
    assert take.run(10) == []


def test_a_device_the_system_no_longer_lists_is_lost_once_nothing_arrives():
    take = Take(sound=lambda t: t < 4.0, arrives=lambda t: t < 4.0)
    take.present = False
    events = take.run(10)
    assert [e for _, e in events] == [W.LOST]
    assert 4.0 + W.LOST_STALL_S <= events[0][0] < 4.0 + W.LOST_STALL_S + 0.1


def test_a_device_missing_from_the_list_while_the_stream_still_delivers_is_not_lost():
    """The list could be wrong (a name that differs); a stream that is still
    sending is proof enough that the microphone is there."""
    take = Take(sound=lambda t: True)
    take.present = False
    assert take.run(30) == []


def test_a_list_that_cannot_be_read_never_says_the_device_left():
    take = Take(sound=lambda t: t < 1.0, arrives=lambda t: t < 1.0)
    take.present = None
    events = take.run(4)
    assert W.LOST not in [e for _, e in events]


def test_once_lost_the_watch_is_finished():
    take = Take(sound=lambda t: False, arrives=lambda t: False, stream_ok=lambda t: False)
    assert [e for _, e in take.run(30)] == [W.LOST]


def test_it_says_nothing_when_nothing_is_recording():
    watch = W.MicWatch(clock=lambda: 0.0)
    assert watch.check({"recording": False, "quiet_s": 99.0, "stream_ok": False}) is None
    assert watch.check(None) is None
    assert watch.check({}) is None


# ── a noise-gated microphone ─────────────────────────────────────────────

def test_a_gated_microphones_pauses_are_pauses_not_a_dead_stream():
    """Sound at the start, then a long think: the stream has been seen to send
    sound after zeros, so 3 minutes of silence is only silence."""
    take = Take(sound=lambda t: t < 4.0, gated=lambda t: True)
    assert take.run(180) == []


def test_the_gate_is_learned_in_the_middle_of_a_silence():
    """Not yet known to be gated at 5 s, so the stream is opened again; once
    it is seen to be, there is no message at 15 s."""
    take = Take(sound=lambda t: False, gated=lambda t: t >= 6.0)
    events = take.run(40)
    assert [e for _, e in events] == [W.REOPEN]


def test_a_gated_microphone_that_leaves_is_still_lost():
    take = Take(sound=lambda t: t < 2.0, arrives=lambda t: t < 2.0, stream_ok=lambda t: t < 2.0,
                gated=lambda t: True)
    assert [e for _, e in take.run(10)] == [W.LOST]


def test_a_message_already_shown_is_still_withdrawn_when_sound_returns_on_a_gated_microphone():
    take = Take(sound=lambda t: t >= 20.0 and t < 25.0, gated=lambda t: t >= 18.0)
    names = [e for _, e in take.run(60)]
    assert names == [W.REOPEN, W.NOTIFY, W.RECOVERED]


def test_the_thresholds_are_the_named_ones():
    assert (W.REOPEN_AFTER_S, W.NOTIFY_AFTER_S) == (5.0, 15.0)
    assert W.REOPEN_AFTER_S < W.NOTIFY_AFTER_S
