"""No line of app.log carries words from a dictation.

app.log is what Download Logs bundles and what people send with a bug report.
Two lines put a person's words in it whatever their settings: "Vocabulary
corrections applied: 'heard' → 'entry'" (19 lines in one Mac's logs, with
Private mode on), and the "REJECTED" line of a second speech-to-text answer,
which listed the negations and numbers it found in the take. Both say how
many now. The one line that may carry text is "Styled text:", which is only
written with ``logging.log_transcripts`` switched on in config.yaml.

The scan below reads the source: every call that writes a log line (or
prints) on the speech-to-text, vocabulary, clean-up, Transform and Command
paths, and every value put into its text. A value that holds words (the
transcript, the clean-up, a selection, a reply, a vocabulary change) may only
appear as a count: inside ``len()``, or as a number worked out from it.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

FILES = ["app.py", "src/transcribe_whisper.py", "src/style_openai.py", "src/style_post.py",
         "src/style_layer.py", "src/learn_words.py", "src/dictionary.py", "src/vocab_rank.py",
         "src/command_action.py", "src/command_mode.py", "src/transform_action.py",
         "src/transforms.py", "src/snippets.py", "src/quality.py", "src/text_context.py"]

# Calls that write a line: by name, or as a method (h.log, self._log, self._say).
WRITERS = {"_log_to_file", "_wlog", "print", "_diag_log", "log", "_log", "_say", "write"}

# Names that hold a person's words on these paths.
WORDS = {"transcript", "styled", "raw", "cleaned", "text", "alt", "_body", "body", "heard",
         "part", "full", "joined", "selection", "instruction", "reply", "_fixed", "fixed",
         "_vocab_changes", "vocab_changes", "changes", "phrase", "word", "words_list",
         "original", "alternate", "missing_negations", "missing_numbers", "prompt",
         "user_message", "content", "spoken", "said", "h", "u", "vword", "transcribed",
         "title", "window_title", "snippet", "expansion", "styled_text", "raw_text"}

# The one line that may: opt-in, in config.yaml (logging.log_transcripts).
ALLOWED = {("app.py", "Styled text: ")}


def _writer(call):
    f = call.func
    name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
    return name in WRITERS


def _counted(node, parents):
    """Is ``node`` only used for a count: inside len(), sum(), or int()?"""
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("len", "sum", "int", "bool", "type"):
            return True
        if isinstance(node, ast.FormattedValue):
            return False
    return False


def words_in_log_calls(path, source):
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    found = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and _writer(call)):
            continue
        literal = "".join(c.value for a in call.args for c in ast.walk(a)
                          if isinstance(c, ast.Constant) and isinstance(c.value, str))
        if any(path == p and marker in literal for p, marker in ALLOWED):
            continue
        for arg in call.args:
            for fv in ast.walk(arg):
                if not isinstance(fv, ast.FormattedValue):
                    continue
                for name in ast.walk(fv.value):
                    if isinstance(name, ast.Name) and name.id in WORDS and not _counted(name, parents):
                        found.append(f"{path}:{call.lineno}: {{{name.id}}} in {literal[:60]!r}")
    return found


def test_no_log_call_on_the_text_paths_interpolates_words():
    found = []
    for rel in FILES:
        p = ROOT / rel
        if p.exists():
            found += words_in_log_calls(rel, p.read_text(encoding="utf-8"))
    assert found == [], "\n" + "\n".join(found)


def test_the_scan_catches_what_it_is_for():
    bad = ('_log_to_file("Vocabulary corrections applied: " + ", ".join('
           'f"\'{h}\' → \'{u}\'" for h, u in _vocab_changes))\n'
           'print(f"Discarded: \'{cleaned}\'")\n'
           '_wlog(f"missing {sorted(missing_negations)}")\n')
    assert len(words_in_log_calls("x.py", bad)) == 4
    good = ('_log_to_file(f"Done: {len(_body.split())} words, {len(_body)} chars")\n'
            '_log_to_file(f"Vocabulary corrections applied: {len(_vocab_changes)}")\n')
    assert words_in_log_calls("x.py", good) == []
    assert words_in_log_calls("app.py", '_log_to_file(f"Styled text: {styled}")') == []
    assert words_in_log_calls("src/other.py", '_log_to_file(f"Styled text: {styled}")') != []


def test_the_vocabulary_line_is_a_count():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '_log_to_file(f"Vocabulary corrections applied: {len(_vocab_changes)}")' in src
    assert "→ '{u}'" not in src


def test_a_refused_second_answer_says_how_many_not_which(monkeypatch):
    import transcribe_whisper as tw
    ok, why = tw._alternate_is_safe_replacement(
        "we should not ship it on the 14th", "we should ship it on the 15th and more words here")
    assert ok is False
    assert why == "alternate drops 1 negation(s)"
    ok, why = tw._alternate_is_safe_replacement(
        "ship it on the 14th to 3 people", "ship it on the 15th to 3 people and more words here")
    assert ok is False and why == "alternate changes or drops 1 number(s)"


# ── the lines already in app.log ─────────────────────────────────────────────

OLD = [
    "09:00:01  Recording started",
    "09:00:05  Vocabulary corrections applied: 'waffle' → 'Waffler', 'mortar' → 'Morta'",
    "09:00:05  Done: 12 words, 64 chars",
    "09:01:05  Vocabulary corrections applied: 'bim' → 'XBim'\r",
    "09:02:00  [vocab] kept earlier fixes as 'sounds like' spellings: Waffler (waffle), Morta (mortar)",
    "09:03:00  Vocabulary corrections applied: 2",
]


def test_old_vocabulary_lines_become_counts():
    import privacy_data as P
    kept, changed = P.redact_vocab_lines(OLD)
    assert changed == 3
    assert kept == [
        "09:00:01  Recording started",
        "09:00:05  Vocabulary corrections applied: 2",
        "09:00:05  Done: 12 words, 64 chars",
        "09:01:05  Vocabulary corrections applied: 1\r",
        "09:02:00  [vocab] kept earlier fixes as 'sounds like' spellings: 2",
        "09:03:00  Vocabulary corrections applied: 2",
    ]
    assert P.redact_vocab_lines(kept) == (kept, 0)
    assert not any(w in "\n".join(kept) for w in ("waffle", "Morta", "mortar", "XBim", "bim"))


def test_the_tidy_at_start_does_it_once_in_both_logs_and_only_when_asked(tmp_path):
    import json
    import privacy_data as P
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({P.LOG_SCRUB_SETTING: True, "dialect": "en-GB"}), encoding="utf-8")
    for name in ("app.log", "app.log.1"):
        (tmp_path / name).write_text("\n".join(OLD) + "\n", encoding="utf-8", newline="")
    # Not before the one-time restore of earlier fixes has read them.
    r = P.tidy_logs_at_start(tmp_path, settings)
    assert r == {"scrubbed": None, "rotated": False}
    assert "waffle" in (tmp_path / "app.log").read_text(encoding="utf-8")
    r = P.tidy_logs_at_start(tmp_path, settings, redact_vocab=True)
    assert r["vocab_redacted"] == 6
    for name in ("app.log", "app.log.1"):
        text = (tmp_path / name).read_bytes().decode("utf-8")
        assert "waffle" not in text and "Morta" not in text and text.endswith("\n")
        assert text.count("\n") == len(OLD)
    saved = json.loads(settings.read_text(encoding="utf-8"))
    assert saved[P.VOCAB_REDACT_SETTING] is True and saved["dialect"] == "en-GB"
    (tmp_path / "app.log").write_text(OLD[1] + "\n", encoding="utf-8")
    assert "vocab_redacted" not in P.tidy_logs_at_start(tmp_path, settings, redact_vocab=True), "once"


def test_the_app_asks_for_it_once_the_earlier_fixes_were_restored_and_logs_counts():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "redact_vocab=_vr_flag.exists()" in src
    assert "kept earlier fixes as 'sounds like' spellings: {len(_added)}" in src
    assert "{e} ({', '.join(v)})" not in src
