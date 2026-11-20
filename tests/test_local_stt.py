"""On-device speech to text (src/local_stt.py), with the engine and the
network replaced: the setting, the model's status, downloading (failure, retry,
resume, damage, cancel), transcription (lazy load, one at a time, a dictation
before a meeting, idle unload) and removal.

The real thing (a real download, a real model) is scripts/local_stt_check.py,
which is not part of this suite.
"""
import hashlib
import io
import sys
import threading
import time
import types
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import local_stt  # noqa: E402
import local_stt_ct2  # noqa: E402
import local_stt_whispercpp  # noqa: E402

BIG = bytes(range(256)) * 4096          # 1 MiB: several chunks at the test chunk size
SMALL = b"{\"a\": 1}"


def _spec(*payloads, engine=local_stt.ENGINE_WHISPERCPP):
    files = tuple(local_stt.ModelFile(name, f"https://example.invalid/{name}", len(data),
                                      hashlib.sha256(data).hexdigest())
                  for name, data in payloads)
    return local_stt.ModelSpec(engine, "Test model", files)


class FakeResponse:
    def __init__(self, data, status=200, fail_after=None):
        self.data, self.status_code, self.fail_after = data, status, fail_after
        self.closed = False

    def iter_content(self, size):
        sent = 0
        for i in range(0, len(self.data), size):
            if self.fail_after is not None and sent >= self.fail_after:
                raise ConnectionError("the connection dropped")
            chunk = self.data[i:i + size]
            sent += len(chunk)
            yield chunk

    def close(self):
        self.closed = True


class FakeServer:
    """Serves the test model's files; honours Range unless told not to."""

    def __init__(self, files):
        self.files = dict(files)
        self.requests = []
        self.fail_after = {}        # name → bytes after which the next response drops (once)
        self.ignore_range = False
        self.down = False

    def get(self, url, headers):
        name = url.rsplit("/", 1)[1]
        self.requests.append((name, dict(headers)))
        if self.down:
            raise ConnectionError("offline")
        data = self.files[name]
        start = 0
        rng = headers.get("Range")
        if rng and not self.ignore_range:
            start = int(rng.split("=")[1].rstrip("-"))
            if start >= len(data):
                return FakeResponse(b"", status=416)
        return FakeResponse(data[start:], status=206 if start else 200,
                            fail_after=self.fail_after.pop(name, None))


class FakeEngine:
    device = "fake"
    instances = []

    def __init__(self, name="m"):
        self.name = name
        self.calls = []
        self.closed = False
        self.active = 0
        self.max_active = 0
        self.hold = 0.0
        self.order = []
        self._lock = threading.Lock()
        FakeEngine.instances.append(self)

    def transcribe(self, audio, language, prompt):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append((len(audio), language, prompt))
            self.order.append(threading.current_thread().name)
            if self.hold:
                time.sleep(self.hold)
            return [{"start": 0.0, "end": 1.0, "text": " Hello there."},
                    {"start": 1.0, "end": 2.0, "text": " General Kenobi. "}]
        finally:
            with self._lock:
                self.active -= 1

    def close(self):
        self.closed = True


@pytest.fixture
def stt(monkeypatch):
    """local_stt with a small test model, a fake server and a fake engine,
    and its module state as new."""
    monkeypatch.setenv("WAFFLER_LOCAL_STT_ENGINE", local_stt.ENGINE_WHISPERCPP)
    spec = _spec(("model.bin", BIG), ("config.json", SMALL))
    monkeypatch.setattr(local_stt, "MODELS", {"test": spec})
    monkeypatch.setattr(local_stt, "default_model", lambda: "test")
    monkeypatch.setattr(local_stt, "is_available", lambda: True)
    monkeypatch.setattr(local_stt, "_CHUNK", 64 * 1024)
    server = FakeServer({"model.bin": BIG, "config.json": SMALL})
    monkeypatch.setattr(local_stt, "_http_get", server.get)
    FakeEngine.instances = []
    loads = []

    def load(name):
        loads.append(name)
        return FakeEngine(name)
    monkeypatch.setattr(local_stt, "_load_engine", load)
    monkeypatch.setattr(local_stt, "_gate", local_stt._Gate())
    monkeypatch.setattr(local_stt, "_engine", None)
    monkeypatch.setattr(local_stt, "_engine_key", None)
    monkeypatch.setattr(local_stt, "_idle_unload_s", 0.0)
    monkeypatch.setattr(local_stt, "_idle_timer", None)
    monkeypatch.setattr(local_stt, "_dl_thread", None)
    monkeypatch.setattr(local_stt, "_dl", {"active": False, "model": "", "done": 0, "total": 0,
                                           "error": "", "cancelled": False})
    monkeypatch.setattr(local_stt, "_last", {"device": None, "load_s": None,
                                             "transcribe_s": None, "unloaded": ""})
    local_stt._dl_cancel.clear()
    ns = types.SimpleNamespace(server=server, loads=loads, spec=spec, mod=local_stt)
    yield ns
    local_stt._cancel_idle_timer()
    local_stt._dl_cancel.set()                      # stop anything a test left running
    local_stt._wait_for_download(5)
    local_stt._dl_cancel.clear()


def _wav(seconds=1.0, rate=16000, channels=1):
    n = int(seconds * rate)
    samples = (np.sin(np.arange(n) / 20.0) * 8000).astype("<i2")
    if channels > 1:
        samples = np.repeat(samples, channels)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def _folder():
    return local_stt.model_dir("test")


# ── the setting ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("stored, mode", [
    ({}, "cloud"), ({"transcription_mode": "cloud"}, "cloud"),
    ({"transcription_mode": "local"}, "local"),
    ({"transcription_mode": "LOCAL"}, "cloud"), ({"transcription_mode": True}, "cloud"),
    (None, "cloud"), ([], "cloud"),
])
def test_only_exactly_local_means_on_this_computer(stored, mode):
    assert local_stt.mode_of(stored) == mode


def test_the_mode_is_read_from_settings_json_each_time(_isolated_data_dir):
    assert local_stt.mode_is_local() is False
    (_isolated_data_dir / "settings.json").write_text('{"transcription_mode": "local"}', encoding="utf-8")
    assert local_stt.mode_is_local() is True
    (_isolated_data_dir / "settings.json").write_text("{ not json", encoding="utf-8")
    assert local_stt.mode_is_local() is False


def test_a_save_is_refused_for_an_unknown_mode_or_the_cloud_with_no_key():
    problem = local_stt.settings_problem
    assert problem({"language": "en"}) is None
    assert problem({"transcription_mode": "local"}, has_key=False) is None
    assert problem({"transcription_mode": "cloud"}, has_key=True) is None
    assert problem({"transcription_mode": "cloud"}, has_key=False) == local_stt.CLOUD_NEEDS_KEY
    assert problem({"transcription_mode": "offline"}) == local_stt.MODE_UNKNOWN
    assert problem({"transcription_mode": None}) == local_stt.MODE_UNKNOWN


# ── which engine, which model, where ─────────────────────────────────────────

def test_apple_silicon_uses_whisper_cpp_and_everything_else_faster_whisper(monkeypatch):
    monkeypatch.delenv("WAFFLER_LOCAL_STT_ENGINE", raising=False)
    monkeypatch.setattr(local_stt.sys, "platform", "darwin")
    monkeypatch.setattr(local_stt.platform, "machine", lambda: "arm64")
    assert local_stt.engine_name() == "whispercpp"
    monkeypatch.setattr(local_stt.platform, "machine", lambda: "x86_64")
    assert local_stt.engine_name() == "faster-whisper"
    monkeypatch.setattr(local_stt.sys, "platform", "win32")
    monkeypatch.setattr(local_stt.platform, "machine", lambda: "AMD64")
    assert local_stt.engine_name() == "faster-whisper"
    monkeypatch.setenv("WAFFLER_LOCAL_STT_ENGINE", "whispercpp")
    assert local_stt.engine_name() == "whispercpp"
    monkeypatch.setenv("WAFFLER_LOCAL_STT_ENGINE", "nonsense")
    assert local_stt.engine_name() == "faster-whisper"


def test_every_model_file_is_pinned_to_a_commit_with_its_size_and_hash():
    engines = set()
    for name, spec in local_stt.MODELS.items():
        engines.add(spec.engine)
        assert spec.files and spec.label
        for f in spec.files:
            repo, rest = f.url.split("/resolve/")
            assert repo.startswith("https://huggingface.co/")
            revision, file_name = rest.split("/", 1)
            assert len(revision) == 40 and int(revision, 16) >= 0, f"{name}: not a commit"
            assert file_name == f.name and "/" not in f.name
            assert f.size > 0 and len(f.sha256) == 64 and int(f.sha256, 16) >= 0
    assert engines == {"whispercpp", "faster-whisper"}
    # whisper.cpp opens one file; its engine is handed the first.
    assert all(len(s.files) == 1 for s in local_stt.MODELS.values() if s.engine == "whispercpp")


def test_the_default_model_suits_the_engine(monkeypatch):
    monkeypatch.setattr(local_stt, "_default_cache", {})
    monkeypatch.setenv("WAFFLER_LOCAL_STT_ENGINE", "whispercpp")
    assert local_stt.default_model() == "large-v3-turbo-q5"
    monkeypatch.setenv("WAFFLER_LOCAL_STT_ENGINE", "faster-whisper")
    monkeypatch.setattr(local_stt_ct2, "auto_model", lambda: "base")
    assert local_stt.default_model() == "base"
    monkeypatch.setattr(local_stt_ct2, "auto_model", lambda: "large-v3-turbo")
    assert local_stt.default_model() == "base"          # asked once: it reads hardware
    for engine in ("whispercpp", "faster-whisper"):
        monkeypatch.setenv("WAFFLER_LOCAL_STT_ENGINE", engine)
        assert local_stt.MODELS[local_stt.default_model()].engine == engine


def test_the_model_lives_in_the_data_folder_never_the_repo(stt, _isolated_data_dir):
    assert local_stt.models_dir() == _isolated_data_dir / "models"
    assert _folder() == _isolated_data_dir / "models" / "test"
    assert ROOT not in _folder().parents


def test_one_model_is_selected_the_named_one_then_a_downloaded_one_then_the_default(
        stt, monkeypatch, _isolated_data_dir):
    other = _spec(("other.bin", SMALL))
    monkeypatch.setattr(local_stt, "MODELS", {"test": stt.spec, "other": other})
    assert local_stt.selected_model() == "test"                         # the default
    assert local_stt.selected_model({"local_stt_model": "other"}) == "other"
    assert local_stt.selected_model({"local_stt_model": "nope"}) == "test"
    folder = local_stt.model_dir("other")
    folder.mkdir(parents=True)
    (folder / "other.bin").write_bytes(SMALL)
    (folder / ".ready").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(local_stt, "default_model", lambda: "test")
    assert local_stt.selected_model() == "other"                        # already here
    monkeypatch.setattr(local_stt, "MODELS", {"test": stt.spec, "other": other._replace(
        engine=local_stt.ENGINE_CT2)})
    assert local_stt.selected_model() == "test"                         # another engine's: not ours


# ── status and downloading ───────────────────────────────────────────────────

def test_status_goes_from_not_downloaded_through_downloading_to_ready(stt):
    st = local_stt.model_status()
    assert (st["state"], st["percent"], st["size_on_disk"], st["available"]) == ("not_downloaded", 0, 0, True)
    assert st["bytes_total"] == len(BIG) + len(SMALL) and st["model"] == "test"
    during = []

    def progress(percent, done, total):
        during.append((percent, done, total, local_stt.model_status()["state"]))
    r = local_stt.download(progress)
    assert r["ok"] is True and r["state"] == "ready" and r["percent"] == 100 and r["error"] == ""
    assert r["size_on_disk"] >= len(BIG) + len(SMALL)
    percents = [p for p, *_ in during]
    assert percents == sorted(set(percents)) and percents[-1] == 100 and len(percents) > 3
    assert all(state == "downloading" for *_, state in during)
    assert all(total == len(BIG) + len(SMALL) for _p, _d, total, _s in during)
    assert (_folder() / "model.bin").read_bytes() == BIG
    assert not list(_folder().glob("*.part"))
    assert local_stt.model_status()["state"] == "ready"


def test_a_second_download_of_a_ready_model_asks_the_network_for_nothing(stt):
    assert local_stt.download()["ok"]
    stt.server.requests.clear()
    seen = []
    r = local_stt.download(lambda *a: seen.append(a))
    assert r["ok"] and stt.server.requests == [] and seen[-1][0] == 100


def test_a_dropped_connection_is_reported_and_the_retry_carries_on(stt):
    stt.server.fail_after["model.bin"] = 300 * 1024
    r = local_stt.download()
    assert r["ok"] is False and r["state"] == "not_downloaded"
    assert r["error"] == local_stt.DOWNLOAD_FAILED
    part = _folder() / "model.bin.part"
    kept = part.stat().st_size
    assert 0 < kept < len(BIG) and BIG.startswith(part.read_bytes())
    assert local_stt.model_status()["error"] == local_stt.DOWNLOAD_FAILED
    assert 0 < local_stt.model_status()["percent"] < 100
    stt.server.requests.clear()
    r = local_stt.download()
    assert r["ok"] is True and r["error"] == ""
    assert stt.server.requests[0] == ("model.bin", {"Range": f"bytes={kept}-"})
    assert (_folder() / "model.bin").read_bytes() == BIG


def test_offline_is_a_plain_sentence_not_an_exception(stt):
    stt.server.down = True
    r = local_stt.download()
    assert r["ok"] is False and r["error"] == local_stt.DOWNLOAD_FAILED and r["state"] == "not_downloaded"
    stt.server.down = False
    assert local_stt.download()["ok"] is True


def test_a_server_that_ignores_the_range_starts_the_file_again(stt):
    stt.server.fail_after["model.bin"] = 200 * 1024
    assert local_stt.download()["ok"] is False
    stt.server.ignore_range = True
    assert local_stt.download()["ok"] is True
    assert (_folder() / "model.bin").read_bytes() == BIG       # not the part-file plus a whole file


def test_a_part_file_the_server_does_not_recognise_is_thrown_away(stt):
    _folder().mkdir(parents=True)
    (_folder() / "config.json.part").write_bytes(b"x" * (len(SMALL) + 50))     # too long
    (_folder() / "model.bin.part").write_bytes(b"\0" * len(BIG))                # full size, wrong bytes
    r = local_stt.download()
    assert r["ok"] is False and r["error"] == local_stt.DOWNLOAD_DAMAGED
    assert not (_folder() / "model.bin.part").exists() and not (_folder() / "model.bin").exists()
    assert local_stt.download()["ok"] is True


def test_a_damaged_download_is_thrown_away_and_never_marked_ready(stt):
    good = stt.server.files["model.bin"]
    stt.server.files["model.bin"] = good[:-1] + b"\x00"        # right size, wrong bytes
    r = local_stt.download()
    assert r["ok"] is False and r["error"] == local_stt.DOWNLOAD_DAMAGED
    assert not (_folder() / "model.bin").exists() and not (_folder() / "model.bin.part").exists()
    assert not (_folder() / ".ready").exists()
    with pytest.raises(local_stt.LocalSTTError) as e:
        local_stt.transcribe(_wav())
    assert e.value.kind == "not_downloaded"
    stt.server.files["model.bin"] = good
    assert local_stt.download()["ok"] is True


def test_a_file_cut_short_after_it_was_marked_ready_is_not_ready(stt):
    assert local_stt.download()["ok"]
    (_folder() / "model.bin").write_bytes(BIG[:1000])
    assert local_stt.model_status()["state"] == "not_downloaded"
    assert local_stt.download()["ok"] is True
    assert (_folder() / "model.bin").read_bytes() == BIG


def test_cancel_keeps_what_arrived_and_the_next_download_resumes(stt):
    def cancel_at_a_third(percent, done, total):
        if percent >= 30:
            assert local_stt.cancel_download() is True
    r = local_stt.download(cancel_at_a_third)
    assert r["ok"] is False and r["cancelled"] is True and r["error"] == ""
    assert r["state"] == "not_downloaded"
    st = local_stt.model_status()
    assert st["cancelled"] is True and 0 < st["size_on_disk"] < len(BIG)
    kept = (_folder() / "model.bin.part").stat().st_size
    assert local_stt.cancel_download() is False                # nothing running now
    stt.server.requests.clear()
    seen = []
    r = local_stt.download(lambda percent, done, total: seen.append((percent, done)))
    assert r["ok"] is True and r["cancelled"] is False
    assert stt.server.requests[0][1] == {"Range": f"bytes={kept}-"}
    assert local_stt.model_status()["cancelled"] is False
    # The bar starts where it stopped, not at nothing.
    assert seen[0][1] >= kept and seen[0][0] >= 30 and seen[-1][0] == 100


def test_progress_counts_a_later_files_part_from_the_start(stt, monkeypatch):
    """The big file is not always first (config.json, then model.bin)."""
    spec = local_stt.ModelSpec(stt.spec.engine, "Test model", tuple(reversed(stt.spec.files)))
    monkeypatch.setattr(local_stt, "MODELS", {"test": spec})
    stt.server.fail_after["model.bin"] = 400 * 1024
    assert local_stt.download()["ok"] is False
    kept = (_folder() / "model.bin.part").stat().st_size
    seen = []
    assert local_stt.download(lambda percent, done, total: seen.append(done))["ok"] is True
    assert seen[0] >= kept and seen == sorted(seen)


def test_only_one_download_runs_at_a_time(stt):
    entered, release = threading.Event(), threading.Event()
    real_get = stt.server.get

    def slow_get(url, headers):
        entered.set()
        assert release.wait(10)
        return real_get(url, headers)
    stt.mod._http_get = slow_get
    results = []
    first = local_stt.start_download(done_cb=results.append)
    assert first["ok"] and first["started"] is True
    assert entered.wait(5)
    assert local_stt.model_status()["state"] == "downloading"
    second = local_stt.download()
    assert second["ok"] is False and second["error"] == local_stt.DOWNLOAD_BUSY
    again = local_stt.start_download()
    assert again["ok"] and again["started"] is False and again["state"] == "downloading"
    release.set()
    local_stt._wait_for_download(10)
    deadline = time.time() + 5
    while not results and time.time() < deadline:
        time.sleep(0.01)
    assert results and results[0]["ok"] is True and local_stt.model_status()["state"] == "ready"


def test_no_room_on_the_disk_is_said_before_anything_is_fetched(stt, monkeypatch):
    usage = types.SimpleNamespace(total=10, used=9, free=1024)
    monkeypatch.setattr(local_stt.shutil, "disk_usage", lambda p: usage)
    r = local_stt.download()
    assert r["ok"] is False and r["error"] == local_stt.DOWNLOAD_NO_SPACE
    assert stt.server.requests == []


def test_a_progress_callback_that_raises_does_not_stop_the_download(stt):
    def boom(*a):
        raise RuntimeError("the page went away")
    assert local_stt.download(boom)["ok"] is True


def test_remove_deletes_the_files_and_releases_the_model(stt):
    assert local_stt.download()["ok"]
    assert local_stt.transcribe(_wav())
    engine = FakeEngine.instances[-1]
    r = local_stt.remove_model()
    assert r["ok"] is True and r["state"] == "not_downloaded" and r["freed_bytes"] >= len(BIG)
    assert engine.closed and not _folder().exists() and r["loaded"] is False
    with pytest.raises(local_stt.LocalSTTError) as e:
        local_stt.transcribe(_wav())
    assert e.value.kind == "not_downloaded" and str(e.value) == local_stt.NOT_DOWNLOADED
    assert local_stt.remove_model()["ok"] is True              # nothing there: still fine


def test_remove_stops_a_download_in_progress(stt):
    entered, release = threading.Event(), threading.Event()
    real_get = stt.server.get

    def slow_get(url, headers):
        entered.set()
        release.wait(10)
        return real_get(url, headers)
    stt.mod._http_get = slow_get
    local_stt.start_download()
    assert entered.wait(5)
    threading.Timer(0.05, release.set).start()
    r = local_stt.remove_model()
    assert r["ok"] is True and r["state"] == "not_downloaded" and not _folder().exists()


# ── transcription ────────────────────────────────────────────────────────────

def test_transcribe_refuses_without_the_engine_or_the_model(stt, monkeypatch):
    with pytest.raises(local_stt.LocalSTTError) as e:
        local_stt.transcribe(_wav())
    assert e.value.kind == "not_downloaded"
    monkeypatch.setattr(local_stt, "is_available", lambda: False)
    with pytest.raises(local_stt.LocalSTTError) as e:
        local_stt.transcribe(_wav())
    assert e.value.kind == "unavailable" and str(e.value) == local_stt.NOT_INSTALLED
    assert stt.loads == []


def test_the_model_is_loaded_at_the_first_transcription_and_kept(stt):
    assert local_stt.download()["ok"]
    assert stt.loads == [] and local_stt.model_status()["loaded"] is False
    assert local_stt.transcribe(_wav(1.0)) == "Hello there. General Kenobi."
    assert local_stt.transcribe(_wav(0.5), language="de", prompt="Kenobi") == "Hello there. General Kenobi."
    assert stt.loads == ["test"]
    engine = FakeEngine.instances[0]
    assert engine.calls == [(16000, None, None), (8000, "de", "Kenobi")]
    st = local_stt.model_status()
    assert st["loaded"] is True and st["device"] == "fake" and st["load_s"] is not None


@pytest.mark.parametrize("language", [None, "", "auto"])
def test_no_language_and_auto_both_let_the_model_decide(stt, language):
    assert local_stt.download()["ok"]
    local_stt.transcribe(_wav(), language=language)
    assert FakeEngine.instances[0].calls[0][1] is None


def test_wav_bytes_and_arrays_both_become_16k_mono_float(stt):
    samples = local_stt._as_samples(_wav(1.0))
    assert samples.dtype == np.float32 and len(samples) == 16000 and abs(samples).max() <= 1.0
    assert len(local_stt._as_samples(_wav(1.0, channels=2))) == 16000
    assert len(local_stt._as_samples(_wav(1.0, rate=48000))) == 16000
    assert len(local_stt._as_samples(_wav(1.0, rate=8000))) == 16000
    ints = (np.ones(100) * 16384).astype(np.int16)
    assert np.allclose(local_stt._as_samples(ints), 0.5)
    floats = np.linspace(-1, 1, 50, dtype=np.float64)
    out = local_stt._as_samples(floats)
    assert out.dtype == np.float32 and out.flags["C_CONTIGUOUS"] and len(out) == 50
    assert len(local_stt._as_samples(np.zeros((30, 2), dtype=np.float32))) == 30


def test_segments_come_back_as_the_engine_gave_them_and_empty_audio_asks_nothing(stt):
    assert local_stt.download()["ok"]
    segs = local_stt.transcribe_segments(np.zeros(16000, dtype=np.float32))
    assert [s["text"] for s in segs] == [" Hello there.", " General Kenobi. "]
    assert local_stt.transcribe_segments(np.zeros(0, dtype=np.float32)) == []
    assert len(FakeEngine.instances[0].calls) == 1


def test_an_engine_that_fails_is_a_plain_error_and_never_the_cloud(stt, monkeypatch):
    assert local_stt.download()["ok"]

    def broken(name):
        raise OSError("bad model file")
    monkeypatch.setattr(local_stt, "_load_engine", broken)
    with pytest.raises(local_stt.LocalSTTError) as e:
        local_stt.transcribe(_wav())
    assert e.value.kind == "failed" and str(e.value) == local_stt.LOAD_FAILED

    class Explodes(FakeEngine):
        def transcribe(self, audio, language, prompt):
            raise RuntimeError("decoder fell over")
    monkeypatch.setattr(local_stt, "_load_engine", lambda name: Explodes())
    with pytest.raises(local_stt.LocalSTTError) as e:
        local_stt.transcribe(_wav())
    assert str(e.value) == local_stt.TRANSCRIBE_FAILED
    # The lock was given back: the next caller is not left waiting.
    monkeypatch.setattr(local_stt, "_load_engine", lambda name: FakeEngine())
    local_stt.unload()
    assert local_stt.transcribe(_wav())


def test_dictation_and_a_meeting_at_the_same_time_take_turns(stt):
    assert local_stt.download()["ok"]
    local_stt.transcribe(_wav())                    # load
    engine = FakeEngine.instances[0]
    engine.hold = 0.02
    out, errors = [], []

    def run(background):
        try:
            for _ in range(5):
                out.append(local_stt.transcribe(_wav(0.2), background=background))
        except Exception as e:      # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=run, args=(i % 2 == 0,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors and len(out) == 30 and set(out) == {"Hello there. General Kenobi."}
    assert engine.max_active == 1                   # never two inside the model at once
    assert stt.loads == ["test"]                    # and it was loaded once


def test_a_waiting_dictation_goes_before_a_waiting_piece_of_a_meeting(stt):
    assert local_stt.download()["ok"]
    local_stt.transcribe(_wav())
    engine = FakeEngine.instances[0]
    engine.order.clear()
    engine.hold = 0.25
    first = threading.Thread(target=local_stt.transcribe, args=(_wav(0.2),),
                             kwargs={"background": True}, name="meeting-1")
    first.start()
    while not engine.active:
        time.sleep(0.005)
    waiting_meeting = threading.Thread(target=local_stt.transcribe, args=(_wav(0.2),),
                                       kwargs={"background": True}, name="meeting-2")
    waiting_meeting.start()
    time.sleep(0.05)                                # meeting-2 is waiting first
    dictation = threading.Thread(target=local_stt.transcribe, args=(_wav(0.2),), name="dictation")
    dictation.start()
    for t in (first, waiting_meeting, dictation):
        t.join(10)
    assert engine.order == ["meeting-1", "dictation", "meeting-2"]


def test_the_model_is_released_after_the_idle_period_and_loads_again(stt):
    assert local_stt.download()["ok"]
    local_stt.set_idle_unload(0.1)
    local_stt.transcribe(_wav())
    engine = FakeEngine.instances[0]
    assert local_stt.model_status()["loaded"] is True
    deadline = time.time() + 5
    while not engine.closed and time.time() < deadline:
        time.sleep(0.01)
    assert engine.closed and local_stt.model_status()["loaded"] is False
    assert local_stt._last["unloaded"] == "idle"
    assert local_stt.transcribe(_wav()) and stt.loads == ["test", "test"]
    assert FakeEngine.instances[-1].closed is False


def test_use_keeps_the_model_and_a_running_transcription_is_never_unloaded(stt):
    assert local_stt.download()["ok"]
    local_stt.set_idle_unload(0.15)
    local_stt.transcribe(_wav())
    engine = FakeEngine.instances[0]
    for _ in range(6):                              # used every 50 ms: never idle for 150
        time.sleep(0.05)
        local_stt.transcribe(_wav(0.1))
    assert engine.closed is False and stt.loads == ["test"]
    engine.hold = 0.5                               # longer than the idle period
    assert local_stt.transcribe(_wav(0.1))
    assert engine.closed is False                   # it was in use the whole time
    local_stt.set_idle_unload(0)                    # keep it for good
    time.sleep(0.3)
    assert engine.closed is False


def test_warm_loads_the_model_ahead_of_the_first_dictation_and_never_raises(stt, monkeypatch):
    assert local_stt.warm() is False and stt.loads == []       # no model: nothing to do
    assert local_stt.download()["ok"]
    assert local_stt.warm() is True and stt.loads == ["test"]
    engine = FakeEngine.instances[0]
    assert engine.calls == [(16000, "en", None)]                # one second of silence
    local_stt.transcribe(_wav())
    assert stt.loads == ["test"], "the dictation found it loaded"

    def broken(name):
        raise OSError("bad model file")
    local_stt.unload()
    monkeypatch.setattr(local_stt, "_load_engine", broken)
    assert local_stt.warm() is False
    monkeypatch.setattr(local_stt, "is_available", lambda: False)
    assert local_stt.warm() is False


def test_the_model_is_closed_before_python_goes_away(stt):
    assert local_stt.download()["ok"]
    local_stt.set_idle_unload(60)
    local_stt.transcribe(_wav())
    engine = FakeEngine.instances[0]
    local_stt._release_at_exit()
    assert engine.closed and local_stt._idle_timer is None
    # A transcription still running at exit is left alone rather than waited for.
    local_stt.transcribe(_wav())
    busy = FakeEngine.instances[-1]
    assert local_stt._gate.acquire(background=False, blocking=False)
    try:
        local_stt._release_at_exit()
        assert busy.closed is False
    finally:
        local_stt._gate.release()


def test_unload_on_request(stt):
    assert local_stt.download()["ok"]
    assert local_stt.unload() is False
    local_stt.transcribe(_wav())
    assert local_stt.unload() is True and FakeEngine.instances[0].closed
    assert local_stt.model_status()["loaded"] is False


def test_nothing_imports_an_engine_until_it_is_used():
    src = (ROOT / "src" / "local_stt.py").read_text(encoding="utf-8")
    top = src[:src.index("def mode_of")]
    assert "pywhispercpp" not in top.split('"""', 2)[2] and "faster_whisper" not in top.split('"""', 2)[2]
    for name in ("local_stt_whispercpp.py", "local_stt_ct2.py"):
        text = (ROOT / "src" / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith(("import ", "from ")):
                assert "pywhispercpp" not in line and "faster_whisper" not in line \
                    and "ctranslate2" not in line, f"{name}: {line}"


# ── the engines (their packages replaced) ────────────────────────────────────

def test_the_whisper_cpp_engine_sets_every_parameter_each_call(monkeypatch, tmp_path):
    made = []

    class Seg:
        def __init__(self, t0, t1, text, probability):
            self.t0, self.t1, self.text, self.probability = t0, t1, text, probability

    class Model:
        def __init__(self, path, **kw):
            made.append((path, kw))
            self.calls = []

        def transcribe(self, audio, **kw):
            self.calls.append(kw)
            return [Seg(0, 252, "Hi Sarah.", 0.5), Seg(302, 684, "Bye.", float("nan"))]
    pkg, mod = types.ModuleType("pywhispercpp"), types.ModuleType("pywhispercpp.model")
    mod.Model = Model
    monkeypatch.setitem(sys.modules, "pywhispercpp", pkg)
    monkeypatch.setitem(sys.modules, "pywhispercpp.model", mod)
    engine = local_stt_whispercpp.WhisperCppEngine(tmp_path / "m.bin")
    path, kw = made[0]
    assert path == str(tmp_path / "m.bin") and kw["params_sampling_strategy"] == 1
    assert kw["print_progress"] is False and kw["redirect_whispercpp_logs_to"] in (None, False)
    audio = np.zeros(1600, dtype=np.float32)
    segs = engine.transcribe(audio, None, None)
    assert segs[0] == {"start": 0.0, "end": 2.52, "text": "Hi Sarah.", "avg_logprob": np.log(0.5)}
    assert segs[1] == {"start": 3.02, "end": 6.84, "text": "Bye."}      # no probability: no guess
    engine.transcribe(audio, "de", "Kenobi, Waffler")
    engine.transcribe(audio, None, None)
    first, second, third = engine._model.calls
    assert (first["language"], first["initial_prompt"]) == ("auto", "")
    assert (second["language"], second["initial_prompt"]) == ("de", "Kenobi, Waffler")
    # Parameters stick to the model, so the third call must clear the second's.
    assert (third["language"], third["initial_prompt"]) == ("auto", "")
    assert all(c["no_context"] is True and c["temperature"] == 0.0
               and c["beam_search"]["beam_size"] == 5 for c in engine._model.calls)
    engine.close()
    assert engine._model is None


def test_whisper_cpp_start_up_lines_are_not_redirected_without_a_real_stderr(monkeypatch):
    monkeypatch.setattr(local_stt_whispercpp.sys, "stderr", None)
    assert local_stt_whispercpp._quiet_target() is False
    monkeypatch.setattr(local_stt_whispercpp.sys, "stderr", io.StringIO())
    assert local_stt_whispercpp._quiet_target() is False       # no file behind it


class _FakeWhisperModel:
    made = []
    fail_on = set()

    def __init__(self, path, **kw):
        self.path, self.kw = path, kw
        self.model = types.SimpleNamespace(unload_model=lambda: setattr(self, "unloaded", True))
        self.unloaded = False
        _FakeWhisperModel.made.append(self)

    def transcribe(self, audio, **kw):
        self.last = kw
        if self.kw["device"] in _FakeWhisperModel.fail_on:
            raise RuntimeError("CUDA failed with error out of memory")
        seg = types.SimpleNamespace(start=0.0, end=1.5, text=" Hello.", avg_logprob=-0.2,
                                    no_speech_prob=0.01)
        return iter([seg]), None


@pytest.fixture
def ct2(monkeypatch):
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = _FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    _FakeWhisperModel.made, _FakeWhisperModel.fail_on = [], set()
    monkeypatch.setattr(local_stt_ct2, "_cuda_ok", None)
    monkeypatch.setattr(local_stt_ct2, "_gpu_retry_at", 0.0)
    monkeypatch.setattr(local_stt_ct2, "_last", {"cuda_error": "", "free_vram_mb": None})
    monkeypatch.delenv("WAFFLER_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("NATTER_WHISPER_DEVICE", raising=False)
    return local_stt_ct2


def test_faster_whisper_opens_the_folder_offline_on_the_cpu(ct2, monkeypatch, tmp_path):
    monkeypatch.setattr(ct2, "_cuda_usable", lambda: (False, "no CUDA device"))
    monkeypatch.setattr(ct2, "_free_vram_mb", lambda: None)
    engine = ct2.Ct2Engine(tmp_path, "small")
    segs = engine.transcribe(np.zeros(1600, dtype=np.float32), "en", "Kenobi")
    assert segs == [{"start": 0.0, "end": 1.5, "text": " Hello.", "avg_logprob": -0.2,
                     "no_speech_prob": 0.01}]
    model = _FakeWhisperModel.made[0]
    assert model.path == str(tmp_path) and model.kw["local_files_only"] is True
    assert (model.kw["device"], model.kw["compute_type"]) == ("cpu", "int8") and engine.device == "cpu"
    assert model.last["language"] == "en" and model.last["initial_prompt"] == "Kenobi"
    assert model.last["vad_filter"] is False and model.last["condition_on_previous_text"] is False
    engine.transcribe(np.zeros(1600, dtype=np.float32), None, None)
    assert len(_FakeWhisperModel.made) == 1 and model.last["language"] is None
    engine.close()
    assert model.unloaded and engine._models == {}


def test_a_gpu_that_runs_out_of_memory_redoes_the_call_on_the_cpu(ct2, monkeypatch, tmp_path):
    monkeypatch.setattr(ct2, "_cuda_usable", lambda: (True, ""))
    monkeypatch.setattr(ct2, "_free_vram_mb", lambda: 8000)
    _FakeWhisperModel.fail_on = {"cuda"}
    engine = ct2.Ct2Engine(tmp_path, "large-v3-turbo")
    segs = engine.transcribe(np.zeros(1600, dtype=np.float32), None, None)
    assert segs and engine.device == "cpu"
    assert [(m.kw["device"], m.kw["compute_type"]) for m in _FakeWhisperModel.made] == [
        ("cuda", "float16"), ("cpu", "int8")]
    assert _FakeWhisperModel.made[0].unloaded               # the card was freed
    assert "out of memory" in ct2.status()["fallback_reason"] and ct2.status()["gpu_retry_in_s"] > 0
    engine.transcribe(np.zeros(1600, dtype=np.float32), None, None)
    assert len(_FakeWhisperModel.made) == 2                 # stays on the CPU for the cooldown


def test_the_gpu_is_used_only_with_room_and_never_when_told_not_to(ct2, monkeypatch, tmp_path):
    monkeypatch.setattr(ct2, "_cuda_usable", lambda: (True, ""))
    monkeypatch.setattr(ct2, "_free_vram_mb", lambda: 8000)
    assert ct2._plan("large-v3-turbo") == ("cuda", "float16")
    monkeypatch.setattr(ct2, "_free_vram_mb", lambda: 2600)
    assert ct2._plan("large-v3-turbo") == ("cuda", "int8_float16")
    monkeypatch.setattr(ct2, "_free_vram_mb", lambda: 1200)
    assert ct2._plan("large-v3-turbo") == ("cpu", "int8")
    assert "using the CPU" in ct2.status()["fallback_reason"]
    monkeypatch.setattr(ct2, "_gpu_retry_at", 0.0)
    monkeypatch.setattr(ct2, "_free_vram_mb", lambda: 8000)
    monkeypatch.setenv("WAFFLER_WHISPER_DEVICE", "cpu")
    assert ct2._plan("large-v3-turbo") == ("cpu", "int8")


def test_the_model_for_a_machine_is_turbo_on_a_gpu_with_room_else_small_or_base(ct2, monkeypatch):
    monkeypatch.setattr(ct2.sys, "platform", "win32")
    monkeypatch.setattr(ct2, "_cuda_usable", lambda: (True, ""))
    monkeypatch.setattr(ct2, "_free_vram_mb", lambda: 8000)
    assert ct2.auto_model() == "large-v3-turbo"
    monkeypatch.setattr(ct2, "_free_vram_mb", lambda: 500)
    monkeypatch.setattr(ct2, "_total_ram_gb", lambda: 16.0)
    monkeypatch.setattr(ct2.os, "cpu_count", lambda: 8)
    assert ct2.auto_model() == "small"
    monkeypatch.setattr(ct2, "_total_ram_gb", lambda: 8.0)
    assert ct2.auto_model() == "base"
    monkeypatch.setattr(ct2, "_cuda_usable", lambda: (False, "no CUDA device"))
    monkeypatch.setattr(ct2, "_total_ram_gb", lambda: 32.0)
    assert ct2.auto_model() == "small"
    # Every name it can give is a model local_stt can download for this engine.
    for name in ("large-v3-turbo", "small", "base"):
        assert local_stt.MODELS[name].engine == local_stt.ENGINE_CT2
