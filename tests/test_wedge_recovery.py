"""What Waffler does when macOS's audio system stops answering (src/wedge_recovery.py).

The deadlock is inside CoreAudio and covers the whole process: no call into
audio comes back, and nothing in Waffler can free it. The only cure is a new
process. So, on a Mac, when the recorder says it is wedged, Waffler keeps what
the person was recording, says it is restarting, starts a small helper that
opens it again once this process is gone, and ends itself without a normal
shutdown (which would hang in the same audio teardown).

Everything with a side effect is injected, so none of this restarts anything,
opens a microphone or shows a message: the decision, the order of the steps,
the rate limit and the state file are tested with fakes. The relaunch helper's
shell script is run for real, with a stand-in for ``open``.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import notifier  # noqa: E402
import privacy_data  # noqa: E402
import user_messages  # noqa: E402
import wedge_recovery as wr  # noqa: E402

BUNDLE = "/Applications/Waffler.app"
EXE = BUNDLE + "/Contents/MacOS/Waffler"
NOW = 1_800_000_000.0


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


class Rig:
    """A recovery with every side effect recorded in order."""

    def __init__(self, tmp_path, *, platform="darwin", frozen=True, executable=EXE,
                 recording=False, wav=b"RIFFfake", keeps=True, busy_for=0, pid=4242,
                 stuck_for=None):
        self.stuck_polls = stuck_for        # None: stuck for ever; n: answers again after n looks
        self.cleared = 0
        self.events = []
        self.shown = []
        self.kept = []
        self.sleeps = []
        self.clock = Clock()
        self.state = tmp_path / "audio_restarts.json"
        self.recording = recording
        self.wav = wav
        self.keeps = keeps
        self.busy_polls = busy_for
        self.spawn_error = None
        self.keep_error = None
        self.keep_hang = None
        self.lines = []
        self.rec = wr.WedgeRecovery(
            platform=platform, frozen=frozen, executable=executable, state_path=self.state,
            take_in_progress=lambda: self.recording,
            busy=self._busy,
            salvage=self._salvage,
            keep=self._keep,
            show=self._show,
            spawn=self._spawn,
            exit_now=self._exit,
            still_stuck=self._still_stuck,
            on_cleared=self._on_cleared,
            sleep=self._sleep,
            clock=self.clock,
            log=self.lines.append,
            pid=pid,
        )

    def _still_stuck(self):
        if self.stuck_polls is None:
            return True
        self.stuck_polls -= 1
        return self.stuck_polls >= 0

    def _on_cleared(self):
        self.cleared += 1

    def _busy(self):
        if self.busy_polls > 0:
            self.busy_polls -= 1
            return True
        return False

    def _salvage(self):
        self.events.append("salvage")
        return self.wav

    def _keep(self, wav):
        if self.keep_hang is not None:
            self.keep_hang.wait(30)
        if self.keep_error is not None:
            raise self.keep_error
        self.events.append("keep")
        self.kept.append(wav)
        return self.keeps

    def _show(self, heading, body):
        self.events.append("show")
        self.shown.append((heading, body))

    def _spawn(self, argv):
        if self.spawn_error is not None:
            raise self.spawn_error
        self.events.append("spawn")
        self.spawned = argv

    def _exit(self, code):
        self.events.append(f"exit:{code}")

    def _sleep(self, seconds):
        self.sleeps.append(seconds)         # recorded, not waited, and the clock stays where it is

    def restarts_on_disk(self):
        try:
            return json.loads(self.state.read_text(encoding="utf-8"))["restarts"]
        except (OSError, ValueError, KeyError):
            return None


# ── the app bundle ───────────────────────────────────────────────────────────

def test_the_bundle_is_found_from_the_running_executable():
    assert wr.app_bundle(EXE) == BUNDLE
    assert wr.app_bundle("/Users/me/Applications/My Waffler.app/Contents/MacOS/Waffler") == \
        "/Users/me/Applications/My Waffler.app"
    assert wr.app_bundle("/Volumes/Waffler/Waffler.app/Contents/MacOS/Waffler") == \
        "/Volumes/Waffler/Waffler.app"


@pytest.mark.parametrize("executable", [
    "/Users/me/waffler/venv/bin/python",
    "C:\\Program Files\\Waffler\\Waffler.exe",
    "/Applications/Waffler.app",
    "/Applications/Waffler.app/Contents/Resources/x",
    "",
    None,
])
def test_anything_that_is_not_inside_an_app_bundle_has_none(executable):
    assert wr.app_bundle(executable) is None


# ── the decision ─────────────────────────────────────────────────────────────

def test_the_decision_table():
    d = wr.decide
    assert d("darwin", True, BUNDLE, 0) == wr.RESTART
    assert d("darwin", True, BUNDLE, 1) == wr.RESTART
    assert d("darwin", True, BUNDLE, wr.MAX_RESTARTS) == wr.TOAST_ONLY, "the third one does not restart"
    assert d("darwin", False, BUNDLE, 0) == wr.LOG_ONLY, "a development run does nothing destructive"
    assert d("darwin", False, None, 0) == wr.LOG_ONLY
    assert d("darwin", True, None, 0) == wr.TOAST_ONLY, "a frozen app with no bundle cannot be opened again"
    assert d("win32", True, None, 0) == wr.TOAST_ONLY, "no restart on Windows: a different audio system"
    assert d("win32", False, None, 0) == wr.LOG_ONLY


def test_the_rate_limit_is_two_restarts_in_ten_minutes():
    assert wr.MAX_RESTARTS == 2
    assert wr.WINDOW_S == 600.0


# ── a Mac, frozen: restart ───────────────────────────────────────────────────

def test_a_frozen_mac_says_so_starts_the_helper_and_ends_the_process(tmp_path):
    r = Rig(tmp_path)
    assert r.rec.handle("stream.stop") == wr.RESTART
    assert r.events == ["show", "spawn", "exit:0"]
    assert r.spawned[:2] == ["/bin/sh", "-c"]
    assert BUNDLE in r.spawned and "4242" in r.spawned and "2" in r.spawned
    assert r.restarts_on_disk() == [NOW]


def test_the_state_is_written_before_the_process_ends(tmp_path):
    """A process that ends first would leave no record, and a Mac whose audio
    stays broken would be restarted in a loop."""
    r = Rig(tmp_path)
    seen = []
    real_spawn = r._spawn
    r._spawn = lambda argv: (seen.append(r.restarts_on_disk()), real_spawn(argv))
    r.rec._spawn = r._spawn
    r.rec.handle("stream.stop")
    assert seen == [[NOW]]


def test_the_message_is_shown_before_the_restart_and_given_a_moment_to_draw(tmp_path):
    r = Rig(tmp_path)
    r.rec.handle("stream.stop")
    assert r.events.index("show") < r.events.index("spawn")
    assert r.sleeps and 0 < r.sleeps[0] <= 2.0


def test_the_message_says_what_is_happening_and_that_the_recording_is_kept(tmp_path):
    r = Rig(tmp_path)
    r.rec.handle("stream.stop")
    heading, body = r.shown[0]
    assert heading == "Waffler is restarting"
    assert body == ("macOS's audio system stopped answering, so Waffler is restarting itself. "
                    "Anything you were recording is kept under Not sent.")


def test_it_only_acts_once_however_often_it_is_told(tmp_path):
    r = Rig(tmp_path)
    assert r.rec.handle("stream.stop") == wr.RESTART
    assert r.rec.handle("coreaudio device list") == wr.ALREADY
    assert r.events.count("spawn") == 1 and r.events.count("exit:0") == 1 and len(r.shown) == 1


def test_two_threads_telling_it_at_once_restart_once(tmp_path):
    r = Rig(tmp_path)
    r.rec._sleep = lambda s: time.sleep(0.05)
    threads = [threading.Thread(target=r.rec.handle, args=(f"op{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert r.events.count("spawn") == 1 and r.events.count("exit:0") == 1


# ── what is being recorded is kept first ─────────────────────────────────────

def test_a_take_in_progress_is_read_from_the_buffer_and_kept_before_anything_else(tmp_path):
    r = Rig(tmp_path, recording=True)
    r.rec.handle("waiting for the stream lock to stop")
    assert r.events == ["salvage", "keep", "show", "spawn", "exit:0"]
    assert r.kept == [b"RIFFfake"]


def test_with_no_take_nothing_is_saved(tmp_path):
    r = Rig(tmp_path, recording=False)
    r.rec.handle("coreaudio device list")
    assert "salvage" not in r.events and "keep" not in r.events


def test_a_take_with_nothing_in_it_is_not_saved_but_is_not_a_loss(tmp_path):
    r = Rig(tmp_path, recording=True, wav=b"")
    r.rec.handle("stream.stop")
    assert "keep" not in r.events
    assert "could not be kept" not in r.shown[0][1]


def test_a_buffer_that_cannot_be_read_is_said_so_and_the_restart_goes_ahead(tmp_path):
    r = Rig(tmp_path, recording=True, wav=None)
    assert r.rec.handle("stream.stop") == wr.RESTART
    assert "keep" not in r.events
    heading, body = r.shown[0]
    assert body == ("macOS's audio system stopped answering, so Waffler is restarting itself. "
                    "The recording you were making could not be kept.")
    assert r.events[-2:] == ["spawn", "exit:0"]


def test_a_recording_that_could_not_be_saved_says_so(tmp_path):
    r = Rig(tmp_path, recording=True, keeps=False)
    r.rec.handle("stream.stop")
    assert "could not be kept" in r.shown[0][1]
    assert r.events[-2:] == ["spawn", "exit:0"]


def test_a_save_that_raises_is_a_recording_not_kept_and_the_restart_goes_ahead(tmp_path):
    r = Rig(tmp_path, recording=True)
    r.keep_error = OSError("disk full")
    assert r.rec.handle("stream.stop") == wr.RESTART
    assert "could not be kept" in r.shown[0][1]
    assert r.events[-2:] == ["spawn", "exit:0"]


def test_a_save_that_never_returns_does_not_hold_the_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "KEEP_TIMEOUT_S", 0.2)
    r = Rig(tmp_path, recording=True)
    r.keep_hang = threading.Event()
    t0 = time.monotonic()
    assert r.rec.handle("stream.stop") == wr.RESTART
    assert time.monotonic() - t0 < 3.0
    assert "could not be kept" in r.shown[0][1]
    assert r.events[-2:] == ["spawn", "exit:0"]
    r.keep_hang.set()


# ── a dictation that is being turned into text finishes first ───────────────

def test_a_dictation_being_turned_into_text_is_waited_for_before_the_restart(tmp_path):
    r = Rig(tmp_path, busy_for=3)
    r.rec.handle("stream.stop")
    assert len(r.sleeps) >= 3, "it looked, waited, looked again"
    assert r.events == ["show", "spawn", "exit:0"], "and then restarted"


def test_the_wait_has_an_end(tmp_path):
    r = Rig(tmp_path, busy_for=10 ** 6)
    r.rec.handle("stream.stop")
    assert r.events[-2:] == ["spawn", "exit:0"]
    assert sum(r.sleeps) <= wr.BUSY_WAIT_S + wr.CONFIRM_S + wr.SETTLE_S + 1


# ── it is only a wedge if CoreAudio is still not answering ───────────────────
# A stop that takes 4 seconds (a Bluetooth headset changing profile) and then
# comes back is slow, not deadlocked, and does not cost a restart: the bound
# that abandons a call is short so the locks are let go, and the restart waits
# a little longer to see whether the call comes back.

def test_a_call_that_comes_back_during_the_wait_is_not_a_wedge(tmp_path):
    r = Rig(tmp_path, stuck_for=3)
    assert r.rec.handle("stream.stop") == wr.CLEARED
    assert r.events == [], "no message, no helper, no exit"
    assert r.cleared == 1
    assert r.restarts_on_disk() is None
    assert any("answered again" in line for line in r.lines)


def test_after_a_false_alarm_a_real_wedge_is_still_acted_on(tmp_path):
    r = Rig(tmp_path, stuck_for=1)
    assert r.rec.handle("stream.stop") == wr.CLEARED
    r.stuck_polls = None                     # and this time it does not come back
    assert r.rec.handle("stream.stop") == wr.RESTART
    assert r.events[-2:] == ["spawn", "exit:0"]


def test_a_wedge_that_lasts_is_confirmed_by_waiting_the_whole_time(tmp_path):
    r = Rig(tmp_path)
    r.rec.handle("stream.stop")
    assert sum(r.sleeps) >= wr.CONFIRM_S
    assert r.events[-2:] == ["spawn", "exit:0"]


def test_it_looks_before_it_waits_so_a_call_that_has_already_come_back_costs_nothing(tmp_path):
    r = Rig(tmp_path, stuck_for=0)
    assert r.rec.handle("stream.stop") == wr.CLEARED
    assert r.sleeps == []


def test_the_message_for_a_machine_that_cannot_restart_is_not_shown_for_a_false_alarm(tmp_path):
    r = Rig(tmp_path, platform="win32", executable="C:\\Waffler\\Waffler.exe", stuck_for=2)
    assert r.rec.handle("stream.stop") == wr.CLEARED
    assert r.events == []


def test_a_development_run_does_not_wait_to_log(tmp_path):
    r = Rig(tmp_path, frozen=False, executable="/Users/me/venv/bin/python")
    r.rec.handle("stream.stop")
    assert r.sleeps == []


def test_the_confirmation_is_six_seconds_looked_at_four_times_a_second():
    assert wr.CONFIRM_S == 6.0 and wr.CONFIRM_POLL_S == 0.25


def test_a_check_that_raises_counts_as_still_stuck(tmp_path):
    r = Rig(tmp_path)

    def boom():
        raise RuntimeError("no")
    r.rec._still_stuck = boom
    assert r.rec.handle("stream.stop") == wr.RESTART


def test_a_take_that_begins_during_the_wait_is_kept_at_once(tmp_path):
    r = Rig(tmp_path, busy_for=10 ** 6)
    polls = []

    def sleep(seconds):
        polls.append(seconds)
        if seconds == wr.BUSY_POLL_S:
            r.recording = True              # the person pressed the key again
    r.rec._sleep = sleep
    r.rec.handle("stream.stop")
    assert "keep" in r.events and r.events[-2:] == ["spawn", "exit:0"]
    assert polls.count(wr.BUSY_POLL_S) == 1, "it did not wait out the rest"


# ── the rate limit ───────────────────────────────────────────────────────────

def test_the_third_wedge_in_ten_minutes_only_shows_the_message(tmp_path):
    state = tmp_path / "audio_restarts.json"
    state.write_text(json.dumps({"version": 1, "restarts": [NOW - 300, NOW - 60]}), encoding="utf-8")
    r = Rig(tmp_path)
    assert r.rec.handle("stream.stop") == wr.TOAST_ONLY
    assert r.events == ["show"], "no helper, no exit"
    heading, body = r.shown[0]
    assert "Quit and open Waffler again" in body
    assert r.restarts_on_disk() == [NOW - 300, NOW - 60], "and it is not counted as a restart"


def test_a_restart_that_is_ten_minutes_old_does_not_count(tmp_path):
    state = tmp_path / "audio_restarts.json"
    state.write_text(json.dumps({"version": 1, "restarts": [NOW - 601, NOW - 900, NOW - 60]}),
                     encoding="utf-8")
    r = Rig(tmp_path)
    assert r.rec.handle("stream.stop") == wr.RESTART
    assert r.restarts_on_disk() == [NOW - 60, NOW], "the old ones are dropped from the file"


def test_two_restarts_are_allowed_and_the_third_is_not(tmp_path):
    outcomes = []
    for i in range(3):
        r = Rig(tmp_path)               # a new process each time, sharing the file
        r.clock.t = NOW + i * 30
        outcomes.append(r.rec.handle("stream.stop"))
    assert outcomes == [wr.RESTART, wr.RESTART, wr.TOAST_ONLY]


# ── not a frozen Mac ─────────────────────────────────────────────────────────

def test_a_development_run_only_logs(tmp_path):
    r = Rig(tmp_path, frozen=False, executable="/Users/me/waffler/venv/bin/python", recording=True)
    assert r.rec.handle("stream.stop") == wr.LOG_ONLY
    assert r.events == [], "no message, no helper, no exit, nothing saved"
    assert r.restarts_on_disk() is None
    assert any("not restarting" in line.lower() for line in r.lines)


def test_windows_only_shows_the_message(tmp_path):
    r = Rig(tmp_path, platform="win32", executable="C:\\Waffler\\Waffler.exe", recording=True)
    assert r.rec.handle("stream.stop") == wr.TOAST_ONLY
    assert r.events == ["show"]
    heading, body = r.shown[0]
    assert body == ("Windows's audio system stopped answering. "
                    "Quit and open Waffler again to get your microphone back.")


def test_a_frozen_mac_outside_a_bundle_only_shows_the_message(tmp_path):
    r = Rig(tmp_path, executable="/opt/waffler/Waffler")
    assert r.rec.handle("stream.stop") == wr.TOAST_ONLY
    assert r.events == ["show"]


def test_when_the_helper_cannot_be_started_the_process_stays_up(tmp_path):
    """Ending a process that nothing will open again would leave the person with
    no Waffler at all."""
    r = Rig(tmp_path)
    r.spawn_error = OSError("fork failed")
    assert r.rec.handle("stream.stop") == wr.TOAST_ONLY
    assert "exit:0" not in r.events
    assert "Quit and open Waffler again" in r.shown[-1][1]


def test_a_message_that_cannot_be_shown_does_not_stop_the_restart(tmp_path):
    r = Rig(tmp_path)

    def broken(heading, body):
        raise RuntimeError("overlay is gone")
    r.rec._show = broken
    assert r.rec.handle("stream.stop") == wr.RESTART
    assert r.events[-2:] == ["spawn", "exit:0"]


# ── the words ────────────────────────────────────────────────────────────────

def test_the_words_are_plain():
    for restarting in (True, False):
        for platform in ("darwin", "win32"):
            for kept in (True, False, None):
                heading, body = user_messages.audio_wedged_message(platform, restarting, kept)
                for text in (heading, body):
                    assert "—" not in text and "–" not in text and "--" not in text
                    assert "Traceback" not in text and "Error" not in text and "CoreAudio" not in text
                    assert text.strip() == text and text


def test_the_message_names_the_system_that_stopped():
    assert "macOS" in user_messages.audio_wedged_message("darwin", True, None)[1]
    assert "Windows" in user_messages.audio_wedged_message("win32", False, None)[1]


def test_the_notifier_has_a_kind_for_it():
    assert notifier.KINDS["audio_wedged"].severity == notifier.ERROR


# ── the state file ───────────────────────────────────────────────────────────

def test_the_file_is_one_of_the_files_delete_my_data_removes(tmp_path):
    assert wr.FILE == "audio_restarts.json"
    assert wr.FILE in privacy_data.DATA_FILES
    assert wr.FILE not in privacy_data.KEPT_FILES
    (tmp_path / wr.FILE).write_text('{"version": 1, "restarts": [1.0]}', encoding="utf-8")
    assert privacy_data.delete_my_data(tmp_path)["ok"]
    assert not (tmp_path / wr.FILE).exists()


def test_the_file_is_left_out_of_the_logs_bundle():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    import ast
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "download_logs")
    listed = [c.value for c in ast.walk(fn) if isinstance(c, ast.Constant)
              and isinstance(c.value, str) and c.value.endswith(".json")]
    assert wr.FILE not in listed, "it is not one of the files that go in"
    assert wr.FILE in ast.unparse(fn), "and the README of the bundle says it is left out"


@pytest.mark.parametrize("content", [
    "", "not json", "[]", "{}", '{"restarts": "soon"}', '{"restarts": [null, "x", {}, true]}',
    '{"version": 1, "restarts": [1e999, -1, 0]}', "\x00\x01\x02",
])
def test_a_file_that_is_not_what_was_written_counts_as_no_restarts_and_is_replaced(tmp_path, content):
    (tmp_path / "audio_restarts.json").write_text(content, encoding="utf-8", errors="replace")
    r = Rig(tmp_path)
    assert r.rec.handle("stream.stop") == wr.RESTART
    assert r.restarts_on_disk() == [NOW]


def test_a_time_in_the_future_is_not_trusted(tmp_path):
    """A clock that was wrong once must not hold the restart off for ever."""
    (tmp_path / "audio_restarts.json").write_text(
        json.dumps({"version": 1, "restarts": [NOW + 10 ** 7, NOW + 10 ** 7 + 1]}), encoding="utf-8")
    r = Rig(tmp_path)
    assert r.rec.handle("stream.stop") == wr.RESTART


def test_a_state_file_that_cannot_be_written_does_not_stop_the_restart(tmp_path):
    blocked = tmp_path / "gone" / "deeper"
    r = Rig(tmp_path)
    r.rec._state_path = blocked / "audio_restarts.json"
    assert r.rec.handle("stream.stop") == wr.RESTART
    assert r.events[-2:] == ["spawn", "exit:0"]


def test_the_file_never_grows_past_a_few_entries(tmp_path):
    log = wr.RestartLog(tmp_path / "audio_restarts.json", clock=Clock())
    for i in range(50):
        log.record(NOW + i * 0.001)
    data = json.loads((tmp_path / "audio_restarts.json").read_text(encoding="utf-8"))
    assert len(data["restarts"]) <= wr.MAX_KEPT


# ── the helper that opens Waffler again ──────────────────────────────────────

posix_only = pytest.mark.skipif(shutil.which("sh") is None or sys.platform.startswith("win"),
                                reason="needs a POSIX shell")


def test_the_helper_is_a_detached_shell_with_the_bundle_as_its_own_argument():
    argv = wr.relaunch_argv(BUNDLE, 4242)
    assert argv[0] == "/bin/sh" and argv[1] == "-c"
    assert BUNDLE not in argv[2], "the path is an argument, so no quote in it can break the script"
    assert BUNDLE in argv and "4242" in argv
    weird = "/Users/o'brien/Waffler's \"App\".app"
    assert weird in wr.relaunch_argv(weird, 1)


def test_the_helper_opens_a_new_instance_by_bundle():
    argv = wr.relaunch_argv(BUNDLE, 4242)
    assert "/usr/bin/open" in argv
    assert 'exec "$4" -n -a "$3"' in argv[2], "open -n -a <bundle>, once the old process is gone"


@posix_only
def test_the_helper_script_is_valid_shell():
    script = wr.relaunch_argv(BUNDLE, 4242)[2]
    assert subprocess.run(["/bin/sh", "-n", "-c", script]).returncode == 0


@posix_only
def test_the_helper_waits_for_the_process_to_go_and_then_opens_the_bundle(tmp_path):
    marker = tmp_path / "opened.txt"
    opener = tmp_path / "fake_open"
    opener.write_text(f'#!/bin/sh\necho "$@" > "{marker}"\n', encoding="utf-8")
    opener.chmod(0o755)
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.0)"])
    # Waffler's own parent (launchd) reaps it the moment it ends; here the test is the
    # parent, and a process that ended but is not reaped still answers ``kill -0``.
    threading.Thread(target=old.wait, daemon=True).start()
    t0 = time.monotonic()
    argv = wr.relaunch_argv(BUNDLE, old.pid, delay_s=0, opener=str(opener), max_polls=100)
    helper = subprocess.Popen(argv, start_new_session=True)
    time.sleep(0.4)
    assert not marker.exists(), "the old process is still there: nothing is opened yet"
    helper.wait(15)
    assert marker.exists()
    assert marker.read_text(encoding="utf-8").split() == ["-n", "-a", BUNDLE]
    assert time.monotonic() - t0 >= 0.9, "it waited for the process to end"


@posix_only
def test_the_helper_ends_a_process_that_will_not_go_and_then_opens_the_bundle(tmp_path):
    marker = tmp_path / "opened.txt"
    opener = tmp_path / "fake_open"
    opener.write_text(f'#!/bin/sh\necho done > "{marker}"\n', encoding="utf-8")
    opener.chmod(0o755)
    stuck = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    argv = wr.relaunch_argv(BUNDLE, stuck.pid, delay_s=0, opener=str(opener), max_polls=3)
    helper = subprocess.Popen(argv, start_new_session=True)
    helper.wait(15)
    assert marker.exists()
    deadline = time.monotonic() + 5
    while stuck.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert stuck.poll() is not None, "the stuck process was ended"
    if stuck.poll() is None:
        stuck.kill()


@posix_only
def test_the_helper_leaves_a_pid_that_is_already_gone_alone(tmp_path):
    marker = tmp_path / "opened.txt"
    opener = tmp_path / "fake_open"
    opener.write_text(f'#!/bin/sh\necho done > "{marker}"\n', encoding="utf-8")
    opener.chmod(0o755)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(10)
    argv = wr.relaunch_argv(BUNDLE, gone.pid, delay_s=0, opener=str(opener), max_polls=3)
    subprocess.Popen(argv, start_new_session=True).wait(15)
    assert marker.exists()


def test_the_real_spawner_detaches_the_helper(monkeypatch):
    calls = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            calls["argv"], calls["kw"] = argv, kw
    monkeypatch.setattr(wr.subprocess, "Popen", FakePopen)
    wr.spawn_detached(["/bin/sh", "-c", "true"])
    assert calls["kw"]["start_new_session"] is True and calls["kw"]["close_fds"] is True
    assert calls["kw"]["stdin"] == subprocess.DEVNULL


@posix_only
def test_a_process_ended_with_os_exit_lets_go_of_the_single_instance_lock(tmp_path):
    """The real lock, in a real process, ended the way the recovery ends Waffler:
    the new Waffler asks for the lock the moment it starts, and must find it free."""
    env = dict(os.environ, WAFFLER_DATA_DIR=str(tmp_path), PYTHONPATH=str(ROOT / "src"))
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import os, sys, single_instance\n"
         "assert single_instance.acquire()\n"
         "print('held', flush=True)\n"
         "sys.stdin.readline()\n"
         "os._exit(0)\n"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, text=True, encoding="utf-8")
    try:
        assert holder.stdout.readline().strip() == "held"
        ask = [sys.executable, "-c", "import single_instance; print(single_instance.acquire())"]
        assert subprocess.run(ask, env=env, capture_output=True, text=True, encoding="utf-8",
                              timeout=30).stdout.strip() == "False", "the lock really is held"
        holder.stdin.write("go\n")
        holder.stdin.flush()
        holder.wait(10)
        assert subprocess.run(ask, env=env, capture_output=True, text=True, encoding="utf-8",
                              timeout=30).stdout.strip() == "True", "and is free the moment the process is gone"
    finally:
        if holder.poll() is None:
            holder.kill()


def test_the_single_instance_lock_is_one_the_kernel_lets_go_when_the_process_ends():
    """The relaunched Waffler asks for the single-instance lock at once. It is
    a flock (a Mac) and the kernel drops it when the process ends, which is what
    ``os._exit`` does: nothing for the new process to be blocked by."""
    src = (ROOT / "src" / "single_instance.py").read_text(encoding="utf-8")
    assert "fcntl.flock" in src and "LOCK_EX | fcntl.LOCK_NB" in src
    assert "releases the lock when the holding process" in src
