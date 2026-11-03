"""3.16.5: the quiet-microphone tip on Windows too.

On the owner's Windows PC dictations arrived at a median -49 dBFS (the
quiet-audio lift added a median 23.8 dB, some takes hitting its 30 dB cap),
and the default input, "Headset Microphone (Realtek(R) Audio)", was at 54%.
The Mac's rules carry over unchanged (median of the last ten under -36 dBFS,
a level under 70%, at most once a week, the Show tips switch, "Don't show
again"); on Windows the level and the device's name are read from the default
capture endpoint (Core Audio, through ctypes: no new dependency) on a thread
that is given 2 seconds, and the button opens Sound settings.

Fakes only: no Core Audio call is made (the fake COM objects below stand in
for Windows'), and no settings page is opened. The one test that reads the
real device runs only on Windows.
"""
import ctypes
import json
import os
import sys
import threading
import time
import types
from ctypes import CFUNCTYPE, POINTER, c_float, c_int, c_int32, c_uint32, c_void_p, cast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import notifier as N  # noqa: E402
import quiet_mic as Q  # noqa: E402
import user_messages as um  # noqa: E402

DAY = 24 * 3600.0
OWNER_DEVICE = "Headset Microphone (Realtek(R) Audio)"


def entry(db):
    return {"timestamp": "2026-11-03T10:00:00", "text": "words", "styled": "Words.",
            "speech_db": db}


# ── the device's name, as the tip says it ───────────────────────────────────

@pytest.mark.parametrize("raw,short", [
    (OWNER_DEVICE, "Headset Microphone"),
    ("Microphone Array (Intel® Smart Sound Technology for Digital Microphones)",
     "Microphone Array"),
    ("Microphone (2- USB Audio Device)", "USB Audio Device"),       # a generic name says which
    ("Microphone (Realtek(R) Audio)", "Realtek(R) Audio"),
    ("Microphone", "Microphone"),
    ("  Yeti   Stereo Microphone ", "Yeti Stereo Microphone"),
    ("Line In\x00\x07 (Hi-Fi)", "Line In"),
    ("A" * 80, "A" * 32),
    ("", None), ("   ", None), (None, None), (42, None),
])
def test_the_tip_names_the_device_briefly(raw, short):
    assert Q.short_device_name(raw) == short


# ── reading the level and the name, on its own thread ───────────────────────

def test_the_reading_is_the_volume_and_the_short_name():
    got = Q.read_windows_input(reader=lambda: Q.MicInput(54, OWNER_DEVICE), platform="win32")
    assert got == Q.MicInput(54, "Headset Microphone")


def test_nothing_is_read_off_windows():
    calls = []
    assert Q.read_windows_input(reader=lambda: calls.append(1), platform="darwin") is None
    assert Q.read_windows_input(reader=lambda: calls.append(1), platform="linux") is None
    assert calls == []


def test_a_reading_that_takes_too_long_is_none_in_two_seconds():
    assert Q.READ_TIMEOUT_S == 2.0
    release = threading.Event()

    def hangs():
        release.wait(5)
        return Q.MicInput(54, OWNER_DEVICE)
    t0 = time.monotonic()
    try:
        assert Q.read_windows_input(reader=hangs, platform="win32", timeout=0.2) is None
        assert time.monotonic() - t0 < 1.5
    finally:
        release.set()


@pytest.mark.parametrize("error", [
    OSError("[WinError -2147023728] Element not found"),
    AttributeError("module 'ctypes' has no attribute 'windll'"),   # not Windows, or no ctypes COM
    ImportError("No module named '_ctypes'"),
    RuntimeError("anything"),
])
def test_a_reading_that_fails_is_none_and_never_raises(error):
    def fails():
        raise error
    assert Q.read_windows_input(reader=fails, platform="win32") is None


@pytest.mark.parametrize("value", [
    None, Q.MicInput(None, OWNER_DEVICE), Q.MicInput(140, OWNER_DEVICE),
    Q.MicInput(-1, OWNER_DEVICE), Q.MicInput(True, OWNER_DEVICE), Q.MicInput("54", OWNER_DEVICE),
    "54", 54,
])
def test_a_volume_that_makes_no_sense_is_none(value):
    assert Q.read_windows_input(reader=lambda: value, platform="win32") is None


def test_a_name_that_cannot_be_read_still_gives_the_volume():
    assert Q.read_windows_input(reader=lambda: Q.MicInput(54, None), platform="win32") == \
        Q.MicInput(54, None)


@pytest.mark.skipif(sys.platform == "win32", reason="off Windows, Core Audio is missing")
def test_off_windows_the_real_reader_finds_nothing_to_use_and_never_raises():
    assert Q.read_windows_input(platform="win32") is None


@pytest.mark.skipif(sys.platform != "win32", reason="reads this PC's microphone")
def test_on_windows_the_real_reader_answers_in_time_and_never_raises():
    """A PC with no microphone (a CI runner) answers None; any other, its
    default input's level and name. Never changes the level."""
    t0 = time.monotonic()
    got = Q.read_windows_input()
    assert time.monotonic() - t0 < Q.READ_TIMEOUT_S + 1
    assert got is None or (isinstance(got.volume, int) and 0 <= got.volume <= 100)
    if got is not None and got.name is not None:
        assert 0 < len(got.name) <= 32


# ── the Core Audio calls, against fake COM objects ──────────────────────────
#
# The signatures here are written out from Microsoft's documentation, apart
# from the module's own, so a wrong one fails: mmdeviceapi.h, endpointvolume.h,
# propsys.h and combaseapi.h. Every COM method returns an HRESULT (32 bits)
# and takes the object first.

HRESULT = c_int32
S_OK, S_FALSE = 0, 1
E_FAIL = -2147467259                    # 0x80004005
E_NOTFOUND = -2147023728                # 0x80070490: no capture device
RPC_E_CHANGED_MODE = -2147417850        # 0x80010106

CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
IID_IAudioEndpointVolume = "5CDF2C82-841E-4546-9722-0CF74078229A"
PKEY_Device_FriendlyName = ("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)


def guid_at(address):
    import uuid
    return str(uuid.UUID(bytes_le=ctypes.string_at(address, 16))).upper()


class FakeCom:
    """IUnknown with a vtable of ctypes callbacks: slot -> (argtypes, fn).
    Any other slot records the call and fails."""

    def __init__(self, name, log, methods):
        self.name, self.log = name, log
        self.released = 0
        self._keep = []
        self.vtbl = (c_void_p * 20)()
        for i in range(20):
            if i == 2:
                self._slot(i, (), self._release)
            elif i in methods:
                argtypes, fn = methods[i]
                self._slot(i, argtypes, fn)
            else:
                self._slot(i, (), self._unexpected(i))
        self.obj = (c_void_p * 1)(cast(self.vtbl, c_void_p).value)
        self.ptr = ctypes.addressof(self.obj)

    def _slot(self, i, argtypes, fn):
        proto = CFUNCTYPE(HRESULT, c_void_p, *argtypes)

        def call(this, *args):
            assert this == self.ptr, f"{self.name} slot {i} called on another object"
            return fn(*args)
        cb = proto(call)
        self._keep.append(cb)
        self.vtbl[i] = cast(cb, c_void_p).value

    def _release(self):
        self.released += 1
        self.log.append((self.name, "Release"))
        return 0

    def _unexpected(self, i):
        def fn():
            self.log.append((self.name, "unexpected slot", i))
            return E_FAIL
        return fn


def put_pointer(out, value):
    cast(out, POINTER(c_void_p))[0] = value


class Windows:
    """Fake ole32 and Core Audio, the owner's PC by default."""

    def __init__(self, *, volume=0.54, name=OWNER_DEVICE, init_hr=S_OK, endpoint_hr=S_OK,
                 activate_hr=S_OK, volume_hr=S_OK, store_hr=S_OK, value_hr=S_OK, vt=31,
                 create_hr=S_OK):
        self.log = []
        self.cleared = 0
        self.uninit = 0
        self.seen = {}
        self._name_buf = ctypes.create_unicode_buffer(name) if name is not None else None
        log = self.log

        def get_scalar(out):
            log.append(("volume", "GetMasterVolumeLevelScalar"))
            if volume_hr == S_OK:
                cast(out, POINTER(c_float))[0] = volume
            return volume_hr
        self.volume = FakeCom("volume", log, {9: ((c_void_p,), get_scalar)})

        def get_value(key, pv):
            self.seen["pkey"] = (guid_at(key), cast(key + 16, POINTER(c_uint32))[0])
            log.append(("store", "GetValue"))
            if value_hr == S_OK and self._name_buf is not None:
                prop = cast(pv, POINTER(Q._PROPVARIANT)).contents
                prop.vt = vt
                prop.data = ctypes.addressof(self._name_buf)
            return value_hr
        self.store = FakeCom("store", log, {5: ((c_void_p, c_void_p), get_value)})

        def activate(iid, clsctx, params, out):
            self.seen["activate"] = (guid_at(iid), clsctx, params)
            log.append(("device", "Activate"))
            if activate_hr == S_OK:
                put_pointer(out, self.volume.ptr)
            return activate_hr

        def open_store(mode, out):
            self.seen["stgm"] = mode
            log.append(("device", "OpenPropertyStore"))
            if store_hr == S_OK:
                put_pointer(out, self.store.ptr)
            return store_hr
        self.device = FakeCom("device", log, {
            3: ((c_void_p, c_uint32, c_void_p, c_void_p), activate),
            4: ((c_uint32, c_void_p), open_store)})

        def default_endpoint(flow, role, out):
            self.seen["endpoint"] = (flow, role)
            log.append(("enumerator", "GetDefaultAudioEndpoint"))
            if endpoint_hr == S_OK:
                put_pointer(out, self.device.ptr)
            return endpoint_hr
        self.enumerator = FakeCom("enumerator", log, {
            4: ((c_int, c_int, c_void_p), default_endpoint)})

        def co_initialize(reserved, flags):
            self.seen["coinit"] = (reserved, flags)
            log.append(("ole32", "CoInitializeEx"))
            return init_hr

        def co_uninitialize():
            self.uninit += 1
            log.append(("ole32", "CoUninitialize"))

        def co_create(clsid, outer, ctx, iid, out):
            self.seen["create"] = (guid_at(clsid), outer, ctx, guid_at(iid))
            log.append(("ole32", "CoCreateInstance"))
            if create_hr == S_OK:
                put_pointer(out, self.enumerator.ptr)
            return create_hr

        def clear(pv):
            self.cleared += 1
            cast(pv, POINTER(Q._PROPVARIANT)).contents.vt = 0
            return S_OK

        self._fns = [
            CFUNCTYPE(HRESULT, c_void_p, c_uint32)(co_initialize),
            CFUNCTYPE(None)(co_uninitialize),
            CFUNCTYPE(HRESULT, c_void_p, c_void_p, c_uint32, c_void_p, c_void_p)(co_create),
            CFUNCTYPE(HRESULT, c_void_p)(clear),
        ]
        self.com = Q._Com(CFUNCTYPE, *self._fns)

    def objects(self):
        return (self.enumerator, self.device, self.volume, self.store)

    def unexpected(self):
        return [line for line in self.log if line[1] == "unexpected slot"]


def test_the_default_capture_device_is_read_as_microsoft_documents_it():
    w = Windows()
    got = Q._read_default_capture(w.com)
    assert got.name == OWNER_DEVICE and got.volume == 54
    assert w.seen["coinit"] == (None, 0)                       # COINIT_MULTITHREADED
    assert w.seen["create"] == (CLSID_MMDeviceEnumerator, None, 23, IID_IMMDeviceEnumerator)
    assert w.seen["endpoint"] == (1, 0)                        # eCapture, eConsole
    assert w.seen["activate"] == (IID_IAudioEndpointVolume, 23, None)
    assert w.seen["stgm"] == 0                                 # STGM_READ
    assert w.seen["pkey"] == PKEY_Device_FriendlyName
    assert w.unexpected() == []                                # nothing else, no Set*Volume*
    assert [o.released for o in w.objects()] == [1, 1, 1, 1]
    assert w.cleared == 1 and w.uninit == 1
    assert w.log[-1] == ("ole32", "CoUninitialize")


@pytest.mark.parametrize("scalar,volume", [(0.0, 0), (0.544, 54), (0.545, 55), (1.0, 100)])
def test_the_level_is_a_whole_percentage(scalar, volume):
    assert Q._read_default_capture(Windows(volume=scalar).com).volume == volume


def test_no_capture_device_is_no_reading_and_everything_is_let_go():
    w = Windows(endpoint_hr=E_NOTFOUND)
    assert Q._read_default_capture(w.com) == Q.MicInput(None, None)
    assert w.enumerator.released == 1 and w.device.released == 0
    assert w.uninit == 1 and w.unexpected() == []


@pytest.mark.parametrize("kw", [dict(activate_hr=E_FAIL), dict(volume_hr=E_FAIL)])
def test_a_level_that_cannot_be_read_is_none_but_the_name_is_kept(kw):
    w = Windows(**kw)
    got = Q._read_default_capture(w.com)
    assert got == Q.MicInput(None, OWNER_DEVICE)
    assert w.enumerator.released == w.device.released == w.store.released == 1
    assert w.unexpected() == []


@pytest.mark.parametrize("kw", [dict(store_hr=E_FAIL), dict(value_hr=E_FAIL), dict(vt=0),
                                dict(name=None)])
def test_a_name_that_cannot_be_read_is_none_but_the_level_is_kept(kw):
    w = Windows(**kw)
    assert Q._read_default_capture(w.com) == Q.MicInput(54, None)
    assert w.enumerator.released == w.device.released == w.volume.released == 1
    assert w.unexpected() == []


def test_com_already_set_up_another_way_on_this_thread_is_used_and_left_alone():
    w = Windows(init_hr=RPC_E_CHANGED_MODE)
    assert Q._read_default_capture(w.com).volume == 54
    assert w.uninit == 0


def test_com_set_up_already_the_same_way_is_balanced():
    w = Windows(init_hr=S_FALSE)
    assert Q._read_default_capture(w.com).volume == 54
    assert w.uninit == 1


def test_com_that_cannot_start_reads_nothing():
    w = Windows(init_hr=E_FAIL)
    assert Q._read_default_capture(w.com) == Q.MicInput(None, None)
    assert w.uninit == 0 and ("ole32", "CoCreateInstance") not in w.log


def test_no_enumerator_reads_nothing():
    w = Windows(create_hr=E_FAIL)
    assert Q._read_default_capture(w.com) == Q.MicInput(None, None)
    assert w.uninit == 1 and w.enumerator.released == 0


def test_through_the_thread_the_owners_pc_reads_54_and_headset_microphone():
    w = Windows()
    got = Q.read_windows_input(reader=lambda: Q._read_default_capture(w.com), platform="win32")
    assert got == Q.MicInput(54, "Headset Microphone")


# ── the tip's words on Windows ──────────────────────────────────────────────

def test_the_windows_tip_names_the_device_and_its_level():
    heading, body = um.quiet_mic_message(54, platform="win32", device="Headset Microphone")
    assert heading == "Your microphone is set low"
    assert body == ("Your microphone (Headset Microphone) is set low (54%), so Waffler has to "
                    "boost it and picks up more background noise. Turn its level up to about "
                    "80% in Sound settings for clearer dictation.")
    for text in (heading, body):
        assert "—" not in text and "–" not in text


def test_without_a_name_the_windows_tip_says_your_microphone():
    _, body = um.quiet_mic_message(54, platform="win32", device=None)
    assert body.startswith("Your microphone is set low (54%), so Waffler has to boost it")
    assert "()" not in body and "Turn its level up to about 80% in Sound settings" in body


def test_the_mac_tip_is_unchanged():
    assert um.quiet_mic_message(40) == um.quiet_mic_message(40, platform="darwin") == (
        "Your microphone is set low",
        "Your Mac's microphone is set low (40%), so Waffler has to boost it and picks up "
        "more background noise. Turn Input volume up to about 80% in Sound settings for "
        "clearer dictation.")


def test_the_tip_never_guesses_about_a_headset():
    """It says what was read, nothing about cables or headsets."""
    _, body = um.quiet_mic_message(54, platform="win32", device="Headset Microphone")
    lowered = body.lower().replace("headset microphone", "")
    for word in ("plug", "headset", "cable", "jack", "unplugged"):
        assert word not in lowered


# ── the button ──────────────────────────────────────────────────────────────

def test_on_windows_the_button_opens_sound_settings():
    opened = []
    assert Q.open_sound_settings(platform="win32", starter=opened.append) is True
    assert opened == ["ms-settings:sound-defaultinputproperties"] and Q.WINDOWS_SOUND_SETTINGS == "ms-settings:sound-defaultinputproperties"


def test_on_windows_a_button_that_cannot_open_settings_never_raises(monkeypatch):
    def fails(uri):
        raise OSError("no handler")
    assert Q.open_sound_settings(platform="win32", starter=fails) is False
    monkeypatch.delattr(os, "startfile", raising=False)     # what a Mac has
    assert Q.open_sound_settings(platform="win32") is False


def test_on_windows_the_mac_opener_is_not_run():
    ran = []
    assert Q.open_sound_settings(runner=lambda *a, **k: ran.append(a), platform="win32",
                                 starter=lambda uri: None) is True
    assert ran == []


# ── the advisor on Windows ──────────────────────────────────────────────────

class Clock:
    def __init__(self, t=2_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class WinRig:
    def __init__(self, tmp_path, *, volume=54, name="Headset Microphone", platform="win32",
                 history=(), allowed=True, could_show=True):
        self.clock = Clock()
        self.volume, self.name = volume, name
        self.win_reads = 0
        self.mac_reads = 0
        self.shown, self.logged, self.history = [], [], list(history)
        self.allowed, self.could = allowed, could_show
        self.store = Q.VolumeLog(tmp_path / Q.STATE_FILE)
        self.advisor = Q.QuietMicAdvisor(self.hooks(platform))

    def hooks(self, platform):
        def read_input():
            self.win_reads += 1
            return None if self.volume is None else Q.MicInput(self.volume, self.name)

        def read_volume():
            self.mac_reads += 1
            return self.volume

        return Q.Hooks(
            platform=platform, history=lambda: list(self.history),
            could_show=lambda kind: self.could, quiet=lambda: True,
            toast_allowed=lambda kind: self.allowed,
            show=lambda heading, body, buttons, timeout: self.shown.append((heading, body, buttons)),
            read_volume=read_volume, read_input=read_input, clock=self.clock,
            sleep=lambda s: None, log=self.logged.append, store=self.store)

    def takes(self, *levels):
        for db in levels:
            self.history.append(entry(db))
            self.advisor.take_done(db)


def test_on_windows_ten_quiet_takes_and_54_percent_give_the_tip(tmp_path):
    rig = WinRig(tmp_path)
    rig.takes(*[-49.0] * 10)
    assert rig.win_reads == 1 and rig.mac_reads == 0
    heading, body, buttons = rig.shown[0]
    assert (heading, body) == um.quiet_mic_message(54, platform="win32", device="Headset Microphone")
    assert [b["label"] for b in buttons] == ["Open Sound settings", "Got it", "Don't show again"]
    assert buttons[0]["action"] == Q.OPEN_SOUND_ACTION
    assert N.parse_tip_action(buttons[2]["action"]) == (N.TIP_MUTE, Q.TIP_KIND)
    assert len(rig.shown) == 1


@pytest.mark.parametrize("volume,tip", [(69, True), (70, False), (85, False)])
def test_on_windows_low_means_under_70_too(tmp_path, volume, tip):
    rig = WinRig(tmp_path, volume=volume)
    rig.takes(*[-49.0] * 10)
    assert bool(rig.shown) is tip and rig.win_reads == 1


def test_a_headset_set_high_enough_with_quiet_speech_says_nothing(tmp_path):
    """Its level is fine, yet speech is very quiet: Waffler does not guess
    (unplugged, the wrong jack); no tip at all."""
    rig = WinRig(tmp_path, volume=90, name="Headset Microphone")
    rig.takes(*[-55.0] * 12)
    assert rig.shown == [] and rig.win_reads == 1


@pytest.mark.parametrize("levels", [[-49.0] * 9, [-30.0] * 10])
def test_on_windows_no_read_unless_ten_takes_are_quiet(tmp_path, levels):
    rig = WinRig(tmp_path)
    rig.takes(*levels)
    assert rig.win_reads == 0 and rig.shown == []


def test_on_windows_nothing_is_read_when_the_tip_could_not_be_shown(tmp_path):
    rig = WinRig(tmp_path, could_show=False)                   # Show tips off, hidden, this week
    rig.takes(*[-49.0] * 12)
    assert rig.win_reads == 0 and rig.shown == []


def test_without_a_name_the_tip_still_comes(tmp_path):
    rig = WinRig(tmp_path, name=None)
    rig.takes(*[-49.0] * 10)
    assert rig.shown[0][1] == um.quiet_mic_message(54, platform="win32", device=None)[1]


def test_on_windows_a_failed_read_waits_a_day(tmp_path):
    rig = WinRig(tmp_path, volume=None)
    rig.takes(*[-49.0] * 13)
    assert rig.win_reads == 1 and rig.shown == []
    rig.clock.t += DAY
    rig.takes(-49.0)
    assert rig.win_reads == 2


def test_the_days_reading_keeps_the_name_across_a_restart(tmp_path):
    first = WinRig(tmp_path, allowed=False)                    # something was on screen
    first.takes(*[-49.0] * 10)
    saved = json.loads((tmp_path / Q.STATE_FILE).read_text(encoding="utf-8"))
    assert saved == {"version": Q.STATE_VERSION, "checked_at": first.clock.t, "volume": 54,
                     "name": "Headset Microphone"}
    second = WinRig(tmp_path, volume=99, name="other", history=[entry(-49.0)] * 10)
    second.clock.t = first.clock.t + 600
    second.takes(-49.0)
    assert second.win_reads == 0
    assert "(Headset Microphone)" in second.shown[0][1] and "(54%)" in second.shown[0][1]


def test_a_stored_name_that_makes_no_sense_is_left_out(tmp_path):
    for name in (42, "", "x" * 200, ["a"]):
        (tmp_path / Q.STATE_FILE).write_text(json.dumps(
            {"version": 1, "checked_at": 2_000_000.0, "volume": 54, "name": name}), encoding="utf-8")
        assert Q.VolumeLog(tmp_path / Q.STATE_FILE).load() == (2_000_000.0, 54, None)


def test_on_a_mac_the_windows_reader_is_never_asked_and_no_name_is_kept(tmp_path):
    rig = WinRig(tmp_path, volume=40, platform="darwin")
    rig.takes(*[-42.5] * 10)
    assert rig.mac_reads == 1 and rig.win_reads == 0
    assert rig.shown[0][1] == um.quiet_mic_message(40)[1]
    saved = json.loads((tmp_path / Q.STATE_FILE).read_text(encoding="utf-8"))
    assert set(saved) == {"version", "checked_at", "volume"}


def test_linux_does_nothing(tmp_path):
    rig = WinRig(tmp_path, platform="linux")
    rig.takes(*[-49.0] * 12)
    assert rig.win_reads == rig.mac_reads == 0 and rig.shown == []


# ── in the app (the real _process, through tests/_pipeline_harness.py) ──────

import _pipeline_harness as H  # noqa: E402


@pytest.fixture
def limits(monkeypatch):
    H.fast_limits(monkeypatch)


class LevelTranscriber(H.FakeTranscriber):
    def __init__(self, level=-49.0, **kw):
        super().__init__(**kw)
        self.level = level

    def transcribe_sync(self, audio_bytes):
        text = super().transcribe_sync(audio_bytes)
        self.last_speech_dbfs = self.level
        return text


def dictate(p):
    t = H.run_process(p, 1)
    t.join(10)
    assert not t.is_alive()


def test_on_windows_the_tenth_quiet_take_brings_the_tip(tmp_path, limits, monkeypatch):
    p = H.make_pipeline(tmp_path, transcriber=LevelTranscriber(-49.0))   # the harness is Windows
    p.ns["HISTORY_FILE"].write_text(json.dumps([entry(-49.0) for _ in range(9)]), encoding="utf-8")
    win, mac = [], []
    monkeypatch.setattr(Q, "read_windows_input",
                        lambda: win.append(1) or Q.MicInput(54, "Headset Microphone"))
    monkeypatch.setattr(Q, "read_input_volume", lambda: mac.append(1) or 40)
    dictate(p)
    heading = "Your microphone is set low"
    assert H.wait_for(lambda: any(t.get("heading") == heading for t in p.overlay.toasts()),
                      timeout=5)
    tip = next(t for t in p.overlay.toasts() if t.get("heading") == heading)
    assert tip["body"].startswith("Your microphone (Headset Microphone) is set low (54%)")
    assert tip["buttons"][0]["action"] == Q.OPEN_SOUND_ACTION
    assert win == [1] and mac == []
    assert p.clipboard.pastes, "and the dictation itself went as ever"
    saved = json.loads((tmp_path / Q.STATE_FILE).read_text(encoding="utf-8"))
    assert saved["volume"] == 54 and saved["name"] == "Headset Microphone"


def test_on_linux_nothing_new_happens(tmp_path, limits, monkeypatch):
    p = H.make_pipeline(tmp_path, transcriber=LevelTranscriber(-49.0))
    p.ns["_platform"] = types.SimpleNamespace(system=lambda: "Linux")
    p.ns["HISTORY_FILE"].write_text(json.dumps([entry(-49.0) for _ in range(9)]), encoding="utf-8")
    reads = []
    monkeypatch.setattr(Q, "read_windows_input", lambda: reads.append(1))
    dictate(p)
    H.wait_for(lambda: False, timeout=0.5)
    assert reads == [] and getattr(p, "_quiet_mic", None) is None


# ── what Privacy and data says ──────────────────────────────────────────────

def test_privacy_says_on_windows_the_microphones_name_is_kept_too():
    html = " ".join((ROOT / "ui/index.html").read_text(encoding="utf-8").split())
    row = html[html.index('id="micVolumeRow"'):]
    row = row[:row.index("</div> </div> </div>")]
    assert "On a Mac" not in row
    assert "on Windows" in row and "name" in row
