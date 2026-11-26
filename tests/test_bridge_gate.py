"""The window's JavaScript reaches only the Api methods in src/bridge_gate.py.

pywebview resolves an unknown name from the page as an attribute path on the
js_api object, underscore methods and dunders included. app.py now gives it a
ClosedApi (no attributes) and exposes each allowed method by name. These
tests use pywebview's own bridge entry point with a stand-in window.
"""
import ast
import re
import threading
import time
from pathlib import Path

import pytest

import bridge_gate

ROOT = Path(__file__).resolve().parent.parent


def _api_public_methods():
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Api":
            return {f.name for f in node.body
                    if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not f.name.startswith("_")}
    raise AssertionError("class Api not found in app.py")


def test_allow_list_is_exactly_the_public_api():
    public = _api_public_methods()
    added = sorted(public - bridge_gate.ALLOWED)
    gone = sorted(bridge_gate.ALLOWED - public)
    assert not added, (f"New public Api methods are not in src/bridge_gate.py ALLOWED, so the "
                       f"window cannot call them; add them there if the window should: {added}")
    assert not gone, f"ALLOWED names an Api method that no longer exists: {gone}"


def test_allow_list_has_no_private_or_dotted_names():
    assert all(not n.startswith("_") and "." not in n for n in bridge_gate.ALLOWED)


def test_app_creates_the_window_with_the_gate():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    call = src[src.index("window = webview.create_window("):]
    call = call[:call.index("\n    )\n")]
    assert "js_api=_bridge_gate.ClosedApi()" in call
    assert "js_api=api" not in call
    assert "_bridge_gate.expose(window, api)" in src


# -- The page's own calls ---------------------------------------------------

def _ui_called_names():
    """Names the window's scripts call on pywebview.api (directly, through an
    `api()` helper, or as a string handed to `a[name]`)."""
    names = set()
    files = list((ROOT / "ui").glob("*.js")) + [ROOT / "ui" / "index.html"]
    files += list((ROOT / "ui" / "meetings-app" / "assets").glob("*.js"))
    for f in files:
        s = f.read_text(encoding="utf-8")
        names |= set(re.findall(r"pywebview\.api\.(\w+)", s))
        names |= set(re.findall(r"pywebview\.api\[\s*['\"](\w+)", s))
        if "api()" in s:
            names |= set(re.findall(r"\b(?:a|api\(\))\.([a-z][a-z0-9]*_[a-z0-9_]+)\b", s))
    return names


def test_window_calls_only_allowed_names():
    called = _ui_called_names()
    assert called, "found no bridge calls in ui/: the pattern is out of date"
    snake = {n for n in called if "_" in n}
    assert not {n for n in called if n.startswith("_")}, "the window calls an underscore name"
    assert not (snake - bridge_gate.ALLOWED), sorted(snake - bridge_gate.ALLOWED)


def test_window_never_calls_a_nested_attribute():
    for f in list((ROOT / "ui").glob("*.js")) + [ROOT / "ui" / "index.html"]:
        s = f.read_text(encoding="utf-8")
        nested = re.findall(r"pywebview\.api\.\w+\.\w+\s*\(", s)
        assert not nested, f"{f.name}: {nested}"


# -- pywebview's bridge, with the gate ---------------------------------------

class _FakeApi:
    def __init__(self):
        self.calls = []

    def get_version(self):
        self.calls.append("get_version")
        return "4.0.0"

    def save_settings(self, settings):
        self.calls.append(("save_settings", settings))
        return {"ok": True}

    def _load_settings_file(self):
        self.calls.append("_load_settings_file")
        return {"secret": True}


class _FakeWindow:
    def __init__(self, js_api):
        self._js_api = js_api
        self._functions = {}
        self._expose_lock = threading.Lock()
        self.ran = []
        self.events = type("E", (), {"loaded": threading.Event()})()

    def expose(self, *funcs):
        for f in funcs:
            self._functions[f.__name__] = f

    def evaluate_js(self, script):
        self.ran.append(script)

    run_js = evaluate_js


def _bridge_call(window, name, params):
    from webview.util import js_bridge_call
    js_bridge_call(window, name, params, "1")
    time.sleep(0.15)    # the call runs on a thread of its own


@pytest.fixture
def gated():
    api = _FakeApi()
    window = _FakeWindow(bridge_gate.ClosedApi())
    exposed = bridge_gate.expose(window, api, allowed={"get_version", "save_settings"})
    assert exposed == ["get_version", "save_settings"]
    return api, window


def test_allowed_name_is_called(gated):
    api, window = gated
    _bridge_call(window, "save_settings", [{"theme": "dark"}])
    assert api.calls == [("save_settings", {"theme": "dark"})]
    assert any('"ok": true' in s for s in window.ran)


@pytest.mark.parametrize("path", [
    "_load_settings_file",
    "__class__",
    "__init__.__globals__.clear",
    "save_settings.__func__",
    "save_settings.__closure__",
    "save_settings.__globals__.clear",
    "get_version.__self__._load_settings_file",
    "__getattribute__",
    "nope",
])
def test_anything_else_resolves_to_nothing(gated, path):
    api, window = gated
    _bridge_call(window, path, [])
    assert api.calls == []
    assert window.ran == []


def test_without_the_gate_pywebview_would_reach_underscore_methods():
    """Why the gate exists: pywebview resolves attribute paths on js_api."""
    api = _FakeApi()
    window = _FakeWindow(api)
    _bridge_call(window, "_load_settings_file", [])
    assert api.calls == ["_load_settings_file"]


def test_closed_api_lists_and_resolves_nothing():
    closed = bridge_gate.ClosedApi()
    assert dir(closed) == []
    for name in ("__class__", "__dict__", "anything", "_x"):
        assert getattr(closed, name, None) is None


def test_pywebview_lists_only_exposed_functions(gated):
    """The list pywebview builds for window.pywebview.api: get_functions over
    the ClosedApi finds nothing, so only the exposed names are listed."""
    api, window = gated
    # inject_pywebview walks dir(js_api) for public names: there are none.
    assert [n for n in dir(window._js_api) if not n.startswith("_")] == []
    assert sorted(window._functions) == ["get_version", "save_settings"]


def test_bridge_function_hides_the_api_object():
    api = _FakeApi()
    [f] = bridge_gate.bridge_functions(api, {"get_version"})
    assert f.__name__ == "get_version" and f() == "4.0.0"
    assert not hasattr(f, "__self__")


def test_private_names_are_refused():
    with pytest.raises(ValueError):
        bridge_gate.bridge_functions(_FakeApi(), {"_load_settings_file"})
