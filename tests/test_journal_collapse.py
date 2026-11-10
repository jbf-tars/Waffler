"""Long Journal entries are shown short (after 3.17.0).

An entry over 100 words shows its first sixty or so, cut at the end of a
sentence when one falls between 40 and 80 words, then "…" and a "Show all N
words" button that opens it in place. A search that matches in the hidden
part opens the entry, and Copy, Edit and Redo always use the whole text.
These tests run ui/logic.js in Node where it is installed and read the ui/
files. Every text is invented.
"""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")


def read(name):
    return (UI / name).read_text(encoding="utf-8")


def js(expr, prelude=""):
    code = (f"const L = require({json.dumps(str(UI / 'logic.js'))});\n{prelude}\n"
            f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def func(src, name):
    body = src[src.index(f"function {name}("):]
    return body[:body.index("\n}\n")]


def words(n, stem="pebble"):
    return " ".join(f"{stem}{i}" for i in range(n))


def view(text, query=""):
    return js(f"L.collapseView({json.dumps(text)}, {json.dumps(query)})")


# ── What is shown ───────────────────────────────────────────────────────────

@needs_node
def test_a_short_entry_is_shown_whole():
    for n in (0, 1, 60, 100):
        v = view(words(n))
        assert v["collapse"] is False and v["expanded"] is True
        assert v["head"] == words(n) and v["words"] == n


@needs_node
def test_a_long_entry_shows_its_first_sixty_words_without_a_sentence_end():
    text = words(140)
    v = view(text)
    assert v["collapse"] is True and v["expanded"] is False
    assert v["words"] == 140
    assert v["head"] == words(60)
    assert text.startswith(v["head"])
    assert v["more"] == "Show all 140 words" and v["less"] == "Show less"
    assert v["sentence"] is False


@needs_node
def test_the_cut_prefers_the_sentence_end_nearest_sixty_words():
    text = f"{words(44)}. {words(10, 'kite')}. {words(12, 'reed')}! {words(60, 'tide')}"
    v = view(text)
    assert v["sentence"] is True
    # Sentence ends at 44, 54 and 66 words: 54 is nearest 60.
    assert v["head"].endswith("kite9.")
    assert len(v["head"].split()) == 54
    assert text.startswith(v["head"])


@needs_node
def test_a_sentence_end_outside_forty_to_eighty_words_is_not_used():
    text = f"{words(20)}. {words(70, 'kite')}. {words(40, 'tide')}"
    v = view(text)
    assert v["sentence"] is False
    assert len(v["head"].split()) == 60


@needs_node
def test_a_cut_mid_sentence_drops_a_dangling_comma_or_dash():
    parts = words(59).split() + ["lantern,"] + words(60, "moss").split()
    v = view(" ".join(parts))
    assert v["head"].endswith("lantern")
    parts[59] = "—"
    assert not view(" ".join(parts))["head"].endswith("—")


@needs_node
def test_paragraphs_and_right_to_left_text_are_cut_at_a_word():
    arabic = " ".join(["كلمة"] * 130)
    v = view(arabic)
    assert v["collapse"] is True and v["head"] == " ".join(["كلمة"] * 60)
    para = words(30) + "\n\n" + words(100, "fern")
    v = view(para)
    assert para.startswith(v["head"]) and "\n\n" in v["head"]


@needs_node
def test_the_count_uses_the_same_words_as_the_cards_count():
    # app.js counts with split(/\s+/).filter(Boolean); collapseView with \S+.
    text = "  " + words(150).replace(" ", "  \n ") + "  "
    assert view(text)["words"] == 150
    assert view(words(1234))["more"] == "Show all 1,234 words"


# ── Search ──────────────────────────────────────────────────────────────────

@needs_node
def test_a_match_in_the_hidden_part_opens_the_entry():
    text = words(60) + " " + words(60, "harbour")
    v = view(text, "HARBOUR5")
    assert v["collapse"] is True and v["expanded"] is True and v["searchHit"] is True


@needs_node
def test_a_match_only_in_the_shown_part_keeps_it_short():
    text = words(60) + " " + words(60, "harbour")
    v = view(text, "pebble1")
    assert v["expanded"] is False


@needs_node
def test_a_match_across_the_cut_opens_the_entry():
    text = words(60) + " " + words(60, "harbour")
    assert view(text, "pebble59 harbour0")["expanded"] is True


@needs_node
def test_folding_a_whole_feed_is_cheap():
    took = js("Date.now() - t0", prelude=(
        "const texts = Array.from({length: 5000}, (_, k) => "
        "Array.from({length: 40 + (k % 400)}, (_, i) => 'word' + i + (i % 17 ? '' : '.')).join(' '));\n"
        "const t0 = Date.now(); texts.forEach((t) => L.collapseView(t, 'word9'));"))
    assert took < 400, took


# ── The card ────────────────────────────────────────────────────────────────

def test_the_card_has_a_real_button_with_aria_expanded():
    src = func(read("app.js"), "makeCard")
    assert "WL.collapseView(displayText, _searchText)" in src
    assert '<button type="button" class="link-btn j-fold" aria-controls="${textId}" aria-expanded=' in src
    assert "moreBtn.setAttribute('aria-expanded', String(expanded))" in src


def test_copy_and_edit_use_the_whole_text():
    app = read("app.js")
    card = func(app, "makeCard")
    assert "copyItem(textEl.classList.contains('raw') ? rawText : displayText, this)" in card
    assert "ta.value = WL.finalText(item);" in func(app, "beginEdit")
    assert "redoEntry(item, div)" in card
    # Nothing reads the drawn (possibly short) text back off the page.
    assert "textEl.textContent" not in card and "textEl.innerText" not in card


def test_the_short_view_draws_only_the_head_and_an_ellipsis():
    src = func(read("app.js"), "_foldedHtml")
    assert "_markedHtml(fold.head, changes" in src
    assert 'class="j-ell" aria-hidden="true"' in src


def test_the_button_uses_theme_tokens_and_logical_alignment():
    css = read("style.css")
    m = re.search(r"\.j-body \.j-fold \{([^}]*)\}", css)
    assert m
    assert "text-align: start" in m.group(1)
    assert "var(--text-2)" in m.group(1)
    assert not re.search(r"#[0-9a-fA-F]{3,6}", m.group(1))
    assert ".j-ent.is-editing .j-fold" in css
