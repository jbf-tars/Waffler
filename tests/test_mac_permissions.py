"""Setup's Mac permission prompts and the Fn key check (src/mac_permissions.py).

OB6: each Allow button calls the prompt macOS provides, so Waffler is already
listed and the user flips one switch, instead of hunting for Waffler.app with
the + button. OB7: when the Fn key also opens the emoji picker (or another
job), setup says so; it never changes the setting. Fakes stand in for
AVFoundation, CoreGraphics, ApplicationServices and `defaults`, so these run
offline on any computer.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import mac_permissions as mp  # noqa: E402


class Runner:
    """Records commands; answers `defaults read` with a set value."""

    def __init__(self, stdout="", returncode=0):
        self.calls = []
        self.stdout, self.returncode = stdout, returncode

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return types.SimpleNamespace(stdout=self.stdout, returncode=self.returncode)

    def opened(self):
        return [c[1] for c in self.calls if c[0] == "/usr/bin/open"]


def fake_avf(status):
    asked = []

    class AVCaptureDevice:
        @staticmethod
        def authorizationStatusForMediaType_(media):
            assert media == "soun"
            return status

        @staticmethod
        def requestAccessForMediaType_completionHandler_(media, handler):
            asked.append(media)

    return types.SimpleNamespace(AVCaptureDevice=AVCaptureDevice, AVMediaTypeAudio="soun"), asked


# ── Microphone ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code, status", [(0, "not_asked"), (1, "restricted"), (2, "denied"), (3, "granted")])
def test_microphone_status_reads_avfoundation(code, status):
    avf, _ = fake_avf(code)
    assert mp.microphone_status(avf, platform="darwin") == status


def test_allow_microphone_shows_the_real_prompt_the_first_time():
    avf, asked = fake_avf(0)
    run = Runner()
    r = mp.request_microphone(avf, platform="darwin", runner=run)
    assert r["prompted"] is True and asked == ["soun"]
    assert run.opened() == []


def test_a_refused_microphone_opens_its_pane_instead():
    """macOS never shows the prompt again after a No."""
    avf, asked = fake_avf(2)
    run = Runner()
    r = mp.request_microphone(avf, platform="darwin", runner=run)
    assert asked == [] and r["opened_settings"] is True
    assert run.opened() == [mp.PANES["microphone"]]


def test_microphone_is_not_asked_off_a_mac():
    assert mp.microphone_status(platform="win32") == "not_applicable"
    assert mp.request_microphone(platform="win32")["prompted"] is False


# ── Keyboard (Input Monitoring) ──────────────────────────────────────────────

class FakeCG:
    def __init__(self, preflight=False, request=False):
        self.preflight, self.request, self.requests = preflight, request, 0

    def CGPreflightListenEventAccess(self):
        return self.preflight

    def CGRequestListenEventAccess(self):
        self.requests += 1
        return self.request


def test_allow_keyboard_calls_cg_request_listen_event_access():
    cg, run = FakeCG(), Runner()
    r = mp.request_input_monitoring(cg, platform="darwin", runner=run)
    assert cg.requests == 1 and r["prompted"] is True
    assert run.opened() == []


def test_keyboard_already_allowed_asks_nothing():
    cg = FakeCG(preflight=True)
    assert mp.request_input_monitoring(cg, platform="darwin")["granted"] is True
    assert cg.requests == 0


def test_a_second_press_after_a_refusal_opens_the_pane():
    cg, run = FakeCG(), Runner()
    r = mp.request_input_monitoring(cg, platform="darwin", runner=run, already_asked=True)
    assert r["opened_settings"] is True
    assert run.opened() == [mp.PANES["input_monitoring"]]


# ── Typing for you (Accessibility) ───────────────────────────────────────────

def test_allow_typing_asks_with_the_prompt_option():
    seen = []
    hi = types.SimpleNamespace(kAXTrustedCheckOptionPrompt="AXTrustedCheckOptionPrompt",
                               AXIsProcessTrustedWithOptions=lambda opts: seen.append(opts) or False)
    r = mp.request_accessibility(hi, platform="darwin", runner=Runner())
    assert seen == [{"AXTrustedCheckOptionPrompt": True}]
    assert r["prompted"] is True and r["granted"] is False


def test_typing_already_allowed():
    hi = types.SimpleNamespace(kAXTrustedCheckOptionPrompt="p", AXIsProcessTrustedWithOptions=lambda o: True)
    assert mp.request_accessibility(hi, platform="darwin")["granted"] is True


def test_a_broken_binding_still_gets_the_user_to_the_pane():
    def boom(opts):
        raise RuntimeError("no binding")
    run = Runner()
    hi = types.SimpleNamespace(kAXTrustedCheckOptionPrompt="p", AXIsProcessTrustedWithOptions=boom)
    r = mp.request_accessibility(hi, platform="darwin", runner=run)
    assert r["opened_settings"] is True and run.opened() == [mp.PANES["accessibility"]]


# ── The Fn (Globe) key ───────────────────────────────────────────────────────

@pytest.mark.parametrize("stdout, rc, usage", [("0\n", 0, 0), ("2\n", 0, 2), ("1", 0, 1), ("", 1, None), ("junk", 0, None)])
def test_fn_usage_is_read_from_defaults(stdout, rc, usage):
    run = Runner(stdout, rc)
    assert mp.read_fn_usage(run, platform="darwin") == usage
    assert run.calls == [["/usr/bin/defaults", "read", "com.apple.HIToolbox", "AppleFnUsageType"]]


def test_fn_usage_is_never_written():
    run = Runner("2")
    mp.read_fn_usage(run, platform="darwin")
    assert all("write" not in c for c in run.calls)


@pytest.mark.parametrize("usage, says", [
    (1, "switches the input source"), (2, "opens the emoji picker"),
    (3, "starts Apple's dictation"), (None, "does something else too"),
])
def test_a_busy_fn_key_is_named(usage, says):
    v = mp.fn_conflict(usage, ["fn"])
    assert v["conflict"] is True
    assert v["title"] == f"Your Fn key also {says}."
    assert '"Do Nothing"' in v["detail"]


def test_no_warning_when_fn_does_nothing_or_is_not_the_hotkey():
    assert mp.fn_conflict(0, ["fn"])["conflict"] is False
    assert mp.fn_conflict(2, ["cmd", "shift"])["conflict"] is False
    assert mp.read_fn_usage(Runner("2"), platform="win32") == mp.FN_DO_NOTHING


def test_messages_are_plain():
    for usage in (1, 2, 3, None):
        v = mp.fn_conflict(usage, ["fn"])
        assert chr(0x2014) not in v["title"] + v["detail"]
        assert "AppleFnUsageType" not in v["title"] + v["detail"]
