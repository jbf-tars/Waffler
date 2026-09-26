"""The tray (Windows) and menu bar (Mac) icon mirror the dictation.

The icon never changed, so with the window hidden nothing showed a dictation
was recording or still being processed. Offline: the icon is made from the
repo's own icon.ico into a temporary folder.
"""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import tray_state  # noqa: E402


def test_each_state_has_a_plain_tooltip():
    assert tray_state.tip_for("idle") == "Waffler"
    assert tray_state.tip_for("recording") == "Waffler: recording"
    assert tray_state.tip_for("working") == "Waffler: working on your dictation"
    assert "Journal" in tray_state.tip_for("not_sent")
    assert tray_state.tip_for("nonsense") == "Waffler"
    for tip in tray_state.TIPS.values():
        assert chr(0x2014) not in tip, "no em dashes"
        assert len(tip) < 128     # the Windows tooltip limit


def test_only_working_changes_the_icon():
    assert [s for s in tray_state.TIPS if tray_state.shows_working_icon(s)] == ["working"]


def test_the_working_icon_is_the_real_icon_with_an_amber_dot(tmp_path):
    PIL = pytest.importorskip("PIL.Image")
    out = tray_state.make_working_icon(ROOT / "icon.ico", tmp_path / "tray-working.ico")
    img = PIL.open(out)
    assert img.format == "ICO"
    sizes = img.info.get("sizes") or {img.size}
    assert (16, 16) in sizes and (32, 32) in sizes
    img.size = (32, 32)
    px = img.convert("RGBA").load()
    r, g, b, a = px[25, 25]               # inside the dot, bottom right
    assert (r, g, b, a) == tray_state.DOT_FILL
    plain = PIL.open(ROOT / "icon.ico")
    plain.size = (32, 32)
    assert plain.convert("RGBA").load()[4, 4] == px[4, 4], "the rest of the icon is unchanged"


def test_the_pipeline_sets_the_tray_through_the_dictation():
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WafflerPipeline")
    src = {n.name: ast.unparse(n) for n in klass.body if isinstance(n, ast.FunctionDef)}
    assert "_set_tray_state(_tray_state.RECORDING)" in src["on_hotkey_press"]
    assert "_set_tray_state(_tray_state.WORKING)" in src["_ui_begin"]
    assert "_tray_state.IDLE" in src["_ui_finished"] and "_tray_state.NOT_SENT" in src["_ui_finished"]
