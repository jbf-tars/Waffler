"""What to do with a dictation's result when something interrupted it.

A finished dictation can be interrupted two very different ways, and the
pipeline treated them identically, which threw away completed work:

  SUPERSEDED   the user pressed the hotkey again while this one was still
               transcribing or styling. They did not ask to discard anything;
               they just started the next thought. The words are valid.

  CANCELLED    the user explicitly cancelled THIS dictation (Esc, or the
               overlay's cancel). They asked for it to go away.

Both used to abort before the history write, so starting a second dictation
too quickly silently destroyed the first one's finished transcript. The result
had already been produced; only the paste was ever unsafe, because the newer
recording now owns the focus and the clipboard.

The policy is therefore:

    pasting is about the PRESENT (whose window and clipboard is this?)
    keeping is about the PAST   (did the user get words out of it?)
"""


def decide(*, superseded: bool, cancelled: bool) -> dict:
    """Return what a finished dictation may still do.

    ``paste`` writes to the clipboard and sends the keystroke. Only ever true
    when this dictation is still the current one and was not cancelled.

    ``save_history`` keeps the transcript. True unless the user explicitly
    cancelled, because superseded work is still work the user did.
    """
    return {
        "paste": not superseded and not cancelled,
        "save_history": not cancelled,
        "reason": ("cancelled" if cancelled
                   else "superseded" if superseded
                   else "ok"),
    }
