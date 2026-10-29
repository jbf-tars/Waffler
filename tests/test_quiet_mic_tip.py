"""3.16.3: say so, on a Mac, when the microphone is set too low.

The owner's Mac had its input volume at 40 of 100: 56 of the owner's last 57 takes
were lifted by the quiet-audio lift, their speech arriving at a median
-42.5 dBFS (clear dictation is near -20) and raised by 21.5 dB, which raises
the room's noise too, and noisy near-silence is what Whisper turns into
"Thank you." Turning the input volume up fixes it at the source.

So each take's speech level is kept with its Journal entry (``speech_db``),
and when the median of the last ten is under -36 dBFS, Waffler reads the
Mac's input volume (osascript, 2 s timeout, at most once a day, never
raising) and, under 70, shows a tip, at most once a week, through the
notifier: "Open Sound settings", "Got it", "Don't show again". It respects
the Show tips switch, and never changes the volume itself. On Windows nothing
new happens.

Fakes only: no osascript is run, no settings pane is opened.
"""
import json
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import history_meta  # noqa: E402
import notifier as N  # noqa: E402
import privacy_data  # noqa: E402
import quiet_mic as Q  # noqa: E402
import user_messages as um  # noqa: E402

DAY = 24 * 3600.0


# ── the levels ──────────────────────────────────────────────────────────────

def entry(db, **extra):
    e = {"timestamp": "2026-10-28T10:00:00", "text": "words", "styled": "Words."}
    if db is not None:
        e["speech_db"] = db
    e.update(extra)
    return e


def test_the_levels_are_the_last_ten_dictations_that_have_one():
    history = ([entry(-20.0)] * 5 + [entry(-45.0)] * 8 + [entry(None)]
               + [entry(-40.0, kind="transform"), entry(-41.0, kind="command")]
               + [entry("loud"), entry(True), entry(float("nan"))] + [entry(-44.0)] * 2)
    assert Q.levels_of(history) == [-20.0] * 0 + [-45.0] * 8 + [-44.0] * 2
    assert Q.levels_of([entry(-20.0)] * 3) == [-20.0] * 3
    assert Q.levels_of("junk") == [] and Q.levels_of([None, 3, "x"]) == []


@pytest.mark.parametrize("levels,quiet", [
    ([-42.5] * 10, True),
    ([-42.5] * 9, False),                       # not ten takes yet
    ([-30.0] * 10, False),
    ([-36.0] * 10, False),                      # at the line is not under it
    ([-40.0] * 6 + [-20.0] * 4, True),          # the median, not the mean
    ([-20.0] * 6 + [-60.0] * 4, False),
])
def test_quiet_means_a_median_under_minus_36_over_ten_takes(levels, quiet):
    assert Q.is_quiet(levels) is quiet


def test_the_thresholds_are_the_chosen_ones():
    assert (Q.TAKES, Q.QUIET_LEVEL_DB, Q.LOW_VOLUME) == (10, -36.0, 70)
    assert Q.CHECK_EVERY_S == DAY and Q.READ_TIMEOUT_S == 2.0
    k = N.KINDS[Q.TIP_KIND]
    assert k.severity == N.TIP and k.category == "hints" and k.min_gap_s == 7 * DAY


# ── reading the input volume ────────────────────────────────────────────────

class Runner:
    def __init__(self, out="40\n", code=0, error=None):
        self.out, self.code, self.error = out, code, error
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(args, self.code, stdout=self.out, stderr="")


def test_the_volume_is_read_with_osascript_in_two_seconds():
    r = Runner("40\n")
    assert Q.read_input_volume(runner=r, platform="darwin") == 40
    args, kwargs = r.calls[0]
    assert args == ["/usr/bin/osascript", "-e", "input volume of (get volume settings)"]
    assert kwargs["timeout"] == 2.0 and kwargs["capture_output"] is True


@pytest.mark.parametrize("runner", [
    Runner("missing value\n"),                  # an input with no volume of its own
    Runner("", code=1),
    Runner("140\n"),
    Runner("-3\n"),
    Runner(error=subprocess.TimeoutExpired("osascript", 2.0)),
    Runner(error=FileNotFoundError("osascript")),
    Runner(error=RuntimeError("anything")),
])
def test_a_volume_that_cannot_be_read_is_none_and_never_raises(runner):
    assert Q.read_input_volume(runner=runner, platform="darwin") is None


def test_nothing_is_read_off_a_mac():
    r = Runner("40\n")
    assert Q.read_input_volume(runner=r, platform="win32") is None
    assert r.calls == []


def test_open_sound_settings_opens_the_input_pane_and_never_raises():
    r = Runner("")
    assert Q.open_sound_settings(runner=r) is True
    assert r.calls[0][0] == ["/usr/bin/open", "x-apple.systempreferences:com.apple.preference.sound?input"]
    assert Q.open_sound_settings(runner=Runner(error=OSError("no"))) is False


# ── the tip's words ─────────────────────────────────────────────────────────

def test_the_tip_says_what_to_do_in_plain_words():
    heading, body = um.quiet_mic_message(40)
    assert heading == "Your microphone is set low"
    assert body == ("Your Mac's microphone is set low (40%), so Waffler has to boost it and "
                    "picks up more background noise. Turn Input volume up to about 80% in "
                    "Sound settings for clearer dictation.")
    for text in (heading, body, um.OPEN_SOUND_SETTINGS):
        assert "—" not in text and "–" not in text
    assert um.OPEN_SOUND_SETTINGS == "Open Sound settings"


# ── the advisor ─────────────────────────────────────────────────────────────

class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Rig:
    """An advisor with fakes for everything it asks the app."""

    def __init__(self, tmp_path, *, volume=40, history=(), platform="darwin",
                 could_show=True, allowed=True, quiet=True):
        self.clock = Clock()
        self.volume = volume
        self.reads = 0
        self.shown = []
        self.logged = []
        self.asked = []
        self.quiet_answers = quiet
        self.could = could_show
        self.allowed = allowed
        self.history = list(history)
        self.history_reads = 0
        self.store = Q.VolumeLog(tmp_path / Q.STATE_FILE)
        self.advisor = Q.QuietMicAdvisor(self.hooks(platform))

    def hooks(self, platform):
        def read_volume():
            self.reads += 1
            return self.volume

        def history():
            self.history_reads += 1
            return list(self.history)

        def quiet():
            if isinstance(self.quiet_answers, list):
                return self.quiet_answers.pop(0) if self.quiet_answers else True
            return self.quiet_answers

        def allowed(kind):
            self.asked.append(kind)
            return self.allowed

        return Q.Hooks(
            platform=platform, history=history, could_show=lambda kind: self.could,
            quiet=quiet, toast_allowed=allowed,
            show=lambda heading, body, buttons, timeout: self.shown.append((heading, body, buttons)),
            read_volume=read_volume, clock=self.clock, sleep=lambda s: None,
            log=self.logged.append, store=self.store)

    def takes(self, *levels):
        """Dictations, as the app has them: the Journal entry is saved first."""
        for db in levels:
            self.history.append(entry(db))
            self.advisor.take_done(db)


def test_ten_quiet_takes_and_a_low_volume_give_the_tip(tmp_path):
    rig = Rig(tmp_path)
    rig.takes(*[-42.5] * 10)
    assert rig.reads == 1
    assert len(rig.shown) == 1
    heading, body, buttons = rig.shown[0]
    assert (heading, body) == um.quiet_mic_message(40)
    assert [b["label"] for b in buttons] == ["Open Sound settings", "Got it", "Don't show again"]
    assert buttons[0]["action"] == Q.OPEN_SOUND_ACTION == "open_sound_input"
    assert N.parse_tip_action(buttons[2]["action"]) == (N.TIP_MUTE, Q.TIP_KIND)
    assert rig.asked == [Q.TIP_KIND]


def test_the_takes_before_this_run_count(tmp_path):
    rig = Rig(tmp_path, history=[entry(-44.0)] * 9)
    rig.takes(-44.0)
    assert rig.reads == 1 and len(rig.shown) == 1
    rig.takes(-44.0)
    assert rig.history_reads == 1                   # read once, then kept in memory


def test_this_take_is_counted_once(tmp_path):
    """Its entry is already in the Journal when the first call reads it, so
    it is not counted again."""
    adv = Q.QuietMicAdvisor(Q.Hooks(
        platform="darwin", history=lambda: [entry(-50.0)] + [entry(-44.0)] * 9 + [entry(-10.0)],
        could_show=lambda k: False, quiet=lambda: True, toast_allowed=lambda k: True,
        show=lambda *a: None, store=Q.VolumeLog(tmp_path / Q.STATE_FILE)))
    adv.take_done(-10.0)
    assert list(adv._levels) == [-44.0] * 9 + [-10.0]
    adv.take_done(-12.0)
    assert list(adv._levels) == [-44.0] * 8 + [-10.0, -12.0]


@pytest.mark.parametrize("levels", [[-42.5] * 9, [-30.0] * 10, [-36.0] * 10])
def test_no_read_and_no_tip_unless_ten_takes_are_quiet(tmp_path, levels):
    rig = Rig(tmp_path)
    rig.takes(*levels)
    assert rig.reads == 0 and rig.shown == []


def test_a_volume_set_high_enough_gives_no_tip(tmp_path):
    rig = Rig(tmp_path, volume=75)
    rig.takes(*[-42.5] * 10)
    assert rig.reads == 1 and rig.shown == []


@pytest.mark.parametrize("volume,tip", [(69, True), (70, False)])
def test_low_means_under_70(tmp_path, volume, tip):
    rig = Rig(tmp_path, volume=volume)
    rig.takes(*[-42.5] * 10)
    assert bool(rig.shown) is tip


def test_the_volume_is_read_at_most_once_a_day(tmp_path):
    rig = Rig(tmp_path, volume=75)
    rig.takes(*[-42.5] * 10)
    rig.clock.t += 3600
    rig.takes(*[-42.5] * 5)
    assert rig.reads == 1
    rig.clock.t += DAY
    rig.takes(-42.5)
    assert rig.reads == 2


def test_once_a_day_holds_across_a_restart(tmp_path):
    first = Rig(tmp_path, volume=75)
    first.takes(*[-42.5] * 10)
    second = Rig(tmp_path, volume=75, history=[entry(-42.5)] * 10)
    second.clock.t = first.clock.t + 3600
    second.takes(-42.5)
    assert second.reads == 0
    saved = json.loads((tmp_path / Q.STATE_FILE).read_text(encoding="utf-8"))
    assert set(saved) == {"version", "checked_at", "volume"} and saved["volume"] == 75


def test_a_reading_from_earlier_today_still_gives_the_tip(tmp_path):
    """The tip could not be shown when the volume was read (something was on
    screen): the day's reading is used, not read again."""
    rig = Rig(tmp_path, allowed=False)
    rig.takes(*[-42.5] * 10)
    assert rig.reads == 1 and rig.shown == []
    rig.allowed = True
    rig.clock.t += 600
    rig.takes(-42.5)
    assert rig.reads == 1 and len(rig.shown) == 1


def test_a_failed_read_waits_a_day_too(tmp_path):
    rig = Rig(tmp_path, volume=None)
    rig.takes(*[-42.5] * 10)
    rig.takes(*[-42.5] * 3)
    assert rig.reads == 1 and rig.shown == []


def test_nothing_is_read_when_the_tip_could_not_be_shown_anyway(tmp_path):
    """Hidden for good, tips off, or shown this week: no osascript at all."""
    rig = Rig(tmp_path, could_show=False)
    rig.takes(*[-42.5] * 12)
    assert rig.reads == 0 and rig.shown == []


def test_the_tip_waits_for_a_quiet_moment(tmp_path):
    rig = Rig(tmp_path, quiet=[False, False, True])
    rig.takes(*[-42.5] * 10)
    assert len(rig.shown) == 1


def test_no_quiet_moment_no_tip(tmp_path):
    rig = Rig(tmp_path, quiet=False)
    rig.takes(*[-42.5] * 10)
    assert rig.shown == [] and rig.asked == []


def test_windows_does_nothing_new(tmp_path):
    rig = Rig(tmp_path, platform="win32")
    rig.takes(*[-42.5] * 12)
    assert rig.reads == 0 and rig.shown == [] and rig.history_reads == 0


def test_a_take_with_no_level_changes_nothing(tmp_path):
    rig = Rig(tmp_path)
    rig.takes(*[-42.5] * 9)
    rig.takes(None, "x", float("nan"))
    assert rig.reads == 0


def test_a_hook_that_fails_never_reaches_the_dictation(tmp_path):
    rig = Rig(tmp_path)

    def boom():
        raise RuntimeError("no history: SECRET")
    rig.advisor.h = rig.advisor.h._replace(history=boom)
    rig.takes(*[-42.5] * 10)                      # no exception
    assert not any("SECRET" in line for line in rig.logged)


def test_forget_starts_afresh(tmp_path):
    rig = Rig(tmp_path, volume=75)
    rig.takes(*[-42.5] * 10)
    assert (tmp_path / Q.STATE_FILE).exists()
    rig.advisor.forget()
    assert not (tmp_path / Q.STATE_FILE).exists()
    rig.history = []
    rig.takes(*[-42.5] * 9)
    assert rig.reads == 1                         # the levels went too


def test_two_takes_at_once_read_the_volume_once(tmp_path):
    rig = Rig(tmp_path)
    gate = threading.Event()
    real = rig.advisor.h.read_volume

    def slow():
        gate.wait(2)
        return real()
    # Like the notifier: once shown, the tip could not be shown again this week.
    rig.advisor.h = rig.advisor.h._replace(read_volume=slow, could_show=lambda kind: not rig.shown)
    rig.takes(*[-42.5] * 9)
    threads = [threading.Thread(target=rig.advisor.take_done, args=(-42.5,)) for _ in range(3)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(5)
    assert rig.reads == 1 and len(rig.shown) == 1


def test_the_state_file_is_a_number_and_a_time_and_is_deleted_with_my_data():
    assert Q.STATE_FILE == "mic_volume.json"
    assert Q.STATE_FILE in privacy_data.DATA_FILES


def test_an_unreadable_state_file_is_a_fresh_start(tmp_path):
    (tmp_path / Q.STATE_FILE).write_text("{not json", encoding="utf-8")
    rig = Rig(tmp_path, volume=75)
    rig.takes(*[-42.5] * 10)
    assert rig.reads == 1


# ── the notifier's "could this tip be shown later" ──────────────────────────

def test_could_show_asks_only_what_lasts(tmp_path):
    clock = Clock()
    ctx = {"value": N.Context(processing=True)}
    n = N.Notifier(store=N.MemoryStore(), clock=clock, context=lambda: ctx["value"])
    assert n.could_show(Q.TIP_KIND) is True             # busy now, but that passes
    ctx["value"] = N.Context(tips_enabled=False)
    assert n.could_show(Q.TIP_KIND) is False
    ctx["value"] = N.Context()
    assert n.claim(Q.TIP_KIND) is True
    n.release()
    clock.t += 3 * DAY
    assert n.could_show(Q.TIP_KIND) is False            # shown this week
    clock.t += 5 * DAY
    assert n.could_show(Q.TIP_KIND) is True
    n.mute(Q.TIP_KIND)
    assert n.could_show(Q.TIP_KIND) is False
    assert n.snapshot()[Q.TIP_KIND]["shown"] == 1       # asking counts nothing


# ── the Journal entry ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value,kept", [
    (-44.53, -44.5), (-12.0, -12.0), (None, None), ("x", None), (True, None),
    (float("nan"), None), (3.0, None), (-200.0, None),
])
def test_the_speech_level_is_kept_with_the_entry_when_it_makes_sense(value, kept):
    out = history_meta.take_fields(speech_db=value)
    assert out.get("speech_db") == kept


# ── in the app (the real _process, through tests/_pipeline_harness.py) ──────

import _pipeline_harness as H  # noqa: E402


@pytest.fixture
def limits(monkeypatch):
    H.fast_limits(monkeypatch)


class LevelTranscriber(H.FakeTranscriber):
    """Groq-only, like the recommended setup, and it measured the take's level."""

    def __init__(self, level=-44.0, **kw):
        super().__init__(**kw)
        self.level = level

    def transcribe_sync(self, audio_bytes):
        text = super().transcribe_sync(audio_bytes)
        self.last_speech_dbfs = self.level
        return text


def dictate(p):
    t = H.run_process(p, 1)
    t.join(10)
    assert not t.is_alive()
    return p


def on_a_mac(p, monkeypatch, volume=40):
    reads = []
    p.ns["_platform"] = types.SimpleNamespace(system=lambda: "Darwin")
    monkeypatch.setattr(Q, "read_input_volume", lambda: reads.append(1) or volume)
    return reads


def quiet_history(p, n=9, db=-44.0):
    p.ns["HISTORY_FILE"].write_text(json.dumps([entry(db) for _ in range(n)]), encoding="utf-8")


def test_the_entry_keeps_the_level_of_its_take(tmp_path, limits):
    p = dictate(H.make_pipeline(tmp_path, transcriber=LevelTranscriber(-44.04)))
    assert H.history(p)[-1]["speech_db"] == -44.0


def test_the_provenance_snapshot_carries_the_level(tmp_path):
    p = H.make_pipeline(tmp_path, transcriber=LevelTranscriber(-41.5))
    _, info = p._transcribe_with_provenance(b"audio")
    assert info["last_speech_dbfs"] == -41.5
    _, info = H.make_pipeline(tmp_path)._transcribe_with_provenance(b"audio")
    assert info["last_speech_dbfs"] is None                 # a transcriber that does not say


def test_on_a_mac_the_tenth_quiet_take_brings_the_tip(tmp_path, limits, monkeypatch):
    p = H.make_pipeline(tmp_path, transcriber=LevelTranscriber(-44.0))
    quiet_history(p)
    reads = on_a_mac(p, monkeypatch)
    dictate(p)
    assert H.wait_for(lambda: any(t.get("heading") == "Your microphone is set low"
                                  for t in p.overlay.toasts()), timeout=5)
    tip = next(t for t in p.overlay.toasts() if t.get("heading") == "Your microphone is set low")
    assert "(40%)" in tip["body"]
    assert [b["action"] for b in tip["buttons"]][0] == Q.OPEN_SOUND_ACTION
    assert reads == [1]
    assert p.clipboard.pastes, "and the dictation itself went as ever"
    saved = json.loads((tmp_path / Q.STATE_FILE).read_text(encoding="utf-8"))
    assert saved["volume"] == 40


def test_with_tips_off_the_volume_is_not_even_read(tmp_path, limits, monkeypatch):
    (tmp_path / "settings.json").write_text(json.dumps({"tips": False}), encoding="utf-8")
    p = H.make_pipeline(tmp_path, transcriber=LevelTranscriber(-44.0))
    quiet_history(p)
    reads = on_a_mac(p, monkeypatch)
    dictate(p)
    H.wait_for(lambda: False, timeout=0.8)
    assert reads == [] and not any(t.get("heading") == "Your microphone is set low"
                                   for t in p.overlay.toasts())


def test_a_loud_enough_take_history_brings_nothing(tmp_path, limits, monkeypatch):
    p = H.make_pipeline(tmp_path, transcriber=LevelTranscriber(-25.0))
    quiet_history(p, db=-25.0)
    reads = on_a_mac(p, monkeypatch)
    dictate(p)
    H.wait_for(lambda: False, timeout=0.5)
    assert reads == []


def test_on_windows_nothing_new_happens(tmp_path, limits, monkeypatch):
    p = H.make_pipeline(tmp_path, transcriber=LevelTranscriber(-44.0))
    quiet_history(p)
    reads = []
    monkeypatch.setattr(Q, "read_input_volume", lambda: reads.append(1) or 40)
    dictate(p)                                              # the harness is "Windows"
    H.wait_for(lambda: False, timeout=0.5)
    assert reads == [] and getattr(p, "_quiet_mic", None) is None
    assert H.history(p)[-1]["speech_db"] == -44.0           # the level is kept all the same


def test_the_tips_button_opens_sound_settings(tmp_path, monkeypatch):
    p = H.make_pipeline(tmp_path)
    opened = []
    monkeypatch.setattr(Q, "open_sound_settings", lambda: opened.append(1) or True)
    p._on_toast_action(Q.OPEN_SOUND_ACTION)
    assert H.wait_for(lambda: opened == [1], timeout=2)
    assert "hide_toast" in p.overlay.names()


def test_delete_my_data_forgets_the_levels_and_the_reading(tmp_path):
    p = H.make_pipeline(tmp_path)
    forgot = []
    p._quiet_mic = types.SimpleNamespace(forget=lambda: forgot.append(1))
    p.forget_notifier()
    assert forgot == [1]


def test_a_note_that_fails_never_reaches_the_dictation(tmp_path, limits, monkeypatch):
    p = H.make_pipeline(tmp_path, transcriber=LevelTranscriber(-44.0))
    p.ns["_platform"] = types.SimpleNamespace(system=lambda: "Darwin")
    monkeypatch.setattr(Q, "QuietMicAdvisor", lambda hooks: (_ for _ in ()).throw(RuntimeError("x")))
    dictate(p)
    assert p.clipboard.pastes and p.page.statuses[-1] == "done"
    assert any("[mic] level not noted (RuntimeError)" in line for line in p.logged)


# ── what Privacy and data says ──────────────────────────────────────────────

def _flat(path):
    return " ".join((ROOT / path).read_text(encoding="utf-8").split())


def test_privacy_says_the_level_and_the_volume_are_kept_here():
    level = ("Each also notes how loud your speech arrived, so Waffler can tell you if your "
             "microphone is set too low.")
    assert level in _flat("ui/index.html") and level in _flat("ui/app.js")
    html = _flat("ui/index.html")
    row = html[html.index('id="micVolumeRow"'):]
    row = row[:row.index("</div> </div> </div>")]
    assert "Microphone volume" in row
    assert "at most once a day" in row and "never changes it" in row
    assert "Delete all my data clears them" in row
    assert "—" not in row and "–" not in row


def test_once_a_day_holds_even_when_the_reading_cannot_be_kept(tmp_path):
    rig = Rig(tmp_path, volume=75)
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the folder should be", encoding="utf-8")
    rig.advisor.h = rig.advisor.h._replace(store=Q.VolumeLog(blocked / Q.STATE_FILE))
    rig.takes(*[-42.5] * 12)
    assert rig.reads == 1
    assert any("[mic] reading not kept" in line for line in rig.logged)
