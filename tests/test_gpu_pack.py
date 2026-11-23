"""NVIDIA's libraries, fetched on one click, so dictation on this computer runs
on an NVIDIA card (src/gpu_pack.py).

Seen on a Windows 11 PC with an RTX 4070 SUPER (Nov 2026): "On this computer"
ran on the processor because cublas64_12.dll was not found (the packaged app
ships without NVIDIA's libraries), so real dictations took 2.4 to 4.5 s to
write down. With the libraries in <data folder>/gpu the next one took 0.43 s.

No network here: the wheels are small zip files made in the test, served by a
fake GET that honours Range, and the card and the engine are stand-ins.
"""
import hashlib
import io
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import gpu_pack  # noqa: E402
import local_stt  # noqa: E402
import local_stt_ct2  # noqa: E402

CUBLAS = {"cublas64_12.dll": b"B" * 3000, "cublasLt64_12.dll": b"L" * 5000, "nvblas64_12.dll": b"N" * 100}
CUDNN = {"cudnn64_9.dll": b"D" * 700, "cudnn_ops64_9.dll": b"O" * 2000, "cudnn_cnn64_9.dll": b"C" * 1500}


def _wheel(member_dir, files, extra=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(member_dir + name, data)
        for name, data in extra:
            z.writestr(name, data)
        z.writestr("nvidia_x-1.0.dist-info/METADATA", "Name: x")
    return buf.getvalue()


class Server:
    """Serves the fake wheels; can cut a file short, or send other bytes."""

    def __init__(self, blobs):
        self.blobs = blobs                 # url -> bytes
        self.asked = []                    # (url, headers)
        self.cut_after = None              # bytes, then the connection drops
        self.wrong = False

    def get(self, url, headers):
        self.asked.append((url, dict(headers)))
        data = self.blobs[url]
        if self.wrong:
            data = bytes(len(data))
        start = int(headers["Range"].split("=")[1].rstrip("-")) if "Range" in headers else 0
        body, cut = data[start:], self.cut_after
        server = self

        class Resp:
            status_code = 206 if start else 200

            def iter_content(self, size):
                sent = 0
                for i in range(0, len(body), 512):
                    if cut is not None and sent >= cut:
                        server.cut_after = None
                        raise ConnectionError("dropped")
                    sent += len(body[i:i + 512])
                    yield body[i:i + 512]

            def close(self):
                pass
        return Resp()


class FakeCt2:
    def __init__(self):
        self.loads = False
        self.arrived = 0

    def _cuda_usable(self):
        return (True, "") if self.loads else (False, "cublas64_12.dll not found")

    def gpu_libraries_arrived(self):
        self.arrived += 1


@pytest.fixture
def pack(monkeypatch, tmp_path):
    blobs, wheels = {}, []
    for name, member, files, extra in (
            ("nvidia_cublas_cu12-test-py3-none-win_amd64.whl", "nvidia/cublas/bin/", CUBLAS, ()),
            ("nvidia_cudnn_cu12-test-py3-none-win_amd64.whl", "nvidia/cudnn/bin/", CUDNN,
             (("nvidia/cudnn/bin/../../../evil.dll", b"E"), ("nvidia/cudnn/bin/sub/inner.dll", b"I"),
              ("nvidia/cudnn/include/cudnn.h", b"H")))):
        data = _wheel(member, files, extra)
        url = f"https://files.example.test/{name}"
        blobs[url] = data
        wheels.append(gpu_pack.Wheel(local_stt.ModelFile(name, url, len(data), hashlib.sha256(data).hexdigest()),
                                     member))
    server = Server(blobs)
    ct2 = FakeCt2()
    monkeypatch.setattr(gpu_pack, "WHEELS", tuple(wheels))
    monkeypatch.setattr(gpu_pack, "DOWNLOAD_BYTES", sum(w.file.size for w in wheels))
    monkeypatch.setattr(gpu_pack, "UNPACKED_BYTES", 20_000)
    monkeypatch.setattr(gpu_pack, "_SPACE_MARGIN", 0)
    monkeypatch.setattr(gpu_pack, "_http_get", server.get)
    monkeypatch.setattr(gpu_pack, "_read_card", lambda: {"count": 1, "total_mb": 12282})
    monkeypatch.setattr(gpu_pack, "_ct2", lambda: ct2)
    monkeypatch.setattr(gpu_pack, "_card_cache", [])
    monkeypatch.setattr(gpu_pack, "_elsewhere", [])
    monkeypatch.setattr(gpu_pack, "_dl", {"active": False, "done": 0, "error": "", "cancelled": False})
    server.ct2 = ct2
    return server


def _dlls_here():
    return sorted(p.name for p in gpu_pack.gpu_dir().glob("*.dll"))


# ── offered only where it helps ──────────────────────────────────────────────

def test_with_no_nvidia_card_nothing_is_offered_and_nothing_is_fetched(pack, monkeypatch):
    monkeypatch.setattr(gpu_pack, "_read_card", lambda: None)
    s = gpu_pack.status()
    assert (s["offered"], s["state"]) == (False, "no_card")
    r = gpu_pack.download()
    assert r["ok"] is False and r["error"] == gpu_pack.NOT_OFFERED and pack.asked == []


def test_when_the_libraries_already_load_nothing_is_offered_or_fetched(pack):
    pack.ct2.loads = True
    s = gpu_pack.status()
    assert (s["offered"], s["state"]) == (False, "not_needed")
    r = gpu_pack.download()
    assert r.get("nothing_to_do") and pack.asked == [] and not gpu_pack.gpu_dir().exists()


def test_a_gpu_folder_linked_there_by_hand_is_left_alone(pack, tmp_path):
    winapi = pytest.importorskip("_winapi")
    if not hasattr(winapi, "CreateJunction"):
        pytest.skip("no junctions here")
    theirs = tmp_path / "someone-elses-gpu"
    theirs.mkdir()
    (theirs / "cublas64_12.dll").write_bytes(b"theirs")
    gpu_pack.gpu_dir().parent.mkdir(parents=True, exist_ok=True)
    winapi.CreateJunction(str(theirs), str(gpu_pack.gpu_dir()))
    assert gpu_pack.status()["state"] == "not_needed"
    assert gpu_pack.download().get("nothing_to_do") and pack.asked == []
    gpu_pack.remove()
    assert (theirs / "cublas64_12.dll").read_bytes() == b"theirs", "nothing of theirs is touched"


def test_a_card_too_small_for_the_small_model_is_not_offered(monkeypatch):
    monkeypatch.setattr(gpu_pack.sys, "platform", "win32")
    monkeypatch.setattr(local_stt, "engine_name", lambda: local_stt.ENGINE_CT2)
    monkeypatch.setattr(local_stt, "is_available", lambda: True)
    monkeypatch.setitem(sys.modules, "ctranslate2", type(sys)("ctranslate2"))
    sys.modules["ctranslate2"].get_cuda_device_count = lambda: 1
    monkeypatch.setattr(gpu_pack, "_total_vram_mb", lambda: 1024)
    assert gpu_pack._read_card() is None
    monkeypatch.setattr(gpu_pack, "_total_vram_mb", lambda: 12282)
    assert gpu_pack._read_card() == {"count": 1, "total_mb": 12282}
    sys.modules["ctranslate2"].get_cuda_device_count = lambda: 0
    assert gpu_pack._read_card() is None


# ── fetched, checked, unpacked ───────────────────────────────────────────────

def test_one_download_fetches_both_wheels_checks_them_and_keeps_only_the_dlls(pack):
    s = gpu_pack.status()
    assert (s["offered"], s["state"], s["bytes_total"]) == (True, "not_downloaded", gpu_pack.DOWNLOAD_BYTES)
    seen = []
    r = gpu_pack.download(lambda percent, done, total: seen.append(percent))
    assert r["ok"] is True and r["state"] == "ready"
    assert _dlls_here() == sorted([n for n in CUBLAS if n != "nvblas64_12.dll"] + list(CUDNN))
    for name, data in {**CUBLAS, **CUDNN}.items():
        if name != "nvblas64_12.dll":
            assert (gpu_pack.gpu_dir() / name).read_bytes() == data
    assert not (gpu_pack.gpu_dir() / ".download").exists(), "the wheels are deleted"
    assert not list(gpu_pack.gpu_dir().parent.rglob("evil.dll")) and not (gpu_pack.gpu_dir() / "inner.dll").exists()
    assert seen[-1] == 100 and seen == sorted(seen)
    assert pack.ct2.arrived == 1, "the engine looks again, with no restart"
    assert json.loads((gpu_pack.gpu_dir() / ".ready").read_text())["files"]["cublas64_12.dll"] == 3000


def test_the_machine_is_read_again_once_the_files_are_here(pack, monkeypatch):
    local_stt._machine_cache[:] = ["an old reading"]
    local_stt._default_cache["faster-whisper"] = "small"
    gpu_pack.download()
    assert local_stt._machine_cache == [] and local_stt._default_cache == {}


def test_a_download_cut_short_carries_on_from_where_it_stopped(pack):
    pack.cut_after = 2048
    r = gpu_pack.download()
    assert r["ok"] is False and r["error"] == gpu_pack.DOWNLOAD_FAILED and r["state"] == "not_downloaded"
    assert 0 < gpu_pack.status()["bytes_done"] < gpu_pack.DOWNLOAD_BYTES
    asked = len(pack.asked)
    assert gpu_pack.download()["state"] == "ready"
    assert "Range" in pack.asked[asked][1], "the second try asks for the rest only"


def test_a_damaged_download_is_thrown_away(pack):
    pack.wrong = True
    r = gpu_pack.download()
    assert r["ok"] is False and r["error"] == gpu_pack.DOWNLOAD_DAMAGED
    assert not list((gpu_pack.gpu_dir() / ".download").glob("*.part")) and _dlls_here() == []
    pack.wrong = False
    assert gpu_pack.download()["state"] == "ready"


def test_not_enough_disk_space_is_said_before_anything_is_fetched(pack, monkeypatch):
    monkeypatch.setattr(gpu_pack.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10, 9, 1))
    r = gpu_pack.download()
    assert r["error"] == gpu_pack.NO_SPACE and pack.asked == []


def test_stop_keeps_what_arrived_and_a_second_download_finishes_it(pack, monkeypatch):
    real = gpu_pack._note

    def stop_part_way(done, cb, last):
        real(done, cb, last)
        if 0 < done < gpu_pack.DOWNLOAD_BYTES:
            gpu_pack.cancel_download()
    monkeypatch.setattr(gpu_pack, "_note", stop_part_way)
    r = gpu_pack.download()
    assert r["cancelled"] is True and r["state"] == "not_downloaded"
    monkeypatch.setattr(gpu_pack, "_note", real)
    assert gpu_pack.download()["state"] == "ready"


def test_the_download_runs_on_its_own_thread_and_says_when_it_ends(pack):
    ended = []
    first = gpu_pack.start_download(done_cb=ended.append)
    assert first["ok"] and first["started"] is True
    gpu_pack._thread.join(10)
    assert ended and ended[0]["state"] == "ready" and gpu_pack.status()["state"] == "ready"


# ── removing ─────────────────────────────────────────────────────────────────

def test_remove_deletes_only_what_the_pack_put_there(pack):
    gpu_pack.download()
    (gpu_pack.gpu_dir() / "notes.txt").write_text("mine")
    r = gpu_pack.remove()
    assert r["ok"] and r["pending"] is False and r["freed_bytes"] > 0
    assert _dlls_here() == [] and (gpu_pack.gpu_dir() / "notes.txt").read_text() == "mine"
    assert gpu_pack.status()["state"] == "not_downloaded"


def test_a_file_in_use_is_deleted_at_the_next_start(pack, monkeypatch):
    gpu_pack.download()
    real_unlink = Path.unlink

    def held(self, *a, **k):
        if self.name == "cudnn_ops64_9.dll":
            raise PermissionError(13, "in use")
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", held)
    r = gpu_pack.remove()
    assert r["pending"] is True and r["note"] == gpu_pack.IN_USE
    assert _dlls_here() == ["cudnn_ops64_9.dll"] and gpu_pack.status()["state"] == "not_downloaded"
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert gpu_pack.finish_pending_removal() is True
    assert _dlls_here() == [] and not (gpu_pack.gpu_dir() / ".remove").exists()


# ── the engine ───────────────────────────────────────────────────────────────

def test_the_engine_tries_the_card_again_when_the_files_arrive(monkeypatch):
    added = []
    monkeypatch.setattr(local_stt_ct2, "_cuda_ok", False)
    monkeypatch.setattr(local_stt_ct2, "_gpu_retry_at", 10 ** 9)
    monkeypatch.setattr(local_stt_ct2, "_dll_dirs_added", True)
    monkeypatch.setattr(local_stt_ct2, "_add_nvidia_dll_dirs",
                        lambda: added.append(local_stt_ct2._dll_dirs_added))
    local_stt_ct2.gpu_libraries_arrived()
    assert added == [False], "the folders are looked for again"
    assert local_stt_ct2._cuda_ok is None and local_stt_ct2._gpu_retry_at == 0.0


def test_the_pinned_wheels_are_nvidias_own_for_windows():
    names = [w.file.name for w in gpu_pack.WHEELS]
    assert names[0].startswith("nvidia_cublas_cu12-12.") and names[1].startswith("nvidia_cudnn_cu12-9.")
    for w in gpu_pack.WHEELS:
        assert w.file.name.endswith("-py3-none-win_amd64.whl")
        assert w.file.url.startswith("https://files.pythonhosted.org/packages/") and w.file.url.endswith(w.file.name)
        assert len(w.file.sha256) == 64 and int(w.file.sha256, 16) >= 0 and w.file.size > 100_000_000
    assert gpu_pack.DOWNLOAD_BYTES == sum(w.file.size for w in gpu_pack.WHEELS)


# ── what the page says ───────────────────────────────────────────────────────

sys.path.insert(0, str(ROOT / "tests"))
from test_few_words import words  # noqa: E402
from test_ui_logic import js, needs_node  # noqa: E402

OFFERED = {"ok": True, "offered": True, "state": "not_downloaded", "bytes_total": 1249463701,
           "percent": 0, "bytes_done": 0, "size_on_disk": 0, "error": ""}


@needs_node
def test_the_row_says_little_and_offers_the_one_button_that_fits():
    views = [js(f"L.gpuRowView({json.dumps({**OFFERED, **over})})") for over in (
        {}, {"state": "downloading", "percent": 42, "bytes_done": 524000000},
        {"state": "ready", "size_on_disk": 1855393376}, {"bytes_done": 9000000},
        {"error": "The graphics card files couldn't be downloaded."})]
    assert [v["state"] for v in views] == ["", "Downloading, 42% of 1.2 GB", "Ready, 1.9 GB",
                                           "Stopped part-way. It carries on from there.",
                                           "It didn\u2019t download. Try again."]
    assert [(v["download"], v["stop"], v["remove"]) for v in views] == [
        ("Download (1.2 GB)", False, False), ("", True, False), ("", False, True),
        ("Download (1.2 GB)", False, False), ("Download (1.2 GB)", False, False)]
    assert views[2]["about"] == "On. Speech runs on your graphics card."
    for v in views:                      # Settings stays few words (tests/test_few_words.py)
        assert v["show"] and v["title"] == "Use your graphics card" and len(words(v["title"])) <= 4
        for line in (v["about"], v["state"], v["download"]):
            assert len(words(line)) <= 9, line
            assert not re.search(r"\b(model|CUDA|cuBLAS|cuDNN|DLL|GPU|VRAM)\b", line), line


@needs_node
def test_the_row_is_not_shown_where_it_would_not_help():
    for g in (None, {"ok": False}, {**OFFERED, "offered": False, "state": "no_card"},
              {**OFFERED, "offered": False, "state": "not_needed"}):
        assert js(f"L.gpuRowView({json.dumps(g)})")["show"] is False, g


@needs_node
def test_on_the_processor_the_card_says_slower_but_free_and_private():
    stt = {"ok": True, "available": True, "state": "ready"}
    line = lambda reason, g=None: js(f"L.localCardLine({json.dumps({**stt, 'machine_reason': reason})}, {json.dumps(g)})")
    assert line("cpu") == line("cpu", OFFERED) == "Slower on this computer, but free and private."
    assert line("small_machine") == "Slow and less accurate here, but free and private."
    # Where it runs on the graphics there is nothing to warn of.
    for fast in (line("apple_silicon"), line("nvidia"), line("cpu", {**OFFERED, "state": "ready"}),
                 line("cpu", {**OFFERED, "offered": False, "state": "not_needed"}), line("unknown")):
        assert fast == "Private. One download."
    assert js("L.localCardLine({available: false, machine_reason: 'cpu'}, null)") == "Private. One download."
    for reason in ("cpu", "small_machine", "apple_silicon"):
        assert len(words(line(reason))) <= 9


@needs_node
def test_setup_tells_a_processor_machine_before_it_chooses():
    stt = {"ok": True, "available": True, "state": "not_downloaded", "bytes_total": 483546902}
    cpu = js(f"L.setupLocalView({json.dumps({**stt, 'suits_machine': True, 'machine_reason': 'cpu'})}, '')")
    small = js(f"L.setupLocalView({json.dumps({**stt, 'suits_machine': False, 'machine_reason': 'small_machine'})}, '')")
    mac = js(f"L.setupLocalView({json.dumps({**stt, 'suits_machine': True, 'machine_reason': 'apple_silicon'})}, '')")
    assert (cpu["slow"], cpu["warn"]) == (False, "Slower on this computer, but free and private.")
    assert (small["slow"], small["warn"]) == (True, ""), "a machine too small keeps its own sentence"
    assert (mac["slow"], mac["warn"]) == (False, "")


def test_the_page_makes_the_row_only_from_what_it_is_told():
    """The page as written says nothing of the card (Settings stays two
    choices of few words); howitruns.js makes the row from gpuRowView."""
    page = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "hirGpu" not in page and 'id="hirLocalD"' in page
    code = (ROOT / "ui" / "howitruns.js").read_text(encoding="utf-8")
    for name in ("gpu_pack_status", "gpu_pack_download", "gpu_pack_cancel", "gpu_pack_remove",
                 "L.gpuRowView(g)", "L.localCardLine(last, lastGpu)", "window.waffler_gpu_pack_changed"):
        assert name in code, name
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    for name in ("def gpu_pack_status", "def gpu_pack_download", "def gpu_pack_cancel", "def gpu_pack_remove",
                 "_gpu_pack.finish_pending_removal()"):
        assert name in app, name
