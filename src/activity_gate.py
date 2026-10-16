"""One door for starting something that takes the microphone or the model.

A dictation, a Transform and a Command run each look at whether another is
under way and then claim the slot for themselves. Done in separate steps, two
near-simultaneous presses could both find the slot free and both claim it: a
dictation and a command on one recorder, where the second ``start()`` empties
the first's buffer and one of the two ``stop()`` calls comes back with nothing.

Each starter takes ``GATE`` for the look and the claim together, so whichever
comes second sees the first's claim. The lock is held for a few lines of
bookkeeping only (never across a recording, a model call or a paste) and is
re-entrant, so a starter that is asked from inside another is not stuck.
"""
import threading

GATE = threading.RLock()
