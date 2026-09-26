"""Plain-English error messages that the app shows to people.

Raw exception text used to reach the screen: "Connection error:
HTTPSConnectionPool(host='api.groq.com', ...)" when checking a key offline,
"GitHub API returned HTTP 403" from the update check, curl output from a
failed download, and "Refusing to download from an untrusted URL." when a
release simply had no installer for this computer. Each message here says
what happened and what to do, in words anyone can follow. The raw detail
still goes to app.log for troubleshooting.

The key-check wording is the setup copy from the 2026-09 onboarding review.
"""

from __future__ import annotations

import math
import re

DOWNLOAD_PAGE = "https://wafflerai.com/download/"

# ── Checking an API key ──────────────────────────────────────────────────────

_KEY_MESSAGES = {
    "offline": "Couldn't reach {p}. Check you're online. If you use a VPN, turn it off "
               "and try again.",
    "unauthorized": "{p} didn't accept that key. Make a new one and click Copy again.",
    "forbidden": "{p} blocked this connection. This usually means a VPN is on. Turn it "
                 "off and try again.",
    "rate_limited": "{p} is busy for a moment. Try again in a few seconds.",
    "no_credit": "This {p} key has no credit yet. Add credit to your {p} account, then "
                 "try again.",
    "other": "Couldn't check that key with {p}. Try again in a moment.",
}

_OFFLINE_NAMES = {
    "APIConnectionError", "APITimeoutError", "ConnectionError", "ConnectTimeout",
    "ReadTimeout", "Timeout", "ConnectError", "TimeoutException", "NewConnectionError",
    "MaxRetryError", "NameResolutionError", "gaierror",
}
_OFFLINE_TEXT = (
    "connection error", "timed out", "timeout", "max retries exceeded",
    "failed to establish a new connection", "getaddrinfo failed",
    "name or service not known", "nodename nor servname", "network is unreachable",
    "connection refused", "connection reset", "httpsconnectionpool",
)


def _status_of(exc) -> int | None:
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def classify_request_error(exc) -> str:
    """'offline', 'unauthorized', 'forbidden', 'rate_limited', 'no_credit' or
    'other', from an SDK or requests exception."""
    status = _status_of(exc)
    text = str(exc)
    lower = text.lower()
    if status == 429 or "429" in text:
        return "no_credit" if "insufficient_quota" in lower else "rate_limited"
    if status == 401:
        return "unauthorized"
    if status == 403:
        return "forbidden"
    names = {cls.__name__ for cls in type(exc).__mro__}
    if names & _OFFLINE_NAMES or isinstance(exc, (ConnectionError, TimeoutError)):
        return "offline"
    if status is None:
        if ("401" in text or "unauthorized" in lower
                or ("invalid" in lower and "key" in lower) or "incorrect api key" in lower):
            return "unauthorized"
        if "403" in text or "access denied" in lower or "forbidden" in lower:
            return "forbidden"
        if any(s in lower for s in _OFFLINE_TEXT):
            return "offline"
    return "other"


def key_check_error(provider: str, exc) -> str:
    """The message for a failed key check with ``provider`` ("Groq", ...)."""
    return _KEY_MESSAGES[classify_request_error(exc)].format(p=provider)


# ── Updates ──────────────────────────────────────────────────────────────────

UPDATE_CHECK_FAILED = "Couldn't check for updates. Try again later."
UPDATE_CHECK_OFFLINE = "Couldn't check for updates. Check you're online and try again."
# The app opens the release's own page for this one (ui/logic.js
# updateCheckView), so the sentence points there.
UPDATE_NO_INSTALLER = ("This update has no installer for this computer yet. "
                       "Try again later, or see the release page.")
UPDATE_DOWNLOAD_FAILED = ("The update didn't download. Try again, or get it from the "
                          "download page.")
UPDATE_INSTALL_FAILED = ("The update couldn't be installed. Get it from the download "
                         "page instead.")


def update_check_error(exc=None) -> str:
    """Message for a failed update check. Without an exception (GitHub
    answered with an error status: 403 is its rate limit) it is the general
    "try again later"; an exception that means no connection gets the
    "check you're online" version."""
    if exc is not None and classify_request_error(exc) == "offline":
        return UPDATE_CHECK_OFFLINE
    return UPDATE_CHECK_FAILED


# ── When the clean-up is skipped, or a limit is reached ──────────────────────
# The pill's messages after a dictation. They used to say "Groq limit hit",
# "Pasted raw", "Add a Cerebras key for fallback", "Auth blocked" and "Try
# another provider key", with a dash in every body. A clean-up that ran out
# of its own time budget was also reported as "Connection failed".

_PROVIDERS = ("Groq", "OpenAI", "Cerebras")
CLEANUP_SKIPPED = "Pasted without the clean-up"


def _provider_in(text: str) -> str:
    """The provider a styler reason names ("Groq: ...", "RATE_LIMIT|...|
    Groq: ..."), or ""."""
    text = str(text or "")
    head = text.split(":", 1)[0].strip()
    if head in _PROVIDERS:
        return head
    for part in text.split("|"):
        head = part.split(":", 1)[0].strip()
        if head in _PROVIDERS:
            return head
    return ""


def _seconds_in(wait: str):
    """Seconds in a provider's wait ("16m12s", "7.66s", "1h2m"), or None."""
    m = re.match(r"^\s*(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?\s*$", str(wait or ""))
    if not m or not m.group(0).strip():
        return None
    try:
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3) or 0)
    except ValueError:
        return None


def _about(seconds: float) -> str:
    """'about 17 minutes' for a wait, rounded up so it is never '0'."""
    s = max(1, math.ceil(seconds))
    if s < 60:
        return f"about {s} second{'s' if s != 1 else ''}"
    minutes = math.ceil(s / 60)
    if minutes < 60:
        return f"about {minutes} minute{'s' if minutes != 1 else ''}"
    # Whole hours keep the heading on one line of the pill's message.
    hours = max(1, round(minutes / 60))
    return f"about {hours} hour{'s' if hours != 1 else ''}"


def _rate_limit_parts(reason: str):
    """(provider, seconds or None, daily) from "RATE_LIMIT|<limit>|<wait>|<detail>"."""
    text = str(reason or "")
    fields = text[text.index("RATE_LIMIT|"):].split("|", 3) if "RATE_LIMIT|" in text else []
    limit = fields[1] if len(fields) > 1 else ""
    wait = fields[2].strip().rstrip(".") if len(fields) > 2 else ""
    detail = fields[3] if len(fields) > 3 else ""
    provider = limit if limit in _PROVIDERS else _provider_in(detail) or _provider_in(text)
    daily = "per day" in limit.lower() or any(k in limit for k in ("TPD", "RPD", "ASD"))
    return provider, _seconds_in(wait), daily


def cleanup_skipped_message(reason: str):
    """(heading, body) for the pill after the words were pasted without the
    clean-up. ``reason`` is the styler's fallback_reason; it stays in the
    log, and nothing of it but a provider's name reaches the screen."""
    text = str(reason or "")
    lower = text.lower()
    provider = _provider_in(text)
    who = provider or "Your clean-up service"
    if "RATE_LIMIT|" in text:
        provider, seconds, daily = _rate_limit_parts(text)
        if seconds:
            when = f"for {_about(seconds)}"
        elif daily:
            when = "until tomorrow"
        else:
            when = "for now"
        who = provider or "Your clean-up service"
        return (f"Clean-up paused {when}",
                f"{who} says you've reached your limit for now. Your words were "
                f"pasted as you said them.")
    if "no styling providers configured" in lower:
        return (CLEANUP_SKIPPED, "No key is set up for the clean-up yet. Add a free Groq "
                                 "key in Settings to turn it on.")
    if "TIMEOUT|" in text:
        # The clean-up ran out of time (its own budget, or the watchdog's).
        # Nothing is wrong with the connection, so it does not say so.
        return (CLEANUP_SKIPPED, "The clean-up took too long, so your words went in as "
                                 "you said them.")
    blocked = ("AUTH:" in text or "access denied" in lower or "unauthorized" in lower
               or "invalid_api_key" in lower or "incorrect api key" in lower)
    if not blocked and "CONNECTION" not in text:
        blocked = bool(re.search(r"\b40[13]\b", text))
    if blocked:
        return (CLEANUP_SKIPPED, f"{who} refused the connection, which usually means a VPN "
                                 f"is on or the key has stopped working. Your words went in "
                                 f"as you said them.")
    if "CONNECTION" in text or "timeout" in lower or "timed out" in lower \
            or "connection" in lower:
        where = provider or "your clean-up service"
        return (CLEANUP_SKIPPED, f"Waffler couldn't reach {where}, so your words went in as "
                                 f"you said them. Check you're online.")
    return (CLEANUP_SKIPPED, "The clean-up didn't work this time, so your words went in as "
                             "you said them.")


def limit_reached_message(error_text: str):
    """(heading, body) when a dictation stopped because a provider's limit
    was reached (a 429, or "RATE_LIMIT|<limit>|<wait>|<detail>")."""
    provider, seconds, daily = _rate_limit_parts(error_text)
    provider = provider or _provider_in(error_text)
    who = provider or "Your speech service"
    if seconds:
        then = f"Try again in {_about(seconds)}."
    elif daily:
        then = "Try again tomorrow."
    else:
        then = "Wait a moment and try again."
    return ("Limit reached", f"{who} says you've reached your limit for now. {then}")
