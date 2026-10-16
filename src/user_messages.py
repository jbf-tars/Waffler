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
# Restart to update waits while a dictation is recorded, cleaned up or a Not
# sent recording is being sent; the window tries again by itself.
UPDATE_WAITING = ("Finishing your dictation first. Waffler will restart to "
                  "update as soon as it's done.")


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
    if "MODEL_UNAVAILABLE" in text:
        # The provider has retired the clean-up model (or this key can't reach
        # it). Not a limit, so no "paused" and no Journal banner: it lasts
        # until an update changes the model or another key can take over.
        return (CLEANUP_SKIPPED, "The clean-up model is no longer available, so your words "
                                 "went in as you said them. That will carry on until Waffler "
                                 "is updated or you add another key.")
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


# ── A dictation Waffler was stopped in the middle of ─────────────────────────
# The Journal card of a take that was being processed when Waffler quit or
# crashed, found again at the next start (src/take_marker.py). The card's own
# sentence is in ui/logic.js (notSentView); a test keeps the two the same.

INTERRUPTED_MESSAGE = ("Waffler was closed before this one was turned into text. "
                       "The recording is saved on this computer.")


# ── Pasting ──────────────────────────────────────────────────────────────────
# What the pill says when the words did not land in the app, and the menu's
# "Paste last" lines. The key to press is the platform's.

def paste_keys(platform: str) -> str:
    return "Cmd+V" if platform == "darwin" else "Ctrl+V"


def paste_failed_message(reason: str, platform: str):
    """(heading, body) for a paste that did not land. The words are on the
    clipboard unless ``reason`` is "clipboard_error"; they are in the
    Journal either way."""
    keys = paste_keys(platform)
    if reason == "accessibility_off":
        return ("Couldn't paste",
                f"Waffler needs Accessibility to paste. Your text is on the clipboard, "
                f"so press {keys} in a text box.")
    if reason == "clipboard_error":
        return ("Couldn't copy to the clipboard",
                "Your text is saved in the Journal. Copy it from there.")
    return ("Couldn't paste", f"Your text is on the clipboard. Click a text box and press {keys}.")


def paste_timed_out_message(platform: str):
    """(heading, body) when the paste took too long and Waffler stopped waiting."""
    return ("Not pasted",
            f"Your text is on the clipboard and in the Journal. Press {paste_keys(platform)} to paste it.")


def last_transcript_title(verb: str, words: int) -> str:
    """The menu line for "Paste last" or "Copy last": a word count and never
    the text, because menus show on screen shares."""
    base = f"{verb} last transcript"
    if words <= 0:
        return base
    return f"{base} ({words:,} word{'s' if words != 1 else ''})"


CLIPBOARD_CHOICE_UNKNOWN = "Waffler doesn't know that clipboard choice. Pick one from the list."


# ── Shortcuts (Settings, Shortcuts): Paste last, Copy last, the cancel key ───
SHORTCUT_BAD = "Waffler couldn't use that shortcut. Hold the keys you want, then save."
SHORTCUT_UNKNOWN = "Waffler doesn't know that shortcut."
SHORTCUT_HOLD_KEYS = "Hold the keys you want, then save."
SHORTCUT_ONE_KEY = "Press one key to cancel with."
SHORTCUT_NOT_SAVED = "Couldn't save the shortcut. Please try again."
# What the Paste last and Copy last shortcuts say when they have nothing to do.
LAST_BUSY = ("Waffler is busy", "Finish what Waffler is doing first, then try again.")
LAST_NOTHING_TO_PASTE = ("Nothing to paste yet", "Dictate something first, then Waffler can paste it again.")
LAST_NOTHING_TO_COPY = ("Nothing to copy yet", "Dictate something first, then Waffler can copy it.")
LAST_COPIED = ("Copied", "Your last dictation is on the clipboard.")
LAST_COPY_FAILED = ("Couldn't copy", "Waffler couldn't reach the clipboard. Try again.")


# ── Tips ─────────────────────────────────────────────────────────────────────

def hold_hint_message(hotkey: str):
    """(heading, body) of the tip shown when a quick tap of the hotkey was
    dropped. ``hotkey`` is the person's own, as the Hotkey page shows it."""
    return ("Don't tap, hold", f"Hold {hotkey} while you speak, then let go.")


SOUNDS_UNKNOWN = "Waffler doesn't know that choice for sounds. Use the switch."
TIPS_UNKNOWN = "Waffler doesn't know that choice for tips. Use the switch."


# Settings, Usage: how fast the person types, for the minutes-saved estimate.
TYPING_SPEED_UNKNOWN = "Typing speed needs a whole number of words a minute, from 10 to 200."


# ── Style (Settings, Style): clean-up On or Off, a style per kind of app, the
# email signature. A refused choice saves none of what came with it.
CLEANUP_CHOICE_UNKNOWN = "Waffler doesn't know that clean-up choice. Use the switch."
STYLE_CHOICE_UNKNOWN = "Waffler doesn't know that style for this kind of app. Pick one from the list."
SIGNATURE_BAD = "Waffler couldn't use that signature. Check the switch and the text, then try again."
SIGNATURE_TOO_LONG = "Keep your signature to 500 characters or fewer."


# ── Permissions checked when you press the hotkey ───────────────────────────
# One short toast per missing permission, each with a button for the pane that
# fixes it. They have to fit the pill's three lines.

PERMISSION_MESSAGES = {
    "microphone": ("Microphone is off",
                   "Waffler can't hear you. Turn Waffler on in System Settings > Privacy & "
                   "Security > Microphone, then try again."),
    "microphone_restricted": ("Microphone is restricted",
                              "A Screen Time or device rule is blocking the microphone. Check "
                              "Privacy & Security > Microphone."),
    "accessibility": ("Accessibility is off",
                      "Waffler needs it to paste into other apps. Turn it on in System Settings > "
                      "Privacy & Security > Accessibility, then try again."),
    "keyboard": ("Waffler can't hear your shortcut",
                 "It isn't receiving your keyboard. Turn Waffler on in System Settings > Privacy "
                 "& Security > Input Monitoring, then restart Waffler."),
}


# ── Transforms (Settings, Transforms; src/transforms.py, src/transform_action.py) ──
# What the Settings checks say when a value is refused (a refused value saves
# none of what came with it). The numbers match src/transforms.py; a test keeps
# them the same.
TRANSFORMS_BAD = "Waffler couldn't use those Transform settings. Check them and try again."
TRANSFORM_UNKNOWN = "Waffler doesn't know that transform. Pick one from the list."
TRANSFORM_RULE_UNKNOWN = "Waffler doesn't know one of those Polish rules. Use the checkboxes."
TRANSFORM_RULES_NONE = "Keep at least one Polish rule on, or add a rule of your own."
TRANSFORM_RULES_TOO_MANY = "You can add up to 5 rules of your own."
TRANSFORM_RULE_EMPTY = "Type the rule first."
TRANSFORM_RULE_LONG = "Keep each rule to 160 characters or fewer."
TRANSFORM_RULE_TWICE = "You already have that rule."
TRANSFORM_OUTLINE_LONG = "Keep the outline to 1,200 characters or fewer."
TRANSFORMS_TOO_MANY = "You can keep up to 5 transforms of your own."
TRANSFORM_NAME_EMPTY = "Give the transform a name."
TRANSFORM_NAME_LONG = "Keep the name to 40 characters or fewer."
TRANSFORM_NAME_TAKEN = "You already have a transform with that name."
TRANSFORM_PROMPT_EMPTY = "Say what the transform should do."
TRANSFORM_PROMPT_LONG = "Keep the instruction to 800 characters or fewer."


def undo_keys(platform: str) -> str:
    return "Cmd+Z" if platform == "darwin" else "Ctrl+Z"


# What the pill says while a transform runs. Each is (heading, body); the body
# says what became of the person's text, because that is the worry.
TRANSFORM_NO_SELECTION = ("Select some text first",
                          "Highlight the text you want to rewrite, then try again.")
TRANSFORM_TOO_LONG = ("Too much text",
                      "That's too much text for one go (limit 1,000 words). Select less and try again.")
TRANSFORM_BUSY_DICTATING = ("Waffler is busy", "Finish what Waffler is doing first, then try again.")
TRANSFORM_BUSY_ALREADY = ("Still working", "A transform is already running. Wait for it to finish.")
TRANSFORM_BLOCKS_DICTATION = ("A transform is running",
                              "Wait for it to finish, or cancel it, then dictate.")
TRANSFORM_CANCELLED = ("Transform cancelled", "Your text is unchanged.")
TRANSFORM_NO_CHANGE = ("Your text already looks good", "Nothing was changed.")
TRANSFORM_BAD_LENGTH = ("Nothing was replaced",
                        "The result looked wrong, so your text is untouched.")
TRANSFORM_UNRELATED = ("Nothing was replaced",
                       "The result didn't look like your text, so it's untouched.")
TRANSFORM_CUT_SHORT = ("Nothing was replaced",
                       "The result was cut short, so your text is untouched.")
TRANSFORM_NO_ANSWER = ("Nothing was replaced",
                       "The service sent back no text, so your text is untouched.")
TRANSFORM_TOO_SLOW = ("Took too long", "The service didn't answer in time. Your text is unchanged.")
TRANSFORM_NOT_READY = ("Not ready yet", "Waffler is still starting. Try again in a moment.")


def transform_read_message(reason: str):
    """(heading, body) for a selection that could not be read. ``reason`` is
    src/text_context.py's (``no_selection``, ``accessibility_off``,
    ``secure_field``, ``too_large``, ``not_allowed_here``, anything else)."""
    if reason in ("", "no_selection"):
        return TRANSFORM_NO_SELECTION
    if reason == "too_large":
        return TRANSFORM_TOO_LONG
    if reason == "accessibility_off":
        return ("Accessibility is off",
                "Waffler needs it to read selected text. Turn it on in System Settings > "
                "Privacy & Security > Accessibility, then try again.")
    if reason == "secure_field":
        return ("Can't read this field", "It looks like a password field, so Waffler left it alone.")
    if reason == "clipboard_not_restorable":
        # Windows reads a selection by copying it, and will not do that over a
        # clipboard it cannot put back (a picture, formatted text from a browser).
        return ("Can't copy right now",
                "Your clipboard holds something Waffler can't put back safely, such as a picture or "
                "formatted text. Copy some plain text or clear it, then try again.")
    if reason == "clipboard_busy":
        return ("Clipboard is busy", "Another program is using the clipboard. Try again in a moment.")
    if reason == "not_allowed_here":
        return ("Can't copy from here",
                "In a terminal, copying would stop the program. Select text in another app.")
    return ("Couldn't read the selection",
            "This app didn't hand over the selected text. Try again, or try another app.")


def transform_not_sure_message(platform: str):
    """The rewrite is on the clipboard because Waffler could not check, in time,
    that the person's text was still selected."""
    return ("Result not pasted",
            f"Waffler couldn't check that your text was still selected, so the result is on "
            f"your clipboard. Press {paste_keys(platform)} where you want it.")


def transform_done_message(name: str, platform: str):
    return (f"{name} done", f"Press {undo_keys(platform)} in your app to undo it.")


def transform_not_pasted_message(platform: str):
    """The result was made but not pasted because the person moved on; it is
    on the clipboard and in the Journal."""
    return ("Result not pasted",
            f"You switched away, so the result is on your clipboard. Press {paste_keys(platform)} "
            f"where you want it.")


def transform_failed_message(reason: str):
    """(heading, body) when no provider answered. ``reason`` is the styler's
    own (``RATE_LIMIT|...``, ``AUTH: ...``, ``CONNECTION: ...``,
    ``MODEL_UNAVAILABLE: ...``, ``TIMEOUT|...``); it stays in the log and
    nothing of it but a provider's name reaches the screen."""
    text = str(reason or "")
    lower = text.lower()
    if "RATE_LIMIT|" in text:
        return limit_reached_message(text)
    provider = _provider_in(text)
    who = provider or "Your service"
    if "MODEL_UNAVAILABLE" in text:
        return ("Couldn't rewrite", "The service's model is no longer available. Your text is unchanged.")
    if "no styling providers configured" in lower:
        return ("Couldn't rewrite", "No key is set up for this yet. Add a free Groq key in Settings. "
                                    "Your text is unchanged.")
    if "TIMEOUT|" in text:
        return TRANSFORM_TOO_SLOW
    blocked = ("AUTH:" in text or "access denied" in lower or "unauthorized" in lower
               or "invalid_api_key" in lower or "incorrect api key" in lower)
    if not blocked and "CONNECTION" not in text:
        blocked = bool(re.search(r"\b40[13]\b", text))
    if blocked:
        return ("Couldn't rewrite", f"{who} refused the connection, which usually means a VPN is on "
                                    f"or the key has stopped working. Your text is unchanged.")
    if "CONNECTION" in text or "timeout" in lower or "timed out" in lower or "connection" in lower:
        return ("Couldn't rewrite", f"Waffler couldn't reach {provider or 'your service'}. "
                                    f"Check you're online. Your text is unchanged.")
    return ("Couldn't rewrite", "Something went wrong with the service. Your text is unchanged.")
TRANSFORM_COPIED = ("Result copied", "Paste it over your selection when you're ready.")
TRANSFORM_MOVED_NOT_COPIED = ("Result not pasted",
                              "You switched away, and the result couldn't be copied. It's in the Journal.")


# ── Command mode (Settings, Command mode; src/command_mode.py, src/command_action.py) ──
# What the Settings checks say when a value is refused (a refused value saves
# none of what came with it).
COMMAND_BAD = "Waffler couldn't use those Command mode settings. Check them and try again."
COMMAND_ENGINE_UNKNOWN = "Waffler doesn't know that search engine. Pick one from the list."

# What the pill says while a command runs. Each is (heading, body), and the body
# says what became of the person's text, because that is the worry.
COMMAND_BUSY_ALREADY = ("Still working", "A command is already running. Wait for it to finish.")
COMMAND_BUSY_ELSEWHERE = ("Waffler is busy", "Finish what Waffler is doing first, then try again.")
COMMAND_BLOCKS_DICTATION = ("A command is running",
                            "Wait for it to finish, or cancel it, then dictate.")
COMMAND_CANCELLED = ("Command cancelled", "Your text is unchanged.")
COMMAND_TOO_LONG = ("That's a long instruction", "Keep it short, a sentence or two, and try again.")
COMMAND_NO_CHANGE = ("Nothing to change", "Your text already matches what you asked for.")
COMMAND_BAD_LENGTH = ("Nothing was replaced", "The result looked wrong, so your text is untouched.")
COMMAND_CUT_SHORT = ("Nothing was replaced", "The result was cut short, so your text is untouched.")
COMMAND_NO_ANSWER = ("Nothing was replaced", "The service sent back no text, so your text is untouched.")
COMMAND_REFUSED = ("Nothing was replaced", "The service wouldn't do that, so your text is untouched.")
COMMAND_DRAFT_COPIED = ("Draft copied",
                        "Your selected text was left as it is. Paste the draft where you want it.")
COMMAND_COPIED = ("Result copied", "Paste it where you want it.")
COMMAND_OPEN_FAILED = ("Couldn't open your browser", "Try again, or search for it yourself.")
COMMAND_TOO_SLOW = ("Took too long", "The service didn't answer in time. Your text is unchanged.")


def command_failed_message(reason: str):
    """(heading, body) when no provider answered the command. ``reason`` is the
    styler's own, as for a transform; only the heading differs."""
    heading, body = transform_failed_message(reason)
    if heading == "Couldn't rewrite":
        heading = "Couldn't run that"
    return heading, body


def command_speech_failed_message(kind: str, provider: str = ""):
    """(heading, body) when the spoken instruction could not be turned into
    text. ``kind`` is src/unsent.py's sort of the failure (offline, blocked,
    timeout, rate_limited, anything else); the raw error stays in the log."""
    who = provider or "your speech service"
    Who = who[:1].upper() + who[1:]
    if kind == "blocked":
        return ("Couldn't hear the command",
                f"{Who} refused the connection, which usually means a VPN is on. Your text is unchanged.")
    if kind == "rate_limited":
        return ("Couldn't hear the command",
                f"{Who} says you've reached your limit for now. Your text is unchanged.")
    if kind == "timeout":
        return ("Couldn't hear the command", f"{Who} took too long to answer. Your text is unchanged.")
    if kind == "offline":
        return ("Couldn't hear the command",
                f"Waffler couldn't reach {who}. Check you're online. Your text is unchanged.")
    return ("Couldn't hear the command", "Something went wrong turning your voice into text. "
                                         "Your text is unchanged.")


def command_done_message(action: str, platform: str):
    """The toast after an edit or a draft was pasted."""
    if action == "edit":
        return ("Text edited", f"Press {undo_keys(platform)} in your app to undo it.")
    return ("Draft added", f"Press {undo_keys(platform)} in your app to remove it.")


def command_searching_message(engine_label: str):
    return (f"Searching {engine_label}", "Your question is open in your browser.")


# ── Vocabulary and Snippets: import, export, edits ───────────────────────────

LEARN_FROM_EDITS_UNKNOWN = "Waffler doesn't know that choice for learning from edits. Use the switch."
VOCAB_EXPORT_EMPTY = "Your Vocabulary is empty, so there is nothing to export."
SNIPPETS_EXPORT_EMPTY = "You have no snippets yet, so there is nothing to export."
EXPORT_FORMAT_UNKNOWN = "Pick CSV or JSON."
EXPORT_FAILED = "Couldn't save the file. Try again."
IMPORT_FAILED = "Couldn't save that. Nothing was changed."
IMPORT_NOTHING = "Waffler found nothing to add in that."
IMPORT_STALE = "Your list changed since that preview. Look at it again."
EDIT_EMPTY = "Type the text first. To remove a dictation, delete it instead."
EDIT_TOO_LONG = "Keep it to 20,000 characters or fewer."
EDIT_NOT_FOUND = "That dictation isn't in your Journal any more."
EDIT_NOT_EDITABLE = "Only a dictation's own words can be edited."
EDIT_FAILED = "Couldn't save your change. Try again."

# Why a row of an imported file was left out: said on the preview, with the row.
_SKIP_WORD = {
    "empty": "No word on this row.",
    "too_long": "Longer than 60 characters.",
    "odd": "It has a character Waffler can't use.",
    "no_letters": "It has no letters in it.",
    "everyday": "An everyday word. Waffler never changes those, so it does nothing without a \"sounds like\".",
    "full": "Your list is full (500 words).",
}


def vocab_skip_message(reason: str, other: str = "") -> str:
    if reason == "is_spelling":
        return f"It is already how {other} sounds." if other else "It is already how another word sounds."
    return _SKIP_WORD.get(reason, "Waffler couldn't use this row.")


def spelling_note(reason: str, spelling: str, other: str = "") -> str:
    """Why one "sounds like" spelling of an imported word was not used."""
    s = f"\"{spelling}\""
    return {
        "spelling_long": f"Left out {s}: longer than 60 characters.",
        "spelling_none": f"Left out {s}: it has no letters in it.",
        "spelling_odd": f"Left out {s}: it has a character Waffler can't use.",
        "spelling_same": f"Left out {s}: it is how the word is written already.",
        "spelling_word": f"Left out {s}: it is a word of its own in your list.",
        "spelling_taken": f"Left out {s}: it already sounds like {other}." if other
        else f"Left out {s}: it already sounds like another word.",
        "spelling_max": f"Left out {s}: a word can have up to 8.",
    }.get(reason, f"Left out {s}.")


_SKIP_SNIPPET = {
    "empty": "No phrase on this row.",
    "no_text": "No text to paste on this row.",
    "trigger_long": "The phrase is longer than 60 characters.",
    "text_long": "The text is longer than 4,000 characters.",
    "full": "You can keep up to 200 snippets.",
}


def snippet_skip_message(reason: str, other: str = "") -> str:
    if reason == "exists":
        return f"\"{other}\" is already a snippet, so yours was kept." if other \
            else "That phrase is already a snippet, so yours was kept."
    if reason == "repeat":
        return "The same phrase is on an earlier row, so the first one was kept."
    return _SKIP_SNIPPET.get(reason, "Waffler couldn't use this row.")

