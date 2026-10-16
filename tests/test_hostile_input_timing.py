"""No text a model or a person can produce makes the app stall in a pattern.

CPython's regex engine never lets go of the interpreter lock, so one pattern that
backtracks for seconds freezes the whole app: the window, the pill, the audio
callback and, on a Mac, the keyboard tap. Command mode once read the model's
reply with a pattern that took minutes on "action" followed by a few thousand
spaces (the audit's H1), so every module that reads a model's reply or the
person's own text is fed the same hostile inputs here.

Each module runs in a child process, so a pattern that backtracks fails the test
within the time limit instead of hanging the suite. The child is this very file
(``python test_hostile_input_timing.py --child <module>``).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

N = 20_000                  # characters of hostile text
LIMIT_S = 1.5               # one call, generous for a slow runner; the bad patterns took 5 s to hours
CHILD_TIMEOUT_S = 240
MODULES = ("command_mode", "transforms", "learn_words", "table_io", "snippets", "snippets_io", "style_post")


def hostile_inputs():
    sp = " " * N
    return {
        "spaces": sp, "tabs": "\t" * N, "newlines": "\n" * N, "letters": "x" * N,
        "action_spaces_x": "action" + sp + "x", "action_colon_spaces": "ACTION:" + sp,
        "action_colon_spaces_x": "action:" + sp + "x", "action_tabs_x": "action" + "\t" * N + "x",
        "edit_spaces": "edit" + sp, "edit_colon_spaces_x": "Edit:" + sp + "x",
        "action_edit_spaces": "ACTION: edit" + sp + "x",
        "action_marks": "action" + "\"'*_`[(" * (N // 7) + "x",
        "underscored": "a_" * (N // 2), "dotted_words": "a." * (N // 2), "dotted_chain": ".".join(["a"] * (N // 2)),
        "angle_brackets": "<a" * (N // 2), "open_angle": "<a" + "x" * N, "spaced_dots": ". " * (N // 2),
        "initials": "A." * (N // 2), "digit_commas": "1," * (N // 2) + "x", "digit_dots": "1." * (N // 2),
        "parens": "(" * N, "quotes": '"' * N, "curly_quotes": "“" * N, "dash_list": "- " * (N // 2),
        "preamble": "Here is" + " x" * (N // 2), "refusals": "I can't " * (N // 8),
        "open_think": "<think>" * (N // 7), "open_think_text": "<think>" + "x" * N,
        "think_no_bracket": "<think " * (N // 7), "think_close_only": "</think>" * (N // 8),
        "markers": "<<<TEXT" * (N // 7), "closing_markers": "TEXT1>>>" * (N // 8),
        "sorry_spaces": "Sorry" + sp, "i_spaces": "I" + sp + "x", "words": "word " * (N // 5),
        "comma_spaces": ", " * (N // 2), "punctuation": "!?." * (N // 3), "brackets": "[" * N,
        "fences": "```\n" * (N // 4), "greeting_spaces": "hi" + sp, "long_greeting": "Dear" + "x " * (N // 2),
        "instruction_label": "Instruction:" + sp, "selected_label": "Selected text" + sp + ":",
        "thai": "ก" * N, "cjk": "一" * N, "equals": "=" * N, "sep_line": "sep=" + sp,
        "bom": "﻿" * N, "capitals": "A" * N, "short_words": "ab " * (N // 3),
        "capital_sentences": "A. " * (N // 3), "numbered": "1) " * (N // 3), "bullets": "• " * (N // 2),
        "long_greeting_line": "Hi " + "x" * N + ",", "questions": "?" * N, "nbsp": " " * N,
        "sentences": "a b. " * (N // 5), "zeros": "0" * N, "json_open": "{" * N, "json_text": '{"action": "' + "x" * N,
        "mixed_marks": "a=b;" * (N // 4), "assignments": "a=" * (N // 2), "calls": "a.b(" * (N // 4),
        "camel": "aB(" * (N // 3), "tags": "<b>" * (N // 3),
    }


def cases(name):
    """``[(function name, callable taking the hostile text)]`` for one module."""
    sys.path.insert(0, str(SRC))
    import importlib
    m = importlib.import_module(name)
    src = "I just wanted to reach out to let you know the meeting has moved to Thursday at three pm."
    if name == "command_mode":
        return [
            ("parse_reply (a selection)", lambda t: m.parse_reply(t, has_selection=True, instruction="shorter",
                                                                 own_text=src)),
            ("parse_reply (no selection)", lambda t: m.parse_reply(t, has_selection=False, instruction="reply",
                                                                  own_text=t[:12_000])),
            ("scrub", lambda t: m.scrub(t, src, "make it shorter")),
            ("scrub (own text hostile)", lambda t: m.scrub(t, t[:12_000], t[:2_000])),
            ("finish_edit", lambda t: m.finish_edit(src, t)),
            ("finish_edit (untagged)", lambda t: m.finish_edit(src, t, tagged=False)),
            ("finish_edit (source hostile)", lambda t: m.finish_edit(t[:12_000], t)),
            ("finish_draft", lambda t: m.finish_draft(t, "write a reply", "Hi Sam, are we still on?")),
            ("finish_draft (context hostile)", lambda t: m.finish_draft(t, t[:2_000], t[:500])),
            ("is_question", m.is_question),
            ("ask_query", lambda t: m.ask_query(t, "what is the capital of Australia")),
            ("ask_query (instruction hostile)", lambda t: m.ask_query("capital of Australia", t)),
            ("tidy_instruction", m.tidy_instruction), ("instruction_problem", m.instruction_problem),
            ("search_url", lambda t: m.search_url("google", t)),
            ("build_request", lambda t: m.build_request(t[:2_000], t[:12_000], t[:500])),
            ("_refused", lambda t: m._refused(t, t[:2_000])),
        ]
    if name == "transforms":
        spec = m.Spec("command", "Command", "custom")
        return [
            ("one_line", lambda t: m.one_line(t, 600)), ("tidy_text", lambda t: m.tidy_text(t, 1000)),
            ("word_count", m.word_count), ("size", m.size),
            ("overlap (reply hostile)", lambda t: m.overlap(src, t)),
            ("selection_problem", m.selection_problem),
            ("clean_output", lambda t: m.clean_output(t, src)),
            ("clean_output (source hostile)", lambda t: m.clean_output(t, t[:12_000])),
            ("normalise", m.normalise), ("strip_dashes", lambda t: m.strip_dashes(t, src)),
            ("finish", lambda t: m.finish(src, t, spec)),
            ("finish (source hostile)", lambda t: m.finish(t[:12_000], t, spec)),
            ("build_request", lambda t: m.build_request(spec, t[:12_000])),
            ("user_message", lambda t: m.user_message(t[:12_000])), ("marker", m.marker),
            ("estimate_tokens", m.estimate_tokens),
        ]
    if name == "learn_words":
        return [
            ("suggest (edited text hostile)", lambda t: m.suggest("hello world", "hello world", t, [], {})),
            ("suggest (heard hostile)", lambda t: m.suggest(t[:4_000], "hello wurld", "hello Waffler", [], {})),
            ("suggest (pasted hostile)", lambda t: m.suggest("hello wurld", t[:4_000], "hello Waffler " + t[:4_000],
                                                             [], {})),
            ("suggest_for_entry", lambda t: m.suggest_for_entry({"text": t[:4_000], "styled": t[:4_000]},
                                                                t[:4_000] + " Waffler", [], {})),
        ]
    if name == "table_io":
        return [
            ("decode (UTF-8)", lambda t: m.decode(t.encode("utf-8"))),
            ("decode (UTF-16)", lambda t: m.decode(t.encode("utf-16"))),
            ("from_payload", lambda t: m.from_payload(t)), ("parse", m.parse), ("sniff_delimiter", m.sniff_delimiter),
            ("render", lambda t: m.render([[t, t]])), ("defuse", m.defuse), ("undefuse", m.undefuse),
            ("column_map", lambda t: m.column_map([t, t], {"a": ["a"]})),
        ]
    if name == "snippets":
        found = [{"trigger": "my email", "expansion": "me@example.com"},
                 {"trigger": "a a a", "expansion": "x"}, {"trigger": "c++", "expansion": "y"}]
        return [
            ("trigger_key", m.trigger_key), ("spoken_key", m.spoken_key),
            ("clean", lambda t: m.clean([{"trigger": t[:60], "expansion": t[:4_000]}])),
            ("whole_utterance", lambda t: m.whole_utterance(t, found)),
            ("apply", lambda t: m.apply(t, found)),
            ("apply (trigger hostile)", lambda t: m.apply("a a a a", [{"trigger": t[:60], "expansion": "x"}])),
        ]
    if name == "snippets_io":
        return [
            ("parse", m.parse), ("plan_from_payload", lambda t: m.plan_from_payload(t, False, [])),
            ("plan_from_payload (csv)", lambda t: m.plan_from_payload("trigger,expansion\n" + t + "," + t, False, [])),
            ("export_bytes", lambda t: m.export_bytes([{"trigger": t[:60], "expansion": t[:4_000]}], "csv")),
        ]
    if name == "style_post":
        return [
            ("eligible", m.eligible), ("apply_style (casual)", lambda t: m.apply_style(t, "casual")),
            ("apply_style (formal)", lambda t: m.apply_style(t, "formal")),
            ("apply_style (excited)", lambda t: m.apply_style(t, "excited")),
            ("apply_style (protected words)", lambda t: m.apply_style(t, "casual", protected_words=[t[:20], "Waffler"])),
            ("_looks_like_code", m._looks_like_code),
        ]
    raise KeyError(name)


def _child(name, only=None):
    """Print one JSON line per function: its slowest hostile input."""
    inputs = hostile_inputs()
    for fname, fn in cases(name):
        try:
            fn("hello world. This is a short line, with a few words.")      # so a first import is not timed
        except Exception:       # noqa: BLE001
            pass
        worst = (0.0, "")
        for iname, text in inputs.items():
            if only and iname != only:
                continue
            started = time.perf_counter()
            try:
                fn(text)
            except Exception:       # noqa: BLE001 - a clean refusal is fine; only the time matters here
                pass
            took = time.perf_counter() - started
            if took > worst[0]:
                worst = (took, iname)
        print(json.dumps({"function": fname, "seconds": worst[0], "input": worst[1]}), flush=True)


def _run_child(name, *extra, timeout=CHILD_TIMEOUT_S):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--child", name, *extra],
                          capture_output=True, text=True, timeout=timeout, env=env)


@pytest.mark.parametrize("name", MODULES)
def test_no_hostile_text_stalls_a_module(name):
    try:
        done = _run_child(name)
    except subprocess.TimeoutExpired as e:
        seen = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        pytest.fail(f"{name} stalled for over {CHILD_TIMEOUT_S} s on hostile text (a pattern is backtracking). "
                    f"Finished before that: {seen.strip().splitlines()[-1:]}")
    assert done.returncode == 0, done.stderr[-2000:]
    rows = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
    assert rows, done.stdout
    slow = [f"{r['function']} took {r['seconds']:.2f} s on '{r['input']}'" for r in rows if r["seconds"] > LIMIT_S]
    assert not slow, f"{name}: " + "; ".join(slow)


def test_a_reply_that_is_the_word_action_and_a_long_run_of_spaces_is_read_at_once():
    """The audit's H1: this reply took minutes and froze the app while it did."""
    code = (
        "import sys, time; sys.path.insert(0, %r); import command_mode as cm\n"
        "best = 9.0\n"
        "for _ in range(3):\n"
        "    started = time.perf_counter()\n"
        "    cm.parse_reply('action' + ' ' * 10000 + 'x', has_selection=True, instruction='shorter')\n"
        "    best = min(best, time.perf_counter() - started)\n"
        "print(best)\n" % str(SRC))
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    try:
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                              timeout=30, env=env)
    except subprocess.TimeoutExpired:
        pytest.fail("reading 'action' and 10,000 spaces took over 30 s")
    assert done.returncode == 0, done.stderr[-2000:]
    assert float(done.stdout.strip()) < 0.05


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--child":
        _child(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
