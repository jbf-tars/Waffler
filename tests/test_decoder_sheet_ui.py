"""Import audio's converter on Windows, in the window: the one sheet an import
puts up when the converter is not here yet ("Import audio needs a one-time
download, 77 MB." Download / Not now), its progress and how it ended.

What the sheet says is a pure function in ui/logic.js (run in Node); the
markup and ui/decoder.js are read from the source, as the other window tests
do. The Python side: tests/test_audio_decoder_prompt.py (app.py) and
tests/test_audio_decoder_pack.py (src/audio_decoder_pack.py).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_ui_logic import code_only, js, needs_node, read  # noqa: E402


def view(state):
    return js(f"L.decoderView({state})")


@needs_node
def test_it_offers_the_download_with_its_size_in_one_line():
    v = view("{stage: 'offer', mb: 77}")
    assert v["title"] == "Import audio needs a one-time download, 77 MB."
    assert (v["go"], v["goText"]) == ("download", "Download")
    assert (v["quiet"], v["quietText"]) == ("not_now", "Not now")
    assert "FFmpeg" in v["note"] and "data folder" in v["note"]
    assert v["count"] == "" and v["bar"] is False


@needs_node
def test_without_a_size_it_still_reads_right():
    assert view("{stage: 'offer'}")["title"] == "Import audio needs a one-time download."


@needs_node
def test_downloading_it_counts_megabytes_and_can_be_cancelled():
    v = view("{stage: 'downloading', done: 34000000, total: 76576818}")
    assert v["title"] == "Downloading the audio converter."
    assert v["count"] == "34 of 77 MB" and v["percent"] == 44 and v["bar"] is True
    assert v["go"] == "" and (v["quiet"], v["quietText"]) == ("cancel", "Cancel")


@needs_node
def test_once_it_has_all_come_down_it_is_checked():
    v = view("{stage: 'checking', done: 76576818, total: 76576818}")
    assert v["title"] == "Checking the audio converter."
    assert v["percent"] == 100 and v["bar"] is True and "imported" in v["note"]


@needs_node
def test_a_failure_says_why_and_closes():
    v = view("{stage: 'failed', sentence: \"The audio converter couldn't be downloaded.\"}")
    assert v["title"] == "The audio converter didn't arrive."
    assert v["note"] == "The audio converter couldn't be downloaded."
    assert (v["go"], v["goText"]) == ("close", "Close") and v["quiet"] == ""


def sheet():
    html = read("index.html")
    start = html.index('<div class="gr" id="decoderSheet"')
    return html[start:html.index("</div>\n</div>", start) + len("</div>\n</div>")]


def test_the_sheet_is_in_the_window_with_what_decoder_js_needs():
    s = sheet()
    for id_ in ("dcTitle", "dcCount", "dcBar", "dcFill", "dcNote", "dcGo", "dcQuiet", "dcLive"):
        assert f'id="{id_}"' in s, id_
    assert 'role="dialog"' in s and 'aria-modal="true"' in s and 'aria-labelledby="dcTitle"' in s
    assert re.search(r'<div class="gr" id="decoderSheet"[^>]*\shidden>', s)
    assert not re.search(r"\son[a-z]+=", s)                    # the window's policy: no inline script


def test_decoder_js_is_loaded_after_logic_js():
    html = read("index.html")
    assert '<script src="decoder.js"></script>' in html
    assert html.index('<script src="logic.js"></script>') < html.index('<script src="decoder.js"></script>')


def test_decoder_js_answers_python_and_tells_it_what_was_chosen():
    src = code_only(read("decoder.js"))
    for name in ("window.waffler_decoder_offer", "window.waffler_decoder_progress", "window.waffler_decoder_done"):
        assert name in src, name
    assert "audio_decoder_answer', 'download'" in src and "audio_decoder_answer', 'not_now'" in src
    # Api.audio_decoder_cancel takes no argument: an undefined one would reach
    # Python as None (JSON null) and fail (seen in WebView2 on the PC).
    assert "tell('audio_decoder_cancel')" in src and "a[name](...args)" in src
    assert "return true" in src                                 # app.py _decoder_show waits for it
    assert "L.decoderView(" in src and "Escape" in src
