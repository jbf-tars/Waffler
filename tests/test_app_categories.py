"""Which kind of app is in front: email, work, personal or other (S5).

``category_for`` is pure, so these walk the tables in src/app_categories.py
rather than restating them: every identifier and name listed there must give
its list's category, the lists must not contradict each other, and the few
rules around them (precedence, browsers, titles, junk input) are pinned
one by one.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import app_categories as ac  # noqa: E402
from app_categories import EMAIL, OTHER, PERSONAL, WORK, category_for  # noqa: E402

TABLES = {
    EMAIL: (ac.EMAIL_BUNDLE_IDS, ac.EMAIL_BUNDLE_PREFIXES, ac.EMAIL_NAMES, ac.EMAIL_SITES),
    WORK: (ac.WORK_BUNDLE_IDS, ac.WORK_BUNDLE_PREFIXES, ac.WORK_NAMES, ac.WORK_SITES),
    PERSONAL: (ac.PERSONAL_BUNDLE_IDS, ac.PERSONAL_BUNDLE_PREFIXES, ac.PERSONAL_NAMES, ac.PERSONAL_SITES),
}


def _everything():
    for cat, (ids, prefixes, names, sites) in TABLES.items():
        yield cat, "id", ids
        yield cat, "prefix", prefixes
        yield cat, "name", names
        yield cat, "site", sites


# ── the answer is one of four words ──────────────────────────────────────────

def test_the_four_categories():
    assert set(ac.CATEGORIES) == {"personal", "work", "email", "other"}
    assert (EMAIL, WORK, PERSONAL, OTHER) == ("email", "work", "personal", "other")


# ── every listed identifier gives its list's category ────────────────────────

@pytest.mark.parametrize("cat", [EMAIL, WORK, PERSONAL])
def test_every_listed_bundle_id_gives_its_category(cat):
    ids = TABLES[cat][0]
    assert ids
    for bundle in ids:
        assert category_for(bundle, "") == cat, bundle
        assert category_for(bundle, "Something Else") == cat, bundle   # the id wins over an odd name


@pytest.mark.parametrize("cat", [EMAIL, WORK, PERSONAL])
def test_every_listed_prefix_covers_a_family_of_identifiers(cat):
    for prefix in TABLES[cat][1]:
        assert category_for(prefix, "") == cat, prefix
        assert category_for(prefix + ".helper", "") == cat, prefix


@pytest.mark.parametrize("cat", [EMAIL, WORK, PERSONAL])
def test_every_listed_name_gives_its_category_with_no_bundle_id(cat):
    names = TABLES[cat][2]
    assert names
    for name in names:
        assert category_for("", name) == cat, name
        assert category_for("", name.upper()) == cat, name              # case never matters
        assert category_for("", name + ".exe") == cat, name             # a Windows process name


@pytest.mark.parametrize("cat", [EMAIL, WORK, PERSONAL])
def test_every_listed_site_counts_in_a_browser_title(cat):
    for site in TABLES[cat][3]:
        assert category_for("com.google.Chrome", "Google Chrome", f"Some page - {site}") == cat, site
        assert category_for("com.apple.Safari", "Safari", site.title()) == cat, site


def test_the_apps_the_owner_named():
    known = {
        "com.apple.mail": EMAIL, "com.microsoft.Outlook": EMAIL, "com.readdle.smartemail-Mac": EMAIL,
        "com.superhuman.electron": EMAIL, "org.mozilla.thunderbird": EMAIL,
        "com.tinyspeck.slackmacgap": WORK, "com.microsoft.teams2": WORK, "us.zoom.xos": WORK,
        "notion.id": WORK, "com.linear": WORK,
        "com.apple.MobileSMS": PERSONAL, "net.whatsapp.WhatsApp": PERSONAL,
        "ru.keepcoder.Telegram": PERSONAL, "org.whispersystems.signal-desktop": PERSONAL,
        "com.hnc.Discord": PERSONAL,
        "com.apple.Notes": OTHER, "com.apple.Terminal": OTHER, "com.apple.dt.Xcode": OTHER,
        "com.microsoft.Word": OTHER, "com.spotify.client": OTHER,
    }
    for bundle, want in known.items():
        assert category_for(bundle, "") == want, bundle


def test_windows_gives_a_name_and_no_bundle_id():
    for name, want in (("OUTLOOK.EXE", EMAIL), ("olk", EMAIL), ("Slack", WORK), ("ms-teams", WORK),
                       ("Zoom", WORK), ("WhatsApp", PERSONAL), ("Telegram", PERSONAL),
                       ("Discord", PERSONAL), ("notepad", OTHER), ("Code", OTHER)):
        assert category_for("", name) == want, name


# ── precedence: email, then work, then personal, then other ──────────────────

def test_an_app_in_two_lists_takes_the_first_in_the_order(monkeypatch):
    monkeypatch.setattr(ac, "_ORDER", (
        (EMAIL, ("com.example.both",), (), (), ()),
        (WORK, ("com.example.both", "com.example.wp"), (), (), ()),
        (PERSONAL, ("com.example.both", "com.example.wp"), (), (), ()),
    ))
    assert category_for("com.example.both", "") == EMAIL
    assert category_for("com.example.wp", "") == WORK


def test_the_order_in_the_module_is_email_work_personal():
    assert [row[0] for row in ac._ORDER] == [EMAIL, WORK, PERSONAL]


def test_no_exact_identifier_or_name_is_in_two_lists():
    seen = {}
    for cat, kind, values in _everything():
        if kind == "prefix":
            continue
        for v in values:
            key = (kind, v)
            assert key not in seen, f"{v!r} is in both {seen[key]} and {cat}"
            seen[key] = cat


def test_the_title_beats_nothing_but_a_known_app_beats_the_title():
    # Slack says what it is; a title cannot turn it into something else.
    assert category_for("com.tinyspeck.slackmacgap", "Slack", "Inbox - Gmail") == WORK
    # A text editor with a webmail-looking file name stays other.
    assert category_for("com.apple.TextEdit", "TextEdit", "Gmail") == OTHER


# ── browsers: the title is a weak signal, the address is never read ──────────

@pytest.mark.parametrize("title,want", [
    ("Inbox (3) - me@example.com - Gmail", EMAIL),
    ("Mail - Sam Jones - Outlook", EMAIL),
    ("general | Acme | Slack", WORK),
    ("Chat | Microsoft Teams", WORK),
    ("ENG-12 Fix the login – Linear", WORK),
    ("(2) WhatsApp", PERSONAL),
    ("#random | Some server - Discord", PERSONAL),
    ("Instagram", PERSONAL),
    ("Messenger", PERSONAL),
])
def test_a_browser_tab_on_a_well_known_site(title, want):
    assert category_for("com.google.Chrome", "Google Chrome", title) == want


@pytest.mark.parametrize("title", [
    None, "", "   ", "New Tab", "Linear algebra notes", "Slack integration guide for admins",
    "How to write an outlook for next year", "Gmailer pro", "mail.google.com",
    "https://mail.google.com/mail/u/0/#inbox",           # an address is not a title, and is never matched
])
def test_a_browser_tab_that_only_mentions_a_site_is_other(title):
    assert category_for("com.google.Chrome", "Google Chrome", title) == OTHER


def test_a_title_with_both_a_mail_site_and_a_chat_site_is_mail():
    assert category_for("com.apple.Safari", "Safari", "Slack digest - Gmail") == EMAIL


def test_every_browser_in_the_table_reads_titles():
    assert ac.BROWSER_BUNDLE_IDS and ac.BROWSER_NAMES
    for bundle in ac.BROWSER_BUNDLE_IDS:
        assert ac.is_browser(bundle, ""), bundle
        assert category_for(bundle, "", "Inbox - Gmail") == EMAIL, bundle
    for name in ac.BROWSER_NAMES:
        assert ac.is_browser("", name), name
        assert category_for("", name, "general | Acme | Slack") == WORK, name


def test_a_web_app_installed_from_chrome_reads_titles_too():
    assert category_for("com.google.Chrome.app.abcdefghij", "Gmail", "Inbox - Gmail") == EMAIL
    assert ac.is_browser("com.google.Chrome.app.abcdefghij", "")


def test_a_non_browser_ignores_its_title():
    for bundle in ("com.apple.Notes", "com.microsoft.Word", "com.apple.Terminal"):
        assert category_for(bundle, "", "Inbox - Gmail") == OTHER, bundle


def test_the_title_is_optional_everywhere():
    assert category_for("com.apple.mail", "Mail") == EMAIL
    assert category_for("com.google.Chrome", "Chrome") == OTHER


# ── unknown and unusable input is just "other" ───────────────────────────────

@pytest.mark.parametrize("bundle,name,title", [
    ("", "", None), (None, None, None), ("com.example.unknown", "Unknown App", "x"),
    (5, 6, 7), (b"com.apple.mail", b"Mail", b"x"), ([], {}, ()), ("   ", "   ", "   "),
])
def test_junk_is_other_and_never_raises(bundle, name, title):
    assert category_for(bundle, name, title) == OTHER


def test_a_name_must_match_whole_not_part():
    assert category_for("", "Mailchimp") == OTHER
    assert category_for("", "Slack Helper") == OTHER
    assert category_for("", "Messages Archive") == OTHER
    assert category_for("", "Teams Manager") == OTHER


# ── the tables themselves ────────────────────────────────────────────────────

def test_every_table_entry_is_lower_case_and_trimmed():
    for cat, kind, values in _everything():
        for v in values:
            assert v == v.strip().lower() and v, f"{cat} {kind}: {v!r}"
    for group in (ac.BROWSER_BUNDLE_IDS, ac.BROWSER_BUNDLE_PREFIXES, ac.BROWSER_NAMES):
        for v in group:
            assert v == v.strip().lower() and v, v


def test_no_entry_is_listed_twice_in_its_own_table():
    for cat, kind, values in _everything():
        assert len(values) == len(set(values)), f"{cat} {kind}"


def test_a_prefix_is_not_so_short_it_swallows_other_apps():
    for cat, kind, values in _everything():
        if kind == "prefix":
            for v in values:
                assert v.count(".") >= 2, f"{v!r} is too broad"
    for v in ac.BROWSER_BUNDLE_PREFIXES:
        assert v.endswith(".") and v.count(".") >= 3, v


def test_site_names_are_not_so_generic_they_match_ordinary_pages():
    generic = {"mail", "chat", "inbox", "home", "new tab", "docs", "app", "web", "page"}
    for cat, (_i, _p, _n, sites) in TABLES.items():
        assert not (generic & set(sites)), (cat, generic & set(sites))
