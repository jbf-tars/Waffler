"""Recordings that were not turned into text, and sending them again.

When speech to text fails (no connection, a VPN block, a limit, or the
watchdog's deadline) the pipeline saves the recording as a WAV in the data
folder's ``unsent/`` and adds a "Not sent" card to the Journal. The card
used to promise "saved so you can retry", but nothing in the app could reach
the file: five recordings sat in one user's unsent/ for months.

This module holds the rules; app.py does the sending. The rules:

  * Only recordings Waffler itself saved are ever sent. A file is sent only
    when a Journal entry marked ``failed`` names it, the name has the shape
    Waffler gives these files, and it resolves inside unsent/. Nothing is
    found by listing the folder, so a stray WAV someone drops there is never
    uploaded.
  * Sending uses the user's own keys, through the same transcriber the
    dictation used. Nothing is pasted: the words go into the Journal card.
  * Automatic retries are bounded: recordings from the last day only, at
    most three automatic attempts each, spaced out, and never while a
    dictation is recording or being processed. Try again on the card always
    works.
  * A request Waffler stopped waiting for is still answered, and billed, by
    the provider. While it runs nothing sends that recording again, and the
    words it brings back go into the card (app.py _collect_late_words).
  * A recording cancelled with Esc during processing is kept, because Esc
    also reaches the app in front, but it is not waiting to be sent: only
    Try again sends it.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

UNSENT_DIRNAME = "unsent"

# recording-2026-09-26T10-04-31.wav, or with -2, -3 ... when two recordings
# failed in the same second (the old name had no suffix, so the second one
# overwrote the first).
_ID_RE = re.compile(r"^recording-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}(?:-\d{1,3})?\.wav$")

# Automatic sending: how old a recording may be, how many tries, and the gap
# before each try (after the first failure, the second, the third).
AUTO_MAX_AGE = timedelta(hours=24)
AUTO_MAX_ATTEMPTS = 3
AUTO_BACKOFF_S = (60.0, 300.0, 900.0)

# Why a recording was not sent. The Journal card turns these into a sentence
# (ui/logic.js notSentView), so the raw error never reaches the screen.
REASON_OFFLINE = "offline"
REASON_BLOCKED = "blocked"
REASON_TIMEOUT = "timeout"
REASON_RATE_LIMITED = "rate_limited"
REASON_LATER = "later"
REASON_STUCK = "stuck"
REASON_ERROR = "error"
# Sent again and it went through, but no words could be heard in it.
REASON_EMPTY = "empty"
# Esc was pressed while it was being turned into text. Esc also reaches the
# app in front, so it may not have been meant for Waffler: the recording is
# kept, but only Try again sends it (and a request already on its way can
# still fill the card).
REASON_CANCELLED = "cancelled"


def classify_reason(error_text: str) -> str:
    """Sort a transcription failure into one of the REASON_ kinds."""
    text = str(error_text or "")
    lower = text.lower()
    if lower == REASON_LATER:
        return REASON_LATER
    if lower == REASON_STUCK:
        return REASON_STUCK
    if lower == REASON_CANCELLED:
        return REASON_CANCELLED
    if "deadline" in lower or "took too long" in lower:
        return REASON_TIMEOUT
    if ("403" in text or "401" in text or "access denied" in lower
            or "unauthorized" in lower or "permission" in lower):
        return REASON_BLOCKED
    if "429" in text or "rate limit" in lower or "rate_limit" in lower:
        return REASON_RATE_LIMITED
    if "timed out" in lower or "timeout" in lower:
        return REASON_TIMEOUT
    if ("connection" in lower or "network" in lower or "resolve" in lower
            or "no transcription backend" in lower or "unreachable" in lower):
        return REASON_OFFLINE
    return REASON_ERROR


def toast_text(reason: str, provider: str = "", saved: bool = True):
    """(heading, body) for the pill's message when a recording is not sent.
    Plain words; the raw error goes to app.log only."""
    p = provider or "your speech service"
    P = p[:1].upper() + p[1:]          # at the start of a sentence
    if not saved:
        return ("Not sent", "This one wasn't turned into text, and the recording "
                            "couldn't be saved. Please say it again.")
    kept = "Your recording is saved in the Journal"
    if reason == REASON_CANCELLED:
        return ("Cancelled", "Nothing was pasted. Your recording is in the Journal "
                             "if you want it after all.")
    if reason == REASON_LATER:
        return ("Saved for later", f"Waffler will send it when {p} answers. "
                                   f"It's in the Journal.")
    if reason == REASON_BLOCKED:
        return ("Not sent", f"{P} refused the connection, which usually means a VPN "
                            f"is on. {kept}: try again from there.")
    if reason == REASON_RATE_LIMITED:
        return ("Not sent", f"{P} says you've reached your limit for now. {kept}, "
                            f"and Waffler will send it later.")
    if reason == REASON_TIMEOUT:
        return ("Not sent", f"{P} took too long to answer. {kept}, and Waffler "
                            f"will send it when {p} answers.")
    if reason == REASON_OFFLINE:
        return ("Not sent", f"Waffler couldn't reach {p}. {kept}, and Waffler will "
                            f"send it when you're back online.")
    if reason == REASON_STUCK:
        return ("That took too long", f"Waffler stopped waiting. {kept}.")
    return ("Not sent", f"This one wasn't turned into text. {kept}.")


# What the entry's "styled" field says. The Journal card writes its own
# sentence from not_sent_reason (ui/logic.js notSentView); this is what an
# export or a search sees.
STYLED_NOTE = "Not sent: this recording hasn't been turned into text yet."
STYLED_NOTE_NO_FILE = "Not sent: this recording couldn't be saved."


def new_unsent_path(unsent_dir: Path, now: datetime = None) -> Path:
    """A fresh, unused file name for a recording saved at ``now``."""
    now = now or datetime.now()
    stamp = now.strftime("%Y-%m-%dT%H-%M-%S")
    path = Path(unsent_dir) / f"recording-{stamp}.wav"
    n = 2
    while path.exists() and n < 1000:
        path = Path(unsent_dir) / f"recording-{stamp}-{n}.wav"
        n += 1
    return path


def is_valid_id(unsent_id: str) -> bool:
    return isinstance(unsent_id, str) and bool(_ID_RE.match(unsent_id))


def entry_id(entry: dict) -> str:
    """The unsent id of a Journal entry, or "" when it is not a Not sent
    entry with a recording. Older entries carry only ``audio_path``."""
    if not isinstance(entry, dict) or not entry.get("failed"):
        return ""
    # Split on both separators: Path() only knows the running platform's, so
    # a Windows path ("C:\\...\\recording-....wav") read on a Mac would
    # otherwise come back whole and fail is_valid_id.
    uid = entry.get("unsent_id") or re.split(r"[\\/]", str(entry.get("audio_path") or ""))[-1]
    return uid if is_valid_id(uid) else ""


def resolve_file(unsent_dir: Path, unsent_id: str):
    """The recording's path when ``unsent_id`` is well formed and the file
    exists inside ``unsent_dir``; otherwise None."""
    if not is_valid_id(unsent_id):
        return None
    base = Path(unsent_dir).resolve()
    path = (base / unsent_id).resolve()
    if path.parent != base or not path.is_file():
        return None
    return path


def find_entry(history: list, unsent_id: str):
    """(index, entry) of the Not sent entry for ``unsent_id``, or (-1, None)."""
    if not is_valid_id(unsent_id):
        return -1, None
    for i in range(len(history) - 1, -1, -1):
        if entry_id(history[i]) == unsent_id:
            return i, history[i]
    return -1, None


def pending(history: list, unsent_dir: Path, *, include_cancelled: bool = True) -> list:
    """Every Not sent entry whose recording is still on disk, oldest first,
    as (unsent_id, entry, path). ``include_cancelled=False`` leaves out the
    ones cancelled with Esc: they are kept, but not waiting to be sent."""
    out = []
    for entry in history:
        uid = entry_id(entry)
        if not uid:
            continue
        if not include_cancelled and not is_waiting(entry):
            continue
        path = resolve_file(unsent_dir, uid)
        if path is not None:
            out.append((uid, entry, path))
    return out


def is_waiting(entry: dict) -> bool:
    """True when Waffler means to send this recording: every Not sent
    recording except one cancelled with Esc, which waits for Try again."""
    return entry.get("not_sent_reason") != REASON_CANCELLED


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


def auto_retry_due(entry: dict, now: datetime = None, ignore_backoff: bool = False) -> bool:
    """True when Waffler may try this recording again on its own now.

    ``ignore_backoff`` skips the wait between tries, for when there is fresh
    evidence the provider answers (a dictation just went through). The age
    and attempt limits still apply.
    """
    now = now or datetime.now()
    made = _parse(entry.get("timestamp"))
    if made is None or now - made > AUTO_MAX_AGE:
        return False
    if entry.get("not_sent_reason") in (REASON_EMPTY, REASON_CANCELLED):
        return False       # nothing could be heard in it, or it was cancelled
    attempts = int(entry.get("auto_attempts") or 0)
    if attempts >= AUTO_MAX_ATTEMPTS:
        return False
    if ignore_backoff:
        return True
    last = _parse(entry.get("last_attempt_at")) or made
    wait = AUTO_BACKOFF_S[min(attempts, len(AUTO_BACKOFF_S) - 1)]
    return (now - last).total_seconds() >= wait


def will_auto_retry(entry: dict, now: datetime = None) -> bool:
    """True when an automatic try is still to come (now or later)."""
    now = now or datetime.now()
    made = _parse(entry.get("timestamp"))
    if made is None or now - made > AUTO_MAX_AGE:
        return False
    if entry.get("not_sent_reason") in (REASON_EMPTY, REASON_CANCELLED):
        return False
    return int(entry.get("auto_attempts") or 0) < AUTO_MAX_ATTEMPTS


def with_attempt(entry: dict, *, auto: bool, reason: str, now: datetime = None) -> dict:
    """A copy of ``entry`` recording one more failed attempt."""
    now = now or datetime.now()
    out = dict(entry)
    if auto:
        out["auto_attempts"] = int(out.get("auto_attempts") or 0) + 1
    out["last_attempt_at"] = now.isoformat(timespec="seconds")
    out["not_sent_reason"] = reason
    out["will_retry"] = will_auto_retry(out, now)
    return out


def resolved(entry: dict, *, transcript: str, styled: str, now: datetime = None) -> dict:
    """A copy of ``entry`` as a normal Journal entry, with the words.

    The timestamp stays the time the user dictated; ``sent_later_at`` says
    when the words arrived.
    """
    now = now or datetime.now()
    out = {k: v for k, v in entry.items()
           if k not in ("failed", "error", "audio_path", "unsent_id", "not_sent_reason",
                        "auto_attempts", "last_attempt_at", "will_retry", "provider_name")}
    out["text"] = transcript
    out["styled"] = styled
    out["word_count"] = len(styled.split())
    out["text_is"] = "asr_filtered"
    out["sent_later_at"] = now.isoformat(timespec="seconds")
    return out
