"""Clean-up paused by a provider's limit, as the window shows it.

When every clean-up provider is at its limit (Groq's free daily limit,
usually), dictation carries on and pastes the words as they were said. The
recording overlay says so once; the Journal now says it too, at the top,
until the pause ends, with a way to add a backup key. The entry itself is
tagged "As said: limit reached".

``from_reason`` reads the styler's fallback reason
("RATE_LIMIT|<limit>|<wait>|<provider>: <detail>"); ``view`` is what the
banner shows at a given moment, or None once the pause is over.
"""

from __future__ import annotations

from datetime import datetime, timedelta

try:
    from user_messages import _rate_limit_parts
except ImportError:  # imported as src.cleanup_pause
    from src.user_messages import _rate_limit_parts

# With no wait in the provider's answer, the styler stops asking for a
# minute (style_openai.py), so the banner lasts as long.
_UNKNOWN_WAIT_S = 60.0

LIMIT_TAG = "limit"      # history item "as_said": the words were kept as said


def from_reason(reason, now: datetime):
    """A pause, or None when the reason isn't a limit.

    {"provider": "Groq", "until": <ISO local time>, "daily": bool}. A daily
    limit with no wait given lasts until midnight."""
    text = str(reason or "")
    if "RATE_LIMIT|" not in text:
        return None
    provider, seconds, daily = _rate_limit_parts(text)
    if seconds:
        until = now + timedelta(seconds=seconds)
    elif daily:
        until = datetime(now.year, now.month, now.day) + timedelta(days=1)
    else:
        until = now + timedelta(seconds=_UNKNOWN_WAIT_S)
    return {"provider": provider or "", "until": until.isoformat(timespec="seconds"),
            "daily": bool(daily)}


def view(pause, now: datetime):
    """What the Journal banner says, or None when there is no pause now."""
    if not pause:
        return None
    try:
        until = datetime.fromisoformat(str(pause.get("until")))
    except (TypeError, ValueError):
        return None
    if until <= now:
        return None
    who = pause.get("provider") or "Your clean-up service"
    if until.date() == now.date():
        when = f"until {until.strftime('%H:%M')}"
    elif until.date() == (now + timedelta(days=1)).date() and until.time() == datetime.min.time():
        when = "until tomorrow"
    else:
        when = f"until {until.strftime('%H:%M')} tomorrow" if until.date() == (now + timedelta(days=1)).date() \
            else f"until {until.strftime('%d %B, %H:%M').lstrip('0')}"
    return {
        "title": f"Clean-up is paused {when}",
        "detail": f"{who} says you've reached your limit for now. Dictation still works: "
                  f"you get your words as you said them.",
        "provider": pause.get("provider") or "",
        "until": until.isoformat(timespec="seconds"),
        "seconds_left": int((until - now).total_seconds()),
    }
