"""What the page calls for speech to text on this computer (app.py ``Api``):
local_stt_status / download / cancel / remove, ``transcription_mode`` through
get_settings and save_settings, and the places that used to insist on a key.

app.py's methods are lifted out of its source and run against fakes, as the
other bridge tests do. The model and the network are never touched.
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
sys.path.insert(0, str(ROOT / "tests"))

import languages  # noqa: E402
import local_stt  # noqa: E402
from _settings_fake import settings_api  # noqa: E402

_SRC = (ROOT / "app.py").read_text(encoding="utf-8")
_TREE = ast.parse(_SRC)
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
_PIPELINE = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")


def _api(*names, **ns_extra):
    """The named Api methods, bound into a namespace of fakes."""
    nodes = [n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names), "an Api method moved or was renamed"
    logged = []
    ns = {"os": os, "json": json, "threading": threading, "_pipeline": None,
          "_languages": languages, "_local_stt": local_stt, "_log_to_file": logged.append,
          "_is_setup_complete": lambda: True, "_initialize_pipeline": lambda: None,
          "_local_stt_changed": lambda: None}
    ns.update(ns_extra)
    exec(compile(ast.Module(nodes, []), "<app.py Api>", "exec"), ns)
    ns["logged"] = logged
    return ns


def _module_fn(name, **ns):
    fn = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    env = {"json": json, "_local_stt": local_stt, **ns}
    exec(compile(ast.Module([fn], []), "<app.py>", "exec"), env)
    return env[name]


@pytest.fixture
def no_keys(monkeypatch):
    for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(name, raising=False)


STATUS = {"available": True, "engine": "whispercpp", "model": "large-v3-turbo-q5",
          "label": "Whisper large v3 turbo", "state": "not_downloaded", "percent": 0,
          "bytes_done": 0, "bytes_total": 574041195, "size_on_disk": 0, "error": "",
          "cancelled": False, "loaded": False, "device": None, "load_s": None, "transcribe_s": None}


# ── the four methods ─────────────────────────────────────────────────────────

def test_the_page_has_the_four_methods_and_they_never_raise(monkeypatch):
    names = ("local_stt_status", "local_stt_download", "local_stt_cancel", "local_stt_remove")
    ns = _api(*names)
    this = types.SimpleNamespace(_load_settings_file=lambda: {})

    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    for attr in ("model_status", "start_download", "cancel_download", "remove_model", "is_available"):
        monkeypatch.setattr(local_stt, attr, boom)
    for name in names:
        r = ns[name](this)
        assert r["ok"] is False and r["error"] and "disk on fire" not in r["error"], name
    assert len(ns["logged"]) == 4


def test_status_is_the_models_state_plus_the_mode(monkeypatch):
    seen = []
    monkeypatch.setattr(local_stt, "model_status", lambda stored=None: seen.append(stored) or dict(STATUS))
    monkeypatch.setattr(local_stt, "machine_verdict", lambda: {"suits": True, "reason": "apple_silicon"})
    ns = _api("local_stt_status")
    this = types.SimpleNamespace(_load_settings_file=lambda: {"transcription_mode": "local"})
    r = ns["local_stt_status"](this)
    # ... and whether this computer suits it, for setup's first screen.
    assert r == {"ok": True, "mode": "local", **STATUS,
                 "suits_machine": True, "machine_reason": "apple_silicon"}
    assert seen == [{"transcription_mode": "local"}]
    this = types.SimpleNamespace(_load_settings_file=lambda: {})
    assert ns["local_stt_status"](this)["mode"] == "cloud"
    json.dumps(r)                                   # it crosses to the page as JSON


def test_download_starts_in_the_background_and_tells_the_page_as_it_moves(monkeypatch):
    told, started = [], {}

    def start_download(progress_cb=None, done_cb=None):
        started.update(progress=progress_cb, done=done_cb)
        return {**STATUS, "state": "downloading", "ok": True, "started": True}
    monkeypatch.setattr(local_stt, "is_available", lambda: True)
    monkeypatch.setattr(local_stt, "start_download", start_download)
    ns = _api("local_stt_download", _local_stt_changed=lambda: told.append(1))
    r = ns["local_stt_download"](types.SimpleNamespace(_load_settings_file=lambda: {}))
    assert r["ok"] is True and r["state"] == "downloading"
    warmed = []
    monkeypatch.setattr(local_stt, "warm", lambda: warmed.append(1))
    monkeypatch.setattr(local_stt, "mode_is_local", lambda stored=None: True)
    started["progress"](37, 100, 270)
    started["done"]({"state": "ready", "ok": True})
    assert len(told) == 2 and warmed == [1], "ready to use the moment it has arrived"
    started["done"]({"state": "not_downloaded", "ok": False, "error": "offline"})
    assert warmed == [1]
    assert any("download started" in line for line in ns["logged"])
    assert any("download ended: ready" in line for line in ns["logged"])


def test_download_says_so_when_this_build_has_no_engine(monkeypatch):
    monkeypatch.setattr(local_stt, "is_available", lambda: False)
    monkeypatch.setattr(local_stt, "model_status", lambda stored=None: {**STATUS, "available": False})
    monkeypatch.setattr(local_stt, "start_download",
                        lambda *a, **k: pytest.fail("nothing to download a model for"))
    ns = _api("local_stt_download")
    r = ns["local_stt_download"](types.SimpleNamespace(_load_settings_file=lambda: {}))
    assert r["ok"] is False and r["error"] == local_stt.NOT_INSTALLED and r["available"] is False


def test_cancel_and_remove(monkeypatch):
    told = []
    monkeypatch.setattr(local_stt, "cancel_download", lambda: True)
    monkeypatch.setattr(local_stt, "remove_model",
                        lambda: {**STATUS, "ok": True, "freed_bytes": 574041195})
    ns = _api("local_stt_cancel", "local_stt_remove", _local_stt_changed=lambda: told.append(1))
    this = types.SimpleNamespace()
    assert ns["local_stt_cancel"](this) == {"ok": True, "was_downloading": True}
    r = ns["local_stt_remove"](this)
    assert r["ok"] is True and r["freed_bytes"] == 574041195 and told == [1]
    assert any("574 MB" in line for line in ns["logged"])


def test_the_page_is_handed_the_new_state_as_json(monkeypatch):
    posted = []
    monkeypatch.setattr(local_stt, "model_status", lambda stored=None: dict(STATUS, percent=42))
    changed = _module_fn("_local_stt_changed", _post_js=posted.append)
    changed()
    assert posted and posted[0].startswith(
        "window.waffler_local_stt_changed && window.waffler_local_stt_changed({")
    assert json.loads(posted[0].split("changed(", 1)[1][:-1])["percent"] == 42
    # A page that is gone, or a status that cannot be read, is nobody's problem.
    monkeypatch.setattr(local_stt, "model_status", lambda stored=None: 1 / 0)
    changed()


# ── transcription_mode through get_settings and save_settings ────────────────

def _get_settings(stored, pipeline=None):
    ns = _api("get_settings", _pipeline=pipeline)
    return ns["get_settings"](types.SimpleNamespace(_load_settings_file=lambda: dict(stored)))


def test_get_settings_reports_the_mode_cloud_unless_local_is_saved(no_keys):
    assert _get_settings({})["transcription_mode"] == "cloud"
    assert _get_settings({"transcription_mode": "bogus"})["transcription_mode"] == "cloud"
    s = _get_settings({"transcription_mode": "local"})
    assert s["transcription_mode"] == "local" and s["local_whisper"] is True
    assert s["local_whisper_active"] is False       # nothing is running in this test


def test_in_local_mode_the_page_is_never_told_a_provider_hears_the_voice(monkeypatch, no_keys):
    """ui/logic.js knows "mlx" and "faster" as "on this Mac" / "on this
    computer". Any other answer and it works the provider out from the keys,
    and the privacy line would say the voice goes to Groq."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_not_a_real_key")
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    assert _get_settings({"transcription_mode": "local"})["transcription_backend"] == "mlx"
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert _get_settings({"transcription_mode": "local"})["transcription_backend"] == "faster"
    assert _get_settings({})["transcription_backend"] == "unknown"
    logic = (ROOT / "ui" / "logic.js").read_text(encoding="utf-8")
    assert "mlx: 'on this Mac'" in logic and "faster: 'on this computer'" in logic


def test_save_settings_stores_the_mode_and_applies_it(tmp_path, monkeypatch, no_keys):
    applied = []
    ns = _api("save_settings")
    api = settings_api(tmp_path, {"language": "en"},
                       _apply_transcription_mode=lambda mode: applied.append(mode))
    r = ns["save_settings"](api, {"transcription_mode": "local"})
    assert r["ok"] is True, r
    assert api.data == {"language": "en", "transcription_mode": "local"} and applied == ["local"]
    assert local_stt.mode_is_local(api.data)
    # Back to the cloud needs a key to use.
    r = ns["save_settings"](api, {"transcription_mode": "cloud"})
    assert r == {"ok": False, "error": local_stt.CLOUD_NEEDS_KEY}
    assert api.data["transcription_mode"] == "local" and applied == ["local"]
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_not_a_real_key")
    assert ns["save_settings"](api, {"transcription_mode": "cloud"})["ok"] is True
    assert api.data["transcription_mode"] == "cloud" and applied == ["local", "cloud"]


def test_save_settings_refuses_an_unknown_mode_and_saves_nothing_else(tmp_path, no_keys):
    ns = _api("save_settings")
    api = settings_api(tmp_path, {"language": "en"})
    r = ns["save_settings"](api, {"transcription_mode": "offline", "language": "fr"})
    assert r == {"ok": False, "error": local_stt.MODE_UNKNOWN}
    assert api.data == {"language": "en"} and api.saves == 0


def test_a_save_that_does_not_name_the_mode_leaves_it_alone(tmp_path, no_keys):
    ns = _api("save_settings")
    api = settings_api(tmp_path, {"transcription_mode": "local"})
    assert ns["save_settings"](api, {"auto_paste": False})["ok"] is True
    assert api.data == {"transcription_mode": "local", "auto_paste": False}


def test_the_old_local_whisper_switch_no_longer_writes_an_environment_variable(tmp_path, no_keys):
    """LOCAL_WHISPER=1 chose between two engines that were never shipped.
    There is one setting now, and it lives in settings.json."""
    assert "LOCAL_WHISPER" not in _SRC.replace('monkeypatch', '')
    assert "LOCAL_WHISPER" not in (ROOT / "src" / "transcribe_whisper.py").read_text(encoding="utf-8")


# ── applying the mode to what is running ─────────────────────────────────────

def test_choosing_local_tells_the_transcriber_now_and_loads_the_model(no_keys, monkeypatch):
    refreshed, warmed = [], threading.Event()
    monkeypatch.setattr(local_stt, "warm", warmed.set)
    pipeline = types.SimpleNamespace(transcriber=types.SimpleNamespace(
        _refresh_backend=lambda: refreshed.append(1)))
    ns = _api("_apply_transcription_mode", _pipeline=pipeline,
              _initialize_pipeline=lambda: pytest.fail("already running"))
    ns["_apply_transcription_mode"](object(), "local")
    assert refreshed == [1] and warmed.wait(5), "in memory before the first dictation"
    warmed.clear()
    ns["_apply_transcription_mode"](object(), "cloud")
    assert refreshed == [1, 1] and not warmed.wait(0.2), "the cloud needs no model"


def test_choosing_local_starts_a_waffler_that_was_waiting_for_a_key(no_keys):
    started = threading.Event()
    ns = _api("_apply_transcription_mode", _pipeline=None, _initialize_pipeline=started.set)
    ns["_apply_transcription_mode"](object(), "local")
    assert started.wait(5)
    # Not before setup is finished, and not for the cloud.
    for mode, done in (("local", False), ("cloud", True)):
        ns = _api("_apply_transcription_mode", _pipeline=None,
                  _is_setup_complete=lambda done=done: done,
                  _initialize_pipeline=lambda: pytest.fail("must not start"))
        ns["_apply_transcription_mode"](object(), mode)


# ── no key is needed in local mode ───────────────────────────────────────────

@pytest.mark.parametrize("done, key, mode, needs", [
    (True, "", "local", False),         # on this computer: no key, no wizard
    (True, "", "cloud", True),          # as before
    (True, "", None, True),             # nothing saved means cloud
    (False, "", "local", True),         # setup itself is still to do
    (True, "gsk_x", "local", False),
])
def test_setup_is_not_asked_for_again_when_speech_is_local(monkeypatch, done, key, mode, needs):
    ns = _api("get_onboarding_status", _is_setup_complete=lambda: done)
    monkeypatch.setenv("GROQ_API_KEY", key)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    stored = {} if mode is None else {"transcription_mode": mode}
    this = types.SimpleNamespace(_load_settings_file=lambda: dict(stored))
    r = ns["get_onboarding_status"](this)
    assert r["needs_setup"] is needs and r["has_key"] is bool(key)


def test_the_pipeline_is_built_without_a_key_only_in_local_mode(_isolated_data_dir):
    build = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_build_pipeline")
    made, logged = [], []

    class Config:
        has_api_key = False
        hotkey = "fn"

        def reload_env(self):
            pass

    class Stop(Exception):
        pass

    def pipeline(config):
        made.append(config)
        raise Stop()            # the rest of _build_pipeline is not this test's
    ns = {"_config": Config(), "_log_to_file": logged.append, "_local_stt": local_stt,
          "WafflerPipeline": pipeline, "_pipeline": None}
    exec(compile(ast.Module([build], []), "<app.py>", "exec"), ns)
    ns["_build_pipeline"]()
    assert made == [] and logged == ["Cannot initialize pipeline: no API key found"]
    (_isolated_data_dir / "settings.json").write_text('{"transcription_mode": "local"}', encoding="utf-8")
    try:
        ns["_build_pipeline"]()
    except Stop:
        pass
    assert len(made) == 1, "local mode: the pipeline is built with no key"


def test_the_key_checks_all_allow_local_mode():
    """The four places that used to stop a Waffler with no key."""
    init = next(n for n in _PIPELINE.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    init_src = ast.get_source_segment(_SRC, init)
    assert "if not groq_key and not openai_key and not _local_stt.mode_is_local():" in init_src
    assert "if not _config.has_api_key and not _local_stt.mode_is_local():" in _SRC
    assert "if (config.has_api_key or _local_stt.mode_is_local()) and _is_setup_complete():" in _SRC
    assert "and (config.has_api_key or _local_stt.mode_is_local())" in _SRC


# ── setup's practice dictation ───────────────────────────────────────────────

def _practice(settings, styler, monkeypatch, keys=()):
    """app.py's own _wizard_finish_practice, run with fakes."""
    import first_run
    import style_layer
    for name in ("GROQ_API_KEY", "OPENAI_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name in keys:
        monkeypatch.setenv(name, "test_not_a_real_key")
    node = next(n for n in _TREE.body
                if isinstance(n, ast.FunctionDef) and n.name == "_wizard_finish_practice")
    pushed, usage = [], []
    ns = {"_wizard_styler": styler, "_read_settings_now": lambda: settings, "_first_run": first_run,
          "_style_layer": style_layer, "_push_wizard_js": lambda fn, payload: pushed.append((fn, payload)),
          "record_usage_safely": lambda *a, **k: usage.append((a, k)),
          "append_history_safely": lambda e: True, "_log_to_file": lambda m: None,
          "os": os, "json": json}
    exec(compile(ast.Module([node], []), "<app.py>", "exec"), ns)
    ns["_wizard_finish_practice"]("so this is the practice", 3.0)
    return dict(pushed)["wizOnPracticeResult"], usage


class _Styler:
    def __init__(self):
        self.calls = []

    def style(self, text):
        self.calls.append(text)
        return text.capitalize() + ".", {"input_tokens": 1, "output_tokens": 1, "api_used": True,
                                         "provider": "groq"}


def test_the_practice_in_local_mode_with_no_key_shows_the_words_and_counts_them_as_local(monkeypatch):
    styler = _Styler()
    result, usage = _practice({"transcription_mode": "local"}, styler, monkeypatch)
    assert styler.calls == [] and result["wrote"] == "so this is the practice"
    assert result["cleaned"] is True and result["note"] == "", "no failure to explain"
    assert usage == [(("whisper",), {"duration_seconds": 3.0, "provider": "local"})]


def test_the_practice_in_local_mode_with_a_key_still_gets_the_clean_up(monkeypatch):
    styler = _Styler()
    result, usage = _practice({"transcription_mode": "local"}, styler, monkeypatch, keys=("GROQ_API_KEY",))
    assert styler.calls == ["so this is the practice"] and result["wrote"] == "So this is the practice."
    assert usage[0] == (("whisper",), {"duration_seconds": 3.0, "provider": "local"})


def test_the_practice_in_cloud_mode_is_counted_as_the_provider_as_before(monkeypatch):
    _result, usage = _practice({}, _Styler(), monkeypatch, keys=("GROQ_API_KEY",))
    assert usage[0] == (("whisper",), {"duration_seconds": 3.0, "provider": "groq"})


def test_the_practice_asks_for_a_key_only_when_speech_is_not_local():
    body = _SRC[_SRC.index("def wizard_start_hotkey_test"):_SRC.index("def wizard_stop_hotkey_test")]
    assert "if not openai_key and not groq_key and not _local_stt.mode_is_local():" in body
