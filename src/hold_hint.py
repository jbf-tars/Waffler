"""The "Don't tap, hold" hint: when a quick tap of the hotkey is dropped.

Waffler throws away a press of under half a second that caught no speech: a
brush of the key, or someone tapping instead of holding. It used to do so
without a word, which leaves a new user wondering why nothing happened. Now it
says what to do, as a quiet tip through the notifier (src/notifier.py): once
every 12 hours at most, naming the person's own hotkey, with "Don't show
again", and never when the take locked hands-free or a second press followed.

This module is the small pure part: which presses count, the pause that waits
for a second press, and the name of the hotkey. The tip's words are in
src/user_messages.py (hold_hint_message); the pipeline hook is in app.py.
"""

from __future__ import annotations

import math

try:
    import hotkey_rules
except ImportError:  # imported as src.hold_hint
    from src import hotkey_rules

# A press shorter than this is a tap. It is the pipeline's accidental-tap
# threshold (app.py _process), which the hint is only asked about.
TAP_MAX_S = 0.5
# After a tap, wait this long for a second press before saying anything: someone
# who taps and straight away holds has already worked it out.
SETTLE_S = 0.9
# How long the hint stays up unless closed. It has two buttons to read.
HINT_SECONDS = 10

DEFAULT_KEYS = {"Darwin": ["fn"], "Windows": ["win", "ctrl"]}


def wants_hint(duration_s, hands_free: bool) -> bool:
    """A tap, and not one that locked hands-free."""
    if hands_free:
        return False
    try:
        d = float(duration_s)
    except (TypeError, ValueError):
        return False
    return math.isfinite(d) and 0 <= d < TAP_MAX_S


def hotkey_name(keys, system: str) -> str:
    """The hotkey as the Hotkey page shows it: "Fn", "Win + Ctrl",
    "Command + Shift + Space". A setting that makes no sense gives the
    platform's default."""
    platform = hotkey_rules.WINDOWS if system == "Windows" else hotkey_rules.MAC
    ok = (isinstance(keys, (list, tuple)) and keys
          and all(isinstance(k, str) and k.strip() for k in keys))
    if not ok:
        keys = DEFAULT_KEYS[platform]
    return hotkey_rules.display([k.strip().lower() for k in keys], platform)
