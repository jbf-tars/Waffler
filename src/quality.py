"""Per-recording quality signals.

Tells the user when a dictation is *probably* wrong, from evidence the pipeline
already has: measured audio and what the pipeline actually did. Everything here
is local, instant and free.

WHY NOT AN LLM REVIEWER
-----------------------
Judging a transcript from its text cannot detect omission. If the speaker says
twelve sentences and eight come back, the eight read perfectly - nothing in the
text indicates anything is absent, so a reviewer approves it every time. That is
worse than no check, because it manufactures confidence. The only ground truth
is the audio. So these signals are derived from MEASURED speech duration and
from observable pipeline behaviour, never from a judgement about the prose.

DESIGN RULES
------------
* Flag, never block. The paste stays instant; the user is told, not gated.
* Content-loss signals outrank advisory ones.
* A clean recording must produce NO flags. If ordinary dictations light up,
  the flags become noise and get ignored, which defeats the point.
* Never invent a signal from a missing measurement.
"""

# Real speech does not sustain below ~1 word per second OF SPEECH. Measured
# across 205 real recordings: every healthy one >= 1.17; the two confirmed
# broken ones sat at 0.32 and 0.81.
_MIN_WORDS_PER_SPEECH_SEC = 1.0
# Below this much measured speech the ratio is too noisy to judge, and short
# utterances ("Yes.") are legitimate.
_MIN_SPEECH_S_TO_JUDGE = 10.0
# Cleanup legitimately removes filler; losing more than a fifth of the words
# is not filler removal.
_MIN_STYLED_RETENTION = 0.80

# Words that cannot end a finished sentence. Ending on one is the signature of
# a mid-clause cut ("Then based on the", "I'm also sure that"). Measured over
# 2851 real recordings: 2.2% end this way, versus 7.8% that end without
# punctuation on a content word - the latter is often a legitimate title or
# fragment, so the two are graded differently rather than lumped together.
_DANGLING_TAIL = {
    "the", "a", "an", "and", "or", "but", "to", "of", "for", "with", "from",
    "that", "this", "in", "on", "at", "is", "are", "was", "were", "be", "been",
    "we", "i", "it", "as", "if", "so", "because", "which", "when", "then",
    "about", "into", "our", "their", "your", "my", "his", "her", "its",
}

# Signals that mean content was probably lost.
_CONTENT_LOSS = {"low_word_rate", "styled_dropped_words", "styling_fallback",
                 "truncated_midsentence"}


def assess(*, speech_seconds, transcript_words, styled_words, asr_filtered,
           styling_provider, styled_text, retry_fired=False,
           deadline_fired=False) -> dict:
    """Return {"level": "ok"|"check"|"low", "flags": [...],
    "words_per_speech_second": float}.

    ``level`` is "low" when a content-loss signal fired, "check" for advisory
    signals worth a glance, "ok" otherwise.
    """
    flags = []
    speech_seconds = float(speech_seconds or 0.0)
    transcript_words = int(transcript_words or 0)
    styled_words = int(styled_words or 0)

    wps = (transcript_words / speech_seconds) if speech_seconds > 0 else 0.0

    # An empty result on a near-silent clip is a normal outcome, not a fault.
    empty_and_silent = transcript_words == 0 and speech_seconds < _MIN_SPEECH_S_TO_JUDGE

    # 1. Impossibly few words for the measured speech. Only judged when there
    #    is enough speech to make the ratio meaningful - never guessed.
    if (speech_seconds >= _MIN_SPEECH_S_TO_JUDGE
            and wps < _MIN_WORDS_PER_SPEECH_SEC):
        flags.append("low_word_rate")

    # 2. Cleanup discarded a large share of the transcript.
    if transcript_words >= 8 and styled_words < transcript_words * _MIN_STYLED_RETENTION:
        flags.append("styled_dropped_words")

    # 3. Cleanup never ran - the user is looking at lightly-cleaned raw text.
    if str(styling_provider or "").lower() in ("basic_clean", "local"):
        flags.append("styling_fallback")

    # 4. Advisory: the ASR filter altered the provider's words. Usually correct,
    #    but it is the one place text is removed without the user seeing it.
    if asr_filtered:
        flags.append("asr_filter_edited")

    # 5. Advisory: a provider had to be retried, or the styling budget expired.
    if retry_fired:
        flags.append("retry_used")
    if deadline_fired:
        flags.append("styling_deadline")

    # 6. Output stops without terminal punctuation. Graded by what it stops
    #    ON: a dangling function word is a near-certain mid-clause cut and is
    #    treated as content loss; anything else is advisory, because a
    #    legitimate title or fragment also lacks a full stop.
    txt = (styled_text or "").strip()
    if txt and txt[-1] not in ".!?:\"')]" and len(txt.split()) >= 4:
        if txt.split()[-1].lower().strip(",;") in _DANGLING_TAIL:
            flags.append("truncated_midsentence")
        else:
            flags.append("unterminated_ending")

    if empty_and_silent:
        flags = [f for f in flags if f not in _CONTENT_LOSS]

    if any(f in _CONTENT_LOSS for f in flags):
        level = "low"
    elif flags:
        level = "check"
    else:
        level = "ok"

    return {"level": level, "flags": flags,
            "words_per_speech_second": round(wps, 2)}


# ── Microphone dropout ──────────────────────────────────────────────────────
# A window is "dead" below this RMS: essentially digital zero.
_DEAD_RMS = 1.0
# ...and carries speech at or above this.
_SPEECH_RMS = 12.0
# A dead run must reach this long before it is even a candidate. Shorter runs
# are pauses, and with noise suppression a pause IS digital silence.
_MIN_DEAD_RUN_S = 3.0
# The run must also cover a real share of the take, so a brief glitch in a long
# recording is not treated as the stream dying.
_MIN_DEAD_RUN_FRACTION = 0.25


def mic_dropout_signal(window_rms, *, window_s: float) -> dict:
    """Decide whether a recording looks like the mic stream DIED.

    The previous rule flagged any recording where >=30% of windows were
    digital silence, on the assumption that "natural pauses keep room-tone".
    Modern capture breaks that assumption: noise suppression emits exact zeros
    while you are not speaking, so pausing to think was indistinguishable from
    the microphone dying. Measured over 861 real recordings it fired 4 times
    and was wrong all 4 times - every one transcribed completely.

    What actually separates the two is SHAPE, not amount:

        gated pauses : many short dead runs, speech resumes after each
        dead stream  : one long dead run that never recovers

    So this measures the longest CONTIGUOUS dead run and requires it to reach
    the end of the recording. A run that recovers proves the mic was alive.

    Even a terminal run is not conclusive - a speaker who stops talking before
    releasing the hotkey produces one too - so this is deliberately named a
    *signal*. The caller should confirm against the transcript before telling
    the user anything is missing.
    """
    n = len(window_rms or [])
    if n == 0:
        return {"suspected": False, "dead_fraction": 0.0,
                "longest_dead_run_s": 0.0, "terminal": False}

    dead = [r < _DEAD_RMS for r in window_rms]
    has_speech = any(r >= _SPEECH_RMS for r in window_rms)

    longest = run = 0
    longest_end = -1
    for i, d in enumerate(dead):
        if d:
            run += 1
            if run > longest:
                longest, longest_end = run, i
        else:
            run = 0

    longest_s = longest * window_s
    total_s = n * window_s
    # "Terminal" = the longest dead run is still going when the recording ends.
    terminal = longest_end == n - 1 and longest > 0

    suspected = (
        has_speech                                   # something was recorded
        and terminal                                 # and it never came back
        and longest_s >= _MIN_DEAD_RUN_S             # for a meaningful stretch
        and (longest_s / total_s) >= _MIN_DEAD_RUN_FRACTION
    )
    return {
        "suspected": bool(suspected),
        "dead_fraction": round(sum(dead) / n, 3),
        "longest_dead_run_s": round(longest_s, 2),
        "terminal": bool(terminal),
    }
