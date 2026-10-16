"""The bridge calls behind Vocabulary v2 (S10): stars, export, import with a
preview, snippets import and export, and "Edit text" with its suggestions.

app.py's methods are lifted out of its source and run against a temporary data
folder, as the other bridge tests do: no window, no network, no clipboard, no
real Desktop. The Vocabulary files are the ones 3.15.1 reads, and the tests
check that it still reads them.
"""
import ast
import json
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import atomic_json  # noqa: E402
import data_paths  # noqa: E402
import dictionary  # noqa: E402
import export_files  # noqa: E402
import history_edit  # noqa: E402
import history_meta  # noqa: E402
import history_ops  # noqa: E402
import snippets  # noqa: E402
import snippets_io  # noqa: E402
import table_io  # noqa: E402
import transcribe_whisper as tw  # noqa: E402
import user_messages as um  # noqa: E402
import vocab_rank  # noqa: E402
from _settings_fake import settings_api  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")

NAMES = ("set_vocab", "get_vocab_book", "_vocab_state", "export_vocabulary", "preview_vocab_import",
         "import_vocabulary", "_snippets_file", "get_snippets", "set_snippets", "export_snippets",
         "preview_snippets_import", "import_snippets", "edit_history_text")


class Env:
    """A bridge object with the real methods, in a temporary folder."""


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = data_paths.data_dir()                      # the per-test folder conftest made
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(tw, "VOCAB_FILE", data / "vocab.json")
    monkeypatch.setattr(tw, "VOCAB_SOUNDS_FILE", data / "vocab_sounds.json")
    logged, revealed, cache_calls, swaps = [], [], [], []
    settings = settings_api(data)

    ns = {
        "json": json, "_log_to_file": logged.append, "DATA_DIR": data,
        "_snippets": snippets, "_snippets_lock": threading.Lock(),
        "read_json_for_update": atomic_json.read_json_for_update,
        "write_json_atomic": lambda p, d: atomic_json.write_json_atomic(p, d, sleep=lambda s: None),
        "_dictionary": dictionary, "_vocab_rank": vocab_rank, "_table_io": table_io,
        "_snippets_io": snippets_io, "_history_edit": history_edit, "_history_meta": history_meta,
        "_history_ops": history_ops,
        "_export_files": types.SimpleNamespace(
            save=lambda data_, stem, ext: export_files.save(data_, stem, ext, folder=desktop),
            reveal_in_folder=lambda path, system: revealed.append(path)),
        "_platform": types.SimpleNamespace(system=lambda: "Darwin"),
        "_history_lock": threading.Lock(),
        "_history_cache": types.SimpleNamespace(
            invalidate=lambda: cache_calls.append(1),
            vocab_usage=lambda: {"by_entry": {"groq": {"count": 4, "last": "2026-10-05T10:00:00"}},
                                 "recent": []}),
        "_pipeline": types.SimpleNamespace(swap_last_text=lambda old, new="": swaps.append((old, new))),
        "VOCAB_EXPORT_EMPTY": um.VOCAB_EXPORT_EMPTY, "SNIPPETS_EXPORT_EMPTY": um.SNIPPETS_EXPORT_EMPTY,
        "EXPORT_FAILED": um.EXPORT_FAILED, "EXPORT_FORMAT_UNKNOWN": um.EXPORT_FORMAT_UNKNOWN,
        "IMPORT_FAILED": um.IMPORT_FAILED, "EDIT_EMPTY": um.EDIT_EMPTY, "EDIT_TOO_LONG": um.EDIT_TOO_LONG,
        "EDIT_NOT_FOUND": um.EDIT_NOT_FOUND, "EDIT_NOT_EDITABLE": um.EDIT_NOT_EDITABLE,
        "EDIT_FAILED": um.EDIT_FAILED,
    }

    def load_history():
        f = data / "history.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []

    ns["_load_history_for_update"] = load_history
    ns["save_history"] = lambda h: atomic_json.write_json_atomic(data / "history.json", h, sleep=lambda s: None)

    nodes = [n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name in NAMES]
    assert {n.name for n in nodes} == set(NAMES), "app.py Api methods moved"
    exec(compile(ast.Module(nodes, []), "<app.py Api>", "exec"), ns)

    class Api:
        _load_settings_file = lambda self: settings._load_settings_file()      # noqa: E731

    for n in NAMES:
        setattr(Api, n, ns[n])
    e = Env()
    e.api, e.data, e.desktop, e.logged, e.revealed = Api(), data, desktop, logged, revealed
    e.cache_calls, e.swaps, e.ns, e.settings = cache_calls, swaps, ns, settings
    return e


def write_history(env, entries):
    (env.data / "history.json").write_text(json.dumps(entries), encoding="utf-8")


def history(env):
    return json.loads((env.data / "history.json").read_text(encoding="utf-8"))


def csv_bytes(text):
    return text.encode("utf-8-sig")


def b64(text):
    import base64
    return base64.b64encode(csv_bytes(text)).decode("ascii")


# ── stars ────────────────────────────────────────────────────────────────────

def test_stars_are_saved_with_the_words_and_come_back(env):
    r = env.api.set_vocab(["Groq", "Morta"], {"Groq": ["grok"]}, ["Morta"])
    assert r["ok"] and r["starred"] == ["Morta"]
    assert json.loads((env.data / "vocab_stars.json").read_text(encoding="utf-8")) == ["Morta"]
    book = env.api.get_vocab_book()
    assert book["starred"] == ["Morta"]
    assert [e["word"] for e in book["entries"]] == ["Groq", "Morta"]
    assert book["entries"][0] == {"word": "Groq", "sounds_like": ["grok"], "count": 4,
                                  "last": "2026-10-05T10:00:00"}


def test_a_save_that_names_no_stars_keeps_the_stars_of_words_still_listed(env):
    env.api.set_vocab(["Groq", "Morta"], None, ["Morta", "Groq"])
    r = env.api.set_vocab(["Morta"])                       # the page of 3.15.1 sends the words alone
    assert r["starred"] == ["Morta"]
    assert json.loads((env.data / "vocab_stars.json").read_text(encoding="utf-8")) == ["Morta"]


def test_an_empty_set_of_stars_clears_them(env):
    env.api.set_vocab(["Groq"], None, ["Groq"])
    assert env.api.set_vocab(["Groq"], None, [])["starred"] == []


def test_the_files_3_15_1_reads_are_unchanged_by_stars(env):
    env.api.set_vocab(["Groq", "Sinéad"], {"Groq": ["grok"]}, ["Sinéad"])
    # vocab.json is still a plain list of strings, vocab_sounds.json still a dict.
    assert json.loads((env.data / "vocab.json").read_text(encoding="utf-8")) == ["Groq", "Sinéad"]
    assert json.loads((env.data / "vocab_sounds.json").read_text(encoding="utf-8")) == {"Groq": ["grok"]}
    # And 3.15.1's own loader, which knows nothing of the stars file, reads them.
    assert tw.load_vocab() == ["Groq", "Sinéad"] and tw.load_sounds_like() == {"Groq": ["grok"]}


def test_a_star_that_cannot_be_written_does_not_cost_the_words(env, monkeypatch):
    def boom(*a, **k):
        raise PermissionError("[WinError 5] Access is denied: 'C:\\Users\\james\\vocab_stars.json'")

    monkeypatch.setattr(vocab_rank, "sync_stars", boom)
    r = env.api.set_vocab(["Groq"], None, ["Groq"])
    assert r["ok"] and r["words"] == ["Groq"] and r["starred"] == []
    assert tw.load_vocab() == ["Groq"]
    assert env.logged and not any("james" in line for line in env.logged)


def test_a_vocab_json_that_the_save_refuses_leaves_the_stars_alone(env):
    env.api.set_vocab(["Groq"], None, ["Groq"])
    r = env.api.set_vocab(["x" * 61], None, [])
    assert r["ok"] is False
    assert vocab_rank.load_stars(["Groq"]) == ["Groq"]


# ── export ───────────────────────────────────────────────────────────────────

def test_export_writes_a_csv_on_the_desktop_and_shows_it(env):
    env.api.set_vocab(["Groq", "=cmd", "Sinéad"], {"Groq": ["grok", "grock"]}, ["Sinéad"])
    r = env.api.export_vocabulary()
    assert r["ok"] and r["format"] == "csv" and r["count"] == 3
    path = Path(r["path"])
    assert path.parent == env.desktop and path.name.startswith("waffler-vocabulary-") and path.suffix == ".csv"
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = table_io.parse(raw.decode("utf-8-sig"))
    assert rows == [["word", "sounds_like", "starred"], ["Groq", "grok; grock", "no"],
                    ["'=cmd", "", "no"], ["Sinéad", "", "yes"]]
    assert env.revealed == [path]


def test_export_of_an_empty_vocabulary_says_so_and_writes_nothing(env):
    r = env.api.export_vocabulary()
    assert r == {"ok": False, "reason": "empty", "error": um.VOCAB_EXPORT_EMPTY}
    assert list(env.desktop.iterdir()) == []


def test_export_that_cannot_write_says_so_without_the_raw_error(env):
    env.api.set_vocab(["Groq"])
    env.ns["_export_files"].save = lambda *a, **k: (_ for _ in ()).throw(
        PermissionError("[Errno 13] Permission denied: '/Users/james/Desktop'"))
    r = env.api.export_vocabulary()
    assert r == {"ok": False, "reason": "error", "error": um.EXPORT_FAILED}
    assert "james" not in json.dumps(r)


def test_what_is_exported_imports_back_into_an_empty_vocabulary_the_same(env):
    env.api.set_vocab(["Groq", "Morta", "Sinéad"], {"Groq": ["grok"], "Sinéad": ["shinade"]}, ["Morta"])
    path = Path(env.api.export_vocabulary()["path"])
    before = (env.api.get_vocab_book()["entries"], env.api.get_vocab_book()["starred"])
    env.api.set_vocab([], {}, [])
    r = env.api.import_vocabulary(path.read_bytes().decode("utf-8-sig"))
    assert r["ok"] and r["changed"]
    after = (env.api.get_vocab_book()["entries"], env.api.get_vocab_book()["starred"])
    assert [e["word"] for e in after[0]] == [e["word"] for e in before[0]]
    assert [e["sounds_like"] for e in after[0]] == [e["sounds_like"] for e in before[0]]
    assert after[1] == before[1]


# ── import ───────────────────────────────────────────────────────────────────

FILE = "word,sounds_like,starred\nGroq,grock,\nPostHog,post hog,yes\nwill,,\n" + "x" * 61 + ",,\n"


def test_the_preview_changes_nothing(env):
    env.api.set_vocab(["Groq"], {"Groq": ["grok"]})
    files_before = sorted(p.name for p in env.data.iterdir())
    r = env.api.preview_vocab_import(FILE)
    assert r["ok"] is True
    assert r["counts"] == {"rows": 4, "new": 1, "updated": 1, "already": 0, "skipped": 2}
    assert r["new"] == ["PostHog"] and r["updated"] == ["Groq"]
    assert [(s["row"], s["reason"]) for s in r["skipped"]] == [(4, "everyday"), (5, "too_long")]
    assert tw.load_vocab() == ["Groq"] and tw.load_sounds_like() == {"Groq": ["grok"]}
    assert sorted(p.name for p in env.data.iterdir()) == files_before


def test_a_preview_of_a_file_that_is_not_readable_is_a_plain_sentence(env):
    r = env.api.preview_vocab_import("a,b\n\"" + "x" * 200000 + "\",c")
    assert r["ok"] is False and r["error"] == table_io.ERR_UNREADABLE
    r = env.api.preview_vocab_import("***", True)
    assert r["ok"] is False and r["error"] == table_io.ERR_UNREADABLE
    r = env.api.preview_vocab_import(None)
    assert r["ok"] is False and "raw" not in r["error"].lower()


def test_the_page_can_send_a_files_bytes_so_its_encoding_is_kept(env):
    r = env.api.preview_vocab_import(b64("word;sounds_like\nSinéad;shinade\n"), True)
    assert r["ok"] and r["new"] == ["Sinéad"]


def test_import_merges_and_saves_and_gives_the_page_what_it_needs_to_undo(env):
    env.api.set_vocab(["Groq", "Morta"], {"Groq": ["grok"]}, ["Morta"])
    r = env.api.import_vocabulary(FILE)
    assert r["ok"] and r["changed"] is True
    assert r["words"] == ["Groq", "Morta", "PostHog"]
    assert r["sounds_like"] == {"Groq": ["grok", "grock"], "PostHog": ["post hog"]}
    assert r["starred"] == ["Morta", "PostHog"]
    assert r["before"] == {"words": ["Groq", "Morta"], "sounds_like": {"Groq": ["grok"]}, "starred": ["Morta"]}
    assert tw.load_vocab() == ["Groq", "Morta", "PostHog"]
    assert tw.load_sounds_like() == {"Groq": ["grok", "grock"], "PostHog": ["post hog"]}
    assert vocab_rank.load_stars(r["words"]) == ["Morta", "PostHog"]
    assert json.loads((env.data / "vocab.json").read_text(encoding="utf-8")) == ["Groq", "Morta", "PostHog"]


def test_the_undo_the_page_sends_puts_everything_back_exactly(env):
    env.api.set_vocab(["Groq", "Morta"], {"Groq": ["grok"]}, ["Morta"])
    r = env.api.import_vocabulary(FILE)
    b = r["before"]
    back = env.api.set_vocab(b["words"], b["sounds_like"], b["starred"])
    assert back["ok"]
    assert tw.load_vocab() == ["Groq", "Morta"]
    assert tw.load_sounds_like() == {"Groq": ["grok"]}
    assert vocab_rank.load_stars(["Groq", "Morta"]) == ["Morta"]


def test_nothing_new_changes_nothing_on_disk(env):
    env.api.set_vocab(["Groq"], {"Groq": ["grok"]}, [])
    before = (env.data / "vocab.json").stat().st_mtime_ns
    r = env.api.import_vocabulary("word,sounds_like\nGROQ,grok\n")
    assert r["ok"] and r["changed"] is False and r["counts"]["already"] == 1
    assert (env.data / "vocab.json").stat().st_mtime_ns == before


def test_import_never_removes_a_word_or_a_spelling(env):
    env.api.set_vocab(["Groq", "Morta", "Sinéad"], {"Groq": ["grok"], "Morta": ["mortar"]}, ["Sinéad"])
    env.api.import_vocabulary("word\nZed\n")
    assert tw.load_vocab() == ["Groq", "Morta", "Sinéad", "Zed"]
    assert tw.load_sounds_like() == {"Groq": ["grok"], "Morta": ["mortar"]}
    assert vocab_rank.load_stars(tw.load_vocab()) == ["Sinéad"]


def test_import_stops_at_five_hundred_words(env):
    env.api.set_vocab([f"Word{i:03d}" for i in range(499)])
    r = env.api.import_vocabulary("word\nZorro\nQuill\n")
    assert r["ok"] and len(r["words"]) == 500 and r["counts"]["skipped"] == 1
    assert r["skipped"][0]["reason"] == "full"
    assert len(tw.load_vocab()) == 500


def test_a_failed_save_changes_nothing_and_says_so_plainly(env, monkeypatch):
    env.api.set_vocab(["Groq"])
    monkeypatch.setattr(tw, "save_vocab", lambda *a, **k: {"ok": False, "error": "Couldn't save your list. Try again."})
    r = env.api.import_vocabulary("word\nZed\n")
    assert r == {"ok": False, "error": "Couldn't save your list. Try again."}
    assert tw.load_vocab() == ["Groq"]


def test_import_logs_counts_and_never_a_word(env):
    env.api.import_vocabulary("word,sounds_like\nSecretName,sekret\n")
    lines = [line for line in env.logged if "import" in line]
    assert lines and not any("SecretName" in line or "sekret" in line for line in env.logged)


# ── snippets ─────────────────────────────────────────────────────────────────

def test_snippets_export_csv_and_json(env):
    env.api.set_snippets([{"trigger": "my address", "expansion": "12 Acacia Avenue,\nLondon"},
                          {"trigger": "=sum", "expansion": "-5"}])
    r = env.api.export_snippets("csv")
    assert r["ok"] and r["count"] == 2 and r["format"] == "csv"
    rows = table_io.parse(Path(r["path"]).read_bytes().decode("utf-8-sig"))
    assert rows == [["trigger", "expansion"], ["my address", "12 Acacia Avenue,\nLondon"], ["'=sum", "'-5"]]
    r = env.api.export_snippets("json")
    assert r["ok"] and Path(r["path"]).suffix == ".json"
    assert json.loads(Path(r["path"]).read_text(encoding="utf-8")) == [
        {"trigger": "my address", "expansion": "12 Acacia Avenue,\nLondon"},
        {"trigger": "=sum", "expansion": "-5"}]
    assert len(env.revealed) == 2


def test_snippets_export_refuses_an_unknown_format_and_an_empty_list(env):
    assert env.api.export_snippets("xml") == {"ok": False, "reason": "format", "error": um.EXPORT_FORMAT_UNKNOWN}
    assert env.api.export_snippets("csv") == {"ok": False, "reason": "empty", "error": um.SNIPPETS_EXPORT_EMPTY}
    assert list(env.desktop.iterdir()) == []


def test_snippets_preview_changes_nothing_and_import_only_adds(env):
    env.api.set_snippets([{"trigger": "brb", "expansion": "be right back"}])
    text = "trigger,expansion\nBRB,something else\nomw,on my way\n"
    before = (env.data / "snippets.json").read_bytes()
    p = env.api.preview_snippets_import(text)
    assert p["ok"] and p["counts"] == {"rows": 2, "new": 1, "already": 0, "skipped": 1}
    assert (env.data / "snippets.json").read_bytes() == before
    r = env.api.import_snippets(text)
    assert r["ok"] and r["changed"] is True
    assert r["snippets"] == [{"trigger": "brb", "expansion": "be right back"},
                             {"trigger": "omw", "expansion": "on my way"}]
    assert r["before"] == [{"trigger": "brb", "expansion": "be right back"}]
    assert env.api.get_snippets() == r["snippets"]
    # The page's Undo is the editor's own save.
    assert env.api.set_snippets(r["before"])["snippets"] == r["before"]


def test_snippets_import_of_nothing_new_writes_nothing(env):
    env.api.set_snippets([{"trigger": "brb", "expansion": "be right back"}])
    before = (env.data / "snippets.json").stat().st_mtime_ns
    r = env.api.import_snippets("trigger,expansion\nbrb,be right back\n")
    assert r["ok"] and r["changed"] is False
    assert (env.data / "snippets.json").stat().st_mtime_ns == before


def test_snippets_import_reads_utf16_from_excel_and_says_plainly_when_it_cannot(env):
    import base64
    raw = "trigger\texpansion\ncafé\tü\n".encode("utf-16")
    r = env.api.import_snippets(base64.b64encode(raw).decode("ascii"), True)
    assert r["ok"] and r["snippets"] == [{"trigger": "café", "expansion": "ü"}]
    bad = env.api.preview_snippets_import("[1, 2")
    assert bad["ok"] is False and bad["error"] == snippets_io.ERR_JSON


def test_snippets_import_keeps_the_limits_of_the_editor(env):
    big = "trigger,expansion\nx," + '"' + "y" * 4001 + '"\n'
    r = env.api.import_snippets(big)
    assert r["ok"] and r["changed"] is False and r["skipped"][0]["reason"] == "text_long"
    assert env.api.get_snippets() == []


# ── Edit text ────────────────────────────────────────────────────────────────

def entry(**kw):
    base = {"id": 1, "timestamp": "2026-10-16T09:00:00", "text": "use waffle daily",
            "styled": "Use waffle daily.", "word_count": 3, "text_is": "asr_filtered"}
    base.update(kw)
    return base


def test_an_edit_is_saved_beside_the_entry_and_the_rest_is_untouched(env):
    write_history(env, [entry(id=1), entry(id=2, timestamp="2026-10-16T10:00:00", text="b", styled="B.")])
    r = env.api.edit_history_text(1, "", "", "Use Waffler daily.")
    assert r["ok"] and r["state"] == "edited"
    saved = history(env)
    assert saved[0]["edited_text"] == "Use Waffler daily."
    assert {k: v for k, v in saved[0].items() if k != "edited_text"} == entry(id=1)
    assert saved[1] == entry(id=2, timestamp="2026-10-16T10:00:00", text="b", styled="B.")
    assert r["item"] == saved[0]
    assert env.cache_calls == [1]
    assert not list(env.data.glob("*.tmp"))


def test_paste_last_is_told_the_new_text(env):
    write_history(env, [entry(id=1)])
    env.api.edit_history_text(1, "", "", "Use Waffler daily.")
    assert env.swaps == [("Use waffle daily.", "Use Waffler daily.")]
    env.api.edit_history_text(1, "", "", "Use Waffler every day.")
    assert env.swaps[-1] == ("Use Waffler daily.", "Use Waffler every day.")


def test_saving_the_pasted_text_again_takes_the_edit_off(env):
    write_history(env, [entry(id=1, edited_text="Use Waffler daily.")])
    r = env.api.edit_history_text(1, "", "", "Use waffle daily.")
    assert r["ok"] and r["state"] == "cleared" and "edited_text" not in history(env)[0]
    assert env.swaps == [("Use Waffler daily.", "Use waffle daily.")]
    assert r["suggestions"] == []


def test_an_edit_that_changes_nothing_writes_nothing(env):
    write_history(env, [entry(id=1)])
    before = (env.data / "history.json").stat().st_mtime_ns
    r = env.api.edit_history_text(1, "", "", "  Use waffle daily.  ")
    assert r["ok"] and r["state"] == "unchanged" and r["suggestions"] == []
    assert (env.data / "history.json").stat().st_mtime_ns == before
    assert env.cache_calls == [] and env.swaps == []


def test_an_old_entry_with_no_id_is_found_by_its_time_and_words(env):
    old = {"timestamp": "2026-09-01T10:00:00", "text": "two", "styled": "Two."}
    write_history(env, [{"timestamp": "2026-09-01T10:00:00", "text": "one", "styled": "One."}, old])
    r = env.api.edit_history_text(None, "2026-09-01T10:00:00", "two", "Two!")
    assert r["ok"] and history(env)[1]["edited_text"] == "Two!" and "edited_text" not in history(env)[0]
    r = env.api.edit_history_text(None, "2026-09-01T10:00:00", "nope", "x")
    assert r["ok"] is False and r["reason"] == "not_found" and r["error"] == um.EDIT_NOT_FOUND


@pytest.mark.parametrize("bad, reason, error", [
    pytest.param("", "empty", um.EDIT_EMPTY, id="empty"),
    pytest.param("  \n ", "empty", um.EDIT_EMPTY, id="blank"),
    pytest.param(None, "empty", um.EDIT_EMPTY, id="none"),
    # An explicit id: pytest would otherwise name the test after the 60,001 characters, and on
    # Windows the environment variable that carries the test name cannot exceed 32,767.
    pytest.param("x" * (history_edit.MAX_EDIT_CHARS + 1), "too_long", um.EDIT_TOO_LONG, id="too_long"),
])
def test_text_that_cannot_be_kept_is_refused_in_plain_words_and_nothing_is_written(env, bad, reason, error):
    write_history(env, [entry(id=1)])
    before = (env.data / "history.json").read_bytes()
    assert env.api.edit_history_text(1, "", "", bad) == {"ok": False, "reason": reason, "error": error}
    assert (env.data / "history.json").read_bytes() == before


def test_a_not_sent_card_a_rewrite_and_a_command_cannot_be_edited(env):
    write_history(env, [
        {"id": 1, "timestamp": "2026-10-16T09:00:00", "failed": True, "error": "x", "text": "", "styled": ""},
        {"id": 2, "timestamp": "2026-10-16T09:01:00", "kind": "transform", "before": "a", "after": "b"},
        {"id": 3, "timestamp": "2026-10-16T09:02:00", "kind": "command", "action": "ask", "query": "q"}])
    for i in (1, 2, 3):
        r = env.api.edit_history_text(i, "", "", "something")
        assert r["ok"] is False and r["reason"] == "not_editable", i


def test_a_history_file_that_cannot_be_written_is_a_plain_answer(env):
    write_history(env, [entry(id=1)])
    env.ns["save_history"] = lambda h: (_ for _ in ()).throw(PermissionError("[WinError 32] in use"))
    r = env.api.edit_history_text(1, "", "", "Use Waffler daily.")
    assert r == {"ok": False, "reason": "error", "error": um.EDIT_FAILED}


# ── the suggestions that come with an edit ───────────────────────────────────

def test_an_edit_that_fixes_a_name_comes_with_a_suggestion_that_adds_nothing(env):
    write_history(env, [entry(id=1)])
    r = env.api.edit_history_text(1, "", "", "Use Waffler daily.")
    assert r["suggestions"] == [{"word": "Waffler", "sounds_like": ["waffle"]}]
    assert tw.load_vocab() == []                       # never added by this call


def test_the_sounds_like_is_what_the_speech_service_wrote_not_the_pasted_word(env):
    write_history(env, [entry(id=1, text="send it to shivon today", styled="Send it to Shivon today.")])
    r = env.api.edit_history_text(1, "", "", "Send it to Siobhan today.")
    assert r["suggestions"] == [{"word": "Siobhan", "sounds_like": ["shivon"]}]


def test_words_already_in_the_vocabulary_are_not_suggested(env):
    env.api.set_vocab(["Waffler"])
    write_history(env, [entry(id=1)])
    assert env.api.edit_history_text(1, "", "", "Use Waffler daily.")["suggestions"] == []


def test_a_spelling_already_taken_is_not_suggested(env):
    env.api.set_vocab(["Morta"], {"Morta": ["waffle"]})
    write_history(env, [entry(id=1)])
    assert env.api.edit_history_text(1, "", "", "Use Waffler daily.")["suggestions"] == []


def test_the_setting_turns_suggestions_off_but_the_edit_is_still_kept(env):
    env.settings._save_settings_file({"learn_from_edits": False})
    write_history(env, [entry(id=1)])
    r = env.api.edit_history_text(1, "", "", "Use Waffler daily.")
    assert r["ok"] and r["suggestions"] == [] and history(env)[0]["edited_text"] == "Use Waffler daily."


def test_a_setting_that_is_not_exactly_false_leaves_suggestions_on(env):
    env.settings._save_settings_file({"learn_from_edits": "no"})
    write_history(env, [entry(id=1)])
    assert env.api.edit_history_text(1, "", "", "Use Waffler daily.")["suggestions"]


def test_suggestions_never_put_words_in_the_log(env):
    write_history(env, [entry(id=1, text="secretname talked", styled="Secretname talked.")])
    env.api.edit_history_text(1, "", "", "Secretnamé talked.")
    assert not any("ecretnam" in line for line in env.logged)
