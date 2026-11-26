"""An import that finds the audio converter missing (Windows) waits while the
window asks "Import audio needs a one-time download, 77 MB.", then carries
on with the same file once it is here, or answers as before on Not now.

app.py: _import_with_decoder (what Api.meetings_api goes through),
_decoder_wanted, _decoder_show / _decoder_progress / _decoder_done (the
window's side), Api.audio_decoder_answer and Api.audio_decoder_cancel (the
prompt's buttons). The prompt's own logic is src/audio_decoder_pack.py Offer
(tests/test_audio_decoder_pack.py); the window's sheet is ui/decoder.js.
"""
import ast
import json
import types
from pathlib import Path

import bridge_gate

ROOT = Path(__file__).resolve().parent.parent
_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")

NEEDS = {"status": 400, "body": {"detail": "x", "ok": False, "reason": "needs_download", "error": "x"}}
DONE = {"status": 200, "body": {"id": "n1"}}


def _lift(*names, **ns):
    nodes = [n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert sorted(n.name for n in nodes) == sorted(names)
    exec(compile(ast.Module(nodes, []), "<app.py>", "exec"), ns)
    return ns


def _method(name, **ns):
    node = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module([node], []), "<app.py Api>", "exec"), ns)
    return ns[name]


def _flow(run_answer, supported=True):
    offers = []
    offer = types.SimpleNamespace(run=lambda show, progress, done: offers.append((show, progress, done)) or run_answer)
    ns = _lift("_decoder_wanted", "_import_with_decoder",
               _decoder_offer=lambda: offer, _decoder_pack=lambda: types.SimpleNamespace(supported=lambda: supported),
               _decoder_show="show", _decoder_progress="progress", _decoder_done="done")
    return ns, offers


def test_the_same_file_is_imported_again_once_the_converter_is_here():
    ns, offers = _flow(True)
    calls = []

    def handle():
        calls.append(1)
        return NEEDS if len(calls) == 1 else DONE

    assert ns["_import_with_decoder"](handle, "POST", "/import") == DONE
    assert len(calls) == 2 and offers == [("show", "progress", "done")]


def test_not_now_answers_as_before():
    ns, offers = _flow(False)
    calls = []
    assert ns["_import_with_decoder"](lambda: calls.append(1) or NEEDS, "POST", "/import") == NEEDS
    assert len(calls) == 1 and len(offers) == 1


def test_only_an_import_refused_for_the_converter_asks():
    for method, path, answer, supported in (
            ("GET", "/app", NEEDS, True),
            ("POST", "/import", {"status": 400, "body": {"detail": "x", "reason": "not_audio"}}, True),
            ("POST", "/import", {"status": 500, "body": {"detail": "Something went wrong."}}, True),
            ("POST", "/import", DONE, True),
            ("POST", "/import", NEEDS, False)):                 # not Windows: nothing to fetch
        ns, offers = _flow(True, supported)
        assert ns["_import_with_decoder"](lambda: answer, method, path) == answer
        assert offers == [], (method, path, answer, supported)


class _Window:
    def __init__(self, answer=True, fail=False):
        self.scripts, self.answer, self.fail = [], answer, fail

    def evaluate_js(self, script):
        self.scripts.append(script)
        if self.fail:
            raise RuntimeError("the window is gone")
        return self.answer


def test_the_window_shows_the_prompt_and_hears_how_it_went():
    window, posted = _Window(), []
    ns = _lift("_decoder_show", "_decoder_progress", "_decoder_done",
               _window=window, _post_js=posted.append, json=json, _log_to_file=lambda m: None)
    assert ns["_decoder_show"]({"stage": "offer", "mb": 77}) is True
    assert window.scripts == ['!!(window.waffler_decoder_offer && window.waffler_decoder_offer({"stage": "offer", "mb": 77}))']
    ns["_decoder_progress"](5000000, 76576818)
    ns["_decoder_done"](False, "The audio converter couldn't be downloaded.")
    assert posted == ["window.waffler_decoder_progress && window.waffler_decoder_progress(5000000, 76576818)",
                      'window.waffler_decoder_done && window.waffler_decoder_done(false, '
                      '"The audio converter couldn\'t be downloaded.")']


def test_no_window_or_a_window_that_cannot_show_it_means_no_prompt():
    for window in (None, _Window(answer=False), _Window(fail=True)):
        ns = _lift("_decoder_show", _window=window, json=json, _log_to_file=lambda m: None)
        assert ns["_decoder_show"]({"stage": "offer", "mb": 77}) is False


def test_the_prompt_s_buttons_reach_the_offer_and_are_allowed():
    said = []
    offer = types.SimpleNamespace(answer=lambda c: said.append(("answer", c)) or {"ok": True},
                                  cancel=lambda: said.append("cancel") or {"ok": True})
    answer = _method("audio_decoder_answer", _decoder_offer=lambda: offer)
    cancel = _method("audio_decoder_cancel", _decoder_offer=lambda: offer)
    assert answer(None, "download") == {"ok": True} and cancel(None) == {"ok": True}
    assert said == [("answer", "download"), "cancel"]
    assert {"audio_decoder_answer", "audio_decoder_cancel"} <= bridge_gate.ALLOWED


def test_meetings_api_goes_through_it():
    node = next(n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name == "meetings_api")
    assert "_import_with_decoder(" in ast.unparse(node)
