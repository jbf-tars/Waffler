"""The pill is small; what happens to the person's words is not.

The old card said, in three lines, where the words had gone ("Your recording
is saved in the Journal", "Your text is on the clipboard"). The pill has six
words. Nothing that SAVES the words changed with it: this file pins that the
recording or the text is kept exactly as before, that it is kept BEFORE the
pill appears, that the pill only ever claims "Saved" or "Copied" when the old
sentence did, and that its one button still reaches the words.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _pipeline_harness as H  # noqa: E402
import pill  # noqa: E402
import pipeline_watchdog as pw  # noqa: E402
import unsent  # noqa: E402
import user_messages as um  # noqa: E402
from test_pill_table import ALL  # noqa: E402


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    H.fast_limits(monkeypatch, TRANSCRIBE_DEADLINE_MIN_S=5.0, TRANSCRIBE_DEADLINE_BASE_S=5.0,
                  TRANSCRIBE_DEADLINE_MAX_S=5.0, STYLE_DEADLINE_S=5.0)


def _dictate(p):
    worker = H.run_process(p)
    worker.join(8)
    assert not worker.is_alive()


# ── the pill never says more than is true ───────────────────────────────

@pytest.mark.parametrize("row", ALL, ids=[r[0] for r in ALL])
def test_saved_and_copied_are_only_said_when_the_long_sentence_says_so(row):
    _style, heading, body, _buttons, _timeout = row[1]
    p = pill.pill_for(*row[1])
    said = f"{heading} {body}".lower()
    if "saved" in p.text.lower() and "not saved" not in p.text.lower():
        assert "journal" in said or "saved" in said or "send it" in said, p.text
        assert "couldn't be saved" not in said and "couldn't save" not in said, p.text
    if p.text.lower().startswith("copied"):
        assert "clipboard" in said or "copied" in said or "paste it" in said, p.text
    if "couldn't be saved" in said or "couldn't save" in said:
        assert "saved" not in p.text.lower().replace("not saved", ""), \
            "words that were lost are never called saved"


def test_words_that_could_not_be_saved_are_said_to_be_gone():
    lost = pill.pill_for("warn", *unsent.toast_text(unsent.REASON_ERROR, saved=False))
    assert lost.text == "Not sent. Say it again" and lost.button is None
    kept = pill.pill_for("warn", *unsent.toast_text(unsent.REASON_ERROR, saved=True))
    assert kept.text == "Not sent. Saved" and kept.button["action"] == "open_journal"


# ── the recording is kept, and kept before the pill appears ─────────────

def test_a_dictation_that_cannot_be_sent_is_saved_before_the_pill_says_so(tmp_path):
    p = H.make_pipeline(tmp_path)
    p.transcriber.error = ConnectionError("Connection error.")
    seen = []
    original = p.overlay.show_toast

    def spy(**kw):
        seen.append((len(list((tmp_path / "unsent").glob("recording-*.wav"))),
                     len([e for e in H.history(p) if e.get("failed")])))
        return original(**kw)
    p.overlay.show_toast = spy
    _dictate(p)
    assert seen == [(1, 1)], "the recording and its Journal card were there when the pill came up"
    [toast] = p.overlay.toasts()
    shown = H.as_pill(toast)
    assert (shown.kind, shown.text) == ("problem", "Not sent. Saved")
    assert shown.button == {"label": "Open", "action": "open_journal"}
    assert shown.seconds is None, "it stays until Open or the cross is used"
    # The long sentence is still what the app says, and so what VoiceOver reads.
    assert "Your recording is saved in the Journal" in shown.description
    [entry] = [e for e in H.history(p) if e.get("failed")]
    assert (tmp_path / "unsent" / entry["unsent_id"]).stat().st_size > 44, "with the audio in it"


def test_the_pills_one_button_opens_the_journal(tmp_path):
    p = H.make_pipeline(tmp_path)
    opened = []
    p._show_journal = lambda: opened.append(1)
    p.transcriber.error = ConnectionError("Connection error.")
    _dictate(p)
    shown = H.as_pill(p.overlay.toasts()[-1])
    p._on_toast_action(shown.button["action"])
    assert opened == [1]


def test_the_cross_only_closes_the_pill_and_the_recording_stays(tmp_path):
    p = H.make_pipeline(tmp_path)
    p.transcriber.error = ConnectionError("Connection error.")
    _dictate(p)
    shown = H.as_pill(p.overlay.toasts()[-1])
    p._on_toast_action(shown.close)
    assert ("hide_toast", (), {}) in p.overlay.calls
    assert len(list((tmp_path / "unsent").glob("recording-*.wav"))) == 1
    assert [e for e in H.history(p) if e.get("failed")]


def test_send_later_from_the_pills_button_keeps_the_recording(tmp_path):
    hang = H.Hang(30)
    p = H.make_pipeline(tmp_path, transcriber=H.FakeTranscriber(hang=hang))
    worker = H.run_process(p)
    assert H.wait_for(lambda: p.overlay.toasts(), timeout=3)
    offer = H.as_pill(p.overlay.toasts()[0])
    assert offer.button["label"] == "Send later"
    p._on_toast_action(offer.button["action"])
    worker.join(3)
    hang.release.set()
    assert not worker.is_alive()
    [entry] = H.history(p)
    assert entry["failed"] and entry["not_sent_reason"] == "later"
    assert len(list((tmp_path / "unsent").glob("recording-*.wav"))) == 1


def test_paste_as_is_from_the_pills_button_pastes_the_words_as_said(tmp_path):
    hang = H.Hang(30)
    p = H.make_pipeline(tmp_path, styler=H.FakeStyler(hang=hang))
    worker = H.run_process(p)
    assert H.wait_for(lambda: p.overlay.toasts(), timeout=3)
    offer = H.as_pill(p.overlay.toasts()[0])
    assert offer.button == {"label": "Paste as is", "action": pw.PASTE_RAW}
    p._on_toast_action(offer.button["action"])
    worker.join(3)
    hang.release.set()
    assert not worker.is_alive()
    assert p.clipboard.pastes == ["ok so ship it on monday"]
    assert H.history(p)[0]["styled"] == "ok so ship it on monday"


def test_a_clean_up_that_fails_still_pastes_and_saves_the_words(tmp_path):
    p = H.make_pipeline(tmp_path, styler=H.FakeStyler(error=RuntimeError("boom")))
    _dictate(p)
    assert len(p.clipboard.pastes) == 1 and p.clipboard.pastes[0]
    assert H.history(p), "in the Journal"
    shown = [H.as_pill(t) for t in p.overlay.toasts()]
    assert [(s.kind, s.text) for s in shown] == [("problem", "Pasted as said")]


def test_every_pill_that_points_at_the_words_has_a_way_to_them():
    """ "Saved" with a button opens the Journal or the entry; a Done pill
    about an entry opens it on a click."""
    for row in ALL:
        p = pill.pill_for(*row[1])
        if p.button and "Saved" in p.text:
            assert p.button["label"] == "Open"
            assert p.button["action"].startswith(("open_journal", "show_transform", "show_command"))
        if p.click:
            assert p.kind == pill.DONE
            assert p.click.startswith(("open_journal", "show_transform", "show_command"))


def test_the_messages_that_tell_where_the_words_are_still_exist_in_full():
    """Nothing was cut from the sentences themselves."""
    assert "on the clipboard" in um.paste_failed_message("no_target", "darwin")[1]
    assert "saved in the Journal" in um.paste_failed_message("clipboard_error", "darwin")[1]
    assert "Your recording is saved in the Journal" in unsent.toast_text(unsent.REASON_OFFLINE, "Groq")[1]
    assert "on the clipboard and in the Journal" in um.paste_timed_out_message("darwin")[1]
