"""The Mac's keyboard-language popup: changed only when asked, and undone.

Waffler used to switch off macOS's input-source shortcuts (60 and 61) on every
launch, silently. src/input_source_popup.py changes them only when the switch
in Settings is turned on, remembers what was there and puts it back. A fake
`defaults` stands in for the real one: no real setting is read or written.
"""
import plistlib
from pathlib import Path

import pytest

import input_source_popup as isp

ROOT = Path(__file__).resolve().parent.parent

CTRL_SPACE = {"enabled": True, "value": {"parameters": [32, 49, 262144], "type": "standard"}}
CUSTOM_61 = {"enabled": True, "value": {"parameters": [32, 49, 1572864], "type": "standard"}}
TWO_SOURCES = [
    {"InputSourceKind": "Keyboard Layout", "KeyboardLayout ID": 0, "KeyboardLayout Name": "U.S."},
    {"InputSourceKind": "Keyboard Layout", "KeyboardLayout ID": 3, "KeyboardLayout Name": "German"},
    {"Bundle ID": "com.apple.CharacterPaletteIM", "InputSourceKind": "Non Keyboard Input Method"},
]


class FakeDefaults:
    def __init__(self, hotkeys=None, sources=None):
        self.domains = {
            isp.DOMAIN: {isp.KEY: dict(hotkeys or {})},
            "com.apple.HIToolbox": {"AppleEnabledInputSources": list(sources or [])},
        }
        self.writes = []
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:2] == ["defaults", "export"]:
            data = self.domains.get(argv[2])
            if data is None:
                return 1, ""
            return 0, plistlib.dumps(data, fmt=plistlib.FMT_XML).decode()
        if argv[:2] == ["defaults", "write"] and argv[4] == "-dict-add":
            domain, key, hk, fragment = argv[2], argv[3], argv[5], argv[6]
            entry = plistlib.loads(
                ('<?xml version="1.0" encoding="UTF-8"?><plist version="1.0">'
                 + fragment + "</plist>").encode())
            self.domains.setdefault(domain, {}).setdefault(key, {})[hk] = entry
            self.writes.append((hk, entry))
            return 0, ""
        return 1, ""

    def entry(self, hk):
        return self.domains[isp.DOMAIN][isp.KEY].get(hk)


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data"


def test_nothing_is_written_by_looking(data):
    fake = FakeDefaults({"60": CTRL_SPACE}, TWO_SOURCES)
    v = isp.view(data, ["fn"], fake)
    assert v == {"on": False, "inherited": False, "would_help": True, "show": True}
    assert fake.writes == []
    assert not (data / isp.RECORD_NAME).exists()


@pytest.mark.parametrize("keys,hotkeys,sources,helps", [
    (["fn"], {}, TWO_SOURCES, True),                       # no entry = macOS default, on
    (None, {}, TWO_SOURCES, True),                         # no key chosen = Fn
    (["fn"], {}, TWO_SOURCES[:1], False),                  # one keyboard language
    (["fn"], {"60": dict(CTRL_SPACE, enabled=False), "61": dict(CUSTOM_61, enabled=False)},
     TWO_SOURCES, False),                                  # both already off
    (["control", "option"], {}, TWO_SOURCES, False),       # Fn is not the key
    (["fn", "shift"], {}, TWO_SOURCES, False),
])
def test_would_help_only_when_it_would(data, keys, hotkeys, sources, helps):
    v = isp.view(data, keys, FakeDefaults(hotkeys, sources))
    assert v["would_help"] is helps and v["show"] is helps


def test_turn_on_remembers_then_switches_off_keeping_the_keys(data):
    fake = FakeDefaults({"60": CTRL_SPACE, "61": CUSTOM_61}, TWO_SOURCES)
    assert isp.turn_on(data, fake, apply_now=False) == {"ok": True, "on": True}
    assert fake.entry("60") == dict(CTRL_SPACE, enabled=False)
    assert fake.entry("61") == dict(CUSTOM_61, enabled=False)
    rec = isp.load_record(data)
    assert rec["on"] is True and rec["previous"] == {"60": CTRL_SPACE, "61": CUSTOM_61}
    assert isp.view(data, ["fn"], fake)["on"] is True


def test_turn_on_twice_writes_once_and_keeps_the_first_memory(data):
    fake = FakeDefaults({"60": CTRL_SPACE, "61": CUSTOM_61}, TWO_SOURCES)
    isp.turn_on(data, fake, apply_now=False)
    n = len(fake.writes)
    isp.turn_on(data, fake, apply_now=False)
    assert len(fake.writes) == n
    assert isp.load_record(data)["previous"]["61"] == CUSTOM_61


def test_turn_off_puts_back_exactly_what_was_there(data):
    fake = FakeDefaults({"60": CTRL_SPACE, "61": CUSTOM_61}, TWO_SOURCES)
    isp.turn_on(data, fake, apply_now=False)
    assert isp.turn_off(data, fake, apply_now=False) == {"ok": True, "on": False}
    assert fake.entry("60") == CTRL_SPACE and fake.entry("61") == CUSTOM_61
    assert isp.is_on(data) is False


def test_turn_off_when_never_on_writes_nothing(data):
    fake = FakeDefaults({"60": CTRL_SPACE}, TWO_SOURCES)
    assert isp.turn_off(data, fake, apply_now=False)["ok"] is True
    assert fake.writes == []


def test_missing_entries_come_back_as_macos_defaults(data):
    fake = FakeDefaults({}, TWO_SOURCES)
    isp.turn_on(data, fake, apply_now=False)
    assert fake.entry("60")["enabled"] is False
    isp.turn_off(data, fake, apply_now=False)
    assert fake.entry("60") == isp.MAC_DEFAULTS["60"]
    assert fake.entry("61") == isp.MAC_DEFAULTS["61"]


def test_a_failed_write_keeps_the_switch_on_so_it_can_be_retried(data):
    fake = FakeDefaults({"60": CTRL_SPACE, "61": CUSTOM_61}, TWO_SOURCES)
    isp.turn_on(data, fake, apply_now=False)
    broken = lambda argv: (1, "") if argv[1] == "write" else fake(argv)  # noqa: E731
    assert isp.turn_off(data, broken, apply_now=False)["ok"] is False
    assert isp.is_on(data) is True


# -- People upgrading from a Waffler that changed it silently ---------------

LEGACY = {"enabled": False}    # what the old code wrote, value and all replaced


def test_legacy_change_is_adopted_once_and_read_only(data):
    fake = FakeDefaults({"60": LEGACY, "61": LEGACY}, TWO_SOURCES)
    assert isp.adopt_legacy_change(data, fake) is True
    assert fake.writes == []
    v = isp.view(data, ["fn"], fake)
    assert v["on"] is True and v["inherited"] is True and v["show"] is True
    # Checked once: a second launch does nothing.
    assert isp.adopt_legacy_change(data, fake) is False


def test_undoing_a_legacy_change_restores_macos_shortcuts(data):
    fake = FakeDefaults({"60": LEGACY, "61": LEGACY}, TWO_SOURCES)
    isp.adopt_legacy_change(data, fake)
    isp.turn_off(data, fake, apply_now=False)
    assert fake.entry("60") == isp.MAC_DEFAULTS["60"]
    assert fake.entry("61") == isp.MAC_DEFAULTS["61"]
    assert isp.view(data, ["fn"], fake)["on"] is False


def test_a_shortcut_the_person_switched_off_is_not_taken_for_waffler(data):
    # macOS's own switch off keeps the key combination: not the old fingerprint.
    fake = FakeDefaults({"60": dict(CTRL_SPACE, enabled=False)}, TWO_SOURCES)
    assert isp.adopt_legacy_change(data, fake) is False
    assert isp.is_on(data) is False


def test_restore_and_forget_for_delete_all_my_data(data):
    fake = FakeDefaults({"60": CTRL_SPACE, "61": CUSTOM_61}, TWO_SOURCES)
    isp.turn_on(data, fake, apply_now=False)
    calls = []
    runner = lambda argv: (calls.append(argv), fake(argv))[1]  # noqa: E731
    isp.restore_and_forget(data, runner)
    assert fake.entry("61") == CUSTOM_61
    assert not (data / isp.RECORD_NAME).exists()


def test_fragment_round_trips_through_plist():
    frag = isp._plist_fragment(CUSTOM_61)
    assert frag.startswith("<dict>") and frag.endswith("</dict>") and "\n" not in frag


# -- app.py --------------------------------------------------------------------

def test_app_no_longer_changes_the_shortcuts_at_launch():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "_disable_input_source_shortcut" not in src
    assert "symbolichotkeys" not in src
    main = src[src.index("\ndef main():"):]
    assert "_adopt_legacy_input_source_change()" in main


def test_settings_offers_the_switch():
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'id="inputSourcePopupRow"' in html
    # Four words on the row (tests/test_few_words.py); the whole sentence is
    # the switch's accessible name.
    assert ">Stop the keyboard-language popup</label>" in html
    assert 'aria-label="Stop the keyboard-language popup when I hold Fn"' in html
    js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    assert "loadInputSourcePopup();" in js and "set_input_source_popup" in js
