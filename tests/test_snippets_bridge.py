"""get_snippets and set_snippets, the bridge the Vocabulary page saves through.

app.py's two methods are lifted out of its source and run against a temporary
folder. The page saves the WHOLE list on every change, so the list is tidied
and held to the limits first, written through the repo's atomic helper (so a
briefly locked file on Windows is retried and a failed write leaves the old
file whole), and an unreadable file is kept aside rather than written over.
Offline: no keys, no network, no private data.
"""
import ast
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import atomic_json  # noqa: E402
import snippets as S  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")


class _Api:
    pass


def _api(tmp_path, *, write=None, read=None):
    logged = []
    ns = {
        "_snippets": S, "_snippets_lock": threading.Lock(),
        "read_json_for_update": read or atomic_json.read_json_for_update,
        "write_json_atomic": write or (lambda p, d: atomic_json.write_json_atomic(
            p, d, sleep=lambda s: None)),
        "_log_to_file": logged.append, "DATA_DIR": tmp_path, "json": json,
    }
    names = ("_snippets_file", "get_snippets", "set_snippets")
    nodes = [n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names), "app.py Api snippets methods moved"
    exec(compile(ast.Module(nodes, []), "<app.py Api>", "exec"), ns)
    for n in names:
        setattr(_Api, n, ns[n])
    api = _Api()
    api.logged = logged
    return api


def sn(*pairs):
    return [{"trigger": t, "expansion": e} for t, e in pairs]


def saved(tmp_path):
    return json.loads((tmp_path / "snippets.json").read_text(encoding="utf-8"))


# ── get ──────────────────────────────────────────────────────────────────────

def test_get_with_nothing_saved_is_an_empty_list(tmp_path):
    assert _api(tmp_path).get_snippets() == []


def test_get_returns_what_was_saved_in_order(tmp_path):
    api = _api(tmp_path)
    api.set_snippets(sn(("b", "B"), ("a", "A")))
    assert api.get_snippets() == sn(("b", "B"), ("a", "A"))


def test_get_reads_a_hand_edited_file_with_a_byte_order_mark(tmp_path):
    (tmp_path / "snippets.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps(sn(("café", "naïve")), ensure_ascii=False).encode("utf-8"))
    assert _api(tmp_path).get_snippets() == sn(("café", "naïve"))


def test_get_never_raises_for_a_file_it_cannot_read(tmp_path):
    (tmp_path / "snippets.json").write_bytes(b"{ not json")
    assert _api(tmp_path).get_snippets() == []


def test_get_keeps_its_old_shape_a_plain_list_of_trigger_and_expansion(tmp_path):
    (tmp_path / "snippets.json").write_text(
        json.dumps([{"trigger": "a", "expansion": "A"}]), encoding="utf-8")
    got = _api(tmp_path).get_snippets()
    assert isinstance(got, list) and got == [{"trigger": "a", "expansion": "A"}]


# ── set: tidying ─────────────────────────────────────────────────────────────

def test_set_tidies_the_list_and_returns_what_it_kept(tmp_path):
    api = _api(tmp_path)
    r = api.set_snippets([
        {"trigger": "  my   address ", "expansion": " 12 Acacia Avenue\nLondon \n"},
        {"trigger": "", "expansion": "dropped: no trigger"},
        {"trigger": "no text", "expansion": "  "},
        {"trigger": "MY ADDRESS", "expansion": "dropped: same trigger"},
        {"trigger": "sig", "expansion": "Best,\nJames", "extra": 1},
        "junk", None,
    ])
    want = sn(("my address", "12 Acacia Avenue\nLondon"), ("sig", "Best,\nJames"))
    assert r == {"ok": True, "count": 2, "snippets": want}
    assert saved(tmp_path) == want


def test_set_keeps_the_old_return_keys(tmp_path):
    r = _api(tmp_path).set_snippets(sn(("a", "A")))
    assert r["ok"] is True and r["count"] == 1


def test_set_an_empty_list_empties_the_file(tmp_path):
    api = _api(tmp_path)
    api.set_snippets(sn(("a", "A")))
    assert api.set_snippets([]) == {"ok": True, "count": 0, "snippets": []}
    assert saved(tmp_path) == []
    assert api.get_snippets() == []


def test_other_languages_and_symbols_are_written_as_themselves(tmp_path):
    api = _api(tmp_path)
    api.set_snippets(sn(("日本語", "こんにちは 🙂 café"), ("c++", "C:\\Users\\me \\n")))
    raw = (tmp_path / "snippets.json").read_bytes().decode("utf-8")
    assert "こんにちは 🙂 café" in raw and "\\u" not in raw          # real characters, UTF-8
    assert saved(tmp_path) == sn(("日本語", "こんにちは 🙂 café"), ("c++", "C:\\Users\\me \\n"))


# ── set: limits ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad, sentence", [
    (sn(("t" * 61, "x")), "A trigger can be up to 60 characters."),
    (sn(("t", "e" * 4001)), "A snippet's text can be up to 4,000 characters."),
    (sn(*[(f"t{i}", "x") for i in range(201)]), "You can keep up to 200 snippets."),
])
def test_a_list_over_a_limit_is_refused_with_one_plain_sentence_and_nothing_is_saved(
        tmp_path, bad, sentence):
    api = _api(tmp_path)
    api.set_snippets(sn(("keep", "me")))
    r = api.set_snippets(bad)
    assert r == {"ok": False, "error": sentence}
    assert saved(tmp_path) == sn(("keep", "me"))


def test_the_limits_are_met_exactly(tmp_path):
    api = _api(tmp_path)
    r = api.set_snippets(sn(("t" * 60, "e" * 4000)))
    assert r["ok"] is True
    r = api.set_snippets(sn(*[(f"t{i}", "x") for i in range(200)]))
    assert r["ok"] is True and r["count"] == 200


@pytest.mark.parametrize("bad", [None, "nope", 5, {"trigger": "a", "expansion": "b"}])
def test_something_that_is_not_a_list_is_refused(tmp_path, bad):
    r = _api(tmp_path).set_snippets(bad)
    assert r["ok"] is False and r["error"] == S.ERR_READ
    assert not (tmp_path / "snippets.json").exists()


# ── set: the write ───────────────────────────────────────────────────────────

def test_no_temporary_file_is_left_behind(tmp_path):
    api = _api(tmp_path)
    for i in range(3):
        api.set_snippets(sn(("a", str(i))))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["snippets.json"]


def test_a_failed_write_says_so_in_a_sentence_and_leaves_the_old_file_whole(tmp_path):
    api = _api(tmp_path)
    api.set_snippets(sn(("a", "A")))
    before = (tmp_path / "snippets.json").read_bytes()

    def fail(path, data):
        raise PermissionError("[WinError 5] Access is denied: 'C:\\Users\\james\\snippets.json'")

    api = _api(tmp_path, write=fail)
    r = api.set_snippets(sn(("b", "B")))
    assert r == {"ok": False, "error": "Couldn't save your snippets. Try again."}
    assert (tmp_path / "snippets.json").read_bytes() == before
    # The detail goes to the log, never to the screen.
    assert any("[snippets] not saved: PermissionError" in m for m in api.logged)
    assert "WinError" not in r["error"]


def test_a_briefly_locked_file_is_retried_by_the_atomic_helper(tmp_path, monkeypatch):
    real = atomic_json.os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("locked")
        return real(src, dst)

    monkeypatch.setattr(atomic_json.os, "replace", flaky)
    api = _api(tmp_path)
    r = api.set_snippets(sn(("a", "A")))
    assert r["ok"] is True and calls["n"] == 3
    assert saved(tmp_path) == sn(("a", "A"))


def test_an_unreadable_file_is_kept_aside_not_written_over(tmp_path):
    (tmp_path / "snippets.json").write_bytes(b"{ a hand edit that went wrong")
    api = _api(tmp_path)
    assert api.set_snippets(sn(("a", "A")))["ok"] is True
    kept = list(tmp_path.glob("snippets.unreadable-*.json"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"{ a hand edit that went wrong"
    assert saved(tmp_path) == sn(("a", "A"))


def test_a_file_that_cannot_be_opened_stops_the_save(tmp_path):
    api0 = _api(tmp_path)
    api0.set_snippets(sn(("a", "A")))

    def locked(path, expect=list):
        raise PermissionError("in use")

    api = _api(tmp_path, read=locked)
    r = api.set_snippets(sn(("b", "B")))
    assert r == {"ok": False, "error": "Couldn't save your snippets. Try again."}
    assert saved(tmp_path) == sn(("a", "A"))


def test_saves_from_several_threads_leave_one_whole_file(tmp_path):
    api = _api(tmp_path)

    def work(n):
        for i in range(10):
            api.set_snippets(sn((f"t{n}", f"v{i}")))

    threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = saved(tmp_path)
    assert len(got) == 1 and got[0]["trigger"].startswith("t") and got[0]["expansion"].startswith("v")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["snippets.json"]


def test_the_error_sentences_are_plain(tmp_path):
    api = _api(tmp_path)
    for bad in (None, sn(("t" * 61, "x")), sn(("t", "e" * 4001)),
                sn(*[(f"t{i}", "x") for i in range(201)])):
        err = api.set_snippets(bad)["error"]
        assert err.endswith(".") and "\u2014" not in err and "Traceback" not in err
