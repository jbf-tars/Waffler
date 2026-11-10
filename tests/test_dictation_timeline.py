"""scripts/dictation_timeline.py: what happened to each dictation, from app.log.

The log here is made up, in the shape app.log has had since 3.16.3: one second
per line, the pill's lines carrying the clock, lines from two dictations
interleaved. The tool prints counts and timings and must never print a line of
the log back (app.log can hold words from a dictation)."""
import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dictation_timeline.py"
_spec = importlib.util.spec_from_file_location("dictation_timeline", _SCRIPT)
tl = importlib.util.module_from_spec(_spec)
sys.modules["dictation_timeline"] = tl          # dataclasses look the module up by name
_spec.loader.exec_module(tl)

T0 = 1794313165.0       # the pill's clock for 12:19:25 local, whatever the zone


def _log(*lines):
    return [line + "\n" for line in lines]


START = "12:19:00  === Waffler starting === (v3.16.5, PROJECT_ROOT=/x)"
PRESS = [
    "12:19:25  [HOTKEY/Fn01] Fn press fired (flagsChanged, flags=0x800100) thread=t",
    "12:19:25  [HOTKEY/L01] _on_hotkey_press entered sticky=False recording=False",
    "12:19:25  [HOTKEY/L01] → starting push-to-talk (fire_press)",
    "12:19:25  Recording started",
    f"12:19:25  [overlay-dbg] event=parent.show.enter t={T0 + 0.171:.3f} gen=20",
    f"12:19:25  [overlay STDERR] [overlay-dbg] event=show.posted t={T0 + 0.212:.3f} gen=20 visible=True",
]
RELEASE = [
    "12:19:32  [HOTKEY/L01] → stopping push-to-talk (fire_release)",
    "12:19:32  Recording stopped, processing",
    f"12:19:32  [overlay STDERR] [overlay-dbg] event=stdin.cmd t={T0 + 7.271:.3f} type=working depth=1 gen=",
    "12:19:32  Recording duration: 7.10s",
    "12:19:32  [pipeline] stopping audio capture...",
    "12:19:32  [pipeline] audio captured: 247852 bytes",
    "12:19:32  [pipeline] audio diag: 7.1s, 31 windows, digital-silence=0%, speech-windows=31, "
    "longest-dead-run=0.0s, terminal=False",
    "12:19:32  [whisper] quiet recording: speech at -30 dBFS, boosted by 10.4 dB",
    "12:19:32  [whisper] clip=8.3s -> 1 chunk(s)",
    "12:19:32  [whisper] single-shot -> 23 words",
    "12:19:32  [pipeline] transcription: 478ms",
]
PASTE = [
    "12:19:33  [pipeline] styling (groq): 749ms",
    "12:19:33  Vocabulary corrections applied: 'sekrit' → 'Secret'",
    "12:19:33  [pipeline] clipboard+paste: 78ms",
    "12:19:33  [pipeline] TOTAL: 1305ms",
    "12:19:33  Done: 23 words, 132 chars",
    f"12:19:33  [overlay STDERR] [overlay-dbg] event=stdin.cmd t={T0 + 8.765:.3f} type=working_end depth=1 gen=",
    "12:19:34  [paste] outcome=unverified reason=opaque restored=True (1.01 s after the keystroke)",
]


def test_a_clean_dictation_is_followed_from_key_to_paste():
    rep = tl.parse(_log(START, *PRESS, *RELEASE, *PASTE))
    assert len(rep.dictations) == 1
    d = rep.dictations[0]
    assert d.version == "3.16.5"
    assert d.outcome == tl.PASTED
    assert d.held_s == 7.10
    assert abs(d.audio_s - 7.744) < 0.01
    assert abs(d.lead_s - 0.644) < 0.01            # the half second before the press, and the tail
    assert (d.speech_db, d.boost_db) == (-30.0, 10.4)
    assert (d.words, d.asr_ms, d.style_by, d.style_ms, d.paste_ms, d.total_ms) == (
        23, 478.0, "groq", 749.0, 78.0, 1305.0)
    assert abs(d.pill_ms - 41.0) < 0.5
    assert abs(d.keyup_to_done_ms - 1494.0) < 0.5
    assert (d.paste_outcome, d.paste_reason) == ("unverified", "opaque")
    assert rep.counts["3.16.5"]["key presses"] == 1


def test_nothing_from_the_log_is_printed_back():
    out = tl.render(tl.parse(_log(START, *PRESS, *RELEASE, *PASTE)), list_rows=True)
    assert "sekrit" not in out and "Secret" not in out
    assert "pasted" in out and "3.16.5" in out


def test_how_a_recording_ended():
    def ended(*tail):
        return tl.parse(_log(START, *PRESS, *RELEASE, *tail)).dictations[0].outcome

    assert ended("12:19:33  Empty transcription result") == tl.EMPTY
    assert ended("12:19:33  Processing 7 cancelled during styling") == tl.CANCELLED_WORK
    assert ended("12:19:33  Processing 7 not pasted (superseded); transcript still saved to History") \
        == tl.SUPERSEDED
    assert ended("12:19:33  [pipeline] transcription not finished (error), keeping the recording: x") \
        == tl.UNSENT
    assert ended() == tl.UNFINISHED
    rep = tl.parse(_log(START, *PRESS, "12:19:30  Recording cancelled by user"))
    assert rep.dictations[0].outcome == tl.CANCELLED_REC
    tap = [line for line in RELEASE[:4]] + [
        "12:19:32  Short press (0.20s) with 0.00s of speech — discarding as accidental tap"]
    assert tl.parse(_log(START, *PRESS, *tap)).dictations[0].outcome == tl.TAP
    quiet = RELEASE[:6] + ["12:19:32  Audio too quiet (overall RMS=0, no 250ms window >= 12.0)",
                           "12:19:32  Dead audio stream (RMS≈0) — rebuilding so next press re-acquires the mic"]
    assert tl.parse(_log(START, *PRESS, *quiet)).dictations[0].outcome == tl.DEAD


def test_the_stream_opened_again_at_the_press_is_noted():
    lines = PRESS[:4] + ["12:19:25  [audio] opening the microphone again: the audio devices changed"] \
        + PRESS[4:] + RELEASE + PASTE
    rep = tl.parse(_log(START, *lines))
    assert rep.dictations[0].reopened_mic == "the audio devices changed"
    assert rep.counts["3.16.5"]["microphone opened again at a press"] == 1
    assert "opened again at the press: median" in tl.render(rep)


def test_a_new_take_while_the_last_is_still_worked_on():
    """The pipeline's lines belong to the take that was stopped, the recorder's
    to the one being recorded."""
    second = [line.replace("12:19:25", "12:19:33") for line in PRESS[:4]]
    rep = tl.parse(_log(START, *PRESS, *RELEASE, *second, *PASTE))
    first, later = rep.dictations
    assert first.outcome == tl.PASTED and first.words == 23
    assert later.outcome == tl.UNFINISHED and later.words is None


def test_the_day_comes_from_the_pills_clock_and_from_midnight():
    late = [line.replace("12:19:", "23:59:") for line in PRESS[:4]]
    after = [line.replace("12:19:", "00:00:") for line in PRESS[:4]]
    rep = tl.parse(_log(START, *PRESS, *RELEASE, *PASTE, *late,
                        "23:59:40  Recording cancelled by user", *after))
    a, b, c = rep.dictations
    assert b.started - a.started == (23 * 3600 + 59 * 60 + 25) - (12 * 3600 + 19 * 60 + 25)
    assert c.started - b.started == 60


def test_wakes_and_input_changes_are_counted_per_version():
    rep = tl.parse(_log(
        START, *PRESS, *RELEASE, *PASTE,
        "12:30:00  [audio-monitor] the computer woke up after 902s",
        "12:30:05  [audio-monitor] the computer woke up after 27s",
        "12:31:00  [audio-monitor] default input changed: 'A' → 'B'",
        "12:31:00  [audio-monitor] monitoring stream restarted on 'B'",
        "12:31:30  [audio] opened the microphone again between presses: the audio devices changed",
        "12:40:00  === Waffler starting === (v3.16.7, PROJECT_ROOT=/x)"))
    assert rep.counts["3.16.5"]["microphone opened again between presses"] == 1
    assert rep.wake_gaps["3.16.5"] == [902.0, 27.0]
    assert rep.counts["3.16.5"]["system input changed"] == 1
    assert rep.counts["3.16.7"]["starts"] == 1
    assert [s[0] for s in rep.sessions] == ["3.16.5", "3.16.7"]


def test_main_reads_the_files_it_is_given(tmp_path, capsys):
    log = tmp_path / "app.log"
    log.write_text("".join(_log(START, *PRESS, *RELEASE, *PASTE)), encoding="utf-8")
    assert tl.main([str(log), "--list"]) == 0
    out = capsys.readouterr().out
    assert "recordings: 1" in out and "sekrit" not in out
