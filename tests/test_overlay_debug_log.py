"""The Mac overlay's debug lines: the full-screen Spaces trail, without the flood.

3.14.68 logged every command the Mac overlay process read from its stdin as an
``[overlay-dbg] event=stdin.cmd`` line, including the ``level`` frames that
drive the pill's bars about 30 times a second while recording. On one Mac they
were 29,474 of the 35,853 lines in app.log, and app.log is cut at 5 MB, so the
history it kept shrank to about a week. ``level`` is no longer logged; the
commands that show, hide or move the pill, and every toast, still are.

src/overlay_process.py imports AppKit, so its stdin reader is lifted out of
the source and run on plain values. No overlay process is started.
"""
import ast
import io
import json
import queue
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAC = ROOT / "src" / "overlay_process.py"
WIN = ROOT / "src" / "overlay_process_windows.py"


def _read_commands(path, commands):
    """Run ``path``'s stdin reader over ``commands`` (then end of input).
    Returns (what it queued, the debug lines it wrote)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body
             if (isinstance(n, ast.FunctionDef) and n.name in ("_stdin_reader", "_dbg"))
             or (isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "").startswith("_UNLOGGED") for t in n.targets))]
    data = b"".join(json.dumps(c).encode("utf-8") + b"\n" for c in commands)
    stderr = io.StringIO()
    # The Mac reader reads stdin's raw buffer, the Windows one iterates the text.
    fake_sys = types.SimpleNamespace(
        stdin=io.TextIOWrapper(io.BytesIO(data), encoding="utf-8"), stderr=stderr)
    queued = queue.Queue()
    ns = {"sys": fake_sys, "json": json, "_cmd_queue": queued}
    exec(compile(ast.Module(nodes, []), str(path), "exec"), ns)
    ns["_stdin_reader"]()
    out = []
    while not queued.empty():
        out.append(queued.get_nowait())
    debug = [line for line in stderr.getvalue().splitlines() if line.startswith("[overlay-dbg]")]
    return out, debug


def test_a_level_command_is_passed_on_but_not_logged():
    queued, debug = _read_commands(MAC, [{"type": "level", "value": 0.4}] * 30)
    assert [c["type"] for c in queued] == ["level"] * 30 + ["quit"], "every frame still reaches the pill"
    assert debug == [], "a second of recording used to write 30 lines"


def test_a_show_is_still_logged_with_its_generation():
    queued, debug = _read_commands(MAC, [{"type": "show", "gen": 7}])
    assert [c["type"] for c in queued] == ["show", "quit"]
    [line] = debug
    assert "event=stdin.cmd" in line and "type=show" in line and "gen=7" in line


def test_only_the_level_frames_drop_out_of_the_trail():
    commands = [{"type": "show", "gen": 3}] + [{"type": "level", "value": 0.2}] * 5 + [
        {"type": "working", "elapsed_seconds": 0.0}, {"type": "working", "elapsed_seconds": 1.0},
        {"type": "show_toast", "style": "warn", "heading": "h", "body": "b"},
        {"type": "hide_toast"}, {"type": "working_end", "result": "done"}, {"type": "hide"}]
    queued, debug = _read_commands(MAC, commands)
    assert len(queued) == len(commands) + 1
    logged = [line.split("type=")[1].split()[0] for line in debug]
    assert logged == ["show", "working", "working", "show_toast", "hide_toast", "working_end", "hide"]


def test_the_windows_overlay_logs_no_command_at_all():
    """It has no debug trail, so there is nothing there to trim."""
    queued, debug = _read_commands(WIN, [{"type": "level", "value": 0.4}, {"type": "show"}])
    assert [c["type"] for c in queued] == ["level", "show", "quit"]
    assert debug == []
