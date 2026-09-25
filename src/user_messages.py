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
