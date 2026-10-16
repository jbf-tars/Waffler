"""What kind of app is this? A guess from the app's identity, nothing more.

``category_for(bundle_id, name, title)`` answers one of four words:

    email     a mail app (Apple Mail, Outlook, Spark, Thunderbird...)
    work      work chat and collaboration (Slack, Teams, Zoom, Notion, Linear...)
    personal  personal messaging (Messages, WhatsApp, Telegram, Signal, Discord...)
    other     everything else, and anything unknown

It is pure: no framework, no window, no clipboard, no file. The caller reads
the frontmost app (src/text_context.py) and asks here what it is.

If an app is in more than one list the first one wins: email, then work, then
personal. Discord is a personal app here on purpose: whether a server is for
work cannot be known from the app, and the quieter guess is the safe one.

Browsers are the one place the window title is used, as a weak hint: a tab
whose title ends in the name of a well-known webmail or chat site counts as
that site. The address is never read (reading it needs a permission Waffler
does not ask for), and for any app that is not a browser the title is ignored.

Extending the lists
-------------------
Everything that decides a category is data in the tables below, written by hand
from public app identifiers. To teach Waffler an app, add its bundle
identifier (macOS: ``mdls -name kMDItemCFBundleIdentifier /Applications/X.app``)
to the right ``*_BUNDLE_IDS`` tuple, and its process or app name (what Windows
reports, lowercase, without ``.exe``) to the right ``*_NAMES`` tuple. Use a
``*_BUNDLE_PREFIXES`` entry only for a whole family of identifiers (Chrome's
installed web apps all start with Chrome's own). Everything is compared in
lower case. The tests walk these tables, so a new entry is covered by the
existing checks and a typo shows up as a failing test.
"""

from __future__ import annotations

import re

EMAIL = "email"
WORK = "work"
PERSONAL = "personal"
OTHER = "other"
CATEGORIES = (PERSONAL, WORK, EMAIL, OTHER)

# ── The tables ───────────────────────────────────────────────────────────────
# Bundle identifiers are matched whole, names are matched whole, prefixes match
# the start of a bundle identifier. All lower case.

EMAIL_BUNDLE_IDS = (
    "com.apple.mail",
    "com.microsoft.outlook",
    "com.mimestream.mimestream",
    "com.freron.mailmate",
    "com.superhuman.electron",
    "org.mozilla.thunderbird",
    "it.bloop.airmail2",
    "io.canarymail.mac",
    "com.postbox-inc.postbox",
    "com.readdle.smartemail-mac",
    "com.readdle.sparkdesktop",
)
EMAIL_BUNDLE_PREFIXES = (
    "com.readdle.smartemail",   # Spark, whichever build
    "com.readdle.sparkdesktop",
)
EMAIL_NAMES = (
    "mail", "outlook", "microsoft outlook", "olk", "hxoutlook", "thunderbird",
    "spark", "spark mail", "superhuman", "mimestream", "mailmate", "airmail",
    "canary mail", "postbox", "mailspring", "em client", "emclient", "hey",
    "front", "proton mail", "protonmail",
)

WORK_BUNDLE_IDS = (
    "com.tinyspeck.slackmacgap",
    "com.microsoft.teams",
    "com.microsoft.teams2",
    "us.zoom.xos",
    "notion.id",
    "com.linear",
    "cisco-systems.spark",          # Webex
    "com.cisco.webexmeetingsapp",
    "mattermost.desktop",
    "org.zulip.zulip-electron",
    "com.loom.desktop",
    "com.clickup.desktop-app",
    "com.basecamp.bc3",
)
WORK_BUNDLE_PREFIXES = (
    "com.tinyspeck.slackmacgap",    # Slack, including its helper builds
    "com.microsoft.teams",
)
WORK_NAMES = (
    "slack", "teams", "ms-teams", "msteams", "microsoft teams", "zoom", "zoom workplace",
    "notion", "linear", "asana", "webex", "cisco webex meetings", "mattermost", "zulip",
    "loom", "trello", "clickup", "basecamp", "google chat", "jira", "confluence",
)

PERSONAL_BUNDLE_IDS = (
    "com.apple.mobilesms",          # Messages
    "net.whatsapp.whatsapp",
    "ru.keepcoder.telegram",
    "org.telegram.desktop",
    "com.tdesktop.telegram",
    "org.whispersystems.signal-desktop",
    "com.hnc.discord",
    "com.tencent.xinwechat",
    "jp.naver.line",
    "com.facebook.archon",          # Messenger
    "com.facebook.archon.developerid",
    "com.viber.osx",
    "com.automattic.beeper.desktop",
)
PERSONAL_BUNDLE_PREFIXES = (
    "com.hnc.discord",
)
PERSONAL_NAMES = (
    "messages", "imessage", "whatsapp", "telegram", "telegram desktop", "signal",
    "discord", "wechat", "weixin", "line", "messenger", "viber", "beeper",
    "instagram",
)

# Browsers: the one kind of app whose window title says something about what
# is open in it. Windows reports their process names.
BROWSER_BUNDLE_IDS = (
    "com.apple.safari",
    "com.apple.safaritechnologypreview",
    "com.google.chrome",
    "com.google.chrome.canary",
    "org.mozilla.firefox",
    "org.mozilla.nightly",
    "com.microsoft.edgemac",
    "company.thebrowser.browser",   # Arc
    "company.thebrowser.dia",
    "com.brave.browser",
    "com.operasoftware.opera",
    "com.vivaldi.vivaldi",
    "org.chromium.chromium",
    "ai.perplexity.comet",
    "app.zen-browser.zen",
    "com.kagi.kagimacos",           # Orion
    "com.duckduckgo.macos.browser",
)
BROWSER_BUNDLE_PREFIXES = (
    "com.google.chrome.",           # web apps installed from Chrome
    "com.microsoft.edgemac.",
    "com.brave.browser.",
)
BROWSER_NAMES = (
    "safari", "google chrome", "chrome", "firefox", "microsoft edge", "msedge", "edge",
    "arc", "brave", "brave browser", "opera", "vivaldi", "chromium", "comet", "zen",
    "orion", "duckduckgo", "dia",
)

# Sites a browser tab can be on, as they name themselves in the tab's title
# ("Inbox (3) - me@example.com - Gmail", "general | Acme | Slack"). Compared
# against each part of the title as a whole, so a page that merely mentions
# "Linear" ("Linear algebra notes") does not count.
EMAIL_SITES = (
    "gmail", "google mail", "outlook", "outlook.com", "outlook.office.com",
    "yahoo mail", "proton mail", "protonmail", "fastmail", "zoho mail", "hey",
    "superhuman", "icloud mail", "shortwave",
)
WORK_SITES = (
    "slack", "microsoft teams", "google chat", "zoom", "notion", "linear", "jira",
    "jira software", "asana", "confluence", "trello", "clickup", "monday.com", "basecamp",
    "mattermost",
)
PERSONAL_SITES = (
    "whatsapp", "whatsapp web", "messenger", "facebook messenger", "telegram",
    "telegram web", "signal", "discord", "instagram", "messages for web",
    "snapchat", "wechat",
)

# ── Reading the tables ───────────────────────────────────────────────────────

_TITLE_PARTS = re.compile(r"\s+[-–—|·•]\s+|\s*\|\s*")
_UNREAD_COUNT = re.compile(r"^\(\d+\)\s*|\s*\(\d+\)$")

_ORDER = (
    (EMAIL, EMAIL_BUNDLE_IDS, EMAIL_BUNDLE_PREFIXES, EMAIL_NAMES, EMAIL_SITES),
    (WORK, WORK_BUNDLE_IDS, WORK_BUNDLE_PREFIXES, WORK_NAMES, WORK_SITES),
    (PERSONAL, PERSONAL_BUNDLE_IDS, PERSONAL_BUNDLE_PREFIXES, PERSONAL_NAMES, PERSONAL_SITES),
)


def _clean(value) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).lower()


def _app_name(name) -> str:
    """The name as the tables spell it: lower case, no ``.exe``."""
    n = _clean(name)
    return n[:-4] if n.endswith(".exe") else n


def is_browser(bundle_id, name="") -> bool:
    """True for a web browser (or a web app installed from one)."""
    b, n = _clean(bundle_id), _app_name(name)
    if b:
        if b in BROWSER_BUNDLE_IDS or any(b.startswith(p) for p in BROWSER_BUNDLE_PREFIXES):
            return True
    return bool(n) and n in BROWSER_NAMES


def _title_parts(title) -> list:
    """The parts of a tab title that could be a site's name, lower case, with
    an unread count such as ``(3)`` taken off."""
    t = _clean(title)
    if not t:
        return []
    parts = []
    for part in _TITLE_PARTS.split(t):
        part = _UNREAD_COUNT.sub("", part).strip()
        if part:
            parts.append(part)
    return parts


def _category_of_app(bundle: str, name: str):
    for category, ids, prefixes, names, _sites in _ORDER:
        if bundle and (bundle in ids or any(bundle.startswith(p) for p in prefixes)):
            return category
        if name and name in names:
            return category
    return None


def _category_of_title(title):
    parts = _title_parts(title)
    if not parts:
        return None
    for category, _ids, _prefixes, _names, sites in _ORDER:
        if any(part in sites for part in parts):
            return category
    return None


def category_for(bundle_id, name, title=None) -> str:
    """``email``, ``work``, ``personal`` or ``other`` for an app.

    ``bundle_id`` and ``name`` are what the system reports (either may be
    empty; Windows has no bundle identifier). ``title`` is the front window's
    title and only matters for a browser. Anything unknown, empty or not a
    string is ``other``.
    """
    bundle, app = _clean(bundle_id), _app_name(name)
    found = _category_of_app(bundle, app)
    if found:
        return found
    if is_browser(bundle, app):
        return _category_of_title(title) or OTHER
    return OTHER
