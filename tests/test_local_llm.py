"""The language model on this computer (src/local_llm.py), with the engine and
the network replaced: who it is offered to, the download (progress, cancel,
resume, damage), loading at first use, one request at a time with a dictation
ahead of meeting notes, the deadline, the idle unload and removal.

The real thing (a real download, the real model) is scripts/local_llm_check.py,
which is not part of this suite.
"""
import hashlib
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import local_llm  # noqa: E402
import local_stt  # noqa: E402

BIG = bytes(range(256)) * 4096          # 1 MiB: several chunks at the test chunk size


class FakeResponse:
    def __init__(self, data, status=200, fail_after=None):
        self.data, self.status_code, self.fail_after = data, status, fail_after

    def iter_content(self, size):
        sent = 0
        for i in range(0, len(self.data), size):
            if self.fail_after is not None and sent >= self.fail_after:
                raise ConnectionError("the connection dropped")
            chunk = self.data[i:i + size]
            sent += len(chunk)
            yield chunk

    def close(self):
        pass


class FakeServer:
    def __init__(self, files):
        self.files = dict(files)
        self.requests = []
        self.fail_after = {}
        self.down = False
        self.slow = 0.0

    def get(self, url, headers):
        name = url.rsplit("/", 1)[1]
        self.requests.append((name, dict(headers)))
        if self.down:
            raise ConnectionError("offline")
        data = self.files[name]
        start = 0
        rng = headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].rstrip("-"))
        resp = FakeResponse(data[start:], status=206 if start else 200,
                            fail_after=self.fail_after.pop(name, None))
        if self.slow:
            inner = resp.iter_content

            def slowly(size):
                for chunk in inner(size):
                    time.sleep(self.slow)
                    yield chunk
            resp.iter_content = slowly
        return resp


class FakeEngine:
    """Answers at once, or token by token (``tokens`` of ``step`` seconds
    each) so a request can be stopped part-way, as the real one can."""
    device = "fake"
    instances = []

    def __init__(self, name="m"):
        self.name = name
        self.calls = []
        self.order = []
        self.closed = False
        self.active = 0
        self.max_active = 0
        self.tokens = 1
        self.step = 0.0
        self.fail = None
        self._lock = threading.Lock()
        FakeEngine.instances.append(self)

    def chat(self, system, messages, max_tokens, temperature, should_stop=None, on_text=None):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append({"system": system, "messages": messages, "max_tokens": max_tokens,
                               "temperature": temperature})
            self.order.append(threading.current_thread().name)
            if self.fail:
                raise self.fail
            text, stopped = "", False
            for i in range(self.tokens):
                if should_stop is not None and should_stop():
                    stopped = True
                    break
                if self.step:
                    time.sleep(self.step)
                text += ("" if not text else " ") + f"word{i}"
                if on_text is not None:
                    on_text(text)
            return {"text": text, "input_tokens": 10, "output_tokens": len(text.split()),
                    "finish_reason": "stopped" if stopped else "stop"}
        finally:
            with self._lock:
                self.active -= 1

    def close(self):
        self.closed = True


M1 = local_stt.Machine("Darwin", "arm64", 10, 16.0)


@pytest.fixture
def llm(monkeypatch):
    """local_llm with a small test model, a fake server and a fake engine, on a
    16 GB Apple Silicon Mac, and its module state as new."""
    spec = local_llm.ModelSpec("Test writer", (local_stt.ModelFile(
        "model.gguf", "https://example.invalid/model.gguf", len(BIG), hashlib.sha256(BIG).hexdigest()),))
    monkeypatch.setattr(local_llm, "MODELS", {"test": spec})
    monkeypatch.setattr(local_llm, "DEFAULT_MODEL", "test")
    monkeypatch.setattr(local_llm, "is_available", lambda: True)
    monkeypatch.setattr(local_llm, "describe_machine", lambda: M1)
    # The engine is in the build, as on the Mac: nothing comes down with the
    # model (on Windows llama.cpp's server does: tests/test_llama_pack.py).
    monkeypatch.setattr(local_llm, "_engine_fetched_with_model", lambda: False)
    monkeypatch.setattr(local_stt, "_CHUNK", 64 * 1024)
    server = FakeServer({"model.gguf": BIG})
    monkeypatch.setattr(local_llm, "_http_get", server.get)
    FakeEngine.instances = []
    loads = []

    def load(name):
        loads.append(name)
        return FakeEngine(name)
    monkeypatch.setattr(local_llm, "_load_engine", load)
    monkeypatch.setattr(local_llm, "_gate", local_stt._Gate())
    monkeypatch.setattr(local_llm, "_engine", None)
    monkeypatch.setattr(local_llm, "_engine_key", None)
    monkeypatch.setattr(local_llm, "_primer", None)
    monkeypatch.setattr(local_llm, "_primed", False)
    monkeypatch.setattr(local_llm, "_exiting", threading.Event())
    monkeypatch.setattr(local_llm, "_idle_unload_s", 0.0)
    monkeypatch.setattr(local_llm, "_idle_timer", None)
    monkeypatch.setattr(local_llm, "_dl", {"active": False, "model": "", "done": 0, "total": 0,
                                           "error": "", "cancelled": False})
    monkeypatch.setattr(local_llm, "_last", {"device": None, "load_s": None, "reply_s": None,
                                             "unloaded": ""})
    local_llm._dl_cancel.clear()
    yield types.SimpleNamespace(server=server, loads=loads, spec=spec)
    local_llm._cancel_idle_timer()
    local_llm._dl_cancel.set()
    local_llm._wait_for_download(5)
    local_llm._dl_cancel.clear()
    local_llm._wait_for_prime(5)


def _ready(llm):
    assert local_llm.download()["ok"]


def _engine():
    return FakeEngine.instances[-1]


MSG = [{"role": "user", "content": "um hello there"}]


# ── what it is, and who it is offered to ─────────────────────────────────────

def test_the_one_model_is_pinned_to_a_commit_with_its_size_and_hash():
    assert list(local_llm.MODELS) == [local_llm.DEFAULT_MODEL]
    spec = local_llm.MODELS[local_llm.DEFAULT_MODEL]
    (f,) = spec.files
    assert f.name.endswith(".gguf") and f.size == 5154941280
    assert f.sha256 == "676c35070db6dbe52f93e9c864ee0fba4eddea94b9c875d9cb10daff453fbaee"
    assert "/resolve/4b4a2c1d584be7264f87aac328a1bc739ce81b6c/" in f.url and f.url.startswith("https://huggingface.co/")
    assert "/resolve/main/" not in f.url


def test_it_lives_beside_the_speech_model_in_the_data_folder(llm, _isolated_data_dir):
    assert local_llm.model_dir("test") == _isolated_data_dir / "models" / "test"
    assert str(ROOT) not in str(local_llm.model_dir("test"))


def test_one_module_whichever_way_it_is_imported():
    import importlib
    assert importlib.import_module("src.local_llm") is local_llm


def test_nothing_imports_the_engine_until_it_is_used():
    src = (ROOT / "src" / "local_llm.py").read_text(encoding="utf-8")
    head = src[:src.index("def _load_engine(")]
    assert "import llama_cpp" not in head and "from llama_cpp" not in head
    for networked in ("openai", "groq", "anthropic", "httpx"):
        assert f"import {networked}" not in src, "it never reaches for a cloud service"


@pytest.mark.parametrize("machine, suits, reason", [
    (local_stt.Machine("Darwin", "arm64", 10, 16.0), True, local_llm.SUITS_APPLE_SILICON),
    (local_stt.Machine("Darwin", "arm64", 10, 15.6), True, local_llm.SUITS_APPLE_SILICON),   # 16 GB reads a little under
    (local_stt.Machine("Darwin", "arm64", 8, 8.0), False, local_llm.LOW_MEMORY),
    (local_stt.Machine("Darwin", "arm64", 8, None), False, local_llm.LOW_MEMORY),
    (local_stt.Machine("Darwin", "x86_64", 8, 32.0), False, local_llm.NOT_MEASURED),
])
def test_the_verdict_is_stricter_on_memory_than_the_speech_models(machine, suits, reason):
    assert local_llm.machine_verdict(machine, card_mb=None) == {"suits": suits, "reason": reason}
    # The speech model's own verdict says yes to an 8 GB Apple Silicon Mac.
    assert local_stt.machine_verdict(local_stt.Machine("Darwin", "arm64", 8, 8.0))["suits"] is True


@pytest.mark.parametrize("machine, card_mb, suits, reason", [
    # Windows, measured on an RTX 4070 SUPER: 3.1 GB on the card, 0.4 to 0.8 s a dictation.
    (local_stt.Machine("Windows", "AMD64", 28, 32.0, True), 12282, True, local_llm.SUITS_NVIDIA),
    (local_stt.Machine("Windows", "AMD64", 4, 8.0), 4096, True, local_llm.SUITS_NVIDIA),
    # On the processor (i7-14700F: 3.9 to 5.7 s a dictation): 16 GB and 8 cores.
    (local_stt.Machine("Windows", "AMD64", 28, 32.0), None, True, local_llm.SUITS_CPU),
    (local_stt.Machine("Windows", "AMD64", 8, 15.8), 2048, True, local_llm.SUITS_CPU),     # a card too small
    (local_stt.Machine("Windows", "AMD64", 4, 32.0), None, False, local_llm.LOW_MEMORY),
    (local_stt.Machine("Windows", "AMD64", 16, 8.0), None, False, local_llm.LOW_MEMORY),
    (local_stt.Machine("Windows", "AMD64", 16, None), None, False, local_llm.LOW_MEMORY),
])
def test_windows_is_offered_it_on_an_nvidia_card_or_a_big_enough_processor(machine, card_mb, suits, reason):
    assert local_llm.machine_verdict(machine, card_mb=card_mb) == {"suits": suits, "reason": reason}


def test_a_computer_it_does_not_suit_is_not_given_the_download(llm, monkeypatch):
    monkeypatch.setattr(local_llm, "describe_machine", lambda: local_stt.Machine("Darwin", "arm64", 8, 8.0))
    result = local_llm.download()
    assert result["ok"] is False and result["error"] == local_llm.NOT_SUITED
    assert llm.server.requests == []
    status = local_llm.model_status()
    assert status["suits_machine"] is False and status["machine_reason"] == local_llm.LOW_MEMORY


# ── the setting, and when it is used ─────────────────────────────────────────

def test_it_is_used_when_ready_and_there_is_no_key_or_the_person_chose_it(llm):
    assert local_llm.should_use(has_key=False) is False, "not downloaded: off"
    _ready(llm)
    assert local_llm.should_use(has_key=False) is True
    assert local_llm.should_use(has_key=True) is False, "with a key nothing changes"
    assert local_llm.should_use(has_key=True, stored={"writing_mode": "local"}) is True
    assert local_llm.should_use(has_key=True, stored={"writing_mode": "cloud"}) is False
    assert local_llm.should_use(has_key=False, stored={"writing_mode": "cloud"}) is True, "no key: there is no cloud"


def test_the_choice_is_read_from_settings_json_each_time(llm, _isolated_data_dir):
    _ready(llm)
    assert local_llm.should_use(has_key=True) is False
    (_isolated_data_dir / "settings.json").write_text('{"writing_mode": "local"}', encoding="utf-8")
    assert local_llm.should_use(has_key=True) is True


def test_a_save_is_refused_for_an_unknown_choice():
    assert local_llm.settings_problem({"sounds": True}) is None
    assert local_llm.settings_problem({"writing_mode": "local"}) is None
    assert local_llm.settings_problem({"writing_mode": "cloud"}) is None
    assert local_llm.settings_problem({"writing_mode": "moon"}) == local_llm.MODE_UNKNOWN


def test_without_the_engine_it_is_never_used(llm, monkeypatch):
    _ready(llm)
    monkeypatch.setattr(local_llm, "is_available", lambda: False)
    assert local_llm.should_use(has_key=False) is False
    with pytest.raises(local_llm.LocalLLMError) as e:
        local_llm.chat("s", MSG)
    assert str(e.value) == local_llm.NOT_INSTALLED and e.value.kind == "unavailable"


# ── downloading ──────────────────────────────────────────────────────────────

def test_status_goes_from_not_downloaded_through_downloading_to_ready(llm):
    s = local_llm.model_status()
    assert (s["state"], s["percent"], s["bytes_total"], s["label"]) == ("not_downloaded", 0, len(BIG), "Test writer")
    assert s["suits_machine"] is True and s["available"] is True
    seen = []
    result = local_llm.download(lambda pct, done, total: seen.append((pct, local_llm.model_status()["state"])))
    assert result["ok"] and result["state"] == "ready" and result["size_on_disk"] > len(BIG)
    assert seen[0][1] == "downloading" and seen[-1][0] == 100
    assert [p for p, _ in seen] == sorted(p for p, _ in seen)
    assert (local_llm.model_dir("test") / "model.gguf").read_bytes() == BIG


def test_a_dropped_connection_is_a_plain_sentence_and_the_retry_carries_on(llm):
    llm.server.fail_after["model.gguf"] = 300 * 1024
    first = local_llm.download()
    assert first["ok"] is False and first["error"] == local_llm.DOWNLOAD_FAILED
    assert first["state"] == "not_downloaded" and 0 < first["bytes_done"] < len(BIG)
    again = local_llm.download()
    assert again["ok"] and again["state"] == "ready"
    assert llm.server.requests[-1][1].get("Range", "").startswith("bytes=")


def test_offline_is_a_plain_sentence_not_an_exception(llm):
    llm.server.down = True
    result = local_llm.download()
    assert result["ok"] is False and result["error"] == local_llm.DOWNLOAD_FAILED
    assert "speech" not in result["error"].lower()


def test_a_damaged_download_is_thrown_away_and_never_marked_ready(llm):
    llm.server.files["model.gguf"] = BIG[:-1] + b"!"
    result = local_llm.download()
    assert result["ok"] is False and result["error"] == local_llm.DOWNLOAD_DAMAGED
    assert not local_llm.is_ready()
    assert not list(local_llm.model_dir("test").glob("model.gguf*"))


def test_cancel_keeps_what_arrived_and_the_next_download_resumes(llm):
    llm.server.slow = 0.02
    started = local_llm.start_download()
    assert started["ok"] and started["started"] and started["state"] == "downloading"
    busy = local_llm.start_download()
    assert busy["started"] is False and busy["ok"] is True, "one download at a time"
    time.sleep(0.1)
    assert local_llm.cancel_download() is True
    local_llm._wait_for_download(5)
    s = local_llm.model_status()
    assert s["state"] == "not_downloaded" and s["cancelled"] and 0 < s["bytes_done"] < len(BIG)
    llm.server.slow = 0.0
    assert local_llm.download()["ok"]
    assert llm.server.requests[-1][1].get("Range", "").startswith("bytes=")


def test_no_room_on_the_disk_is_said_before_anything_is_fetched(llm, monkeypatch):
    monkeypatch.setattr(local_llm.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=10))
    result = local_llm.download()
    assert result["error"] == local_llm.DOWNLOAD_NO_SPACE and llm.server.requests == []


def test_remove_deletes_the_file_and_releases_the_model(llm):
    _ready(llm)
    local_llm.chat("s", MSG)
    engine = _engine()
    result = local_llm.remove_model()
    assert result["ok"] and result["freed_bytes"] > len(BIG) and result["state"] == "not_downloaded"
    assert engine.closed and not local_llm.model_dir("test").exists()
    assert local_llm.should_use(has_key=False) is False


# ── one request at a time, loaded at first use ───────────────────────────────

def test_a_request_before_the_download_is_a_plain_error(llm):
    with pytest.raises(local_llm.LocalLLMError) as e:
        local_llm.chat("s", MSG)
    assert str(e.value) == local_llm.NOT_DOWNLOADED and e.value.kind == "not_downloaded"
    assert llm.loads == []


def test_the_model_is_loaded_at_the_first_request_and_kept(llm):
    _ready(llm)
    assert llm.loads == [] and local_llm.model_status()["loaded"] is False
    reply = local_llm.chat("be tidy", MSG, max_tokens=50, temperature=0.1)
    assert reply["text"] == "word0" and reply["finish_reason"] == "stop"
    local_llm.chat("be tidy", MSG)
    assert llm.loads == ["test"] and local_llm.model_status()["loaded"] is True
    call = _engine().calls[0]
    assert call["system"] == "be tidy" and call["messages"] == MSG and call["max_tokens"] == 50


def test_an_engine_that_fails_is_a_plain_error_and_never_the_cloud(llm):
    _ready(llm)
    local_llm.chat("s", MSG)
    _engine().fail = RuntimeError("metal went away")
    with pytest.raises(local_llm.LocalLLMError) as e:
        local_llm.chat("s", MSG)
    assert str(e.value) == local_llm.FAILED and e.value.kind == "failed"
    assert "Nothing was sent anywhere" in local_llm.FAILED


def test_a_model_that_will_not_open_says_so(llm, monkeypatch):
    _ready(llm)

    def broken(name):
        raise OSError("not a gguf")
    monkeypatch.setattr(local_llm, "_load_engine", broken)
    with pytest.raises(local_llm.LocalLLMError) as e:
        local_llm.chat("s", MSG)
    assert str(e.value) == local_llm.LOAD_FAILED


def test_two_requests_at_once_take_turns(llm):
    _ready(llm)
    local_llm.chat("s", MSG)
    engine = _engine()
    engine.tokens, engine.step = 5, 0.02
    threads = [threading.Thread(target=local_llm.chat, args=("s", MSG)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert engine.max_active == 1 and len(engine.calls) == 4


def test_a_dictation_stops_meeting_notes_part_way_and_the_notes_start_again(llm):
    """Notes can take a minute; a dictation must not wait behind them. The
    notes give way at the next word, and are written again afterwards."""
    _ready(llm)
    local_llm.chat("s", MSG)
    engine = _engine()
    engine.tokens, engine.step = 40, 0.02          # notes: 0.8 s if left alone
    out = {}

    def notes():
        out["notes"] = local_llm.chat("notes", MSG, background=True)
    t = threading.Thread(target=notes, name="notes")
    t.start()
    time.sleep(0.15)
    engine.tokens = 2
    t0 = time.monotonic()
    out["dictation"] = local_llm.chat("tidy", MSG, kind="cleanup")
    waited = time.monotonic() - t0
    engine.tokens = 40
    t.join(10)
    assert waited < 0.5, "the dictation did not wait for the notes to finish"
    assert out["dictation"]["text"] == "word0 word1"
    assert out["notes"]["finish_reason"] == "stop" and len(out["notes"]["text"].split()) == 40
    systems = [c["system"] for c in engine.calls]
    assert systems[1:] == ["notes", "tidy", "notes"], "stopped, the dictation, then the notes again"
    assert engine.max_active == 1


def test_a_request_past_its_deadline_stops_and_says_it_was_too_slow(llm):
    _ready(llm)
    local_llm.chat("s", MSG)
    engine = _engine()
    engine.tokens, engine.step = 200, 0.02
    t0 = time.monotonic()
    with pytest.raises(local_llm.LocalLLMError) as e:
        local_llm.chat("s", MSG, deadline=time.monotonic() + 0.15)
    assert e.value.kind == "deadline" and str(e.value) == local_llm.TOO_SLOW
    assert time.monotonic() - t0 < 1.0, "it stopped; it did not write all 200 words"
    assert engine.active == 0
    # And the model is free for the next request at once.
    engine.tokens, engine.step = 1, 0.0
    assert local_llm.chat("s", MSG)["text"] == "word0"


def test_a_deadline_already_gone_asks_the_model_nothing(llm):
    _ready(llm)
    with pytest.raises(local_llm.LocalLLMError) as e:
        local_llm.chat("s", MSG, deadline=time.monotonic() - 1)
    assert e.value.kind == "deadline" and llm.loads == []


def test_words_are_handed_over_as_they_are_written(llm):
    _ready(llm)
    local_llm.chat("s", MSG)
    _engine().tokens = 3
    seen = []
    local_llm.chat("s", MSG, on_text=seen.append)
    assert seen == ["word0", "word0 word1", "word0 word1 word2"]


# ── the long clean-up prompt is read ahead of time ───────────────────────────

def test_warm_loads_the_model_and_reads_the_clean_up_prompt_once(llm):
    _ready(llm)
    local_llm.set_primer(lambda: ("clean-up rules", "the long prompt"))
    assert local_llm.warm() is True
    engine = _engine()
    assert [c["system"] for c in engine.calls] == ["clean-up rules"] and engine.calls[0]["max_tokens"] == 1
    assert local_llm.warm() is True
    assert len(engine.calls) == 1, "already read"


def test_after_notes_the_clean_up_prompt_is_read_again_and_not_after_a_dictation(llm):
    _ready(llm)
    local_llm.set_primer(lambda: ("clean-up rules", "the long prompt"))
    local_llm.warm()
    engine = _engine()
    local_llm.chat("tidy", MSG, kind="cleanup")
    local_llm._wait_for_prime(5)
    assert [c["system"] for c in engine.calls] == ["clean-up rules", "tidy"]
    local_llm.chat("notes", MSG, background=True)
    local_llm._wait_for_prime(5)
    assert [c["system"] for c in engine.calls][2:] == ["notes", "clean-up rules"]


def test_warm_does_nothing_without_the_model_and_never_raises(llm, monkeypatch):
    assert local_llm.warm() is False and llm.loads == []
    _ready(llm)

    def broken(name):
        raise OSError("no")
    monkeypatch.setattr(local_llm, "_load_engine", broken)
    assert local_llm.warm() is False


# ── memory is given back ─────────────────────────────────────────────────────

def test_the_model_is_released_after_the_idle_period_and_loads_again(llm):
    _ready(llm)
    local_llm.set_idle_unload(0.1)
    local_llm.chat("s", MSG)
    first = _engine()
    deadline = time.monotonic() + 3
    while not first.closed and time.monotonic() < deadline:
        time.sleep(0.02)
    assert first.closed and local_llm.model_status()["loaded"] is False
    local_llm.chat("s", MSG)
    assert llm.loads == ["test", "test"]


def test_unload_on_request(llm):
    _ready(llm)
    local_llm.chat("s", MSG)
    assert local_llm.unload() is True and _engine().closed
    assert local_llm.unload() is False
    local_llm.chat("s", MSG)
    assert llm.loads == ["test", "test"]


def test_as_the_program_ends_a_running_request_is_stopped_and_the_model_closed(llm):
    """llama.cpp aborts the process if its GPU device goes while a model is
    open, so quitting in the middle of a meeting's notes must close it."""
    _ready(llm)
    local_llm.chat("s", MSG)
    engine = _engine()
    engine.tokens, engine.step = 500, 0.02          # ten seconds if left alone
    out = {}

    def notes():
        try:
            local_llm.chat("notes", MSG, background=True)
        except local_llm.LocalLLMError as e:
            out["error"] = e
    t = threading.Thread(target=notes)
    t.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    local_llm._release_at_exit()
    assert time.monotonic() - t0 < 1.0
    t.join(5)
    assert engine.closed and local_llm.is_loaded() is False
    assert str(out["error"]) == local_llm.FAILED
    # And nothing loads it again on the way out.
    with pytest.raises(local_llm.LocalLLMError):
        local_llm.chat("s", MSG)
    assert llm.loads == ["test"]


# ── the real engine's wrapper, with llama.cpp replaced ───────────────────────

class _FakeLlama:
    made = []
    n_tokens = 16           # what llama.cpp has read and written, after a reply

    def __init__(self, path, **kw):
        self.path, self.kw, self.requests = path, kw, []
        self.closed = self.stream_closed = False
        _FakeLlama.made.append(self)

    def create_chat_completion(self, **kw):
        self.requests.append(kw)

        def chunks():
            try:
                if kw["messages"][-1]["content"] == "too long":
                    raise ValueError("Requested tokens (20000) exceed context window of 16384")
                for piece in (" Hello", " there", ".", " "):
                    yield {"choices": [{"delta": {"content": piece}, "finish_reason": None}]}
                yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
            finally:
                self.stream_closed = True
        return chunks()

    def close(self):
        self.closed = True


@pytest.fixture
def llamacpp(monkeypatch):
    import local_llm_llamacpp
    fake = types.ModuleType("llama_cpp")
    fake.Llama = _FakeLlama
    monkeypatch.setitem(sys.modules, "llama_cpp", fake)
    _FakeLlama.made = []
    return local_llm_llamacpp


USER = [{"role": "user", "content": "hi"}]


def test_the_engine_opens_the_file_on_the_gpu_with_room_for_a_long_meeting(llamacpp, tmp_path):
    engine = llamacpp.LlamaCppEngine(tmp_path / "m.gguf")
    made = _FakeLlama.made[0]
    assert made.path == str(tmp_path / "m.gguf")
    assert made.kw["n_gpu_layers"] == -1 and made.kw["n_ctx"] == llamacpp.N_CTX == 16384
    assert made.kw["flash_attn"] is True and made.kw["verbose"] is False
    reply = engine.chat("sys", USER, 100, 0.1)
    assert reply == {"text": "Hello there.", "input_tokens": 12, "output_tokens": 4, "finish_reason": "stop"}
    req = made.requests[0]
    assert req["messages"] == [{"role": "system", "content": "sys"}, *USER]
    assert req["max_tokens"] == 100 and req["temperature"] == 0.1 and req["stream"] is True
    engine.close()
    assert made.closed


def test_the_engine_hands_over_the_words_as_they_are_written(llamacpp, tmp_path):
    engine = llamacpp.LlamaCppEngine(tmp_path / "m.gguf")
    seen = []
    reply = engine.chat("sys", USER, 100, 0.0, on_text=seen.append)
    assert seen == [" Hello", " Hello there", " Hello there.", " Hello there. "]
    assert reply["text"] == "Hello there."


def test_the_engine_stops_at_the_next_word_when_told_to(llamacpp, tmp_path):
    engine = llamacpp.LlamaCppEngine(tmp_path / "m.gguf")
    asked = []

    def should_stop():
        asked.append(1)
        return len(asked) >= 2
    reply = engine.chat("sys", USER, 100, 0.0, should_stop=should_stop)
    assert reply["finish_reason"] == "stopped" and reply["text"] == "Hello there"
    assert _FakeLlama.made[0].stream_closed, "the generation is ended, not left running"


def test_a_prompt_that_does_not_fit_is_its_own_error(llamacpp, tmp_path, llm, monkeypatch):
    engine = llamacpp.LlamaCppEngine(tmp_path / "m.gguf")
    with pytest.raises(llamacpp.ContextTooLong):
        engine.chat("sys", [{"role": "user", "content": "too long"}], 100, 0.0)
    # And through the module it is a plain sentence.
    _ready(llm)
    monkeypatch.setattr(local_llm, "_load_engine", lambda name: engine)
    with pytest.raises(local_llm.LocalLLMError) as e:
        local_llm.chat("sys", [{"role": "user", "content": "too long"}])
    assert str(e.value) == local_llm.TOO_LONG and e.value.kind == "too_long"
