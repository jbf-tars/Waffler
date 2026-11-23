"""The writing model's engine on Windows: llama.cpp's own server
(src/local_llm_server.py), driven as src/local_llm.py drives the Mac's engine.

Measured on a Windows 11 PC with an RTX 4070 SUPER (Nov 2026), with the
writing model the Mac uses (Gemma 4 E4B, 4-bit) and llama.cpp b11168: all 43
layers on the card in 3.1 GB, loaded in 3.5 s; the app's own clean-up tidied
dictations of 34 to 69 words in 0.42 to 0.81 s, and a meeting's notes in 2.4
s, with no connection to anywhere but the computer. The first run turned
"£8,400" into "Â£8,400": the stream is UTF-8 with no charset, which requests
reads as Latin-1. That is pinned here.

The server here is a stand-in (tests/_fake_llama_server.py) that answers as
the real one does: no model, no network.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import local_llm  # noqa: E402
import local_llm_server as srv  # noqa: E402

FAKE = ROOT / "tests" / "_fake_llama_server.py"


@pytest.fixture
def engine(monkeypatch, tmp_path):
    """The real engine, starting the stand-in in place of llama-server.exe."""
    real_popen = subprocess.Popen
    seen = {}

    def popen(cmd, *a, **k):
        seen["cmd"] = list(cmd)
        return real_popen([sys.executable, str(FAKE)] + list(cmd[1:]), *a, **k)
    monkeypatch.setattr(srv.subprocess, "Popen", popen)
    made = []

    def make(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        e = srv.LlamaServerEngine(tmp_path / "model.gguf", server=FAKE, log_file=tmp_path / "server.log")
        made.append(e)
        return e
    make.seen = seen
    yield make
    for e in made:
        e.close()


def test_the_reply_comes_back_whole_pound_signs_and_all(engine):
    e = engine()
    said = []
    reply = e.chat("You tidy text.", [{"role": "user", "content": "thanks eight thousand four hundred is fine"}],
                   max_tokens=64, temperature=0.0, on_text=said.append)
    assert reply["text"] == "Thanks — £8,400 is fine."
    assert reply["finish_reason"] == "stop" and reply["input_tokens"] == 1234 and reply["output_tokens"] == 5
    assert said[-1] == "Thanks — £8,400 is fine." and len(said) == 5, "read word by word"


def test_a_reply_is_stopped_at_the_next_word(engine):
    e = engine()
    asked = []

    def stop():
        asked.append(1)
        return len(asked) >= 3
    reply = e.chat("s", [{"role": "user", "content": "SLOW 40"}], 512, 0.0, should_stop=stop)
    assert reply["finish_reason"] == "stopped" and reply["text"] == "word0 word1 word2"
    # The connection was closed and the server carries on for the next one.
    assert e.chat("s", [{"role": "user", "content": "hi"}], 16, 0.0)["text"].startswith("Thanks")


def test_a_prompt_too_long_for_the_window_is_said_so(engine):
    with pytest.raises(srv.ContextTooLong):
        engine().chat("s", [{"role": "user", "content": "TOO LONG"}], 16, 0.0)


def test_how_many_layers_went_on_the_card_is_read_from_its_log(engine):
    e = engine()
    assert (e.device, e.gpu_layers) == ("cuda", 43) and e.load_s is not None
    # The processor build also says "offloaded 43/43 layers to GPU" (its
    # CPU_REPACK copy): with no buffer on a card it is the processor. Seen on
    # Windows with llama.cpp b11168's processor build.
    e2 = engine(FAKE_DEVICE="cpu")
    assert (e2.device, e2.gpu_layers) == ("cpu", 0)


def test_the_server_listens_on_this_computer_only_and_fits_itself_to_the_card(engine):
    engine()
    cmd = engine.seen["cmd"]
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--fit") + 1] == "on" and "-ngl" not in cmd
    assert cmd[cmd.index("-c") + 1] == str(srv.N_CTX) and cmd[cmd.index("-np") + 1] == "1"


def test_the_processor_only_when_asked(engine):
    engine(WAFFLER_LLM_DEVICE="cpu")
    cmd = engine.seen["cmd"]
    assert cmd[cmd.index("-ngl") + 1] == "0"


def test_close_ends_the_server(engine):
    e = engine()
    proc = e._proc
    e.close()
    assert proc.poll() is not None
    with pytest.raises(srv.ServerFailed):
        e.chat("s", [{"role": "user", "content": "hi"}], 8, 0.0)


def test_a_server_that_stops_while_loading_is_a_plain_failure(monkeypatch, tmp_path):
    real_popen = subprocess.Popen
    monkeypatch.setattr(srv.subprocess, "Popen",
                        lambda cmd, *a, **k: real_popen([sys.executable, "-c", "raise SystemExit(3)"], **{
                            x: k[x] for x in ("stdin", "stdout", "stderr") if x in k}))
    with pytest.raises(srv.ServerFailed):
        srv.LlamaServerEngine(tmp_path / "model.gguf", server=FAKE, log_file=tmp_path / "s.log")


def test_the_server_is_found_by_name_or_in_the_data_folder(monkeypatch, tmp_path):
    monkeypatch.delenv(srv.SERVER_ENV, raising=False)
    assert srv.find_server() is None
    here = srv.data_dir() / "llama"
    here.mkdir(parents=True)
    (here / srv._SERVER_NAME).write_bytes(b"")
    assert srv.find_server() == here / srv._SERVER_NAME
    named = tmp_path / "elsewhere.exe"
    named.write_bytes(b"")
    monkeypatch.setenv(srv.SERVER_ENV, str(named))
    assert srv.find_server() == named


def test_on_windows_the_writing_model_runs_on_the_server_where_there_is_no_llama_cpp(monkeypatch, tmp_path):
    monkeypatch.setattr(local_llm.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(local_llm.sys, "platform", "win32")
    monkeypatch.setattr(local_llm, "_available", [])
    monkeypatch.setattr(srv, "find_server", lambda: None)
    # No server yet: none to run, but one comes down with the model (src/llama_pack.py).
    assert local_llm._engine_kind() == "" and local_llm.is_available() is True
    monkeypatch.setattr(srv, "find_server", lambda: tmp_path / "llama-server.exe")
    local_llm.forget_engine()
    assert local_llm._engine_kind() == "server" and local_llm.is_available() is True
    opened = []
    monkeypatch.setattr(srv, "LlamaServerEngine", lambda model_file: opened.append(model_file) or "engine")
    assert local_llm._load_engine(local_llm.DEFAULT_MODEL) == "engine"
    assert opened[0].name == local_llm.MODELS[local_llm.DEFAULT_MODEL].files[0].name


# ── what it read, kept on disk ───────────────────────────────────────────────
# Measured on a processor (i7-14700F): the clean-up's 5,624-token prompt took
# about 40 s to read, and came again after every idle unload; a dictation that
# arrived then was pasted as said.

def test_what_the_slot_read_is_kept_on_disk_and_read_back(engine):
    e = engine()
    cmd = engine.seen["cmd"]
    assert cmd[cmd.index("--slot-save-path") + 1] == str(srv.cache_dir())
    assert e.restore_prompt("cleanup-abc") is False, "nothing kept yet"
    assert e.save_prompt("cleanup-abc") is True and (srv.cache_dir() / "cleanup-abc.bin").is_file()
    e.close()
    again = engine()
    assert again.restore_prompt("cleanup-abc") is True


def test_only_the_latest_read_is_kept(engine):
    e = engine()
    e.save_prompt("cleanup-old")
    e.save_prompt("cleanup-new")
    assert [p.name for p in srv.cache_dir().glob("*.bin")] == ["cleanup-new.bin"]


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "x" * 65, "c:evil"])
def test_a_name_that_is_not_a_plain_word_is_never_sent(engine, bad):
    e = engine()
    assert e.save_prompt(bad) is False and e.restore_prompt(bad) is False


def test_the_writing_model_reads_its_prompt_back_after_an_unload_instead_of_again(monkeypatch):
    """local_llm: after priming the engine is told to keep it; a newly loaded
    engine is asked to read it back, and then counts as primed."""
    calls = []

    class Engine:
        def chat(self, system, messages, max_tokens, temperature, should_stop=None, on_text=None):
            calls.append(("chat", messages[0]["content"]))
            return {"text": "x", "input_tokens": 1, "output_tokens": 1, "finish_reason": "stop"}

        def save_prompt(self, key):
            calls.append(("save", key))
            return True

        def restore_prompt(self, key):
            calls.append(("restore", key))
            return True

        def close(self):
            pass
    import local_stt
    import threading
    monkeypatch.setattr(local_llm, "_load_engine", lambda name: Engine())
    monkeypatch.setattr(local_llm, "_gate", local_stt._Gate())
    monkeypatch.setattr(local_llm, "_engine", None)
    monkeypatch.setattr(local_llm, "_engine_key", None)
    monkeypatch.setattr(local_llm, "_primed", False)
    monkeypatch.setattr(local_llm, "_exiting", threading.Event())
    monkeypatch.setattr(local_llm, "_idle_unload_s", 0.0)
    monkeypatch.setattr(local_llm, "_primer", lambda: ("rules", "Hello."))
    local_llm._gate.acquire(background=False)
    try:
        local_llm._ensure_engine(local_llm.DEFAULT_MODEL)
    finally:
        local_llm._gate.release()
    key = local_llm._prompt_key("rules", "Hello.")
    assert calls == [("restore", key)] and local_llm._primed is True, "read back, not read again"
    monkeypatch.setattr(local_llm, "_primed", False)
    assert local_llm._prime() is True
    assert calls[-2:] == [("chat", "Hello."), ("save", key)]
    assert local_llm._prompt_key("rules changed", "Hello.") != key, "a changed prompt is never read back"


def test_on_the_processor_the_full_window_cache_is_kept_so_a_kept_prompt_reads_back(engine, monkeypatch):
    """Measured with the processor build: without --swa-full the server said
    "forcing full prompt re-processing due to lack of cache data" after a read
    back, and the first dictation after an unload took 46.5 s and was pasted
    as said; with it, 9.6 s, tidied, with 85 tokens read instead of 5,702."""
    import gpu_pack
    monkeypatch.setattr(gpu_pack, "card", lambda: None)
    engine()
    assert "--swa-full" in engine.seen["cmd"]
    monkeypatch.setattr(gpu_pack, "card", lambda: {"count": 1, "total_mb": 12282})
    engine()
    assert "--swa-full" not in engine.seen["cmd"], "on a card the prompt reads in about a second anyway"
    engine(WAFFLER_LLM_DEVICE="cpu")
    assert "--swa-full" in engine.seen["cmd"]
