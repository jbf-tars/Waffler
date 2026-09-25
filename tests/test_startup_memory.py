"""Start-up memory: NumPy's thread pool is capped, and the overlay stays light.

The installed app had about 878 MB (main) and 811 MB (overlay) of private
memory, almost all of it OpenBLAS's per-core thread pool, which NumPy starts on
import although Waffler never uses BLAS. On a 28-thread PC, "import numpy"
committed 754 MB across 27 threads, against 15 MB and 4 threads with
OPENBLAS_NUM_THREADS=1. The overlay subprocess paid the same, because app.py
imported the whole app (pywebview, NumPy, the AI SDKs) before noticing it was
the overlay.

app.py is not executed here (its imports need a desktop, and the owner's copy
may be running); its statement order is read from the source instead.
"""
import ast
import os
import re
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "rthook_thread_caps.py"
CAPS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS")


# ── the runtime hook ─────────────────────────────────────────────────────────

def test_the_hook_caps_every_blas_pool(monkeypatch):
    for var in CAPS:
        monkeypatch.delenv(var, raising=False)
    runpy.run_path(str(HOOK))
    for var in CAPS:
        assert os.environ.get(var) == "1", var


def test_the_hook_keeps_a_value_the_user_set(monkeypatch):
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "4")
    runpy.run_path(str(HOOK))
    assert os.environ["OPENBLAS_NUM_THREADS"] == "4"


@pytest.mark.parametrize("spec", ["Waffler_windows.spec", "Waffler_mac.spec"])
def test_both_builds_register_the_hook(spec):
    """Waffler_windows.spec had runtime_hooks=[] so no hook ever ran."""
    tree = ast.parse((ROOT / spec).read_text(encoding="utf-8"))
    analysis = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Analysis")
    hooks = next(k.value for k in analysis.keywords if k.arg == "runtime_hooks")
    assert "rthook_thread_caps.py" in ast.unparse(hooks), ast.unparse(hooks)


# ── app.py's own order ───────────────────────────────────────────────────────

_TREE = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
_HEAVY = {"webview", "numpy", "sounddevice", "openai", "groq", "config", "audio",
          "transcribe_whisper", "style_openai", "overlay", "clipboard",
          "permissions_manager", "audio_devices", "windows_hotkey", "smart_hotkey"}


def _imported(node):
    if isinstance(node, ast.Import):
        return {a.name.split(".")[0] for a in node.names}
    if isinstance(node, ast.ImportFrom) and node.module:
        return {node.module.split(".")[0]}
    return set()


def _index(pred):
    return next(i for i, n in enumerate(_TREE.body) if pred(n))


def _is_overlay_dispatch(n):
    return isinstance(n, ast.If) and "'--overlay' in sys.argv" in ast.unparse(n.test)


def _is_caps_loop(n):
    return isinstance(n, ast.For) and "OPENBLAS_NUM_THREADS" in ast.unparse(n.iter)


def test_app_sets_the_caps_before_any_heavy_import():
    caps = _index(_is_caps_loop)
    first_heavy = _index(lambda n: _imported(n) & _HEAVY)
    assert caps < first_heavy


def test_the_overlay_is_dispatched_before_the_main_app_imports():
    dispatch = _index(_is_overlay_dispatch)
    before = set()
    for n in _TREE.body[:dispatch]:
        before |= _imported(n)
    assert not (before & _HEAVY), sorted(before & _HEAVY)
    # Everything it needs is already in place: src/ on the path, UTF-8 streams.
    head = "\n".join(ast.unparse(n) for n in _TREE.body[:dispatch])
    assert "sys.path.insert(0" in head
    assert "sys.stderr = _fix_stream(sys.stderr)" in head


def test_the_overlay_dispatch_still_exits_and_covers_both_platforms():
    block = ast.unparse(next(n for n in _TREE.body if _is_overlay_dispatch(n)))
    assert "overlay_process_windows.main()" in block
    assert "overlay_process.main()" in block
    assert re.search(r"sys\.exit\(0\)\s*$", block)


def test_there_is_only_one_overlay_dispatch():
    assert sum(1 for n in _TREE.body if _is_overlay_dispatch(n)) == 1
