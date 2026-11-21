"""Every pop-up Waffler can raise, and the one small pill it is now.

The table below is the approved design's board ("Every pop-up, rewritten"),
row by row: the old message, as the app really builds it (the real heading,
body and buttons from src/user_messages.py, src/unsent.py, src/pasteboard.py
and the lines written out in app.py), and what the pill shows for it: its
kind, its one line, its one button, how long it stays, and what a click does.

The old long sentence is not thrown away: it is the pill's description, for
VoiceOver and the log.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import mic_supervisor as ms  # noqa: E402
import notifier  # noqa: E402
import pasteboard  # noqa: E402
import pill  # noqa: E402
import pipeline_watchdog as pw  # noqa: E402
import quiet_mic  # noqa: E402
import unsent  # noqa: E402
import user_messages as um  # noqa: E402

DONE, WORKING, ASK, PROBLEM, TIP = pill.DONE, pill.WORKING, pill.ASK, pill.PROBLEM, pill.TIP

DISMISS = {"label": "Dismiss", "action": "dismiss", "kind": "secondary"}
JOURNAL = [{"label": "Show in Journal", "action": "open_journal", "kind": "primary"}, DISMISS]
SHOW_T = [{"label": "Show in Journal", "action": "show_transform:41", "kind": "primary"}, DISMISS]
SHOW_C = [{"label": "Show in Journal", "action": "show_command:42", "kind": "primary"}, DISMISS]
AX = [{"label": "Open Settings", "action": "open_ax_settings", "kind": "primary"}, DISMISS]
STILL_WORKING = [
    {"label": "Keep waiting", "action": pw.KEEP_WAITING, "kind": "primary"},
    {"label": "Send later", "action": pw.SEND_LATER, "kind": "secondary"},
    {"label": "Cancel", "action": "cancel_processing", "kind": "danger"},
]
STILL_TIDYING = [
    {"label": "Paste as is", "action": pw.PASTE_RAW, "kind": "primary"},
    {"label": "Keep waiting", "action": pw.KEEP_WAITING, "kind": "secondary"},
    {"label": "Cancel", "action": "cancel_processing", "kind": "danger"},
]
MIC_ASK = [{"label": "Use it", "action": "mic_use:abc", "kind": "primary"},
           {"label": "Not now", "action": "mic_later:abc", "kind": "secondary"}]
CHOOSE = {"label": um.CHOOSE_MICROPHONE, "action": ms.CHOOSE_ACTION, "kind": "primary"}


def _perm(name, action):
    heading, body = um.PERMISSION_MESSAGES[name]
    return ("error", heading, body,
            [{"label": "Open Settings", "action": action, "kind": "primary"}, DISMISS], 12)


def _paste_failed(reason):
    toast = pasteboard.paste_failure_toast(
        pasteboard.PasteOutcome(pasteboard.FAILED, reason=reason, on_clipboard=reason != "clipboard_error"),
        "darwin")
    return ("warn", toast["heading"], toast["body"], toast["buttons"], None)


def _row(style, message, buttons=None, timeout=None):
    return (style, message[0], message[1], buttons, timeout)


# (id, the old pop-up, kind, the line, button label, button action, seconds, click)
# seconds None: it stays until answered, withdrawn or replaced.
ROWS = [
    # ── Done: a tick, one or two words, two seconds; a click opens the entry ──
    ("polish_done", _row("info", um.transform_done_message("Polish", "darwin"), SHOW_T, 8),
     DONE, "Polished", None, None, 2.0, "show_transform:41"),
    ("text_edited", _row("info", um.command_done_message("edit", "darwin"), SHOW_C, 8),
     DONE, "Edited", None, None, 2.0, "show_command:42"),
    ("draft_added", _row("info", um.command_done_message("draft", "darwin"), SHOW_C, 8),
     DONE, "Draft added", None, None, 2.0, "show_command:42"),
    ("searching", _row("info", um.command_searching_message("Perplexity"), SHOW_C, 8),
     DONE, "Searching", None, None, 2.0, "show_command:42"),
    ("result_copied", _row("info", um.TRANSFORM_COPIED, SHOW_T, 8),
     DONE, "Copied", None, None, 2.0, "show_transform:41"),
    ("command_copied", _row("info", um.COMMAND_COPIED, SHOW_C, 8),
     DONE, "Copied", None, None, 2.0, "show_command:42"),
    ("draft_copied", _row("info", um.COMMAND_DRAFT_COPIED, SHOW_C, 8),
     DONE, "Draft copied", None, None, 2.0, "show_command:42"),
    ("last_copied", _row("info", um.LAST_COPIED, None, 3),
     DONE, "Copied", None, None, 2.0, None),
    ("not_pasted_switched_away", _row("info", um.transform_not_pasted_message("darwin"), SHOW_T, 8),
     DONE, "Copied. Press Cmd+V", None, None, 2.0, "show_transform:41"),
    ("not_pasted_switched_away_windows", _row("info", um.transform_not_pasted_message("win32"), SHOW_T, 8),
     DONE, "Copied. Press Ctrl+V", None, None, 2.0, "show_transform:41"),
    ("not_pasted_not_sure", _row("info", um.transform_not_sure_message("darwin"), SHOW_T, 8),
     DONE, "Copied. Press Cmd+V", None, None, 2.0, "show_transform:41"),
    ("terminal_copied", _row("info", um.TERMINAL_COPIED, SHOW_T, 8),
     DONE, "Copied. Paste it yourself", None, None, 2.0, "show_transform:41"),
    ("transform_cancelled", _row("info", um.TRANSFORM_CANCELLED, None, 6),
     DONE, "Cancelled", None, None, 2.0, None),
    ("already_good", _row("info", um.TRANSFORM_NO_CHANGE, None, 6),
     DONE, "Nothing to change", None, None, 2.0, None),
    ("saved_for_later", _row("warn", unsent.toast_text(unsent.REASON_LATER, "Groq"), JOURNAL),
     DONE, "Saved for later", None, None, 2.0, "open_journal"),

    # ── Working: three dots; it becomes its Done or Problem pill in place ──
    ("busy", _row("info", um.LAST_BUSY, None, 3), WORKING, "One moment", None, None, None, None),
    ("busy_transform", _row("info", um.TRANSFORM_BUSY_DICTATING, None, 6),
     WORKING, "One moment", None, None, None, None),
    ("still_working_transform", _row("info", um.TRANSFORM_BUSY_ALREADY, None, 6),
     WORKING, "One moment", None, None, None, None),
    ("still_working_command", _row("info", um.COMMAND_BUSY_ALREADY, None, 6),
     WORKING, "One moment", None, None, None, None),
    ("restarting", _row("warn", um.audio_wedged_message("darwin", True), None, 12),
     WORKING, "Restarting", None, None, None, None),
    ("recording_stopped", _row("warn", um.take_limit_stopped(20)),
     WORKING, "Tidying", None, None, None, None),

    # ── Your call: one question, one button, a cross; leaving it is safe ──
    ("still_working_on_it", ("info", "Still working on it",
                             "Groq is slow to answer. Your recording is safe: keep waiting, "
                             "or let Waffler send it later.", STILL_WORKING, None),
     ASK, "Still working", "Send later", pw.SEND_LATER, None, None),
    ("still_cleaning_up", ("info", "Still cleaning up",
                           "The clean-up is slow. You can paste your words as you said them instead.",
                           STILL_TIDYING, None),
     ASK, "Still tidying", "Paste as is", pw.PASTE_RAW, None, None),
    ("mic_detected", _row("info", um.mic_detected_message("AirPods"), MIC_ASK, 20),
     ASK, "AirPods connected", "Use it", "mic_use:abc", 20.0, None),
    ("long_recording", _row("warn", um.take_limit_warning(), None, 6),
     ASK, "One minute left", None, None, 6.0, None),

    # ── Problem: a red dot, what is wrong, the one fix ──
    ("no_audio", ("error", "We couldn't hear you", "Check your mic is connected and not muted.", None, None),
     PROBLEM, "Didn’t hear you", "Mic", "select_mic", None, None),
    ("mic_cant_hear", _row("error", um.MIC_CANT_HEAR, [CHOOSE, DISMISS], 12),
     PROBLEM, "Can’t hear your mic", "Choose", ms.CHOOSE_ACTION, None, None),
    ("mic_reset", ("warn", "Mic reset", "Your mic stopped responding after sleep or a device change. "
                   "It has been reset: press and speak again.", None, None),
     PROBLEM, "Mic cut out. Say it again", None, None, 6.0, None),
    ("mic_dropped_out", ("warn", "Mic dropped out", "Your mic cut out partway through - some of this "
                         "may be missing. Mic reset; please re-record.", None, None),
     PROBLEM, "Mic cut out. Say it again", None, None, 6.0, None),
    ("mic_slow_to_stop", ("warn", "Your mic was slow to stop", "Nothing was pasted. If the recording "
                          "comes through, Waffler keeps it in the Journal and sends it from there.",
                          None, None),
     PROBLEM, "Mic cut out. Say it again", None, None, 6.0, None),
    ("mic_disconnected", _row("warn", um.MIC_DISCONNECTED, None, 8),
     PROBLEM, "Mic disconnected", None, None, 6.0, None),
    ("mic_stuck", _row("warn", um.audio_wedged_message("darwin", False), None, 12),
     PROBLEM, "Mic is stuck", "Restart", pill.RESTART_ACTION, None, None),
    ("permission_microphone", _perm("microphone", "open_mic_settings"),
     PROBLEM, "Microphone is off", "Allow", "open_mic_settings", None, None),
    ("permission_accessibility", _perm("accessibility", "open_ax_settings"),
     PROBLEM, "Can’t paste yet", "Allow", "open_ax_settings", None, None),
    ("permission_keyboard", _perm("keyboard", "open_input_settings"),
     PROBLEM, "Can’t hear your shortcut", "Allow", "open_input_settings", None, None),
    ("paste_failed", _paste_failed("no_target"),
     PROBLEM, "Copied. Press Cmd+V", None, None, 6.0, None),
    ("clipboard_copy_failed", _paste_failed("clipboard_error"),
     PROBLEM, "Saved, not pasted", "Open", "open_journal", None, None),
    ("clipboard_copy_failed_inline", ("warn", "Couldn't copy to the clipboard",
                                      "Your text is saved in the Journal. Copy it from there.", None, None),
     PROBLEM, "Saved, not pasted", "Open", "open_journal", None, None),
    ("not_sent_blocked", _row("warn", unsent.toast_text(unsent.REASON_BLOCKED, "Groq"), JOURNAL),
     PROBLEM, "Not sent. Saved", "Open", "open_journal", None, None),
    ("not_sent_rate_limited", _row("warn", unsent.toast_text(unsent.REASON_RATE_LIMITED, "Groq"), JOURNAL),
     PROBLEM, "Not sent. Saved", "Open", "open_journal", None, None),
    ("not_sent_timeout", _row("warn", unsent.toast_text(unsent.REASON_TIMEOUT, "Groq"), JOURNAL),
     PROBLEM, "Not sent. Saved", "Open", "open_journal", None, None),
    ("not_sent_offline", _row("warn", unsent.toast_text(unsent.REASON_OFFLINE, "Groq"), JOURNAL),
     PROBLEM, "Not sent. Saved", "Open", "open_journal", None, None),
    ("not_sent_failed", _row("warn", unsent.toast_text(unsent.REASON_ERROR, "Groq"), JOURNAL),
     PROBLEM, "Not sent. Saved", "Open", "open_journal", None, None),
    ("took_too_long", _row("warn", unsent.toast_text(unsent.REASON_STUCK, saved=True)),
     PROBLEM, "Took too long. Saved", None, None, 6.0, None),
    ("cleanup_skipped_timeout", _row("warn", um.cleanup_skipped_message("TIMEOUT|8s")),
     PROBLEM, "Pasted as said", None, None, 6.0, None),
    ("cleanup_skipped_limit", _row("warn", um.cleanup_skipped_message("RATE_LIMIT|Groq|10m0s|x")),
     PROBLEM, "Pasted as said", None, None, 6.0, None),
    ("cleanup_skipped_model", _row("warn", um.cleanup_skipped_message("MODEL_UNAVAILABLE: x")),
     PROBLEM, "Pasted as said", None, None, 6.0, None),
    ("cleanup_skipped_local", _row("warn", um.cleanup_skipped_message("LOCAL|failed")),
     PROBLEM, "Pasted as said", None, None, 6.0, None),
    ("cleanup_skipped_blocked", _row("warn", um.cleanup_skipped_message("Groq: AUTH: 401")),
     PROBLEM, "Pasted as said", None, None, 6.0, None),
    ("cleanup_skipped_offline", _row("warn", um.cleanup_skipped_message("Groq: CONNECTION: x")),
     PROBLEM, "Pasted as said", None, None, 6.0, None),
    ("cleanup_skipped_other", _row("warn", um.cleanup_skipped_message("")),
     PROBLEM, "Pasted as said", None, None, 6.0, None),
    ("cleanup_skipped_no_key", _row("warn", um.cleanup_skipped_message("no styling providers configured")),
     PROBLEM, "Pasted as said", "Add key", pill.KEYS_ACTION, None, None),
    ("limit_reached", _row("warn", um.limit_reached_message("RATE_LIMIT|Groq|10m0s|x")),
     PROBLEM, "Limit reached", None, None, 6.0, None),
    ("connection_failed", ("warn", "Connection failed",
                           "Couldn't reach the server. Check your internet or VPN.", None, None),
     PROBLEM, "No connection", None, None, 6.0, None),
    ("access_denied", ("warn", "Access denied", "API key may be invalid, or your VPN server's IP is "
                       "blocked. Try a different VPN server.", None, None),
     PROBLEM, "Key refused", "Settings", pill.KEYS_ACTION, None, None),
    ("couldnt_rewrite", _row("warn", um.transform_failed_message(""), None, 6),
     PROBLEM, "Couldn’t do that", None, None, 6.0, None),
    ("couldnt_rewrite_model", _row("warn", um.transform_failed_message("MODEL_UNAVAILABLE: x"), None, 6),
     PROBLEM, "Couldn’t do that", None, None, 6.0, None),
    ("couldnt_rewrite_no_key", _row("warn", um.transform_failed_message("no styling providers configured"),
                                    None, 6),
     PROBLEM, "Couldn’t do that", None, None, 6.0, None),
    ("couldnt_rewrite_timeout", _row("warn", um.transform_failed_message("TIMEOUT|8s"), None, 6),
     PROBLEM, "Couldn’t do that", None, None, 6.0, None),
    ("couldnt_rewrite_blocked", _row("warn", um.transform_failed_message("Groq: AUTH: 401"), None, 6),
     PROBLEM, "Couldn’t do that", None, None, 6.0, None),
    ("couldnt_rewrite_offline", _row("warn", um.transform_failed_message("Groq: CONNECTION: x"), None, 6),
     PROBLEM, "Couldn’t do that", None, None, 6.0, None),
    ("couldnt_rewrite_limit", _row("warn", um.transform_failed_message("RATE_LIMIT|Groq|10m0s|x"), None, 6),
     PROBLEM, "Limit reached", None, None, 6.0, None),
    ("couldnt_run_that", _row("warn", um.command_failed_message(""), None, 6),
     PROBLEM, "Couldn’t do that", None, None, 6.0, None),
    ("couldnt_hear_command", _row("warn", um.command_speech_failed_message("offline", "Groq"), None, 6),
     PROBLEM, "Didn’t catch that", None, None, 6.0, None),
    ("couldnt_hear_command_blocked", _row("warn", um.command_speech_failed_message("blocked", "Groq"), None, 6),
     PROBLEM, "Didn’t catch that", None, None, 6.0, None),
    ("nothing_replaced", _row("warn", um.TRANSFORM_BAD_LENGTH, None, 6),
     PROBLEM, "Left as it was", None, None, 6.0, None),
    ("nothing_replaced_refused", _row("warn", um.COMMAND_REFUSED, None, 6),
     PROBLEM, "Left as it was", None, None, 6.0, None),
    ("select_first", _row("info", um.TRANSFORM_NO_SELECTION, None, 6),
     PROBLEM, "Select some text first", None, None, 6.0, None),
    ("too_much_text", _row("info", um.TRANSFORM_TOO_LONG, None, 6),
     PROBLEM, "Too much at once", None, None, 6.0, None),
    ("long_instruction", _row("info", um.COMMAND_TOO_LONG, None, 6),
     PROBLEM, "Too much at once", None, None, 6.0, None),
    ("cant_read_field", _row("warn", um.transform_read_message("secure_field"), None, 6),
     PROBLEM, "Can’t read that here", None, None, 6.0, None),
    ("clipboard_busy", _row("warn", um.transform_read_message("clipboard_busy"), None, 6),
     PROBLEM, "Can’t read that here", None, None, 6.0, None),
    ("cant_copy_from_here", _row("warn", um.transform_read_message("not_allowed_here"), None, 6),
     PROBLEM, "Can’t read that here", None, None, 6.0, None),
    ("nothing_to_paste", _row("info", um.LAST_NOTHING_TO_PASTE, None, 3),
     PROBLEM, "Nothing to paste yet", None, None, 6.0, None),
    ("meeting_not_recorded", ("error", "The meeting couldn't be recorded", "Try again.", None, 8),
     PROBLEM, "Couldn’t record", "Mic", "select_mic", None, None),
    ("something_went_wrong", ("warn", "Something went wrong", "Your words are in the Journal.", None, None),
     PROBLEM, "Something went wrong. Saved", None, None, 6.0, None),

    # ── Tip: quiet, rare; its cross counts as a dismissal, as "Got it" did ──
    ("hold_hint", _row("info", um.hold_hint_message("fn"), notifier.tip_buttons("hold_hint"), 10),
     TIP, "Hold fn to talk", None, None, 10.0, None),
    ("lid_closed", _row("info", um.MIC_LID_CLOSED, [CHOOSE] + notifier.tip_buttons("lid_closed"), 10),
     TIP, "Lid closed. Mic is muffled", "Choose", ms.CHOOSE_ACTION, 10.0, None),
    ("quiet_mic", _row("info", um.quiet_mic_message(30),
                       [{"label": um.OPEN_SOUND_SETTINGS, "action": quiet_mic.OPEN_SOUND_ACTION,
                         "kind": "primary"}] + notifier.tip_buttons("quiet_mic"), 15),
     TIP, "Mic is set low", "Fix", quiet_mic.OPEN_SOUND_ACTION, 15.0, None),
]

# Messages the board does not show: the kind and the words were chosen by the
# same rules (six words at most, one button, the fix when there is one).
CHOSEN = [
    ("custom_transform_done", _row("info", um.transform_done_message("Make it formal", "darwin"), SHOW_T, 8),
     DONE, "Done", None, None, 2.0, "show_transform:41"),
    ("command_cancelled", _row("info", um.COMMAND_CANCELLED, None, 6),
     DONE, "Cancelled", None, None, 2.0, None),
    ("command_no_change", _row("info", um.COMMAND_NO_CHANGE, None, 6),
     DONE, "Nothing to change", None, None, 2.0, None),
    ("cancelled_kept", ("warn", "Cancelled", "Nothing was pasted. Your words are in the Journal if "
                        "you want them after all.", JOURNAL, None),
     DONE, "Cancelled", None, None, 2.0, "open_journal"),
    ("cancelled_recording_kept", _row("warn", unsent.toast_text(unsent.REASON_CANCELLED), JOURNAL),
     DONE, "Cancelled", None, None, 2.0, "open_journal"),
    ("cancelled_not_saved", ("warn", "Cancelled", "Nothing was pasted, and Waffler couldn't save your "
                             "words to the Journal.", None, None),
     PROBLEM, "Cancelled. Not saved", None, None, 6.0, None),
    ("recovered", _row("warn", unsent.toast_text(unsent.REASON_INTERRUPTED), JOURNAL),
     DONE, "Recovered", None, None, 2.0, "open_journal"),
    ("blocks_dictation", _row("info", um.TRANSFORM_BLOCKS_DICTATION, None, 6),
     WORKING, "One moment", None, None, None, None),
    ("command_blocks_dictation", _row("info", um.COMMAND_BLOCKS_DICTATION, None, 6),
     WORKING, "One moment", None, None, None, None),
    ("not_ready", _row("info", um.TRANSFORM_NOT_READY, None, 6),
     WORKING, "One moment", None, None, None, None),
    ("permission_microphone_restricted", _perm("microphone_restricted", "open_mic_settings"),
     PROBLEM, "Microphone is blocked", "Open", "open_mic_settings", None, None),
    ("read_needs_accessibility", _row("warn", um.transform_read_message("accessibility_off"), AX, 12),
     PROBLEM, "Can’t read text yet", "Allow", "open_ax_settings", None, None),
    ("paste_needs_accessibility", _paste_failed("accessibility_off"),
     PROBLEM, "Copied. Press Cmd+V", "Allow", "open_ax_settings", None, None),
    ("paste_timed_out", _row("warn", um.paste_timed_out_message("darwin")),
     PROBLEM, "Copied. Press Cmd+V", None, None, 6.0, None),
    ("paste_timed_out_windows", _row("warn", um.paste_timed_out_message("win32")),
     PROBLEM, "Copied. Press Ctrl+V", None, None, 6.0, None),
    ("copy_failed", _row("info", um.LAST_COPY_FAILED, None, 3),
     PROBLEM, "Couldn’t copy", None, None, 6.0, None),
    ("nothing_to_copy", _row("info", um.LAST_NOTHING_TO_COPY, None, 3),
     PROBLEM, "Nothing to copy yet", None, None, 6.0, None),
    ("moved_not_copied", _row("warn", um.TRANSFORM_MOVED_NOT_COPIED, SHOW_T, 8),
     PROBLEM, "Saved, not pasted", "Open", "show_transform:41", None, None),
    ("not_sent_not_saved", _row("warn", unsent.toast_text(unsent.REASON_ERROR, saved=False)),
     PROBLEM, "Not sent. Say it again", None, None, 6.0, None),
    ("took_too_long_not_saved", ("warn", "That took too long", "Waffler stopped waiting. If your words "
                                 "come through, they'll be in the Journal.", None, None),
     PROBLEM, "Took too long", None, None, 6.0, None),
    ("too_slow", _row("warn", um.TRANSFORM_TOO_SLOW, None, 6),
     PROBLEM, "Couldn’t do that", None, None, 6.0, None),
    ("cant_copy_right_now", _row("warn", um.transform_read_message("clipboard_not_restorable"), None, 6),
     PROBLEM, "Can’t read that here", None, None, 6.0, None),
    ("couldnt_read_selection", _row("warn", um.transform_read_message("other"), None, 6),
     PROBLEM, "Can’t read that here", None, None, 6.0, None),
    ("command_mic_failed", ("warn", "Couldn't record", "Waffler couldn't use your microphone. "
                            "Check it is connected and try again.", None, 6),
     PROBLEM, "Couldn’t record", "Mic", "select_mic", None, None),
    ("something_went_wrong_lost", ("warn", "Something went wrong",
                                   "That dictation didn't go through. Please try again.", None, None),
     PROBLEM, "Something went wrong", None, None, 6.0, None),
    ("not_saved_to_history", ("warn", "Not saved to History",
                              "Your text was pasted, but Waffler couldn't save it to History.", None, None),
     PROBLEM, "Pasted, not saved", None, None, 6.0, None),
    ("browser_not_opened", _row("warn", um.COMMAND_OPEN_FAILED, None, 6),
     PROBLEM, "Couldn’t open your browser", None, None, 6.0, None),
    ("long_mic_name", _row("info", um.mic_detected_message("Siobhan's very long named studio microphone"),
                           MIC_ASK, 20),
     ASK, "New microphone connected", "Use it", "mic_use:abc", 20.0, None),
    # Windows has no meeting bar, so there the two meeting lines stay, small.
    ("meeting_started_windows", ("info", "Recording the meeting", "Tap Win + Ctrl twice again to finish. "
                                 "You can still hold it to dictate.", None, 6),
     DONE, "Recording the meeting", None, None, 2.0, None),
    ("meeting_finished_windows", ("info", "Meeting finished", "The notes are being written. They will "
                                  "be in Waffler in a moment.", None, 6),
     DONE, "Meeting finished", None, None, 2.0, None),
]

ALL = ROWS + CHOSEN


def _pill(old):
    style, heading, body, buttons, timeout = old
    return pill.pill_for(style, heading, body, buttons, timeout)


@pytest.mark.parametrize("row", ALL, ids=[r[0] for r in ALL])
def test_every_pop_up_is_its_pill(row):
    _id, old, kind, text, label, action, seconds, click = row
    p = _pill(old)
    assert p is not None
    assert (p.kind, p.text) == (kind, text)
    assert (p.button or {}).get("label") == label
    assert (p.button or {}).get("action") == action
    assert p.seconds == seconds
    assert p.click == click


@pytest.mark.parametrize("row", ALL, ids=[r[0] for r in ALL])
def test_one_line_of_six_words_at_most_and_one_button(row):
    p = _pill(row[1])
    assert "\n" not in p.text and 1 <= len(p.text.split()) <= pill.MAX_WORDS
    if p.button:
        assert set(p.button) == {"label", "action"}
        assert 1 <= len(p.button["label"].split()) <= 3 and p.button["action"]


@pytest.mark.parametrize("row", ALL, ids=[r[0] for r in ALL])
def test_the_long_sentence_is_kept_as_the_description(row):
    _style, heading, body, _buttons, _timeout = row[1]
    p = _pill(row[1])
    assert heading in p.description and body in p.description


def test_the_board_is_all_here():
    """The board has 12 Done, 4 Working, 4 Your call, 26 Problem and 3 Tip rows.
    Rows that stand for several old messages have one line here for each."""
    by_kind = {}
    for row in ROWS:
        by_kind.setdefault(row[2], set()).add((row[3], row[4]))
    assert {text for text, _ in by_kind[DONE]} == {
        "Polished", "Edited", "Draft added", "Searching", "Copied", "Draft copied",
        "Copied. Press Cmd+V", "Copied. Press Ctrl+V", "Copied. Paste it yourself", "Cancelled",
        "Nothing to change", "Saved for later"}
    assert by_kind[WORKING] == {("One moment", None), ("Restarting", None), ("Tidying", None)}
    assert by_kind[ASK] == {("Still working", "Send later"), ("Still tidying", "Paste as is"),
                            ("AirPods connected", "Use it"), ("One minute left", None)}
    assert by_kind[TIP] == {("Hold fn to talk", None), ("Lid closed. Mic is muffled", "Choose"),
                            ("Mic is set low", "Fix")}
    assert by_kind[PROBLEM] == {
        ("Didn’t hear you", "Mic"), ("Can’t hear your mic", "Choose"),
        ("Mic cut out. Say it again", None), ("Mic disconnected", None), ("Mic is stuck", "Restart"),
        ("Microphone is off", "Allow"), ("Can’t paste yet", "Allow"),
        ("Can’t hear your shortcut", "Allow"), ("Copied. Press Cmd+V", None),
        ("Saved, not pasted", "Open"), ("Not sent. Saved", "Open"), ("Took too long. Saved", None),
        ("Pasted as said", None), ("Pasted as said", "Add key"), ("Limit reached", None),
        ("No connection", None), ("Key refused", "Settings"), ("Couldn’t do that", None),
        ("Didn’t catch that", None), ("Left as it was", None), ("Select some text first", None),
        ("Too much at once", None), ("Can’t read that here", None), ("Nothing to paste yet", None),
        ("Couldn’t record", "Mic"), ("Something went wrong. Saved", None)}


# ── Gone ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("provider,used,kind", [("groq", "openai", "unreachable"),
                                                ("openai", "groq", "blocked"),
                                                ("cerebras", "groq", "unreachable")])
def test_used_another_service_is_not_a_pop_up(provider, used, kind):
    heading, body = um.failover_message(provider, used, kind)
    assert pill.pill_for("info", heading, body, None, 8) is None


# ── The rules of each kind ──────────────────────────────────────────────

def test_done_has_no_button_and_goes_in_two_seconds():
    for row in ALL:
        p = _pill(row[1])
        if p.kind == DONE:
            assert p.button is None and p.close is None and p.seconds == pill.DONE_S == 2.0


def test_done_opens_its_entry_and_only_when_there_is_one():
    with_entry = pill.pill_for("info", *um.transform_done_message("Polish", "darwin"), SHOW_T, 8)
    without = pill.pill_for("info", *um.transform_done_message("Polish", "darwin"), None, 8)
    assert with_entry.click == "show_transform:41"
    assert without.click is None


def test_working_stays_and_has_nothing_to_press():
    for row in ALL:
        p = _pill(row[1])
        if p.kind == WORKING:
            assert p.seconds is None and p.button is None and p.close is None and p.click is None


def test_a_problem_stays_six_seconds_or_until_answered_when_it_has_a_button():
    for row in ALL:
        p = _pill(row[1])
        if p.kind == PROBLEM:
            if p.button:
                assert p.seconds is None and p.close == pill.DISMISS_ACTION
            else:
                assert p.seconds == pill.PROBLEM_S == 6.0 and p.close is None


def test_a_cross_only_where_the_pill_does_not_go_by_itself_or_is_a_tip():
    for row in ALL:
        p = _pill(row[1])
        if p.close is not None:
            assert p.kind == TIP or p.button is not None


def test_a_tip_keeps_its_time_and_its_cross_counts_as_a_dismissal():
    """ "Got it" was the dismissal that, twice, keeps a tip away for good
    (src/notifier.py DISMISSALS_BEFORE_QUIET). The cross is that now."""
    for kind_id, row_id in (("hold_hint", "hold_hint"), ("lid_closed", "lid_closed"),
                            ("quiet_mic", "quiet_mic")):
        row = next(r for r in ROWS if r[0] == row_id)
        p = _pill(row[1])
        assert p.kind == TIP
        assert p.close == f"{notifier.TIP_DISMISS}:{kind_id}"
        assert notifier.parse_tip_action(p.close) == (notifier.TIP_DISMISS, kind_id)
        assert p.seconds == float(row[1][4])


def test_twice_crossed_a_tip_is_not_shown_again():
    n = notifier.Notifier(context=lambda: notifier.Context())
    p = _pill(next(r for r in ROWS if r[0] == "hold_hint")[1])
    verb, kind_id = notifier.parse_tip_action(p.close)
    assert n.request(kind_id).show
    n.dismiss(kind_id)
    n.release()
    n.dismiss(kind_id)
    assert n.request(kind_id).reason == "dismissed"


# ── Your call: doing nothing is always safe ─────────────────────────────

def test_still_working_keeps_waiting_unless_told_otherwise():
    """Three buttons became one. "Keep waiting" is what happens when the
    pill is left alone: it never sends that answer, and it never goes by
    itself. "Send later" is the button; the cross is the old Cancel."""
    p = pill.pill_for("info", "Still working on it", "x", STILL_WORKING, None)
    assert p.kind == ASK and p.seconds is None
    assert p.button == {"label": "Send later", "action": pw.SEND_LATER}
    assert p.close == "cancel_processing"
    assert p.click is None
    assert pw.KEEP_WAITING not in (p.button["action"], p.close, p.click)


def test_still_tidying_keeps_waiting_unless_told_otherwise():
    p = pill.pill_for("info", "Still cleaning up", "x", STILL_TIDYING, None)
    assert p.kind == ASK and p.seconds is None
    assert p.button == {"label": "Paste as is", "action": pw.PASTE_RAW}
    assert p.close == "cancel_processing"
    assert p.click is None


def test_a_new_microphone_is_left_alone_unless_use_it_is_pressed():
    """The cross is the old "Not now"; left alone the pill goes after the
    same 20 seconds and nothing changes."""
    p = pill.pill_for("info", *um.mic_detected_message("AirPods"), MIC_ASK, ms.PROMPT_S)
    assert p.button == {"label": "Use it", "action": "mic_use:abc"}
    assert p.close == "mic_later:abc"
    assert p.seconds == ms.PROMPT_S and p.click is None


def test_one_minute_left_asks_for_nothing():
    p = pill.pill_for("warn", *um.take_limit_warning(), None, 6)
    assert (p.button, p.close, p.click, p.seconds) == (None, None, None, 6.0)


def test_a_your_call_pill_never_acts_by_itself():
    for row in ALL:
        p = _pill(row[1])
        if p.kind == ASK:
            assert p.click is None           # a stray click on the pill does nothing


# ── A message nobody listed ─────────────────────────────────────────────

def test_an_unlisted_error_is_a_problem_in_six_words():
    p = pill.pill_for("warn", "A brand new thing went wrong in a surprising new way", "Body.", None, None)
    assert p.kind == PROBLEM and len(p.text.split()) <= pill.MAX_WORDS
    assert p.seconds == 6.0 and "Body." in p.description


def test_an_unlisted_notice_is_done():
    p = pill.pill_for("info", "All set", "Body.", None, None)
    assert (p.kind, p.text, p.seconds) == (DONE, "All set", 2.0)


def test_an_unlisted_message_keeps_its_one_real_button():
    p = pill.pill_for("error", "New trouble", "Body.",
                      [{"label": "Open Settings", "action": "open_mic_settings", "kind": "primary"}, DISMISS],
                      None)
    assert p.button == {"label": "Open Settings", "action": "open_mic_settings"}
    assert p.close == pill.DISMISS_ACTION and p.seconds is None


def test_the_old_cancel_question_is_one_button_and_a_cross():
    """Nothing raises it today (the X cancels at once), but the style is still
    in the protocol: Discard is the button, the cross keeps going."""
    p = pill.pill_for("cancel", "Discard this recording?", "", None, None)
    assert p.kind == ASK and p.button == {"label": "Discard", "action": "confirm"}
    assert p.close == pill.DISMISS_ACTION and p.seconds is None


def test_bad_input_never_raises():
    for args in ((None, None, None, None, None), ("info", 3, 4, "x", "y"), ("warn", "", "", [None, 3], -1)):
        p = pill.pill_for(*args)
        assert p is None or p.text


# ── What travels to the overlay ─────────────────────────────────────────

def test_as_dict_is_plain_json():
    import json
    p = pill.pill_for("info", "Still working on it", "x", STILL_WORKING, None)
    d = json.loads(json.dumps(p.as_dict()))
    assert d == {"kind": "ask", "text": "Still working", "button": {"label": "Send later",
                 "action": "send_later"}, "close": "cancel_processing", "click": None,
                 "seconds": None, "description": "Still working on it. x"}
    assert pill.from_dict(d) == p


def test_from_dict_refuses_nonsense():
    assert pill.from_dict(None) is None
    assert pill.from_dict({"kind": "banner", "text": "x"}) is None
    assert pill.from_dict({"kind": "done", "text": ""}) is None


# ── The size of the pill (the design's numbers) ─────────────────────────

def _measure(text, size, weight):
    return 7.0 * len(text)


def test_the_design_numbers():
    assert pill.HEIGHT == 40 and pill.MARK == 22
    assert (pill.FILL, pill.INK, pill.INK_QUIET) == ("#14110F", "#F4F0EA", "#CEC6BB")
    assert (pill.GOLD, pill.RED) == ("#E8C267", "#F0624F")
    assert (pill.BUTTON_FILL, pill.BUTTON_INK, pill.BUTTON_H) == ("#F4F0EA", "#12100E", 28)
    assert (pill.TEXT_PT, pill.BUTTON_PT) == (14, 12)
    assert pill.HAIRLINE_ALPHA == 0.14


def test_layout_of_a_done_pill():
    p = pill.pill_for("info", "Copied", "x", None, None)
    lay = pill.layout(p, _measure)
    # 9 | mark 22 | 10 | tick 14 | 10 | text | 16
    assert lay.mark_x == 9 and lay.glyph_x == 41 and lay.text_x == 65
    assert lay.width == 65 + 7 * len("Copied") + 16
    assert lay.button is None and lay.close is None and lay.height == 40


def test_layout_with_a_button_and_a_cross():
    p = pill.pill_for("info", "Still working on it", "x", STILL_WORKING, None)
    lay = pill.layout(p, _measure)
    # Your call has no glyph: 9 | mark 22 | 10 | text | 10+2 | button | 10-4 | cross 28 | 6
    text_w = 7 * len("Still working")
    button_w = 7 * len("Send later") + 24
    assert lay.glyph_x is None and lay.text_x == 41
    assert lay.button == (41 + text_w + 12, 6, button_w, 28)
    assert lay.close == (41 + text_w + 12 + button_w + 6, 6, 28, 28)
    assert lay.width == lay.close[0] + 28 + 6


def test_layout_hit_testing():
    p = pill.pill_for("info", "Still working on it", "x", STILL_WORKING, None)
    lay = pill.layout(p, _measure)
    bx, by, bw, bh = lay.button
    cx, cy, cw, ch = lay.close
    assert pill.hit(lay, bx + bw / 2, by + bh / 2) == "button"
    assert pill.hit(lay, cx + cw / 2, cy + ch / 2) == "close"
    assert pill.hit(lay, 20, 20) == "body"
    assert pill.hit(lay, -5, 20) is None and pill.hit(lay, 20, 45) is None


def test_what_a_click_sends():
    ask = pill.pill_for("info", "Still working on it", "x", STILL_WORKING, None)
    assert pill.action_for(ask, "button") == pw.SEND_LATER
    assert pill.action_for(ask, "close") == "cancel_processing"
    assert pill.action_for(ask, "body") is None            # and the pill stays
    done = pill.pill_for("info", *um.transform_done_message("Polish", "darwin"), SHOW_T, 8)
    assert pill.action_for(done, "body") == "show_transform:41"
    plain = pill.pill_for("info", *um.LAST_COPIED, None, 3)
    assert pill.action_for(plain, "body") == pill.DISMISS_ACTION    # a click just closes it
    problem = pill.pill_for("warn", "Connection failed", "x", None, None)
    assert pill.action_for(problem, "body") == pill.DISMISS_ACTION


# ── Never on top of the dictation pill or the meeting bar ───────────────

def test_the_pill_sits_above_the_dictation_pill_with_a_gap():
    # the dictation pill's window is 69 x 97 at (500, 100); screen coordinates grow upwards
    x, y = pill.origin_above(anchor=(500, 100), anchor_size=(69, 97), width=160)
    assert y == 100 + 97 + pill.GAP
    assert x == 500 + (69 - 160) / 2.0
    assert not pill.overlaps((x, y, 160, pill.HEIGHT), (500, 100, 69, 97))


def test_the_pill_moves_beside_the_meeting_bar_when_they_would_touch():
    meeting = (600, 850, 178, 40)
    screen = (0, 0, 1440, 900)
    moved = pill.clear_of((640, 845, 160, 40), meeting, screen)
    assert not pill.overlaps(moved, meeting)
    assert moved[1] == meeting[1]                       # side by side, the same line
    assert moved[0] == meeting[0] + meeting[2] + pill.GAP
    assert moved[0] + moved[2] <= screen[0] + screen[2]


def test_the_pill_goes_under_the_meeting_bar_when_there_is_no_room_beside_it():
    meeting = (10, 850, 178, 40)
    screen = (0, 0, 300, 900)
    moved = pill.clear_of((20, 845, 160, 40), meeting, screen)
    assert not pill.overlaps(moved, meeting)
    assert moved[1] == meeting[1] - pill.GAP - 40


def test_a_pill_that_touches_nothing_stays_where_it_was():
    rect = (640, 200, 160, 40)
    assert pill.clear_of(rect, (600, 850, 178, 40), (0, 0, 1440, 900)) == rect
    assert pill.clear_of(rect, None, (0, 0, 1440, 900)) == rect


# ── Working becomes its Done pill in place ──────────────────────────────

def test_working_ends_as_done_or_goes():
    working = pill.pill_for("warn", *um.take_limit_stopped(20))
    done = pill.after_work(working, "done")
    assert (done.kind, done.text, done.seconds) == (DONE, "Done", 2.0)
    assert pill.after_work(working, "quiet") is None
    # Anything that is not a Working pill is left exactly as it is.
    problem = pill.pill_for("warn", "Connection failed", "x", None, None)
    assert pill.after_work(problem, "done") is problem
    assert pill.after_work(problem, "quiet") is problem
