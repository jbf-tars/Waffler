"""A release build carries the speech ENGINE and never the MODEL.

requirements*.txt and the two PyInstaller specs, read as text, and the specs'
on-device block run with PyInstaller's helpers replaced (the build tool is not
installed where the tests run). The real builds are not run here.
"""
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import local_stt  # noqa: E402

SPECS = ("Waffler_mac.spec", "Waffler_windows.spec")


def _text(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _requirements(name):
    return [line.strip() for line in _text(name).splitlines()
            if line.strip() and not line.strip().startswith("#")]


def test_the_mac_requirements_install_the_engine_for_the_machine():
    lines = _requirements("requirements.txt")
    cpp = next(line for line in lines if line.startswith("pywhispercpp"))
    fw = next(line for line in lines if line.startswith("faster-whisper"))
    assert 'platform_machine == "arm64"' in cpp
    # 1.5.1 is the first version with the calls src/local_stt_whispercpp.py makes.
    assert re.match(r"pywhispercpp>=1\.5\.1\b", cpp)
    # Intel only, and not where its onnxruntime dependency has no wheel: without
    # the marker `pip install -r requirements.txt` fails outright there.
    assert 'platform_machine != "arm64"' in fw and 'python_version < "3.14"' in fw


def test_the_windows_requirements_install_faster_whisper():
    lines = _requirements("requirements_windows.txt")
    assert any(line.startswith("faster-whisper>=") for line in lines)
    assert not any(line.startswith("pywhispercpp") for line in lines)


def test_no_requirement_brings_a_heavier_engine_back():
    for name in ("requirements.txt", "requirements_windows.txt"):
        names = {re.split(r"[<>=;\s]", line, maxsplit=1)[0].lower() for line in _requirements(name)}
        assert not names & {"mlx-whisper", "mlx", "torch", "openai-whisper"}, name


@pytest.mark.parametrize("spec", SPECS)
def test_the_spec_names_the_engine_and_never_bundles_a_model(spec):
    text = _text(spec)
    assert "*_STT_HIDDEN," in text, "the engine is imported inside functions: it must be named"
    assert "'onnxruntime'," in text[text.index("excludes=["):], "85 MB that is never imported"
    datas = text[text.index("datas="):text.index("hiddenimports=[")]
    for word in ("models", "ggml", ".bin", "model.bin"):
        assert word not in datas, f"{spec} must not bundle a model ({word})"
    for model in local_stt.MODELS.values():
        big = max(model.files, key=lambda f: f.size)
        assert big.name not in text, f"{spec} names the model file {big.name}"


def _run_block(spec, monkeypatch, installed):
    """The spec's on-device block, run with PyInstaller's helpers replaced."""
    text = _text(spec)
    block = text[text.index("# ── On-device speech to text (src/local_stt.py)"):text.index("a = Analysis(")]
    hooks = types.ModuleType("PyInstaller.utils.hooks")
    hooks.copy_metadata = lambda pkg: (_ for _ in ()).throw(RuntimeError("no metadata")) \
        if pkg not in installed else [(f"/site/{pkg}.dist-info", f"{pkg}.dist-info")]
    hooks.collect_dynamic_libs = lambda pkg: (_ for _ in ()).throw(RuntimeError("not installed")) \
        if pkg not in installed else [(f"/site/{pkg}/lib.dll", pkg)]
    for name in ("PyInstaller", "PyInstaller.utils"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: object() if name in installed else
                        (None if name in ("pywhispercpp", "faster_whisper", "llama_cpp") else real(name, *a)))
    ns = {}
    exec(compile(block, spec, "exec"), ns)
    return ns


def test_the_mac_spec_bundles_whisper_cpp_and_its_metadata(monkeypatch):
    ns = _run_block("Waffler_mac.spec", monkeypatch, {"pywhispercpp"})
    assert {"pywhispercpp.model", "_pywhispercpp"} <= set(ns["_STT_HIDDEN"])
    assert "faster_whisper" not in ns["_STT_HIDDEN"], "not installed on Apple Silicon: not named"
    # pywhispercpp reads its own version from its metadata as it is imported.
    assert ns["_STT_DATAS"] == [("/site/pywhispercpp.dist-info", "pywhispercpp.dist-info")]


def test_the_windows_spec_bundles_faster_whisper_and_ctranslate2s_libraries(monkeypatch):
    ns = _run_block("Waffler_windows.spec", monkeypatch, {"faster_whisper", "ctranslate2"})
    assert {"faster_whisper", "ctranslate2", "tokenizers", "huggingface_hub", "av"} <= set(ns["_STT_HIDDEN"])
    # ctranslate2 loads its DLLs from its own package folder.
    assert ns["_STT_BINARIES"] == [("/site/ctranslate2/lib.dll", "ctranslate2")]


@pytest.mark.parametrize("spec", SPECS)
def test_a_build_machine_without_the_engine_still_builds(spec, monkeypatch):
    ns = _run_block(spec, monkeypatch, set())
    assert ns["_STT_HIDDEN"] == []
    assert ns.get("_STT_DATAS", []) == [] and ns.get("_STT_BINARIES", []) == []


# ── the writing model's engine (src/local_llm.py): optional, never the model ─

def test_no_requirements_file_installs_the_writing_models_engine():
    """PyPI has no binary wheel for it: listed, it would make every install
    compile llama.cpp, and fail where there is no compiler. The command for a
    release build is written beside it instead."""
    for name in ("requirements.txt", "requirements_windows.txt"):
        names = {re.split(r"[<>=;\s]", line, maxsplit=1)[0].lower() for line in _requirements(name)}
        assert "llama-cpp-python" not in names, name
    text = _text("requirements.txt")
    assert 'pip install "llama-cpp-python==0.3.37" --no-binary llama-cpp-python' in text
    assert "MACOSX_DEPLOYMENT_TARGET=14.0" in text and "-DGGML_METAL=on" in text


def test_the_mac_spec_bundles_the_writing_models_engine_only_when_it_is_installed(monkeypatch):
    import local_llm
    text = _text("Waffler_mac.spec")
    # (The computer-sound helper is passed in the same list, after them.)
    assert "*_LLM_HIDDEN," in text and "binaries=_LLM_BINARIES + _SYSAUDIO_BINARIES," in text
    for model in local_llm.MODELS.values():
        for f in model.files:
            assert f.name not in text and ".gguf" not in text, "the model is never bundled"
    ns = _run_block("Waffler_mac.spec", monkeypatch, {"pywhispercpp", "llama_cpp"})
    assert {"llama_cpp", "llama_cpp.llama_chat_format"} <= set(ns["_LLM_HIDDEN"])
    # It opens its libraries by path from llama_cpp/lib: they are collected there.
    assert ns["_LLM_BINARIES"] == [("/site/llama_cpp/lib.dll", "llama_cpp")]
    ns = _run_block("Waffler_mac.spec", monkeypatch, {"pywhispercpp"})
    assert ns["_LLM_HIDDEN"] == [] and ns["_LLM_BINARIES"] == [], "a build without it still builds"


def test_the_windows_build_does_not_carry_it():
    """Not measured on Windows, and not offered there (src/local_llm.py
    machine_verdict): nothing in that build names it."""
    assert "llama_cpp" not in _text("Waffler_windows.spec")
