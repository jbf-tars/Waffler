"""Settings, "Try it here": the result shows in the box and nowhere else.

The owner saw the big floating message ("Polish done. Press Cmd+Z in your app
to undo it.", with "Show in Journal" and "Dismiss") over the Try it here box.

Where it comes from: that message is written in one place only, the end of a
real Transform run (src/transform_action.py; a real Command's is
src/command_action.py). The page's "Polish it" and "Do it" buttons cannot reach
it: they call Api.try_transform / try_command (src/try_it.py), which show
nothing and keep nothing (checked below, and by pressing them in a browser
with a stand-in app). So a real run happened there. The real shortcut works in
every app, and Waffler's own window is an app: with the box's text selected,
the real Transform (or a spoken command) rewrites the box, raised its message
over the window and added an entry to the Journal. (In the owner's screenshot
the box still has the keyboard, which a real run needs.)

Now a real run that lands in Waffler's own window puts its result in the box
and stops there: no message, no Journal entry. In any other app nothing
changes, and whatever goes wrong is still said.
"""
import ast
import dataclasses
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import command_action as ca  # noqa: E402
import pasteboard  # noqa: E402
import pipeline_watchdog as pw  # noqa: E402
import text_context as tc  # noqa: E402
import transform_action as ta  # noqa: E402
import test_command_action as C  # noqa: E402
import test_transform_action as T  # noqa: E402
import try_it  # noqa: E402
from test_ui_logic import code_only, js, needs_node, read  # noqa: E402


class OwnTransform(T.Rig):
    """A transform run while the app in front is Waffler itself."""
    own = True

    def hooks(self):
        return dataclasses.replace(super().hooks(), own_window=self._own)

    def _own(self):
        if isinstance(self.own, Exception):
            raise self.own
        return self.own


class OwnCommand(C.Rig):
    """A spoken command while the app in front is Waffler itself."""
    own = True

    def hooks(self):
        return dataclasses.replace(super().hooks(), own_window=self._own)

    def _own(self):
        if isinstance(self.own, Exception):
            raise self.own
        return self.own


# ── a real Transform in Waffler's own window ─────────────────────────────────

def test_a_transform_in_wafflers_own_window_puts_the_result_in_the_box_and_says_nothing():
    rig = OwnTransform()
    assert rig.go() == ta.STARTED
    (paste,) = rig.pastes
    assert paste["text"] == T.GOOD, "the rewrite still goes into the box"
    assert rig.toasts == [], "no message over the window"
    assert rig.saved == [] and rig.announced == [], "nothing in the Journal"
    assert rig.remembered == []
    assert rig.usage == [rig.chat_result], "the call is still counted in Usage"
    assert rig.ui[-1] == ("end", pw.DONE) and not rig.runner.busy()
    assert any("Waffler's own window" in line for line in rig.logged)


def test_in_any_other_app_the_message_and_the_journal_entry_are_as_they_were():
    rig = OwnTransform()
    rig.own = False
    assert rig.go() == ta.STARTED
    assert rig.last()[2] == "Polish done" and rig.last()[4][0]["label"] == "Show in Journal"
    assert len(rig.saved) == 1 and rig.announced == rig.saved
    # A runner given no answer to the question (the default) is in someone else's app.
    plain = T.Rig()
    assert plain.go() == ta.STARTED and plain.last()[2] == "Polish done" and len(plain.saved) == 1


def test_when_it_cannot_be_told_whose_window_it_is_the_run_is_said_and_kept():
    for unsure in (RuntimeError("no window server"), None, "yes", 1):
        rig = OwnTransform()
        rig.own = unsure
        assert rig.go() == ta.STARTED
        assert rig.last()[2] == "Polish done" and len(rig.saved) == 1, repr(unsure)


def test_a_paste_that_failed_in_wafflers_own_window_is_still_said_and_kept():
    rig = OwnTransform()
    rig.paste_outcome = pasteboard.PasteOutcome(pasteboard.FAILED, on_clipboard=True)
    assert rig.go() == ta.STARTED
    assert rig.remembered == [T.GOOD], "Copy again still copies the rewrite"
    assert len(rig.saved) == 1, "the words are not lost: they are in the Journal"
    assert rig.ui[-1] == ("end", pw.ERROR)


def test_with_paste_switched_off_the_copy_is_still_said_in_wafflers_own_window():
    rig = OwnTransform(settings={"auto_paste": False})
    assert rig.go() == ta.STARTED
    assert rig.pastes == [] and rig.copies == [T.GOOD]
    assert rig.toasts, "where the rewrite went has to be said"


# ── a real Command in Waffler's own window ───────────────────────────────────

def test_a_command_in_wafflers_own_window_puts_the_result_in_the_box_and_says_nothing():
    rig = OwnCommand()
    assert rig.go() == ca.STARTED
    (paste,) = rig.pastes
    assert paste["text"] == C.SHORT
    assert rig.shown(ca.RESULT) == [], "no message over the window"
    assert rig.saved == [] and rig.announced == [], "nothing in the Journal"
    assert rig.end()[1] == pw.DONE
    assert any("Waffler's own window" in line for line in rig.logged)


def test_a_command_in_any_other_app_is_said_and_kept_as_before():
    rig = OwnCommand()
    rig.own = False
    assert rig.go() == ca.STARTED
    assert rig.last()[2] == "Text edited" and len(rig.saved) == 1
    rig = OwnCommand()
    rig.own = RuntimeError("no window server")
    assert rig.go() == ca.STARTED
    assert rig.last()[2] == "Text edited" and len(rig.saved) == 1


# ── how the app answers "is it my own window?" ───────────────────────────────

def test_the_app_in_front_is_told_by_its_process():
    reader = types.SimpleNamespace(_front=lambda: tc.Front("com.apple.Notes", "Notes", 4242))
    old = tc._default_reader
    try:
        tc._default_reader = lambda: reader
        assert tc.front_pid() == 4242
        tc._default_reader = lambda: types.SimpleNamespace(_front=lambda: None)
        assert tc.front_pid() == 0
        tc._default_reader = lambda: (_ for _ in ()).throw(RuntimeError("no AppKit"))
        assert tc.front_pid() == 0
    finally:
        tc._default_reader = old


def _own_window_fn(front_pid, own_pid=900):
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_own_window_in_front")
    logged = []
    ns = {"_text_context": types.SimpleNamespace(front_pid=front_pid),
          "os": types.SimpleNamespace(getpid=lambda: own_pid), "_log_to_file": logged.append}
    exec(compile(ast.Module([node], []), "<app.py>", "exec"), ns)
    return ns["_own_window_in_front"], logged


def test_wafflers_own_window_is_the_one_whose_process_is_this_one():
    assert _own_window_fn(lambda: 900)[0]() is True
    assert _own_window_fn(lambda: 901)[0]() is False
    assert _own_window_fn(lambda: 0, own_pid=0)[0]() is False, "an unknown app in front is never Waffler"

    def boom():
        raise RuntimeError("gone")
    fn, logged = _own_window_fn(boom)
    assert fn() is False and logged


def test_both_runners_are_given_the_answer():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    for start, end in (("    def _transform_hooks(self):", "    def _dictation_busy(self)"),
                       ("    def _command_hooks(self):", "    def _command_blocked(self)")):
        block = src[src.index(start):src.index(end)]
        assert "own_window=_own_window_in_front," in block, start


# ── the page's own buttons were never the cause, and stay that way ───────────

def test_the_try_buttons_reach_only_the_try_methods():
    page = code_only(read("tryit.js"))
    called = set(__import__("re").findall(r"\bp\.(\w+)", page))
    assert called == {"try_it_status", "try_transform", "try_command", "open_command_search"}
    assert "showToast" not in page, "what happened is said in the box's own line"
    for other in ("app.js", "meetings.js", "account.js"):
        assert "transformsTryBtn" not in read(other) and "commandTryBtn" not in read(other)


def test_the_try_methods_show_nothing_and_keep_nothing():
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    api = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
    for name in ("try_transform", "try_command", "_try_it_hooks"):
        fn = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == name)
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | \
                {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
        for word in names:
            low = word.lower()
            assert not any(bad in low for bad in ("toast", "overlay", "history", "notify", "run_transform",
                                                  "paste", "clipboard", "_say")), f"{name} uses {word}"
    # And the module they call has no way to say or keep anything (its own test
    # reads its source for the Journal, the clipboard and other apps).
    assert not hasattr(try_it, "say") and "say" not in try_it.transform.__kwdefaults__


@needs_node
def test_a_polish_in_the_box_is_one_word_beside_the_button():
    view = js("L.tryItResult('transform', {ok: true, changed: true, text: 'Tidy.'})")
    assert view == {"text": "Tidy.", "said": "Polished.", "error": False, "undo": True, "link": None}
    view = js("L.tryItResult('command', {ok: true, action: 'edit', changed: true, text: 'Short.'})")
    assert view["said"] == "Done." and view["text"] == "Short."
