"""Api's real settings.json methods, run against a temporary folder.

app.py cannot be imported in the tests (pywebview, audio), so the four
methods that read and write settings.json are lifted out of its source, as
the other tests lift what they check. Tests that exercise a bridge call
which changes a setting use this object as ``self``: the read, the change and
the save then go through the same _editing_settings the app uses.

Not a test module itself (no test_ prefix).
"""
import ast
import contextlib
import copy
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import atomic_json  # noqa: E402

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_API = next(n for n in _TREE.body if isinstance(n, ast.ClassDef) and n.name == "Api")
_NAMES = ("_settings_file", "_load_settings_file", "_save_settings_file", "_editing_settings")


def settings_api(data_dir, initial=None, **attrs):
    """An object with the real settings methods, saving to
    ``data_dir/settings.json``. ``initial`` is written first. ``obj.data``
    is what the file holds now, ``obj.saves`` how many saves there were,
    and ``obj.saved`` a dict every save is merged into."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    logged = []
    ns = {
        "json": json, "copy": copy, "contextlib": contextlib,
        "DATA_DIR": data_dir, "_settings_lock": threading.RLock(),
        "write_json_atomic": lambda p, d: atomic_json.write_json_atomic(p, d, sleep=lambda s: None),
        "read_json_for_update": atomic_json.read_json_for_update,
        "_log_to_file": logged.append,
    }
    nodes = [n for n in _API.body if isinstance(n, ast.FunctionDef) and n.name in _NAMES]
    assert {n.name for n in nodes} == set(_NAMES), "app.py Api settings methods moved"
    exec(compile(ast.Module(nodes, []), "<app.py Api>", "exec"), ns)

    class SettingsApi:
        saves = 0

        @property
        def data(self):
            p = data_dir / "settings.json"
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

        def _save_settings_file(self, d):
            ns["_save_settings_file"](self, d)
            self.saves += 1
            self.saved.update(d)

    for name in ("_settings_file", "_load_settings_file", "_editing_settings"):
        setattr(SettingsApi, name, ns[name])
    obj = SettingsApi()
    obj.saved = {}
    obj.logged = logged
    for k, v in attrs.items():
        setattr(obj, k, v)
    if initial is not None:
        atomic_json.write_json_atomic(data_dir / "settings.json", initial, sleep=lambda s: None)
    return obj
