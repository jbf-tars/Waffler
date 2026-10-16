"""Reading a selection by copying it, with the clipboard left as it was (S5).

Nothing here touches the real clipboard or sends a key. The sequence runs
against an in-memory clipboard and a fake clock (sleeping moves time on, and
can fire "the app writes its data a moment later"); the macOS pasteboard
itself is exercised on a private ``NSPasteboard.pasteboardWithUniqueName()``.
"""
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import selection_copy as sc  # noqa: E402
from pasteboard import MacPasteboard, Snapshot  # noqa: E402

IS_MAC = sys.platform == "darwin"
STRING = "public.utf8-plain-text"
SELECTION = "the words the user selected"


class World:
    """A clock that only moves when something sleeps, plus things that
    happen at set times."""

    def __init__(self):
        self.now = 100.0
        self.due = []                       # (time, callable)

    def clock(self):
        return self.now

    def sleep(self, dt):
        self.now += dt
        ready = [c for t, c in self.due if t <= self.now]
        self.due = [(t, c) for t, c in self.due if t > self.now]
        for c in ready:
            c()

    def at(self, delay, fn):
        self.due.append((self.now + delay, fn))


class Clipboard:
    """In memory, with the surface CopyRoundTrip needs (MacPasteboard's)."""

    def __init__(self, items=None, skipped=""):
        self.items = [list(row) for row in (items or [])]
        self.skipped = skipped
        self.cc = 10
        self.restores = []
        self.read_error = None
        self.restore_error = None

    def change_count(self):
        return self.cc

    def snapshot(self):
        if self.skipped:
            return Snapshot((), skipped=self.skipped, change_count=self.cc)
        return Snapshot(tuple(tuple(row) for row in self.items), change_count=self.cc)

    def write(self, items):
        """What an app does when it copies: replaces everything."""
        self.items = [list(row) for row in items]
        self.cc += 1

    def current_text(self):
        if self.read_error:
            raise self.read_error
        for row in self.items:
            for t, data in row:
                if t == STRING:
                    return data.decode("utf-8")
        return ""

    def restore(self, snap, expect):
        self.restores.append(expect)
        if self.restore_error:
            raise self.restore_error
        if snap is None or snap.skipped or self.cc != expect:
            return False
        self.items = [list(row) for row in snap.items]
        self.cc += 1
        return True


class Lock:
    def __init__(self, held=False):
        self.held = held
        self.acquired = 0

    def acquire(self, timeout=None):
        if self.held:
            return False
        self.acquired += 1
        self.held = True
        return True

    def release(self):
        assert self.held, "released a lock that was not held"
        self.held = False


def trip(cb, world, send=None, flags=None, lock=None, log=None):
    return sc.CopyRoundTrip(cb, send, flags=flags, lock=lock or Lock(), sleep=world.sleep,
                            clock=world.clock, log=log or (lambda m: None))


ORIGINAL = [
    [(STRING, "something the user had copied".encode()), ("public.rtf", b"{\\rtf1 hi}"),
     ("com.waffler.test", b"\x00\x01\x02binary")],
    [("public.png", b"\x89PNG-not-really" * 50)],
]


def copying_app(cb, world, text=SELECTION, delay=0.0):
    """A ``send_copy`` that makes the app put ``text`` on the clipboard, at
    once or ``delay`` seconds later (some apps clear first and write after)."""
    sent = []

    def send():
        sent.append(world.now)
        data = [[(STRING, text.encode("utf-8")), ("public.html", f"<b>{text}</b>".encode())]]
        if delay:
            cb.write([])                                   # cleared now...
            world.at(delay, lambda: cb.write(data))        # ...written a moment later
        else:
            cb.write(data)
    send.sent = sent
    return send


# ── the round trip ───────────────────────────────────────────────────────────

def test_the_selection_comes_back_and_the_clipboard_is_exactly_as_it_was():
    w, cb = World(), Clipboard(ORIGINAL)
    send = copying_app(cb, w)
    result = trip(cb, w, send).read(1.0)
    assert result == sc.CopyResult(SELECTION, sc.OK) and result.ok
    assert cb.items == [list(r) for r in ORIGINAL], "every item, every type, byte for byte"
    assert len(send.sent) == 1


def test_an_empty_clipboard_ends_up_empty_again():
    w, cb = World(), Clipboard([])
    assert trip(cb, w, copying_app(cb, w)).read(1.0).text == SELECTION
    assert cb.items == []


def test_an_app_that_clears_first_and_writes_a_moment_later_is_waited_for():
    w, cb = World(), Clipboard(ORIGINAL)
    result = trip(cb, w, copying_app(cb, w, delay=0.08)).read(1.0)
    assert result.text == SELECTION, "must not read the half-written clipboard"
    assert cb.items == [list(r) for r in ORIGINAL]


def test_nothing_copied_means_the_clipboard_was_never_touched():
    w, cb = World(), Clipboard(ORIGINAL)
    result = trip(cb, w, lambda: None).read(0.5)         # the key did nothing: no selection
    assert result == sc.CopyResult("", sc.NO_COPY) and not result.ok
    assert cb.items == [list(r) for r in ORIGINAL] and cb.cc == 10
    assert cb.restores == [], "there was nothing of ours to take back"


def test_a_copy_of_something_that_is_not_text_gives_no_text_and_still_restores():
    w, cb = World(), Clipboard(ORIGINAL)

    def copy_an_image():
        cb.write([[("public.png", b"img")]])
    result = trip(cb, w, copy_an_image).read(1.0)
    assert result == sc.CopyResult("", sc.NO_COPY)
    assert cb.items == [list(r) for r in ORIGINAL]


@pytest.mark.parametrize("why", ["concealed", "transient", "auto_generated", "remote",
                                 "too_large", "slow", "unreadable"])
def test_a_clipboard_that_cannot_be_put_back_is_never_copied_over(why):
    w, cb = World(), Clipboard(ORIGINAL, skipped=why)
    send = copying_app(cb, w)
    result = trip(cb, w, send).read(1.0)
    assert result == sc.CopyResult("", sc.NOT_RESTORABLE)
    assert send.sent == [], "no keystroke: it would replace what the user copied for good"
    assert cb.items == [list(r) for r in ORIGINAL]


def test_something_the_user_copies_between_the_read_and_the_restore_is_left_alone():
    w, cb = World(), Clipboard(ORIGINAL)
    real_read = cb.current_text

    def read_then_user_copies():
        text = real_read()
        cb.write([[(STRING, b"the user copied this")]])     # right after we looked
        return text
    cb.current_text = read_then_user_copies
    result = trip(cb, w, copying_app(cb, w)).read(1.0)
    assert result.text == SELECTION, "the selection was read before that"
    assert cb.items == [[(STRING, b"the user copied this")]], "their copy survives"


def test_a_restore_that_fails_is_logged_without_the_text_and_does_not_raise():
    w, cb = World(), Clipboard(ORIGINAL)
    cb.restore_error = RuntimeError("could not write the restored items")
    logs = []
    result = trip(cb, w, copying_app(cb, w), log=logs.append).read(1.0)
    assert result.text == SELECTION
    assert logs and all(SELECTION not in m for m in logs)


def test_the_keystroke_failing_is_reported_and_touches_nothing():
    w, cb = World(), Clipboard(ORIGINAL)

    def boom():
        raise OSError("event tap refused")
    result = trip(cb, w, boom).read(1.0)
    assert result == sc.CopyResult("", sc.KEYSTROKE_FAILED)
    assert cb.items == [list(r) for r in ORIGINAL] and cb.cc == 10


def test_a_clipboard_that_cannot_be_read_back_is_restored_and_never_raises():
    w, cb = World(), Clipboard(ORIGINAL)
    cb.read_error = RuntimeError("pasteboard went away")
    result = trip(cb, w, copying_app(cb, w)).read(1.0)
    assert result == sc.CopyResult("", sc.NO_COPY)
    assert cb.items == [list(r) for r in ORIGINAL]


def test_a_snapshot_that_raises_is_not_an_exception_for_the_caller():
    w = World()

    class Broken(Clipboard):
        def snapshot(self):
            raise RuntimeError("no pasteboard")
    send = copying_app(Broken(), w)
    result = trip(Broken(), w, send).read(1.0)
    assert result == sc.CopyResult("", sc.UNREADABLE) and send.sent == []


def test_the_budget_ends_the_wait():
    w, cb = World(), Clipboard(ORIGINAL)
    send = copying_app(cb, w, delay=5.0)                  # far too slow
    result = trip(cb, w, send).read(0.3)
    assert not result.ok
    assert w.now - 100.0 < 1.0, "the round trip stayed within about its budget"
    assert trip(cb, w, send).read(0).reason == sc.TIMED_OUT


def test_the_hotkeys_own_modifiers_are_waited_for_first():
    w, cb = World(), Clipboard(ORIGINAL)
    send = copying_app(cb, w)
    down_until = w.now + 0.12
    held = lambda: 0x00800000 if w.now < down_until else 0      # Fn still down    # noqa: E731
    trip(cb, w, send, flags=held).read(1.0)
    assert send.sent[0] >= down_until, "Cmd+C waited for the keys to come up"


def test_another_clipboard_operation_in_progress_is_not_waited_for_long_or_interrupted():
    w, cb = World(), Clipboard(ORIGINAL)
    send = copying_app(cb, w)
    lock = Lock(held=True)                # a paste is mid-sequence
    result = trip(cb, w, send, lock=lock).read(1.0)
    assert result == sc.CopyResult("", sc.BUSY)
    assert send.sent == [] and lock.held, "the other operation's lock is not ours to release"


def test_the_lock_is_released_on_every_path():
    for case in ("ok", "none", "skipped", "boom"):
        w = World()
        cb = Clipboard(ORIGINAL, skipped="concealed" if case == "skipped" else "")
        send = {"ok": copying_app(cb, w), "none": lambda: None,
                "skipped": lambda: None, "boom": lambda: (_ for _ in ()).throw(OSError("x"))}[case]
        lock = Lock()
        trip(cb, w, send, lock=lock).read(0.3)
        assert lock.held is False, case


def test_it_shares_the_pastes_lock_by_default():
    import pasteboard
    rt = sc.CopyRoundTrip(Clipboard(), lambda: None)
    assert rt._lock is pasteboard._PASTE_LOCK


def test_two_round_trips_do_not_overlap():
    """The real lock: while one is mid-sequence the other gives up as busy."""
    release = threading.Event()
    entered = threading.Event()

    class Slow(Clipboard):
        def snapshot(self):
            entered.set()
            release.wait(5)
            return super().snapshot()

    t = threading.Thread(target=lambda: sc.CopyRoundTrip(Slow(ORIGINAL), lambda: None,
                                                         lock_wait=2.0).read(1.0))
    t.start()
    assert entered.wait(2)
    other = sc.CopyRoundTrip(Clipboard(ORIGINAL), lambda: None, lock_wait=0.05).read(0.5)
    release.set()
    t.join(5)
    assert other.reason == sc.BUSY


# ── which apps may be sent the copy key ──────────────────────────────────────

@pytest.mark.parametrize("name", ["Terminal", "iTerm2", "Warp", "powershell", "anything"])
def test_every_app_may_be_sent_cmd_c_on_a_mac(name):
    assert sc.keystroke_allowed(name, "darwin") is True


@pytest.mark.parametrize("name", ["cmd", "powershell", "pwsh.exe", "WindowsTerminal", "conhost",
                                  "wsl", "mintty", "Alacritty", "putty", "wezterm-gui"])
def test_a_windows_console_is_never_sent_ctrl_c(name):
    assert sc.keystroke_allowed(name, "win32") is False


@pytest.mark.parametrize("name", ["notepad", "Code", "chrome", "OUTLOOK.EXE", "slack", "WINWORD"])
def test_other_windows_apps_may_be_sent_ctrl_c(name):
    assert sc.keystroke_allowed(name, "win32") is True


def test_when_windows_cannot_say_what_is_in_front_no_ctrl_c_is_sent():
    assert sc.keystroke_allowed("", "win32") is False
    assert sc.keystroke_allowed(None, "win32") is False


# ── the Windows clipboard (text only) ────────────────────────────────────────

class FakeWinPyperclip:
    def __init__(self, text=""):
        self.text = text
        self.copied = []

    def paste(self):
        return self.text

    def copy(self, text):
        self.copied.append(text)
        self.text = text


def winclip(formats, text="", seq=7):
    py = FakeWinPyperclip(text)
    state = {"seq": seq}
    wc = sc.WindowsTextClipboard(formats=lambda: list(formats), sequence=lambda: state["seq"],
                                 pyperclip=py)
    return wc, py, state


def test_a_text_only_windows_clipboard_is_put_back():
    wc, py, state = winclip([13, 1, 7, 16], text="what I had")
    snap = wc.snapshot()
    assert not snap.skipped and snap.items
    py.text = "the selection"
    state["seq"] += 1
    assert wc.current_text() == "the selection"
    assert wc.restore(snap, state["seq"]) is True
    assert py.text == "what I had"


def test_an_empty_windows_clipboard_is_emptied_again():
    wc, py, state = winclip([], text="")
    snap = wc.snapshot()
    assert not snap.skipped and snap.items == ()
    py.text = "the selection"
    state["seq"] += 1
    assert wc.restore(snap, state["seq"]) is True
    assert py.text == ""


@pytest.mark.parametrize("formats", [[2, 8], [15], [13, 0xC0A5], [49161]])
def test_a_windows_clipboard_with_anything_but_text_is_not_touched(formats):
    wc, py, state = winclip(formats, text="x")
    snap = wc.snapshot()
    assert snap.skipped == "not_text"
    assert wc.restore(snap, state["seq"]) is False
    w, send = World(), []
    result = sc.CopyRoundTrip(wc, lambda: send.append(1), lock=Lock(), sleep=w.sleep,
                              clock=w.clock).read(0.5)
    assert result.reason == sc.NOT_RESTORABLE and send == [], "no Ctrl+C over an image or files"


def test_a_windows_clipboard_that_cannot_be_inspected_is_not_touched():
    def boom():
        raise OSError("clipboard is in use")
    wc = sc.WindowsTextClipboard(formats=boom, sequence=lambda: 1, pyperclip=FakeWinPyperclip())
    assert wc.snapshot().skipped == "unreadable"


def test_a_windows_restore_respects_a_newer_copy():
    wc, py, state = winclip([13], text="mine")
    snap = wc.snapshot()
    state["seq"] += 5                      # someone copied since
    assert wc.restore(snap, state["seq"] - 1) is False
    assert py.copied == []


def test_the_whole_windows_round_trip():
    wc, py, state = winclip([13, 1], text="what I had")
    w = World()

    def ctrl_c():
        py.text = SELECTION
        state["seq"] += 1
    result = sc.CopyRoundTrip(wc, ctrl_c, lock=Lock(), sleep=w.sleep, clock=w.clock).read(1.0)
    assert result.text == SELECTION and py.text == "what I had"


# ── the macOS pasteboard, on a private one ───────────────────────────────────

@pytest.fixture
def private_pb():
    if not IS_MAC:
        pytest.skip("needs macOS (AppKit)")
    from AppKit import NSPasteboard
    pb = NSPasteboard.pasteboardWithUniqueName()
    yield pb
    pb.releaseGlobally()


def _write(pb, items):
    from AppKit import NSPasteboardItem
    from Foundation import NSData
    objs = []
    for types in items:
        it = NSPasteboardItem.alloc().init()
        for t, data in types:
            it.setData_forType_(NSData.dataWithBytes_length_(data, len(data)), t)
        objs.append(it)
    pb.clearContents()
    assert pb.writeObjects_(objs)


def _dump(pb):
    return [sorted((str(t), bytes(i.dataForType_(t))) for t in i.types())
            for i in (pb.pasteboardItems() or [])]


@pytest.mark.skipif(not IS_MAC, reason="needs macOS (AppKit)")
def test_mac_pasteboard_reads_text(private_pb):
    mp = MacPasteboard(private_pb)
    private_pb.clearContents()
    assert mp.current_text() == ""
    _write(private_pb, [[(STRING, "héllo wörld".encode())]])
    assert mp.current_text() == "héllo wörld"


@pytest.mark.skipif(not IS_MAC, reason="needs macOS (AppKit)")
def test_a_real_pasteboard_round_trip_restores_every_type_byte_for_byte(private_pb):
    original = [
        [(STRING, b"my own clipboard"), ("public.rtf", b"{\\rtf1 hi}"), ("com.waffler.test", b"\x00\x01\x02")],
        [("public.png", b"\x89PNG-not-really" * 100)],
    ]
    _write(private_pb, original)
    before = _dump(private_pb)
    mp = MacPasteboard(private_pb)

    def app_copies():
        _write(private_pb, [[(STRING, SELECTION.encode()), ("public.html", b"<b>x</b>")]])

    result = sc.CopyRoundTrip(mp, app_copies, lock=Lock()).read(2.0)
    assert result == sc.CopyResult(SELECTION, sc.OK)
    assert _dump(private_pb) == before


@pytest.mark.skipif(not IS_MAC, reason="needs macOS (AppKit)")
def test_a_real_pasteboard_with_a_concealed_item_is_not_copied_over(private_pb):
    _write(private_pb, [[(STRING, b"a password"), ("org.nspasteboard.ConcealedType", b"")]])
    sent = []
    result = sc.CopyRoundTrip(MacPasteboard(private_pb), lambda: sent.append(1), lock=Lock()).read(1.0)
    assert result.reason == sc.NOT_RESTORABLE and sent == []


@pytest.mark.skipif(not IS_MAC, reason="needs macOS (AppKit)")
def test_a_real_pasteboard_where_nothing_is_copied_is_untouched(private_pb):
    _write(private_pb, [[(STRING, b"mine")]])
    before, count = _dump(private_pb), private_pb.changeCount()
    result = sc.CopyRoundTrip(MacPasteboard(private_pb), lambda: None, lock=Lock()).read(0.2)
    assert result.reason == sc.NO_COPY
    assert _dump(private_pb) == before and private_pb.changeCount() == count


def test_default_round_trip_is_none_where_there_is_no_clipboard(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "linux")
    assert sc.default_round_trip() is None


# ── Windows waits for the shortcut's own modifier keys, as a Mac does ────────

def test_the_windows_modifier_flags_are_in_the_bits_the_wait_reads():
    import mac_input
    import pasteboard
    held = lambda *vks: (lambda vk: vk in vks)       # noqa: E731
    assert sc.windows_modifier_flags(held()) == 0
    assert sc.windows_modifier_flags(held(0x10)) == mac_input.MOD_SHIFT
    assert sc.windows_modifier_flags(held(0x11)) == mac_input.MOD_CONTROL
    assert sc.windows_modifier_flags(held(0x12)) == mac_input.MOD_OPTION
    assert sc.windows_modifier_flags(held(0x5B)) == mac_input.MOD_COMMAND
    assert sc.windows_modifier_flags(held(0x5C)) == mac_input.MOD_COMMAND
    both = sc.windows_modifier_flags(held(0x11, 0x12))
    assert both == mac_input.MOD_CONTROL | mac_input.MOD_OPTION
    assert both & pasteboard.HOTKEY_MODIFIER_MASK == both, "the wait looks at every one of them"


def test_the_copy_waits_for_alt_to_come_up_before_ctrl_c(monkeypatch):
    """The Transform shortcut has two modifier keys down when the copy starts;
    Ctrl+C sent with Alt still held is Ctrl+Alt+C to the app."""
    import mac_input
    world = World()
    state = {"held": mac_input.MOD_CONTROL | mac_input.MOD_OPTION}
    world.at(0.1, lambda: state.update(held=0))
    sent = []
    pb = Clipboard([[(STRING, b"mine")]])
    rt = sc.CopyRoundTrip(pb, lambda: sent.append(dict(state)), flags=lambda: state["held"], lock=Lock(),
                          sleep=world.sleep, clock=world.clock)
    rt.read(1.0)
    assert sent and sent[0]["held"] == 0, "Ctrl+C went only once the keys were up"


def test_windows_is_given_the_modifier_reader(monkeypatch):
    monkeypatch.setattr(sc.sys, "platform", "win32")
    trip = sc.default_round_trip()
    assert trip is not None and trip._flags is sc.windows_modifier_flags


# ── the Copy key follows the keyboard layout, like the Paste key ─────────────

QWERTY = {9: "v", 8: "c", 47: "."}
DVORAK = {47: "v", 38: "c", 9: ".", 8: "j"}           # c sits where QWERTY has "j"
DVORAK_QWERTY_CMD = {9: "v", 8: "c", 47: "."}         # Command shortcuts keep QWERTY positions
RUSSIAN = {8: "с", 9: "м"}


def test_the_copy_keycode_is_8_on_qwerty_and_where_c_lives_elsewhere():
    import mac_input
    assert mac_input.resolve_keycode(QWERTY.get, "c", 8, None) == 8
    assert mac_input.resolve_keycode(DVORAK.get, "c", 8, None) == 38
    assert mac_input.resolve_keycode(DVORAK_QWERTY_CMD.get, "c", 8, None) == 8
    assert mac_input.resolve_keycode(RUSSIAN.get, "c", 8, None) is None, "no Latin c: the caller decides"
    assert mac_input.resolve_keycode(RUSSIAN.get, "c", 8, 8) == 8
    assert mac_input.resolve_keycode(None, "c", 8, 8) == 8


def test_the_paste_keycode_resolution_is_unchanged_by_the_generalisation():
    import mac_input
    assert mac_input.resolve_v_keycode(QWERTY.get) == 9
    assert mac_input.resolve_v_keycode(DVORAK.get) == 47
    assert mac_input.resolve_v_keycode({9: "V", 30: "v"}.get) == 9, "9 wins when two keys make a v"
    assert mac_input.resolve_v_keycode(RUSSIAN.get, default=None) is None   # no Latin v on that table
    assert mac_input.resolve_v_keycode(RUSSIAN.get) == 9                    # so QWERTY's, by default
    assert mac_input.resolve_v_keycode({}.get, default=None) is None


@pytest.mark.skipif(not IS_MAC, reason="needs macOS (AppKit)")
def test_the_copy_keycode_comes_from_the_layout_and_is_cached_per_letter(monkeypatch):
    import mac_input
    monkeypatch.setattr(mac_input, "_cached_keycodes", {})
    monkeypatch.setattr(mac_input, "_layout_char_mapper",
                        lambda ascii_capable=False: DVORAK.get if not ascii_capable else None)
    assert mac_input.c_keycode_for_current_layout() == 38      # tests run on the main thread
    assert mac_input.v_keycode_for_current_layout() == 47
    assert mac_input._cached_keycodes == {"c": 38, "v": 47}
    monkeypatch.setattr(mac_input, "_layout_char_mapper", lambda ascii_capable=False: None)
    assert mac_input.c_keycode_for_current_layout() == 38, "the last good answer when the layout cannot be read"
    monkeypatch.setattr(mac_input, "_cached_keycodes", {})
    assert mac_input.c_keycode_for_current_layout() == 8, "QWERTY's, when it never could"


def test_off_a_mac_the_keycodes_are_qwertys(monkeypatch):
    import mac_input
    monkeypatch.setattr(mac_input.sys, "platform", "win32")
    assert mac_input.c_keycode_for_current_layout() == 8 and mac_input.v_keycode_for_current_layout() == 9
