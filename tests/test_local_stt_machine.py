"""Does this computer suit speech to text on it? (src/local_stt.py
machine_verdict), and what the page is told about it.

Setup's first screen puts "On this computer" first and marks it "Recommended
for this computer" where the machine suits it, and puts the key first where it
does not (tests/test_setup_free_way.py). The rule is the table in
docs/OFFLINE-TRANSCRIPTION.md: Apple Silicon yes; Windows and Intel Macs by an
NVIDIA card with room, or 8+ logical cores and 12+ GB of memory.

No hardware is probed: machines are described by hand, and app.py's methods
are lifted out and run against fakes, as in tests/test_local_stt_api.py.
"""
import ast
import json
import os
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import local_stt  # noqa: E402
import local_stt_ct2  # noqa: E402


M = local_stt.Machine


@pytest.mark.parametrize("machine, suits, reason", [
    # Apple Silicon: whisper.cpp on the GPU, whatever the size of the Mac.
    (M("Darwin", "arm64", 8, 8.0), True, "apple_silicon"),
    (M("Darwin", "arm64", 10, 16.0), True, "apple_silicon"),
    # Windows with an NVIDIA card that has room for the large model.
    (M("Windows", "AMD64", 4, 8.0, nvidia=True), True, "nvidia"),
    # Windows or an Intel Mac on the CPU: 8+ logical cores and 12+ GB.
    (M("Windows", "AMD64", 8, 16.0), True, "cpu"),
    (M("Windows", "AMD64", 16, 11.8), True, "cpu"),      # "12 GB" reports a little under
    (M("Darwin", "x86_64", 8, 16.0), True, "cpu"),
    # Smaller machines get the small model on a slow processor: it works, slowly.
    (M("Windows", "AMD64", 4, 16.0), False, "small_machine"),
    (M("Windows", "AMD64", 8, 8.0), False, "small_machine"),
    (M("Darwin", "x86_64", 4, 8.0), False, "small_machine"),
    (M("Windows", "AMD64", 12, None), False, "small_machine"),   # memory unknown: don't promise
    # An NVIDIA card is no help to a Mac (CTranslate2 has no CUDA there).
    (M("Darwin", "x86_64", 4, 8.0, nvidia=True), False, "small_machine"),
])
def test_the_verdict_follows_the_documents_table(machine, suits, reason):
    assert local_stt.machine_verdict(machine) == {"suits": suits, "reason": reason}


def test_the_verdict_and_the_model_choice_are_one_rule():
    """The processor line is src/local_stt_ct2's (the model it would pick), so
    "suits" can never disagree with the model that gets downloaded."""
    for cores in (2, 4, 7, 8, 16):
        for ram in (None, 4.0, 8.0, 11.4, 11.5, 12.0, 32.0):
            want = local_stt_ct2.cpu_model_for(cores, ram) == "small"
            assert local_stt.machine_verdict(M("Windows", "AMD64", cores, ram))["suits"] is want
    assert local_stt_ct2.cpu_model_for(8, 11.5) == "small" and local_stt_ct2.cpu_model_for(8, 11.4) == "base"


def test_this_machine_is_described_once_and_without_loading_an_engine(monkeypatch):
    monkeypatch.setattr(local_stt, "_machine_cache", [])
    monkeypatch.setattr(local_stt.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(local_stt.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(local_stt, "default_model", lambda: pytest.fail("Apple Silicon needs no hardware probe"))
    m = local_stt.describe_machine()
    assert (m.system, m.arch, m.nvidia) == ("Darwin", "arm64", False) and m.cores >= 1
    assert local_stt.machine_verdict() == {"suits": True, "reason": "apple_silicon"}
    monkeypatch.setattr(local_stt.platform, "machine", lambda: "x86_64")
    assert local_stt.describe_machine() is m, "hardware is read once"


def test_a_windows_pc_is_described_from_its_processor_memory_and_card(monkeypatch):
    monkeypatch.setattr(local_stt, "_machine_cache", [])
    monkeypatch.setattr(local_stt.platform, "system", lambda: "Windows")
    monkeypatch.setattr(local_stt.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(local_stt.os, "cpu_count", lambda: 6)
    monkeypatch.setattr(local_stt_ct2, "_total_ram_gb", lambda: 15.9)
    monkeypatch.setattr(local_stt, "engine_name", lambda: local_stt.ENGINE_CT2)
    monkeypatch.setattr(local_stt, "default_model", lambda: "large-v3-turbo")   # a card with room
    m = local_stt.describe_machine()
    assert m == M("Windows", "AMD64", 6, 15.9, True)
    assert local_stt.machine_verdict() == {"suits": True, "reason": "nvidia"}
    monkeypatch.setattr(local_stt, "_machine_cache", [])
    monkeypatch.setattr(local_stt, "default_model", lambda: "base")             # no usable card
    assert local_stt.machine_verdict() == {"suits": False, "reason": "small_machine"}


def test_a_machine_that_cannot_be_read_is_not_promised_anything(monkeypatch):
    monkeypatch.setattr(local_stt, "_machine_cache", [])
    monkeypatch.setattr(local_stt.platform, "system", lambda: "Windows")
    monkeypatch.setattr(local_stt.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(local_stt, "engine_name", lambda: local_stt.ENGINE_CT2)

    def boom():
        raise RuntimeError("no hardware")
    monkeypatch.setattr(local_stt, "default_model", boom)
    monkeypatch.setattr(local_stt_ct2, "_total_ram_gb", boom)
    assert local_stt.machine_verdict() == {"suits": False, "reason": "small_machine"}


# ── what the page is told ────────────────────────────────────────────────────

_SRC = (ROOT / "app.py").read_text(encoding="utf-8")
_TREE = ast.parse(_SRC)
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


def _api(*names, **ns_extra):
    nodes = [n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names), "an Api method moved or was renamed"
    ns = {"os": os, "json": json, "threading": threading, "_local_stt": local_stt,
          "_log_to_file": lambda m: None, "_is_setup_complete": lambda: True}
    ns.update(ns_extra)
    exec(compile(ast.Module(nodes, []), "<app.py Api>", "exec"), ns)
    return ns


def test_the_status_carries_the_verdict(monkeypatch):
    monkeypatch.setattr(local_stt, "model_status", lambda stored=None: {"state": "not_downloaded", "available": True})
    monkeypatch.setattr(local_stt, "machine_verdict", lambda: {"suits": False, "reason": "small_machine"})
    r = _api("local_stt_status")["local_stt_status"](types.SimpleNamespace(_load_settings_file=lambda: {}))
    assert r["ok"] is True and r["suits_machine"] is False and r["machine_reason"] == "small_machine"
    json.dumps(r)


def test_a_verdict_that_fails_does_not_take_the_status_with_it(monkeypatch):
    monkeypatch.setattr(local_stt, "model_status", lambda stored=None: {"state": "ready", "available": True})

    def boom():
        raise RuntimeError("no hardware")
    monkeypatch.setattr(local_stt, "machine_verdict", boom)
    r = _api("local_stt_status")["local_stt_status"](types.SimpleNamespace(_load_settings_file=lambda: {}))
    assert r["ok"] is True and r["state"] == "ready" and r["suits_machine"] is False


@pytest.mark.parametrize("done, key, mode, needs, resume", [
    (False, "", "local", True, "permissions"),   # mid-setup on the free way: carry on
    (False, "", None, True, "permissions"),
    (True, "", "local", False, ""),              # finished on the free way: complete, no wizard
    (True, "", "cloud", True, "permissions"),
    (True, "gsk_x", "cloud", False, ""),         # a key: exactly as before
])
def test_setup_counts_the_free_way_as_complete_and_says_which_way(monkeypatch, done, key, mode, needs, resume):
    ns = _api("get_onboarding_status", _is_setup_complete=lambda: done)
    monkeypatch.setenv("GROQ_API_KEY", key)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    stored = {"setup_step": "permissions"}
    if mode:
        stored["transcription_mode"] = mode
    r = ns["get_onboarding_status"](types.SimpleNamespace(_load_settings_file=lambda: dict(stored)))
    assert r["needs_setup"] is needs and r["resume_step"] == resume
    assert r["transcription_mode"] == ("local" if mode == "local" else "cloud")
    assert r["has_key"] is bool(key)
