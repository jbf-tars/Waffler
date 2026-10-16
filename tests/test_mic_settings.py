"""Setting: "mic_ranking", the ranked list of microphones (S12), and the bridge
calls around it. The real Api methods from app.py run against temporary folders
and a fake list of devices: no microphone is opened, nothing is shown.
"""
import ast
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import audio_devices  # noqa: E402
import mic_devices as md  # noqa: E402
import mic_ranking as mr  # noqa: E402
import user_messages as um  # noqa: E402
from _settings_fake import settings_api  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")

MAC_APIS = [{"name": "Core Audio"}]


def info(name, ch=1):
    return {"name": name, "max_input_channels": ch, "max_output_channels": 0, "hostapi": 0}


def pa(*names, default=0):
    return md.devices_from_portaudio([info(n) for n in names], MAC_APIS, default_index=default)


class FakePipeline:
    def __init__(self):
        self.applied = 0
        self.audio = types.SimpleNamespace(opened_device=lambda: "MacBook Pro Microphone")

    def apply_mic_preferences(self):
        self.applied += 1


class World:
    """The devices the fake system lists; change ``pa`` or ``live`` mid-test."""

    def __init__(self, monkeypatch, names=("MacBook Pro Microphone", "USB Headset", "Yeti"), default=0, live=None):
        self.pa = pa(*names, default=default)
        self.live = live
        self.legacy = None
        monkeypatch.setattr(md, "pa_inputs", lambda: list(self.pa))
        monkeypatch.setattr(md, "live_inputs", lambda: None if self.live is None else list(self.live))
        monkeypatch.setattr(md, "current_inputs",
                            lambda: list(self.live if self.live is not None else self.pa))


def bridge(tmp_path, world, initial=None, pipeline=None, names=()):
    """The Api's mic methods (and the settings ones) bound to a settings fake."""
    obj = settings_api(tmp_path, initial)
    logged = []
    ns = {"os": __import__("os"), "_pipeline": pipeline, "_log_to_file": logged.append,
          "_mic_devices": md, "_mic_ranking": mr, "get_selected_device_index": lambda: world.legacy,
          "CLIPBOARD_CHOICE_UNKNOWN": um.CLIPBOARD_CHOICE_UNKNOWN, "SOUNDS_UNKNOWN": um.SOUNDS_UNKNOWN,
          "TIPS_UNKNOWN": um.TIPS_UNKNOWN, "_languages": __import__("languages")}
    wanted = {"get_selected_device", "_mic_ranking_in", "_change_mic_ranking", "migrate_mic_ranking",
              "get_mic_setup", "set_mic_ranking", "set_audio_device", "save_settings", "get_settings"} | set(names)
    nodes = [n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in nodes} == wanted
    exec(compile(ast.Module(nodes, []), "<app.py Api>", "exec"), ns)
    for n in wanted:
        setattr(obj, n, types.MethodType(ns[n], obj))
    obj.logged = logged
    return obj


YETI = {"name": "Yeti", "id": "api:Core Audio"}


# ── reading ──────────────────────────────────────────────────────────────

def test_get_settings_carries_the_ranking_or_an_empty_list(tmp_path, monkeypatch):
    w = World(monkeypatch)
    assert bridge(tmp_path, w).get_settings()["mic_ranking"] == []
    api = bridge(tmp_path / "b", w, {"mic_ranking": [YETI, {"name": "USB Headset"}, 5, {"name": "yeti (2)"}]})
    assert api.get_settings()["mic_ranking"] == [YETI, {"name": "USB Headset"}]


# ── saving, as a setting ─────────────────────────────────────────────────

def test_save_settings_stores_the_list_and_applies_it_at_once(tmp_path, monkeypatch):
    w, pipe = World(monkeypatch), FakePipeline()
    api = bridge(tmp_path, w, {"auto_paste": False}, pipe)
    r = api.save_settings({"mic_ranking": [YETI, {"name": "USB Headset"}]})
    assert r["ok"] is True and any(n.startswith("Microphones: 2") for n in r["notes"])
    assert api.data == {"auto_paste": False, "mic_ranking": [YETI, {"name": "USB Headset"}]}
    assert pipe.applied == 1


def test_saving_an_empty_list_is_a_choice_to_follow_the_system(tmp_path, monkeypatch):
    w = World(monkeypatch)
    api = bridge(tmp_path, w, {"mic_ranking": [YETI]})
    assert api.save_settings({"mic_ranking": []})["ok"] is True
    assert api.data["mic_ranking"] == [] and mr.has_ranking(api.data)


@pytest.mark.parametrize("bad", ["Yeti", None, [{"name": "A"}, {"name": "A"}], [{"name": ""}],
                                 [{"name": f"M{i}"} for i in range(9)], [{"name": "A", "id": 5}], ["A"]])
def test_a_bad_list_is_refused_and_saves_nothing_of_the_payload(tmp_path, monkeypatch, bad):
    w, pipe = World(monkeypatch), FakePipeline()
    api = bridge(tmp_path, w, {"dialect": "en-GB", "mic_ranking": [YETI]}, pipe)
    r = api.save_settings({"mic_ranking": bad, "dialect": "en-US"})
    assert r == {"ok": False, "error": um.MIC_RANKING_UNKNOWN}
    assert api.data == {"dialect": "en-GB", "mic_ranking": [YETI]} and api.saves == 0 and pipe.applied == 0


def test_a_save_of_something_else_leaves_the_ranking_and_does_not_touch_the_recorder(tmp_path, monkeypatch):
    w, pipe = World(monkeypatch), FakePipeline()
    api = bridge(tmp_path, w, {"mic_ranking": [YETI]}, pipe)
    assert api.save_settings({"auto_paste": False})["ok"] is True
    assert api.data["mic_ranking"] == [YETI] and pipe.applied == 0


def test_a_recorder_that_fails_to_apply_never_fails_the_save(tmp_path, monkeypatch):
    class Broken:
        def apply_mic_preferences(self):
            raise RuntimeError("recorder gone")
    api = bridge(tmp_path, World(monkeypatch), None, Broken())
    assert api.save_settings({"mic_ranking": [YETI]})["ok"] is True
    assert any("not applied live" in line for line in api.logged)


# ── the page's calls ─────────────────────────────────────────────────────

def test_the_setup_lists_the_ranking_with_what_is_plugged_in(tmp_path, monkeypatch):
    w = World(monkeypatch)
    api = bridge(tmp_path, w, {"mic_ranking": [{"name": "Scarlett"}, YETI]}, FakePipeline())
    v = api.get_mic_setup()
    assert v["ok"] is True and v["listed"] is True and v["in_use"] == "MacBook Pro Microphone"
    assert [(r["name"], r["present"], r["active"]) for r in v["ranking"]] == [
        ("Scarlett", False, False), ("Yeti", True, True)]
    assert [a["name"] for a in v["add"]] == ["MacBook Pro Microphone", "USB Headset"]
    assert v["following_system"] is False and v["system"] == "MacBook Pro Microphone"


def test_with_no_list_the_setup_follows_the_system(tmp_path, monkeypatch):
    v = bridge(tmp_path, World(monkeypatch)).get_mic_setup()
    assert v["ranking"] == [] and v["following_system"] is True and v["in_use"] is None


def test_the_setup_says_when_the_list_of_devices_could_not_be_read(tmp_path, monkeypatch):
    w = World(monkeypatch, names=())
    assert bridge(tmp_path, w).get_mic_setup()["listed"] is False


def test_the_setup_never_raises(tmp_path, monkeypatch):
    w = World(monkeypatch)
    monkeypatch.setattr(md, "current_inputs", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    r = bridge(tmp_path, w).get_mic_setup()
    assert r == {"ok": False, "error": "Couldn't list your microphones. Try again."}


def test_the_page_saves_the_whole_list_and_gets_the_setup_back(tmp_path, monkeypatch):
    w, pipe = World(monkeypatch), FakePipeline()
    api = bridge(tmp_path, w, None, pipe)
    v = api.set_mic_ranking([{"name": "Yeti", "id": "api:Core Audio"}, {"name": "USB Headset"}])
    assert v["ok"] is True and [r["name"] for r in v["ranking"]] == ["Yeti", "USB Headset"]
    assert v["ranking"][0]["active"] is True
    assert api.data["mic_ranking"] == [YETI, {"name": "USB Headset"}] and pipe.applied == 1


def test_a_refused_list_from_the_page_says_why_and_saves_nothing(tmp_path, monkeypatch):
    api = bridge(tmp_path, World(monkeypatch), {"mic_ranking": [YETI]}, FakePipeline())
    r = api.set_mic_ranking([{"name": "A"}, {"name": "A"}])
    assert r == {"ok": False, "error": um.MIC_RANKING_UNKNOWN} and api.saves == 0


# ── the single microphone of an older version ────────────────────────────

def test_the_old_index_becomes_rank_one_once_and_the_old_key_is_left_alone(tmp_path, monkeypatch):
    w = World(monkeypatch)
    w.legacy = 2
    cfg = tmp_path / "config.json"
    cfg.write_text('{"audio_device_index": 2}', encoding="utf-8")
    api = bridge(tmp_path / "s", w)
    assert api.migrate_mic_ranking() is True
    assert api.data["mic_ranking"] == [{"name": "Yeti", "id": "api:Core Audio"}]
    assert api.migrate_mic_ranking() is False                 # once
    assert json.loads(cfg.read_text(encoding="utf-8")) == {"audio_device_index": 2}


def test_a_ranking_that_exists_is_never_replaced_by_the_old_index(tmp_path, monkeypatch):
    w = World(monkeypatch)
    w.legacy = 2
    api = bridge(tmp_path, w, {"mic_ranking": []})
    assert api.migrate_mic_ranking() is False and api.data["mic_ranking"] == []


def test_an_index_that_is_not_an_input_now_is_tried_again_later_not_guessed(tmp_path, monkeypatch):
    w = World(monkeypatch)
    w.legacy = 9
    api = bridge(tmp_path, w)
    assert api.migrate_mic_ranking() is False and "mic_ranking" not in api.data
    w.pa = pa("MacBook Pro Microphone", "USB Headset", "Yeti", "A", "B", "C", "D", "E", "F", "G")
    assert api.migrate_mic_ranking() is True                   # the device came back (at that number)
    assert api.data["mic_ranking"][0]["name"] == "G"


def test_no_old_key_means_nothing_to_migrate(tmp_path, monkeypatch):
    api = bridge(tmp_path, World(monkeypatch))
    assert api.migrate_mic_ranking() is False and api.data == {}


def test_the_setup_migrates_on_first_read(tmp_path, monkeypatch):
    w = World(monkeypatch)
    w.legacy = 1
    api = bridge(tmp_path, w)
    v = api.get_mic_setup()
    assert [r["name"] for r in v["ranking"]] == ["USB Headset"] and v["ranking"][0]["active"]
    assert api.data["mic_ranking"] == [{"name": "USB Headset", "id": "api:Core Audio"}]


def test_the_old_microphone_is_not_lost_when_a_new_one_is_put_first(tmp_path, monkeypatch):
    """Rank 1 for a new microphone while the ranking has not been written:
    the old one is carried in first, then the new one goes in front."""
    w = World(monkeypatch)
    w.legacy = 1
    api = bridge(tmp_path, w)
    new = api._change_mic_ranking(lambda r: mr.promote(r, {"name": "Yeti", "id": "api:Core Audio"}))
    assert [e["name"] for e in new] == ["Yeti", "USB Headset"]
    assert [e["name"] for e in api.data["mic_ranking"]] == ["Yeti", "USB Headset"]


def test_a_ranking_edit_is_one_locked_save(tmp_path, monkeypatch):
    api = bridge(tmp_path, World(monkeypatch), {"auto_paste": False})
    api._change_mic_ranking(lambda r: mr.promote(r, YETI))
    assert api.saves == 1 and api.data == {"auto_paste": False, "mic_ranking": [YETI]}


# ── the picker the setup practice still uses ─────────────────────────────

def test_picking_by_number_puts_that_microphone_first_by_name(tmp_path, monkeypatch):
    w, pipe = World(monkeypatch), FakePipeline()
    api = bridge(tmp_path, w, {"mic_ranking": [{"name": "USB Headset"}]}, pipe)
    r = api.set_audio_device(2)
    assert r == {"ok": True, "name": "Yeti"}
    assert [e["name"] for e in api.data["mic_ranking"]] == ["Yeti", "USB Headset"]
    assert pipe.applied == 1


def test_picking_a_number_that_is_not_a_microphone_is_refused(tmp_path, monkeypatch):
    api = bridge(tmp_path, World(monkeypatch), None, FakePipeline())
    r = api.set_audio_device(42)
    assert r["ok"] is False and "couldn't find that microphone" in r["error"].lower()
    assert api.set_audio_device("x")["ok"] is False
    assert api.saves == 0


def test_the_selected_device_is_the_one_in_use_or_the_system_default(tmp_path, monkeypatch):
    w = World(monkeypatch)
    api = bridge(tmp_path, w, {"mic_ranking": [{"name": "Scarlett"}, {"name": "Yeti"}]})
    assert api.get_selected_device() == {"index": 2, "name": "Yeti"}
    assert bridge(tmp_path / "b", w, {"mic_ranking": [{"name": "Scarlett"}]}).get_selected_device() == {
        "index": None, "name": "System Default"}
    assert bridge(tmp_path / "c", w).get_selected_device() == {"index": None, "name": "System Default"}


def test_the_selected_device_reads_the_old_index_while_there_is_no_ranking(tmp_path, monkeypatch):
    w = World(monkeypatch)
    w.legacy = 1
    assert bridge(tmp_path, w).get_selected_device() == {"index": 1, "name": "USB Headset"}


# ── the file lists ───────────────────────────────────────────────────────

def test_the_ranking_is_a_setting_and_the_decisions_are_a_data_file():
    import privacy_data
    assert "mic_devices.json" in privacy_data.DATA_FILES
    assert "settings.json" in privacy_data.KEPT_FILES          # the ranking stays with the settings


def test_the_logs_bundle_and_its_readme_leave_the_decisions_out():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "download_logs")
    listed = [c.value for c in ast.walk(fn) if isinstance(c, ast.Constant) and isinstance(c.value, str)
              and c.value.endswith(".json")]
    assert "mic_devices.json" not in listed
    assert "mic_devices.json" in ast.unparse(fn)                # named in the README as left out
