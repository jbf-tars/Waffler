"""Upgrading from 3.17.2 to 4.0.0: nothing lost, nothing asked again.

The in-app updater replaces the app and leaves the data folder
(~/.waffler-hosted, or WAFFLER_DATA_DIR) as 3.17.2 left it. This builds such a
folder, in the formats 3.17.2 wrote (read from ``git show v3.17.2:<file>``:
app.py, src/config.py, src/usage_log.py, src/transcribe_whisper.py,
src/vocab_rank.py, src/snippets.py, src/history_meta.py, src/unsent.py,
src/notifier.py, src/recent_audio.py, src/mic_ranking.py, ...), and opens it
with 4.0.0's own code: the src modules as they are, and app.py's functions and
Api methods lifted out of its source the way the other tests do (app.py cannot
be imported in the tests).

What 3.17.2 kept, file by file
------------------------------
history.json        a JSON list of entries: dictations (timestamp, text,
                    styled, word_count, text_is, id, and the history_meta
                    fields), an edited one (edited_text), a transform
                    (kind/before/after), the setup's practice entry (source
                    "setup") and a Not sent card (failed, unsent_id, audio_path)
                    whose recording is in unsent/.
settings.json       a JSON object: the keys Api.save_settings and the other
                    setters wrote (below, ``SETTINGS``).
.env                the keys file: KEY=value lines, read by python-dotenv
                    (src/config.py); 3.17.2 also wrote LOCAL_WHISPER there.
setup_complete.json {"complete": true, "completed_at": ...}.
vocab.json          the plain list of words; vocab_sounds.json
                    {word: [spellings]}; vocab_sounds_kept.json the everyday
                    spellings kept; vocab_stars.json the starred words.
usage.jsonl         one usage row per line; usage.json the older list, still
                    there for someone who never dictated after 3.15.
snippets.json       [{"trigger", "expansion"}].
notifications.json  {"version": 1, "kinds": {...}, "muted_categories": [...]}.
config.json         {"audio_device_index": n} (the single microphone of old).
debug_audio/        the kept recent recordings, rec-YYYYMMDD-HHMMSS.wav.
quality.jsonl       metadata lines, no words.
"""
import ast
import json
import os
import sys
import threading
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import action_hotkeys  # noqa: E402
import atomic_json  # noqa: E402
import double_tap  # noqa: E402
import history_edit  # noqa: E402
import journal_data  # noqa: E402
import learn_words  # noqa: E402
import local_stt  # noqa: E402
import mic_ranking  # noqa: E402
import notifier  # noqa: E402
import privacy_data  # noqa: E402
import recent_audio  # noqa: E402
import snippets  # noqa: E402
import style_layer  # noqa: E402
import transforms  # noqa: E402
import unsent  # noqa: E402
import usage_log  # noqa: E402
import vocab_rank  # noqa: E402

from _settings_fake import settings_api  # noqa: E402

_SRC = (ROOT / "app.py").read_text(encoding="utf-8")
_TREE = ast.parse(_SRC)
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


def _top(name, **ns):
    """A top-level function of app.py, run in ``ns``."""
    node = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    env = {"json": json, "os": os, "datetime": datetime, "_log_to_file": lambda m: None, **ns}
    exec(compile(ast.Module([node], []), "<app.py>", "exec"), env)
    return env[name]


def _api(name, **ns):
    """An Api method of app.py, run in ``ns``."""
    node = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == name)
    env = {"json": json, "os": os, "_log_to_file": lambda m: None, **ns}
    exec(compile(ast.Module([node], []), "<app.py Api>", "exec"), env)
    return env[name]


# ── the folder 3.17.2 left ───────────────────────────────────────────────────

NOW = datetime(2026, 11, 20, 9, 30, 0)


def _ts(days_ago, minutes=0):
    return (NOW - timedelta(days=days_ago, minutes=minutes)).isoformat(timespec="seconds")


UNSENT_ID = "recording-2026-11-19T17-04-12.wav"
KEPT_AUDIO = "rec-20261120-091500.wav"

HISTORY = [
    # From before ids (3.14): no id, no history_meta fields.
    {"timestamp": _ts(400), "text": "an old one from last year", "styled": "An old one from last year.",
     "word_count": 6},
    # The setup's practice entry (src/first_run.py journal_entry).
    {"timestamp": _ts(30), "text": "testing one two", "styled": "Testing one, two.", "word_count": 3,
     "text_is": "asr_filtered", "source": "setup", "id": 1},
    # A dictation with everything 3.17.2 wrote (app.py _process, history_meta.take_fields).
    {"timestamp": _ts(2), "text": "send the deck to malek by friday",
     "styled": "Send the deck to Malak by Friday.", "word_count": 7, "text_is": "asr_filtered",
     "id": 2, "duration_s": 4.2, "speech_s": 3.1, "speech_db": -28.0, "lang": "en",
     "provider": {"stt": "groq", "cleanup": "groq"}, "paste": "pasted", "app": "Mail",
     "app_bundle": "com.apple.mail", "category": "email", "style": "formal",
     "audio": KEPT_AUDIO, "vocab_changes": [["Malek", "Malak"]],
     "asr_text": "send the deck to malek by friday um",
     "quality": {"level": "warn", "flags": ["slow"], "words_per_speech_second": 2.3}},
    # Edited afterwards in the Journal (src/history_edit.py).
    {"timestamp": _ts(1), "text": "the meeting is at three", "styled": "The meeting is at 3.",
     "word_count": 5, "id": 3, history_edit.KEY: "The meeting is at 3pm.", "as_said": "limit"},
    # A transform (src/transforms.py history_entry): no text, no styled.
    {"timestamp": _ts(1, 5), "kind": "transform", "before": "teh draft", "after": "The draft.",
     "transform_id": "polish", "transform_name": "Polish", "word_count": 2, "id": 4,
     "provider": {"transform": "groq"}, "took_s": 1.2},
    # Not sent: the recording waits in unsent/ (app.py _save_unsent).
    {"timestamp": _ts(0, 25), "text": "", "styled": unsent.STYLED_NOTE, "word_count": 0,
     "failed": True, "error": "Connection error.", "audio_path": "", "unsent_id": UNSENT_ID,
     "not_sent_reason": "offline", "provider_name": "Groq", "will_retry": True, "id": 5,
     "duration_s": 6.0, "app": "Slack"},
]

SETTINGS = {
    # The hotkey and the shortcuts (Api.set_hotkey, shortcut_settings).
    "hotkey_keys": ["ctrl", "option"],
    "paste_last_hotkey": ["ctrl", "option", "v"],
    "copy_last_hotkey": ["ctrl", "option", "c"],
    "cancel_key": "f8",
    "double_tap_handsfree": True,
    # Transcription and clean-up.
    "language": "en",
    "dialect": "en-GB",
    "provider_order": ["groq", "cerebras", "openai"],
    "prompt_style": "normal",
    "cleanup": "on",
    "style_by_category": {"personal": "very_casual", "work": "excited", "email": "formal", "other": "auto"},
    "email_signature": {"enabled": True, "text": "Best,\nSam"},
    # A Mac's keys, saved on a Mac: merged() checks them for the platform it is told.
    "transforms": transforms.merged({}, {"transforms": {"enabled": True,
                                                        "hotkey": ["ctrl", "option", "t"]}},
                                    platform=action_hotkeys.MAC),
    "command_mode": {"enabled": True, "hotkey": ["ctrl", "option", "k"], "web_engine": "perplexity"},
    # Pasting, sounds, tips, the pill.
    "auto_paste": True,
    "clipboard_after_paste": "keep",
    "sounds": False,
    "tips": False,
    "idle_pill": True,
    "learn_from_edits": True,
    # Microphones (src/mic_ranking.py).
    "mic_ranking": [{"name": "Shure MV7"}, {"name": "MacBook Pro Microphone"}],
    # Privacy and data, Usage, the look.
    "keep_recent_audio": True,
    "history_keep_days": 0,
    "typing_wpm": 55,
    "theme": "dark",
    # One-time jobs 3.15 and 3.17 marked done (src/privacy_data.py).
    privacy_data.LOG_SCRUB_SETTING: True,
    privacy_data.VOCAB_REDACT_SETTING: True,
}

ENV = ("GROQ_API_KEY=gsk_test_groq\n"
       "OPENAI_API_KEY=sk-test-openai\n"
       "CEREBRAS_API_KEY=csk-test-cerebras\n"
       "LOCAL_WHISPER=0\n")
KEYS = {"GROQ_API_KEY": "gsk_test_groq",
        "OPENAI_API_KEY": "sk-test-openai",
        "CEREBRAS_API_KEY": "csk-test-cerebras"}

VOCAB = ["Malak", "Waffler", "Groq", "Kubernetes", "Siobhán"]
SOUNDS = {"Waffler": ["waffle her", "waffla"], "Groq": ["grok", "grock"], "Malak": ["Malek"],
          "Kubernetes": ["your"]}
SOUNDS_KEPT = {"Kubernetes": ["your"]}
STARS = ["Malak", "Siobhán"]

USAGE_ROWS = [
    {"timestamp": _ts(2), "type": "whisper", "provider": "groq", "input_tokens": 0, "output_tokens": 0,
     "cost_usd": 0.000155, "model": "whisper-large-v3", "rate_verified": True, "duration_seconds": 4.2},
    {"timestamp": _ts(2), "type": "gpt", "provider": "groq", "input_tokens": 812, "output_tokens": 14,
     "cost_usd": 0.000132, "model": "openai/gpt-oss-120b", "rate_verified": True},
    {"timestamp": _ts(1, 5), "type": "gpt", "provider": "groq", "input_tokens": 300, "output_tokens": 9,
     "cost_usd": 0.00005, "model": "openai/gpt-oss-120b", "rate_verified": True, "purpose": "transform"},
]
LEGACY_USAGE = [
    {"timestamp": _ts(90), "type": "whisper", "provider": "openai", "input_tokens": 0, "output_tokens": 0,
     "cost_usd": 0.0006, "duration_seconds": 6.0},
]

SNIPPETS = [{"trigger": "my address", "expansion": "1 High Street\nLondon"},
            {"trigger": "sig", "expansion": "Thanks, Sam"}]

NOTIFICATIONS = {"version": 1,
                 "kinds": {"hold_hint": {"shown": 3, "last_shown": 1763000000.0, "dismissed": 2,
                                         "muted": True},
                           "lid_closed": {"shown": 1, "last_shown": 1763100000.0, "dismissed": 0,
                                          "muted": False}},
                 "muted_categories": []}

WAV = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"


@pytest.fixture
def old(_isolated_data_dir):
    """The data folder as 3.17.2 left it (the per-test WAFFLER_DATA_DIR)."""
    d = _isolated_data_dir

    def put(name, data):
        (d / name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    put("history.json", HISTORY)
    put("settings.json", SETTINGS)
    (d / ".env").write_text(ENV, encoding="utf-8")
    put("setup_complete.json", {"complete": True, "completed_at": _ts(30)})
    put("vocab.json", VOCAB)
    put("vocab_sounds.json", SOUNDS)
    put("vocab_sounds_kept.json", SOUNDS_KEPT)
    put("vocab_stars.json", STARS)
    (d / "usage.jsonl").write_text("".join(json.dumps(r) + "\n" for r in USAGE_ROWS), encoding="utf-8")
    put("usage.json", LEGACY_USAGE)
    put("snippets.json", SNIPPETS)
    put("notifications.json", NOTIFICATIONS)
    put("config.json", {"audio_device_index": 2})
    (d / "unsent").mkdir()
    (d / "unsent" / UNSENT_ID).write_bytes(WAV)
    (d / recent_audio.DIRNAME).mkdir()
    (d / recent_audio.DIRNAME / KEPT_AUDIO).write_bytes(WAV)
    (d / "quality.jsonl").write_text(json.dumps({"timestamp": _ts(2), "level": "warn"}) + "\n",
                                     encoding="utf-8")
    return d


# ── the Journal ──────────────────────────────────────────────────────────────

def _history_fns(d):
    """app.py's history functions on folder ``d``, as main() and the window use them."""
    ns = {"DATA_DIR": d, "HISTORY_FILE": d / "history.json",
          "read_json_for_update": atomic_json.read_json_for_update,
          "write_json_atomic": lambda p, x: atomic_json.write_json_atomic(p, x, sleep=lambda s: None),
          "_privacy": privacy_data, "_history_retention_day": None}
    ns["ensure_data_dir"] = _top("ensure_data_dir", **ns)
    for name in ("load_history", "_load_history_for_update", "save_history", "_history_keep_days"):
        ns[name] = _top(name, **ns)
    ns["_retain_history"] = _top("_retain_history", **ns)
    return ns


def test_every_journal_entry_survives_the_first_start(old):
    ns = _history_fns(old)
    before = json.loads((old / "history.json").read_text(encoding="utf-8"))
    # main(): retention at start-up, forced, then saved only if it removed any.
    history = ns["_load_history_for_update"]()
    kept = ns["_retain_history"](history, force=True)
    assert kept == before == HISTORY, "Keep everything (3.17.2's default) keeps everything"
    # What the window reads is every entry, as written.
    assert ns["load_history"]() == HISTORY
    assert not list(old.glob("history.unreadable-*.json"))


def test_the_journal_shows_old_entries_with_their_words(old):
    history = _history_fns(old)["load_history"]()
    page = journal_data.page(history, limit=50)
    shown = {e.get("id"): e for e in page}
    # Every entry is on the page, the one from before ids included.
    assert len(page) == len(HISTORY)
    assert any(e.get("text") == "an old one from last year" for e in page)
    assert history_edit.final_text(shown[3]) == "The meeting is at 3pm."
    assert shown[2]["vocab_changes"] == [["Malek", "Malak"]] and shown[2]["audio"] == KEPT_AUDIO
    assert shown[4]["after"] == "The draft."
    # Counts and search work on them.
    stats = journal_data.compute_stats(history, NOW.date())
    assert stats["total_words"] >= 7 + 5 + 3 + 6
    assert journal_data.matches(shown[2], "Malak")


def test_the_not_sent_recording_is_still_waiting(old):
    history = _history_fns(old)["load_history"]()
    waiting = unsent.pending(history, old / "unsent")
    assert [(uid, e["id"], path) for uid, e, path in waiting] == [
        (UNSENT_ID, 5, (old / "unsent" / UNSENT_ID).resolve())]
    assert unsent.is_waiting(waiting[0][1])
    # The kept recent recording behind "Redo this take" is found too.
    assert recent_audio.resolve(old, KEPT_AUDIO) == old / recent_audio.DIRNAME / KEPT_AUDIO
    assert recent_audio.enabled(SETTINGS)


def test_a_new_entry_after_the_update_carries_on_the_ids(old):
    ns = _history_fns(old)
    import history_ids
    ns.update({"_history_lock": threading.Lock(), "_history_ids": history_ids})
    append = _top("append_history", **ns)
    entry = {"timestamp": NOW.isoformat(timespec="seconds"), "text": "first in four", "styled": "First in 4.",
             "word_count": 3}
    append(entry)
    after = ns["load_history"]()
    assert after[:-1] == HISTORY and after[-1]["id"] == 6


# ── settings ─────────────────────────────────────────────────────────────────

def test_settings_are_read_as_they_were(old):
    api = settings_api(old)
    stored = api._load_settings_file()
    assert stored == SETTINGS
    # And as the features read them.
    assert mic_ranking.ranking_of(stored) == SETTINGS["mic_ranking"] and mic_ranking.has_ranking(stored)
    assert style_layer.styles_by_category(stored) == SETTINGS["style_by_category"]
    assert style_layer.signature_settings(stored) == {"enabled": True, "text": "Best,\nSam"}
    assert transforms.settings(stored)["enabled"] is True
    assert transforms.settings(stored)["hotkey"] == SETTINGS["transforms"]["hotkey"] != []
    assert notifier.tips_enabled(stored) is False
    assert learn_words.enabled(stored) is True, "switched on by hand: still on"
    get_theme = _api("get_theme")
    assert get_theme(api) == {"theme": "dark"}


def test_new_settings_read_an_old_file_as_the_old_behaviour(old):
    stored = settings_api(old)._load_settings_file()
    # No transcription_mode: the cloud, on the keys, as before.
    assert local_stt.mode_of(stored) == "cloud" and not local_stt.mode_is_local(stored)
    import account
    assert account.plus_chosen(stored) is False
    # Nothing new is switched on behind the person's back.
    assert stored.get("account_offer_dismissed") is None


def test_the_one_time_jobs_already_done_are_not_done_again(old):
    (old / "app.log").write_text("09:00:00  === Waffler starting === (v3.17.2)\n", encoding="utf-8")
    result = privacy_data.tidy_logs_at_start(old, old / "settings.json", redact_vocab=True)
    assert result["scrubbed"] is None and "vocab_redacted" not in result
    assert json.loads((old / "settings.json").read_text(encoding="utf-8")) == SETTINGS


def test_the_mic_ranking_is_not_migrated_over(old):
    """main() starts migrate_mic_ranking; with a ranking saved it changes nothing."""
    api = settings_api(old)
    logged = []
    asked = []
    fn = _api("migrate_mic_ranking", _mic_ranking=mic_ranking,
              get_selected_device_index=lambda: asked.append("index") or 2,
              _mic_devices=types.SimpleNamespace(pa_inputs=lambda: [], live_inputs=lambda: []),
              _log_to_file=logged.append)
    assert fn(api) is False and asked == [] and logged == []
    assert settings_api(old)._load_settings_file()["mic_ranking"] == SETTINGS["mic_ranking"]


# ── hands-free double-tap and the new meeting tap ────────────────────────────

def _keep_taps(d, logged):
    return _top("_keep_hands_free_taps", DATA_DIR=d, _settings_lock=threading.RLock(),
                _double_tap=double_tap, write_json_atomic=atomic_json.write_json_atomic,
                _log_to_file=logged.append)


def test_a_hands_free_double_tap_is_not_taken_over_by_meetings(old):
    """3.17.2: two taps of the hotkey went hands-free (switched on by hand).
    4.0.0's meeting tap is on unless switched off and takes two taps first, so
    without the start-up check the update turned that person's two taps into
    a meeting recording."""
    logged = []
    _keep_taps(old, logged)()
    stored = json.loads((old / "settings.json").read_text(encoding="utf-8"))
    assert stored["meeting_double_tap"] is False and stored["meeting_double_tap_checked"] is True
    assert {k: v for k, v in stored.items() if not k.startswith("meeting_double_tap")} == SETTINGS
    assert len(logged) == 1 and "hands-free" in logged[0]
    # What the listener is given at start (apply_meeting_tap) and what Settings shows.
    assert (stored.get("meeting_double_tap") is not False) is False
    assert double_tap.enabled(stored) is True
    # Once only: a meeting tap turned on afterwards stays on.
    stored["meeting_double_tap"] = True
    atomic_json.write_json_atomic(old / "settings.json", stored)
    _keep_taps(old, logged)()
    assert json.loads((old / "settings.json").read_text(encoding="utf-8"))["meeting_double_tap"] is True


@pytest.mark.parametrize("stored, meeting", [
    ({}, None),                                                    # never touched: meetings get the taps
    ({"double_tap_handsfree": False}, None),
    ({"double_tap_handsfree": True, "meeting_double_tap": True}, True),   # a 4.0 choice wins
    ({"double_tap_handsfree": True, "meeting_double_tap_checked": True}, None),  # done already
])
def test_the_meeting_tap_is_left_alone_otherwise(tmp_path, stored, meeting):
    (tmp_path / "settings.json").write_text(json.dumps(stored), encoding="utf-8")
    _keep_taps(tmp_path, [])()
    after = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert after.get("meeting_double_tap") is meeting
    assert after.get("meeting_double_tap_checked") is True


def test_the_tap_check_writes_nothing_without_a_readable_settings_file(tmp_path):
    _keep_taps(tmp_path, [])()
    assert not (tmp_path / "settings.json").exists(), "a new install gets no file from it"
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    logged = []
    _keep_taps(tmp_path, logged)()
    assert (tmp_path / "settings.json").read_text(encoding="utf-8") == "{not json"
    assert logged == ["[double-tap] upgrade check skipped: JSONDecodeError"]


def test_main_runs_the_tap_check_before_the_hotkey_is_made():
    main = _SRC[_SRC.index("\ndef main():"):]
    assert main.index("_keep_hands_free_taps()") < main.index("_initialize_pipeline()")


# ── keys ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def clean_env(monkeypatch):
    """The key variables empty, and put back as they were after the test
    (python-dotenv writes to os.environ itself)."""
    for name in (*KEYS, "LOCAL_WHISPER", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "")
    return monkeypatch


def test_the_keys_file_is_read_by_4_0(old, clean_env):
    import config
    cfg = config.Config(str(ROOT / "config.yaml"))
    cfg.reload_env()                  # reads <data folder>/.env, as at start
    assert cfg.groq_api_key == KEYS["GROQ_API_KEY"] and cfg.openai_api_key == KEYS["OPENAI_API_KEY"]
    assert cfg.cerebras_api_key == KEYS["CEREBRAS_API_KEY"] and cfg.has_api_key


def test_meetings_find_the_same_keys(old, clean_env):
    import meetings_ctl
    meetings_ctl._share_waffler_keys()
    assert os.environ["GROQ_API_KEY"] == KEYS["GROQ_API_KEY"]
    assert os.environ["OPENAI_API_KEY"] == KEYS["OPENAI_API_KEY"]


def test_changing_a_key_in_4_0_keeps_the_rest_of_the_old_file(old):
    update = _api("_update_env_var", DATA_DIR=old)
    update(object(), "GROQ_API_KEY", "gsk_test_new_key")
    lines = (old / ".env").read_text(encoding="utf-8").splitlines()
    assert lines == ["GROQ_API_KEY=gsk_test_new_key", f"OPENAI_API_KEY={KEYS['OPENAI_API_KEY']}",
                     f"CEREBRAS_API_KEY={KEYS['CEREBRAS_API_KEY']}", "LOCAL_WHISPER=0"]


# ── first-run screens ────────────────────────────────────────────────────────

def test_setup_counts_as_done(old):
    done = _top("_is_setup_complete", SETUP_FILE=old / "setup_complete.json")
    assert done() is True


def test_onboarding_says_no_setup_and_home_opens(old, clean_env):
    for k, v in KEYS.items():
        clean_env.setenv(k, v)        # what config.py loaded from .env
    status = _api("get_onboarding_status",
                  _is_setup_complete=_top("_is_setup_complete", SETUP_FILE=old / "setup_complete.json"),
                  _plus_chosen_here=lambda: False)
    r = status(settings_api(old))
    assert r["needs_setup"] is False and r["setup_complete"] is True
    assert r["has_groq_key"] and r["has_openai_key"] and r["resume_step"] == ""
    assert r["transcription_mode"] == "cloud"
    assert r["just_updated"] == "", "no update outcome recorded: nothing to say"


def test_home_says_once_that_the_update_went_in(old, clean_env):
    clean_env.setenv("GROQ_API_KEY", KEYS["GROQ_API_KEY"])
    import updater
    updater.record_pending_update("4.0.0", base_dir=old)    # what 3.17.2 wrote before restarting
    pending = updater.check_pending_update("4.0.0", base_dir=old)
    assert pending["ok"] is True and pending["expected"] == "4.0.0"
    assert not (old / updater.PENDING_MARKER_NAME).exists()
    ns = {"_UPDATE_DONE_NOTICE": pending["expected"],            # main() keeps it
          "_is_setup_complete": lambda: True, "_plus_chosen_here": lambda: False}
    status = _api("get_onboarding_status", **ns)
    api = settings_api(old)
    assert status(api)["just_updated"] == "4.0.0"
    assert status(api)["just_updated"] == "", "said once"


def test_the_update_notice_waits_while_setup_is_needed(old, clean_env):
    status = _api("get_onboarding_status", _UPDATE_DONE_NOTICE="4.0.0",
                  _is_setup_complete=lambda: True, _plus_chosen_here=lambda: False)
    r = status(settings_api(old))              # no key in the environment: setup again
    assert r["needs_setup"] is True and r["just_updated"] == ""
    assert status.__globals__["_UPDATE_DONE_NOTICE"] == "4.0.0", "not used up"


def test_main_keeps_a_good_update_outcome_for_home():
    main = _SRC[_SRC.index("\ndef main():"):]
    block = main[main.index("_upd.check_pending_update(_waffler_version)"):]
    block = block[:block.index("except Exception as _e:")]
    assert 'globals()["_UPDATE_FAILURE_NOTICE"] = _pending' in block
    assert 'globals()["_UPDATE_DONE_NOTICE"] = _pending["expected"]' in block


def test_the_pipeline_starts_without_setup(old, clean_env):
    """main() builds the pipeline (the hotkey works at once) when there is a
    key and setup is done; 3.17.2's folder has both."""
    import config
    cfg = config.Config(str(ROOT / "config.yaml"))
    cfg.reload_env()
    done = _top("_is_setup_complete", SETUP_FILE=old / "setup_complete.json")()
    cond = "(config.has_api_key or _local_stt.mode_is_local() or _plus_chosen_here()) and _is_setup_complete()"
    assert cond in _SRC
    assert (cfg.has_api_key or local_stt.mode_is_local() or False) and done


# ── vocabulary, snippets, usage, tips ────────────────────────────────────────

def test_vocabulary_stars_and_sounds_are_all_there(old, monkeypatch):
    import transcribe_whisper as tw
    monkeypatch.setattr(tw, "VOCAB_FILE", old / "vocab.json")
    monkeypatch.setattr(tw, "VOCAB_SOUNDS_FILE", old / "vocab_sounds.json")
    assert tw.load_vocab() == VOCAB
    assert tw.load_sounds_like() == SOUNDS
    assert tw.load_sounds_kept() == SOUNDS_KEPT
    assert vocab_rank.load_stars(VOCAB) == STARS
    assert vocab_rank.load_stars(VOCAB, path=old / vocab_rank.STARS_FILE) == STARS
    # Saving the list in 4.0 keeps the spellings and the stars.
    out = tw.save_vocab(VOCAB + ["Natter"])
    assert out["ok"] and tw.load_sounds_like() == SOUNDS
    assert vocab_rank.sync_stars(tw.load_vocab()) == STARS


def test_snippets_are_all_there(old):
    assert snippets.load(old / "snippets.json") == SNIPPETS
    assert snippets.apply("my address", SNIPPETS) != "my address"


def test_usage_moves_in_once_and_loses_no_row(old):
    ns = {"DATA_DIR": old, "USAGE_FILE": old / "usage.json", "USAGE_LOG_FILE": old / "usage.jsonl",
          "_usage_lock": threading.Lock(), "_usage_log": usage_log}
    ns["ensure_data_dir"] = _top("ensure_data_dir", DATA_DIR=old)
    _top("_move_old_usage", **ns)()                 # main(), on its own thread
    rows = _top("load_usage", **ns)()
    assert rows == USAGE_ROWS + LEGACY_USAGE
    assert not (old / "usage.json").exists() and len(list(old.glob("usage.backup-*.json"))) == 1
    _top("_move_old_usage", **ns)()                 # the next start: nothing more
    assert _top("load_usage", **ns)() == rows


def test_tips_hidden_in_3_17_stay_hidden(old):
    n = notifier.Notifier(store=notifier.FileStore(old / "notifications.json"),
                          context=lambda: notifier.Context(setup_done=True, tips_enabled=True))
    assert n.could_show("hold_hint") is False          # muted with "Don't show again"
    assert n.could_show("mic_disconnected") is True    # an error is never a tip


# ── what is new ──────────────────────────────────────────────────────────────

def test_about_has_a_new_in_4_0_and_home_a_line_for_it():
    logic = (ROOT / "ui" / "logic.js").read_text(encoding="utf-8")
    news = logic[logic.index("const WHATS_NEW = {"):logic.index("function whatsNew(")]
    assert "'4.0': [" in news
    app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    check = app[app.index("async function checkOnboarding()"):app.index("function showWizard(")]
    assert "_showJustUpdated(status.just_updated)" in check
    shown = app[app.index("function _showJustUpdated("):]
    shown = shown[:shown.index("\n}")]
    assert "WL.justUpdatedView(version)" in shown and "_journalNotices()" in shown
    assert "_settingsSection = 'about'" in shown and "showPage('settings')" in shown
