"""llama.cpp's own server, fetched with the writing model on Windows
(src/llama_pack.py), so the tidy-up and meeting notes run on a PC too.

Windows has no llama-cpp-python build Waffler can ship, so "On this computer"
pasted text as it was said there, with no paragraphs. Measured on a Windows
PC with the writing model run by llama.cpp b11168's server: 0.4 to 0.8 s a
dictation on an RTX 4070 SUPER, 3.9 to 5.7 s on the processor.

No network: the release zips and the NVIDIA wheel are small files made here,
served by a fake GET that honours Range.
"""
import hashlib
import io
import json
import sys
import types
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import gpu_pack  # noqa: E402
import llama_pack  # noqa: E402
import local_llm  # noqa: E402
import local_llm_server  # noqa: E402
import local_stt  # noqa: E402


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


CPU_FILES = {"llama-server.exe": b"S" * 900, "ggml-cpu-x64.dll": b"C" * 700, "llama.dll": b"L" * 500}
CUDA_FILES = {"llama-server.exe": b"S" * 900, "ggml-cuda.dll": b"G" * 2000, "ggml-cpu-x64.dll": b"C" * 700}


class Server:
    def __init__(self):
        self.blobs, self.asked = {}, []

    def add(self, name, data):
        url = f"https://files.example.test/{name}"
        self.blobs[url] = data
        return local_stt.ModelFile(name, url, len(data), hashlib.sha256(data).hexdigest())

    def get(self, url, headers):
        self.asked.append(url)
        data = self.blobs[url]
        start = int(headers["Range"].split("=")[1].rstrip("-")) if "Range" in headers else 0
        body = data[start:]

        class Resp:
            status_code = 206 if start else 200

            def iter_content(self, size):
                for i in range(0, len(body), 256):
                    yield body[i:i + 256]

            def close(self):
                pass
        return Resp()


@pytest.fixture
def pack(monkeypatch):
    s = Server()
    cpu = s.add("llama-bX-bin-win-cpu-x64.zip", _zip({**CPU_FILES, "../evil.dll": b"E", "sub/inner.dll": b"I"}))
    cuda = s.add("llama-bX-bin-win-cuda-x64.zip", _zip(CUDA_FILES))
    cudart = s.add("nvidia_cuda_runtime_cu12-x-py3-none-win_amd64.whl", _zip({
        "nvidia/cuda_runtime/bin/cudart64_12.dll": b"R" * 300, "nvidia/cuda_runtime/include/x.h": b"h",
        "nvidia_cuda_runtime_cu12.dist-info/METADATA": b"m"}))
    monkeypatch.setattr(llama_pack, "CPU", (llama_pack.Asset(cpu),))
    monkeypatch.setattr(llama_pack, "CUDA", (llama_pack.Asset(cuda), llama_pack.Asset(cudart, "nvidia/cuda_runtime/bin/")))
    monkeypatch.setattr(gpu_pack, "card", lambda: None)
    monkeypatch.setattr(local_llm_server, "_SERVER_NAME", "llama-server.exe")
    monkeypatch.delenv(local_llm_server.SERVER_ENV, raising=False)
    return s


def _fetch(server):
    import threading
    llama_pack.fetch(lambda name, n: None, threading.Event(), get=server.get)


def test_a_pc_with_no_nvidia_card_gets_the_processor_build_and_one_with_gets_the_cuda_one(pack, monkeypatch):
    assert llama_pack.plan()[0] == "cpu" and len(llama_pack.plan()[1]) == 1
    monkeypatch.setattr(gpu_pack, "card", lambda: {"count": 1, "total_mb": 12282})
    kind, assets = llama_pack.plan()
    assert kind == "cuda" and [a.member_dir for a in assets] == ["", "nvidia/cuda_runtime/bin/"]


def test_the_real_assets_are_llama_cpps_own_release_and_nvidias_runtime():
    for a in llama_pack.CPU + llama_pack.CUDA:
        assert len(a.file.sha256) == 64 and a.file.size > 1_000_000
    assert llama_pack.CPU[0].file.url == (
        "https://github.com/ggml-org/llama.cpp/releases/download/b11168/llama-b11168-bin-win-cpu-x64.zip")
    assert llama_pack.CUDA[0].file.url.startswith("https://github.com/ggml-org/llama.cpp/releases/download/b11168/")
    assert llama_pack.CUDA[1].file.url.startswith("https://files.pythonhosted.org/packages/")


def test_the_processor_build_is_fetched_checked_and_unpacked_where_the_engine_looks(pack):
    assert llama_pack.is_ready() is False and local_llm_server.find_server() is None
    _fetch(pack)
    here = llama_pack.folder()
    assert sorted(p.name for p in here.iterdir() if p.is_file()) == sorted(list(CPU_FILES) + [".ready"])
    assert not list(here.parent.rglob("evil.dll")) and not (here / "inner.dll").exists()
    assert not (here / ".download").exists(), "the zip is deleted"
    assert llama_pack.is_ready() and local_llm_server.find_server() == here / "llama-server.exe"
    assert json.loads((here / ".ready").read_text())["engine"] == "cpu"


def test_with_a_card_the_cuda_build_comes_with_nvidias_runtime(pack, monkeypatch):
    monkeypatch.setattr(gpu_pack, "card", lambda: {"count": 1, "total_mb": 12282})
    _fetch(pack)
    here = llama_pack.folder()
    assert (here / "ggml-cuda.dll").exists() and (here / "cudart64_12.dll").read_bytes() == b"R" * 300
    assert not (here / "x.h").exists()


def test_a_release_zip_with_its_files_in_one_folder_is_unpacked_the_same(pack, monkeypatch):
    nested = pack.add("nested.zip", _zip({f"llama-b1/{n}": d for n, d in CPU_FILES.items()}))
    monkeypatch.setattr(llama_pack, "CPU", (llama_pack.Asset(nested),))
    _fetch(pack)
    assert (llama_pack.folder() / "llama-server.exe").read_bytes() == CPU_FILES["llama-server.exe"]


def test_remove_takes_only_what_it_put_there(pack):
    _fetch(pack)
    (llama_pack.folder() / "mine.txt").write_text("mine")
    freed = llama_pack.remove()
    assert freed >= sum(len(d) for d in CPU_FILES.values())
    assert [p.name for p in llama_pack.folder().iterdir()] == ["mine.txt"] and not llama_pack.is_ready()


# ── with the writing model, on Windows ───────────────────────────────────────

@pytest.fixture
def windows_llm(pack, monkeypatch):
    model = b"M" * 5000
    spec = local_llm.ModelSpec("Test writer", (pack.add("model.gguf", model),))
    monkeypatch.setattr(local_llm, "MODELS", {"test": spec})
    monkeypatch.setattr(local_llm, "DEFAULT_MODEL", "test")
    monkeypatch.setattr(local_llm, "_http_get", pack.get)
    monkeypatch.setattr(local_llm.sys, "platform", "win32")
    real_find = local_llm.importlib.util.find_spec
    monkeypatch.setattr(local_llm.importlib.util, "find_spec",
                        lambda name, *a: None if name == "llama_cpp" else real_find(name, *a))
    monkeypatch.setattr(llama_pack, "supported", lambda: True)
    monkeypatch.setattr(local_llm, "_available", [])
    monkeypatch.setattr(local_llm, "describe_machine", lambda: local_stt.Machine("Windows", "AMD64", 28, 32.0))
    monkeypatch.setattr(local_llm, "_card_mb", lambda: None)
    monkeypatch.setattr(local_llm, "_dl", {"active": False, "model": "", "done": 0, "total": 0,
                                           "error": "", "cancelled": False})
    local_llm._dl_cancel.clear()
    return types.SimpleNamespace(spec=spec, server=pack)


def test_on_windows_one_download_brings_the_model_and_the_engine(windows_llm):
    before = local_llm.model_status()
    engine = sum(a.file.size for a in llama_pack.CPU)
    assert before["available"] is True and before["state"] == "not_downloaded"
    assert before["bytes_total"] == windows_llm.spec.size + engine, "the size said includes the engine"
    assert (before["suits_machine"], before["machine_reason"]) == (True, "cpu")
    seen = []
    result = local_llm.download(lambda percent, done, total: seen.append((percent, total)))
    assert result["ok"] is True and result["state"] == "ready"
    assert local_llm.is_ready() and local_llm._engine_kind() == "server"
    assert seen[-1] == (100, windows_llm.spec.size + engine)
    assert [u.rsplit("/", 1)[1] for u in windows_llm.server.asked] == ["model.gguf", "llama-bX-bin-win-cpu-x64.zip"]


def test_on_windows_the_model_without_its_engine_is_not_ready(windows_llm, monkeypatch):
    local_llm.download()
    assert local_llm.is_ready()
    llama_pack.remove()
    local_llm.forget_engine()
    assert local_llm.is_ready() is False, "a model with nothing to run it is not used"
    assert local_llm.model_status()["state"] == "not_downloaded"
    asked = len(windows_llm.server.asked)
    assert local_llm.download()["state"] == "ready"
    assert [u.rsplit("/", 1)[1] for u in windows_llm.server.asked[asked:]] == ["llama-bX-bin-win-cpu-x64.zip"], \
        "only the engine comes down again"


def test_on_windows_removing_the_model_removes_its_engine_too(windows_llm):
    local_llm.download()
    result = local_llm.remove_model()
    assert result["ok"] and not llama_pack.is_ready() and local_llm_server.find_server() is None
    assert result["freed_bytes"] >= windows_llm.spec.size + sum(len(d) for d in CPU_FILES.values())


# ── what Settings says on Windows ────────────────────────────────────────────

sys.path.insert(0, str(ROOT / "tests"))
from test_few_words import words  # noqa: E402
from test_ui_logic import js, needs_node  # noqa: E402


@needs_node
def test_settings_says_how_quick_it_is_on_windows_and_warns_on_the_processor():
    llm = {"ok": True, "available": True, "suits_machine": True, "state": "not_downloaded",
           "bytes_total": 5154941280 + 18567853}
    view = lambda reason: js(f"L.writingRowView({json.dumps({**llm, 'machine_reason': reason})}, false)")
    card, cpu, mac = view("nvidia"), view("cpu"), view("apple_silicon")
    assert "NVIDIA graphics card" in card["how"] and "under a second" in card["how"]
    assert cpu["about"] == "Optional. Slower here, but free and private."
    assert "four to six seconds" in cpu["how"] and "nothing leaves this computer" in cpu["how"]
    assert mac["how"].startswith("The writing model is for an Apple Silicon Mac"), "the Mac's words are its own"
    assert card["download"] == cpu["download"] == "Download (5.2 GB)"
    for v in (card, cpu, mac):
        assert len(words(v["about"])) <= 9 and len(words(v["download"])) <= 9
