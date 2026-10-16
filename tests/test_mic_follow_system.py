"""With no microphone saved, Waffler follows the system (S12, item 1).

Found by reading the code (R1 gotcha 11), here confirmed with a test: with
nothing saved, ``get_selected_device_index()`` answered the default input's
PortAudio INDEX and ``app.py`` handed that to the recorder as if the person
had chosen it. An explicit choice wins over everything in
``audio._resolve_input_device``, so a Bluetooth default (AirPods) was opened
after all, the recorder did not follow a new default, and an index can mean
another device after PortAudio is started again. "No saved microphone" now
means ``None``: the recorder applies its normal default and its Bluetooth
avoidance.

No microphone is opened: the device queries and the streams are fakes.
"""
import ast
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import audio as audio_mod  # noqa: E402
import audio_devices  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))


class FakeSD:
    """Just enough of sounddevice for the device queries."""

    def __init__(self, names, default=0):
        self.devices = [{"name": n, "max_input_channels": 1, "max_output_channels": 0,
                         "hostapi": 0, "default_samplerate": 48000.0} for n in names]
        self.default = default

    def query_devices(self, device=None, kind=None):
        if kind == "input":
            return dict(self.devices[self.default], index=self.default)
        if device is None:
            return list(self.devices)
        return self.devices[device]


@pytest.fixture
def fake_sd(monkeypatch, tmp_path):
    sd = FakeSD(["MacBook Pro Microphone", "AirPods Pro", "Yeti"], default=1)
    monkeypatch.setattr(audio_devices, "sd", sd)
    monkeypatch.setattr(audio_devices, "_HAS_SD", True)
    monkeypatch.setattr(audio_devices, "CONFIG_FILE", tmp_path / "config.json")
    return sd


def test_nothing_saved_is_none_not_the_default_devices_index(fake_sd):
    assert not audio_devices.CONFIG_FILE.exists()
    assert audio_devices.get_selected_device_index() is None
    # The helper that answers "which index is the default" is still there for
    # the callers that do want it.
    assert audio_devices.get_default_device_index() == 1


def test_a_saved_index_is_still_read(fake_sd):
    audio_devices.set_selected_device_index(2)
    assert audio_devices.get_selected_device_index() == 2
    audio_devices.set_selected_device_index(None)
    assert audio_devices.get_selected_device_index() is None


def test_no_saved_mic_keeps_the_bluetooth_avoidance(fake_sd, monkeypatch):
    """The recorder is given None, and None means "the OS default, unless it
    is a Bluetooth headset"."""
    monkeypatch.setattr(audio_mod, "sd", fake_sd)
    assert audio_mod._resolve_input_device(audio_devices.get_selected_device_index()) == 0
    # The old behaviour: the default's index went in as an explicit choice
    # and the AirPods were opened.
    assert audio_mod._resolve_input_device(1) == 1


def test_the_pipeline_builds_its_recorder_without_a_pinned_index():
    """app.py's recorder is made by one helper. It is given no index, and a
    resolver that chooses each time a stream is opened; the old saved index is
    handed over as a function to be read, not read here."""
    node = next(n for n in _TREE.body
                if isinstance(n, ast.FunctionDef) and n.name == "_new_recorder")
    made = {}

    class Recorder:
        def __init__(self, **kw):
            made.update(kw)

    def pinned():
        pytest.fail("a pinned index is read when the recorder is built")

    ns = {"AudioRecorder": Recorder,
          "get_selected_device_index": pinned,
          "_mic_ranking": types.SimpleNamespace(
              open_resolver=lambda read_settings, read_legacy=None: ("resolver", read_settings, read_legacy)),
          "_read_settings_now": lambda: {}}
    exec(compile(ast.Module([node], []), "<app.py>", "exec"), ns)
    ns["_new_recorder"](types.SimpleNamespace(sample_rate=16000, channels=1))
    assert made["device_index"] is None
    kind, read_settings, read_legacy = made["device_resolver"]
    assert kind == "resolver" and read_settings is ns["_read_settings_now"]
    assert read_legacy is pinned
    assert made["sample_rate"] == 16000 and made["channels"] == 1


def test_the_pipeline_keeps_no_index_of_its_own():
    """WafflerPipeline.__init__ no longer reads the saved index at all."""
    pipeline = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    init = next(n for n in pipeline.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    assert "get_selected_device_index" not in ast.unparse(init)
    assert "_new_recorder(config)" in ast.unparse(init)
