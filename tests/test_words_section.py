"""The Vocabulary is out of sight: Settings, Words.

The owner never tended the Vocabulary page and it kept getting in his way: a
place in the top bar, a "Names coming out wrong?" line on Home, a "Vocabulary
changed X to Y" line and an underline on Journal cards. A review of his real
use found that it earns its place on a handful of words and that he had never
once opened it to maintain them. So it is no longer a page:

  * the top bar has Home, Meetings and Settings;
  * the words are one Settings section, "Words", after Style: one sentence,
    a box to add a word, the words with a cross each, Import and Export;
  * everything else (what a word sounds like, stars, search and sort, Try a
    sentence, the suggestions switch, Snippets) is under the section's one
    collapsed "How it works";
  * Home and the Journal say nothing about it: a correction is simply made.
    The entry still records it ("vocab_changes"), so the counts in Settings
    still add up;
  * stars are hidden while the speech service is sent no prompt (Groq), since
    a star only orders the words of that prompt.

The decisions with logic are in ui/logic.js and run in Node; the rest reads
the page as written.
"""
import re
from pathlib import Path

from test_ui_logic import code_only, js, needs_node

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"


def read(name):
    return (UI / name).read_text(encoding="utf-8")


def func(src, name):
    start = src.index(f"function {name}(")
    body = src[start:]
    return body[:body.index("\n}\n")]


HTML = read("index.html")
APP = read("app.js")
CODE = code_only(APP)
CSS = code_only(read("style.css"))
VISIBLE = " ".join(re.sub(r"<[^>]+>", " ", re.sub(r"<!--.*?-->", " ", HTML, flags=re.S)).split())


def section():
    start = HTML.index('<section class="s-sec" id="secWords" data-sec="words" hidden>')
    return HTML[start:HTML.index("</section>", start)]


def in_view():
    sec = section()
    return sec[:sec.index('<div class="how">')]


def how():
    sec = section()
    return sec[sec.index('<div class="how">'):]


# ── it is not a page any more ────────────────────────────────────────────────

def test_the_top_bar_is_home_meetings_and_settings():
    nav = HTML[HTML.index('<nav class="topbar-nav"'):]
    nav = nav[:nav.index("</nav>")]
    assert re.findall(r">([^<]+)</button>", nav) == ["Home", "Meetings", "Settings"]
    assert "navVocab" not in HTML and "navVocab" not in CODE
    assert "vocabularyPanel" not in HTML and "vocabularyPanel" not in CODE
    assert "vocabulary-panel" not in CSS and ".vside" not in CSS and ".vmain" not in CSS


def test_words_is_a_settings_section_after_style():
    nav = HTML[HTML.index('aria-label="Settings sections"'):]
    nav = nav[:nav.index("</nav>")]
    labels = re.findall(r">([^<]+)</button>", nav)
    assert labels[labels.index("Style") + 1] == "Words"
    assert """data-sec="words" data-onclick="showSettingsSection('words')">Words</button>""" in nav
    assert HTML.index('id="secStyle"') < HTML.index('id="secWords"') < HTML.index('id="secTransforms"')
    sections = re.search(r"const SETTINGS_SECTIONS = \[([^\]]+)\]", CODE).group(1)
    assert "'style', 'words', 'transforms'" in sections


def test_opening_the_section_loads_the_words_as_saved():
    body = func(CODE, "showSettingsSection")
    assert "if (sec === 'words') loadVocabPage();" in body
    load = func(CODE, "loadVocabPage")
    assert "_setWordsHow(false)" in load, "How it works starts closed on every visit"
    assert load.index("_setWordsHow(false)") < load.index("renderVocab()")


def test_anything_that_still_asks_for_the_page_gets_the_section():
    body = func(CODE, "showPage")
    assert "if (page === 'vocabulary') { _settingsSection = 'words'; page = 'settings'; }" in body
    assert body.index("page === 'vocabulary'") < body.index("_currentPage = page")
    # And nothing in the window asks for the page.
    assert "showPage('vocabulary')" not in HTML and "showPage('vocabulary')" not in CODE


# ── what is in view ──────────────────────────────────────────────────────────

def test_one_title_and_one_sentence():
    view = in_view()
    assert '<h1 class="ptitle" id="vocabTitle" tabindex="-1">Words</h1>' in view
    assert '<p class="pdesc">Names and unusual words Waffler should spell your way.</p>' in view
    assert view.count('class="pdesc"') == 1


def test_in_view_there_is_only_add_the_list_and_the_file_buttons():
    view = in_view()
    assert 'id="vocabInput"' in view and '<ul class="w-list" id="wordsList" aria-label="Your words"></ul>' in view
    assert 'id="vocabImportBtn"' in view and 'id="vocabExportBtn"' in view
    # The second box of the add form is there, hidden until How it works is open.
    assert '<label class="vocab-field vocab-field-sl" id="vocabSoundsField" hidden>' in view
    for hidden_away in ('id="vocabSearch"', 'id="vocabSort"', 'id="vocabSelectBtn"', 'id="vocabBulk"',
                        'id="vocabList"', 'id="vocabTryInput"', 'id="vocabRecent"', 'id="learnToggle"',
                        'id="snippetsSection"', 'id="vocabEveryday"', 'id="vocabSummary"', "Star"):
        assert hidden_away not in view, hidden_away


def test_the_list_in_view_is_a_word_and_a_cross():
    body = func(CODE, "renderWordsList")
    row = re.search(r"return `(<li class=\"w-i\".*?</li>)`;", body, re.S).group(1)
    assert row == ('<li class="w-i" data-word="${w}"><span class="w-w">${w}</span>'
                   '<button type="button" class="rbtn w-x" data-word="${w}" aria-label="Remove ${w}" '
                   "title=\"Remove\">${WI.icon('x')}</button></li>")
    assert "deleteVocabWord(b.getAttribute('data-word'))" in body
    assert "escHtml(e.word)" in body
    assert "ul.hidden = !entries.length;" in body
    assert "renderWordsList();" in func(CODE, "renderVocab")


def test_a_word_typed_in_other_capitals_is_asked_about_in_view():
    """The table that used to ask "Replace PostHog with POSTHOG?" is under How
    it works now, so the question is asked where the word was typed."""
    assert ('<div class="w-ask" id="wordsAsk" role="group" aria-label="A word already in your list" hidden></div>'
            in in_view())
    body = func(CODE, "renderWordsList")
    assert "ask.hidden = !_vocabAsk;" in body
    assert "respellVocabWord()" in body and 'data-act="keep"' in body and 'data-act="replace"' in body
    add = func(CODE, "addVocabWord")
    assert "document.querySelector('#wordsAsk [data-act=\"keep\"]')" in add


def test_removing_a_word_from_the_list_in_view_keeps_the_focus_in_it():
    body = func(CODE, "deleteVocabWord")
    assert "const crosses = inTable ? '#vocabList .vt-del' : '#wordsList .w-x';" in body
    assert body.count("document.querySelectorAll(crosses)") == 2


# ── How it works ─────────────────────────────────────────────────────────────

def test_everything_else_is_under_the_one_collapsed_disclosure():
    box = how()
    assert ('<button type="button" class="link-btn how-btn" id="wordsHowBtn" aria-expanded="false" '
            'aria-controls="wordsHow" data-onclick="toggleWordsHow(this)">How it works<') in box
    assert '<div class="how-body w-adv" id="wordsHow" hidden>' in box
    for ident in ("vocabEveryday", "vocabSummary", "vocabFind", "vocabSearch", "vocabSort", "vocabSelectBtn",
                  "vocabBulk", "vocabList", "vocabTryBox", "vocabRecentBox", "vocabLearn", "learnToggle",
                  "snippetsSection", "snipList", "wordsStarsNote"):
        assert box.count(f'id="{ident}"') == 1, ident
    # Nothing nested closes the Settings section early, and no heading competes with the section's.
    assert "<section" not in section()[10:] and "<h2" not in box
    # A row in a "How it works" box is not a Settings row (tests/test_few_words.py).
    assert 'class="row"' not in box


def test_the_disclosure_also_shows_the_box_for_what_a_word_sounds_like():
    body = func(CODE, "_setWordsHow")
    assert "box.hidden = !open;" in body and "btn.setAttribute('aria-expanded', String(!!open));" in body
    assert "field.hidden = !open;" in body
    # Closed, a spelling typed earlier is not saved with the next word.
    assert re.search(r"if \(!open\) \{.*?sounds\.value = '';", body, re.S)
    toggle = func(CODE, "toggleWordsHow")
    assert "_setWordsHow(box.hidden)" in toggle
    assert ".vocab-field[hidden] { display: none; }" in CSS


def test_it_says_how_words_are_matched_now():
    box = " ".join(re.sub(r"<[^>]+>", " ", how()).split())
    assert "Waffler fixes these words after speech to text and before the clean-up." in box
    assert ("A word is fixed when it is heard as written, in a close spelling, or as one of the spellings "
            "you give it under Sounds like. Everyday words are left as you said them.") in box
    # A word with spellings is matched loosely too (tests/test_vocab_sounds_like.py),
    # so the page no longer says a spelling stops it.
    for untrue in ("matches loosely", "Matched loosely", "matched loosely", "stops guessing"):
        assert untrue not in VISIBLE and untrue not in CODE, untrue


@needs_node
def test_an_unanswered_everyday_spelling_is_said_to_be_left_alone():
    """It is never used as a match until the person keeps it
    (tests/test_vocab_everyday_sounds.py); the page said it was still changed
    "where it is written like a name"."""
    t = js("L.vocabEverydayNotice({word: 'Noor', spelling: 'your'})")
    assert t["title"] == '"your" for Noor is an everyday word.'
    assert t["detail"] == ('Waffler leaves it alone until you choose. Keep it to change every "your" you '
                           "dictate to Noor, or remove it.")
    assert "written like a name" not in read("logic.js")


# ── stars only while they do something ───────────────────────────────────────

@needs_node
def test_the_sorts_leave_out_starred_first_when_no_prompt_is_sent():
    assert [s["id"] for s in js("L.vocabSortsFor(true)")] == ["used", "recent", "starred", "az"]
    assert [s["id"] for s in js("L.vocabSortsFor(false)")] == ["used", "recent", "az"]
    assert js("L.vocabSortFor('starred', true)") == "starred"
    assert js("L.vocabSortFor('starred', false)") == "used"      # a remembered choice that no longer applies
    assert js("L.vocabSortFor('az', false)") == "az" and js("L.vocabSortFor('nope', true)") == "used"


def test_the_page_keeps_what_the_app_said_about_the_prompt():
    body = func(CODE, "_vocabReloadBook")
    assert "promptUsed: book.prompt_used === true," in body
    assert "promptUsed: false" in body, "the fallback without get_vocab_book"


def test_stars_are_drawn_only_when_the_prompt_is_used():
    body = func(CODE, "renderVocab")
    assert "const stars = _vocabBook.promptUsed === true;" in body
    assert "${stars ? `<button type=\"button\" class=\"rbtn vt-star\"" in body
    assert "WL.vocabSortFor(_vocabSort, stars)" in body
    sync = func(CODE, "_syncVocabStars")
    for ident in ("vocabBulkStar", "vocabBulkUnstar", "wordsStarsNote"):
        assert ident in sync, ident
    assert "el.hidden = !stars;" in sync
    assert "WL.vocabSortsFor(stars)" in sync
    assert "_syncVocabStars(stars);" in body
    # Hidden in the page as written, so nothing flashes before the app answers.
    assert '<li id="wordsStarsNote" hidden>' in HTML
    assert 'id="vocabBulkStar" hidden' in HTML and 'id="vocabBulkUnstar" hidden' in HTML


# ── the suggestions switch ───────────────────────────────────────────────────

@needs_node
def test_the_switch_is_on_only_when_the_app_says_so():
    assert js("L.learnSwitchOn({ok: true, on: true})") is True
    for answer in ("{ok: true, on: false}", "{ok: true}", "{}", "null", "undefined", "'yes'", "{on: 'true'}"):
        assert js(f"L.learnSwitchOn({answer})") is False, answer


def test_a_bridge_error_leaves_the_switch_off():
    """It was ticked when the call failed, and when the answer had no "on":
    a switch that says on while nothing is suggested."""
    body = func(CODE, "loadLearnSwitch")
    assert "t.checked = WL.learnSwitchOn(s);" in body
    assert re.search(r"catch \(_\) \{\s*t\.checked = false;\s*\}", body)
    assert "t.checked = true" not in body


def test_the_switch_is_under_how_it_works_and_still_says_it_only_offers():
    box = how()
    assert '<label class="vlearn-t" for="learnToggle">Suggest words from my edits</label>' in box
    line = re.search(r'<p class="vlearn-d" id="learnDesc">([^<]+)</p>', box).group(1)
    assert line == ("When you correct a dictation in the Journal, Waffler offers to add a name it got wrong. "
                    "It only offers. It never adds a word by itself.")


# ── Home and the Journal say nothing ─────────────────────────────────────────

def test_home_does_not_point_at_the_words():
    for said in ("Teach it your words", "Names coming out wrong?"):
        assert said not in HTML and said not in CODE, said
    assert "j-teach" not in HTML and "j-teach" not in CSS and "j-teach" not in CODE


def test_a_journal_card_does_not_say_what_was_changed():
    card = func(CODE, "makeCard")
    for gone in ("Vocabulary changed", "card-vocab", "vocabChanges", "vocabLine"):
        assert gone not in card, gone
    assert "word-hl" not in CODE and ".word-hl" not in CSS and ".card-vocab" not in CSS and ".j-vd" not in CSS
    assert "markWords" not in CODE


def test_the_entry_still_records_the_correction():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'item["vocab_changes"] = [[h, u] for h, u in _vocab_changes]' in app
    # And the counts in Settings, Words are still made from it.
    assert "_history_cache.vocab_usage()" in app


# ── the word people see ──────────────────────────────────────────────────────

def test_the_page_calls_them_your_words():
    for old in ("Your Vocabulary makes it", "Add to my Vocabulary", "already in your Vocabulary",
                "Your Vocabulary is full", "to your Vocabulary", "When your Vocabulary changes",
                "Try your Vocabulary"):
        assert old not in VISIBLE and old not in CODE and old not in read("logic.js"), old
