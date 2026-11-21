"""First run with no key and no account: "download the free model and get to
work", or a key of your own.

Setup's first step is a choice of two cards. Which one comes first and is
marked "Recommended for this computer" follows the machine
(src/local_stt.py machine_verdict; tests/test_local_stt_machine.py). Everything after it takes "on this computer"
as a complete setup: the stepper, resuming, the Try it step (which waits for
the model), Settings, and the "add a key" lines in the main window.

No network and no model: the page's decisions are pure functions in
ui/logic.js (run in Node where it is installed), the download is the module's
own with its HTTP call replaced, and app.py is read as text.
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
sys.path.insert(0, str(ROOT / "src"))

import local_stt  # noqa: E402

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")

MB574 = 574041195


def read(name):
    return (UI / name).read_text(encoding="utf-8")


def js(expr):
    code = (f"const L = require({json.dumps(str(UI / 'logic.js'))});\n"
            f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def func(src, name):
    body = src[src.index(f"function {name}("):]
    return body[:body.index("\n}\n")]


def setup_html():
    html = read("index.html")
    a = html.index('<div class="wizard-overlay ob" id="wizardOverlay"')
    return html[a:html.index("<!-- Toast -->", a)]


def visible_text(html):
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<(script|style|svg)[^>]*>.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def stt(**over):
    """local_stt_status() as the page gets it."""
    base = {"ok": True, "mode": "cloud", "available": True, "engine": "whispercpp",
            "model": "large-v3-turbo-q5", "label": "Whisper large v3 turbo", "state": "not_downloaded",
            "percent": 0, "bytes_done": 0, "bytes_total": MB574, "size_on_disk": 0, "error": "",
            "cancelled": False, "loaded": False, "suits_machine": True, "machine_reason": "apple_silicon"}
    base.update(over)
    return base


_SRC = (ROOT / "app.py").read_text(encoding="utf-8")


# ── the download behind the card: progress, stop, carry on ───────────────────

class _Resp:
    def __init__(self, data, status, gate=None):
        self.data, self.status_code, self.gate = data, status, gate

    def iter_content(self, size):
        for i in range(0, len(self.data), size):
            if self.gate is not None:
                self.gate(i)
            yield self.data[i:i + size]

    def close(self):
        pass


def test_a_stopped_download_shows_where_it_got_to_and_carries_on_from_there(monkeypatch, tmp_path):
    """What the wizard's bar is drawn from: percent while it runs, the part
    kept when it is stopped, a Range request when it carries on, ready at the
    end. The module's own HTTP hook is replaced; nothing leaves the machine."""
    data = bytes(range(256)) * 2048                      # 512 KiB in 64 KiB chunks
    spec = local_stt.ModelSpec(local_stt.ENGINE_WHISPERCPP, "Test model", (
        local_stt.ModelFile("m.bin", "https://example.invalid/m.bin", len(data),
                            hashlib.sha256(data).hexdigest()),))
    monkeypatch.setitem(local_stt.MODELS, "test", spec)
    monkeypatch.setattr(local_stt, "selected_model", lambda stored=None: "test")
    monkeypatch.setattr(local_stt, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(local_stt, "is_available", lambda: True)
    monkeypatch.setattr(local_stt, "_CHUNK", 64 * 1024)
    monkeypatch.setattr(local_stt, "_dl", {"active": False, "model": "", "done": 0, "total": 0,
                                           "error": "", "cancelled": False})
    ranges, seen = [], []

    def stop_at_half(offset):
        if offset >= len(data) // 2:
            local_stt.cancel_download()

    def get(url, headers):
        ranges.append(headers.get("Range"))
        start = int(headers["Range"][6:-1]) if headers.get("Range") else 0
        return _Resp(data[start:], 206 if start else 200, stop_at_half if not start else None)
    monkeypatch.setattr(local_stt, "_http_get", get)

    first = local_stt.download(lambda pct, done, total: seen.append(pct))
    assert first["ok"] is False and first["cancelled"] is True and first["state"] == "not_downloaded"
    assert 40 <= first["percent"] < 100 and first["bytes_done"] > 0 and first["error"] == ""
    assert seen == sorted(seen) and seen[-1] == first["percent"]
    view = js(f"L.setupLocalView({json.dumps({**first, 'ok': True, 'suits_machine': True})}, 'local')") \
        if NODE else {"state": "stopped"}
    assert view["state"] == "stopped"

    second = local_stt.download(lambda pct, done, total: seen.append(pct))
    assert second["ok"] is True and second["state"] == "ready" and second["percent"] == 100
    assert ranges[0] is None and ranges[1] == f"bytes={first['bytes_done']}-", "it carried on, not started again"
    assert seen[-1] == 100


# ── the page's decisions (ui/logic.js) ───────────────────────────────────────

@needs_node
def test_which_way_was_chosen_and_what_the_first_step_is_called():
    assert js("L.setupWay({has_key: false, transcription_mode: 'cloud'})") == ""
    assert js("L.setupWay({has_key: true, transcription_mode: 'cloud'})") == "key"
    assert js("L.setupWay({has_key: true})") == "key"                 # an older status: as before
    assert js("L.setupWay({has_key: false, transcription_mode: 'local'})") == "local"
    assert js("L.setupWay({has_key: true, transcription_mode: 'local'})") == "local"
    assert js("L.setupWay(null)") == ""
    assert js("['', 'local', 'key'].map((w) => L.setupStepLabel('connect', w))") == [
        "How it runs", "On this computer", "Connect Groq"]
    assert js("['permissions', 'try', 'anywhere'].map((s) => L.setupStepLabel(s, 'local'))") == [
        "Permissions", "Try it", "Use it anywhere"]


@needs_node
def test_the_recommended_card_follows_the_machine():
    assert js(f"L.setupRecommended({json.dumps(stt())})") == "local"
    assert js(f"L.setupRecommended({json.dumps(stt(suits_machine=False))})") == "key"
    assert js(f"L.setupRecommended({json.dumps(stt(available=False))})") == "key"
    assert js("L.setupRecommended(null)") == "key"                    # nothing known: promise nothing
    assert js("L.setupRecommended({ok: false, error: 'x'})") == "key"


@needs_node
def test_the_free_card_in_each_state():
    def view(status, way=""):
        return js(f"L.setupLocalView({json.dumps(status)}, {json.dumps(way)})")

    idle = view(stt())
    assert (idle["state"], idle["button"], idle["canStop"], idle["done"]) == ("idle", "Download and start", False, False)
    assert idle["size"] == "574 MB" and idle["rec"] == "local" and idle["slow"] is False
    assert idle["line"] == "" and idle["canChoose"] is True

    going = view(stt(state="downloading", percent=42, bytes_done=241_000_000), "local")
    assert (going["state"], going["percent"], going["button"], going["canStop"]) == ("downloading", 42, "", True)
    assert going["line"] == "Downloading: 42% of 574 MB. Carry on with setup while it arrives."
    assert going["done"] is True, "setup can carry on while it downloads"

    stopped = view(stt(percent=42, bytes_done=241_000_000, cancelled=True), "local")
    assert (stopped["state"], stopped["button"], stopped["canStop"]) == ("stopped", "Carry on downloading", False)
    assert stopped["line"] == "Stopped at 42%. It carries on from where it stopped."
    assert stopped["done"] is False, "a stopped download is not a setup"
    # The same after the app was closed part-way: only the part on disk says so.
    assert view(stt(percent=42, bytes_done=241_000_000), "")["state"] == "stopped"

    failed = view(stt(percent=10, bytes_done=57_000_000, error="The speech model couldn't be downloaded."), "local")
    assert (failed["state"], failed["button"]) == ("failed", "Try again")
    assert failed["line"] == "The speech model couldn't be downloaded." and failed["done"] is False

    ready = view(stt(state="ready", percent=100, bytes_done=MB574, size_on_disk=MB574), "local")
    assert (ready["state"], ready["button"], ready["done"]) == ("ready", "", True)
    assert ready["line"] == "Downloaded and ready. No key, no account."
    # Already on this computer but not chosen yet (setup run again): one click, no download.
    there = view(stt(state="ready", percent=100, bytes_done=MB574), "key")
    assert (there["button"], there["done"]) == ("Use this computer", False)
    assert there["line"] == "Already downloaded (574 MB). Ready to use."


@needs_node
def test_a_slow_machine_and_a_build_without_the_engine():
    slow = js(f"L.setupLocalView({json.dumps(stt(suits_machine=False, machine_reason='small_machine'))}, '')")
    assert slow["rec"] == "key" and slow["slow"] is True
    assert slow["canChoose"] is True and slow["button"] == "Download and start", "slow, but still on offer"
    off = js(f"L.setupLocalView({json.dumps(stt(available=False))}, '')")
    assert (off["state"], off["button"], off["canChoose"], off["done"]) == ("unavailable", "", False, False)
    assert off["line"] == "This copy of Waffler can't turn speech into text on this computer yet. Use your own key."
    assert off["slow"] is False, "one reason at a time"
    # A status that could not be read is treated the same way.
    assert js("L.setupLocalView({ok: false}, '')")["state"] == "unavailable"
    assert js("L.setupLocalView(null, '')")["state"] == "unavailable"


@needs_node
def test_sizes_are_said_as_people_say_them():
    assert js("[574041195, 190085487, 1622605978, 0, null].map(L.modelSize)") == [
        "574 MB", "190 MB", "1.6 GB", "", ""]


@needs_node
def test_the_first_step_is_complete_either_way():
    going = json.dumps(stt(state="downloading", percent=5))
    idle = json.dumps(stt())
    # The choice screen: the free way once it is downloading or ready.
    assert js(f"L.setupConnectDone('choose', 'local', false, {going})") is True
    assert js(f"L.setupConnectDone('choose', 'local', false, {idle})") is False
    assert js(f"L.setupConnectDone('choose', '', false, {idle})") is False
    # A key that passed its check, looked at from the choice screen.
    assert js(f"L.setupConnectDone('choose', 'key', true, {idle})") is True
    # The key screens: exactly as before, the key and nothing else.
    for state in ("start", "waiting", "checking", "error"):
        assert js(f"L.setupConnectDone('{state}', 'local', false, {going})") is False, state
    assert js(f"L.setupConnectDone('connected', 'key', true, {idle})") is True
    # Back to the two cards from any of the key screens but the last.
    assert js("['choose', 'start', 'waiting', 'checking', 'error', 'connected'].map(L.setupCanGoBack)") == [
        False, True, True, True, True, False]


@needs_node
def test_the_mode_is_saved_at_the_right_moment():
    ready = json.dumps(stt(state="ready"))
    going = json.dumps(stt(state="downloading"))
    # No key: "on this computer" is saved at once, so a reopened setup knows.
    assert js(f"L.setupModeToSave('local', {going}, false)") == "local"
    # A key that works: dictation stays on it until the model has arrived.
    assert js(f"L.setupModeToSave('local', {going}, true)") == ""
    assert js(f"L.setupModeToSave('local', {ready}, true)") == "local"
    # Nothing to save when it is already so.
    assert js(f"L.setupModeToSave('local', {json.dumps(stt(state='ready', mode='local'))}, true)") == ""
    # Choosing a key after trying the free way goes back to the key.
    assert js(f"L.setupModeToSave('key', {json.dumps(stt(mode='local'))}, true)") == "cloud"
    assert js(f"L.setupModeToSave('key', {json.dumps(stt(mode='local'))}, false)") == "", "cloud needs a key"
    assert js(f"L.setupModeToSave('key', {json.dumps(stt())}, true)") == ""
    assert js(f"L.setupModeToSave('', {ready}, false)") == ""
    assert js(f"L.setupModeToSave('local', {json.dumps(stt(available=False))}, false)") == ""


@needs_node
def test_try_it_waits_for_the_model_with_a_plain_line():
    def wait(status, way="local"):
        return js(f"L.setupModelWait({json.dumps(status)}, {json.dumps(way)})")

    going = wait(stt(state="downloading", percent=62))
    assert going["waiting"] is True and going["percent"] == 62 and going["button"] == ""
    assert going["line"] == "The speech model is still downloading: 62% of 574 MB. You can talk as soon as it's here."
    assert going["short"] == "Speech model: 62%"
    # For a status region: the same news without a number that ticks.
    assert going["steady"] == "The speech model is still downloading. You can talk as soon as it's here."
    stopped = wait(stt(percent=62, bytes_done=350_000_000))
    assert stopped["waiting"] is True and stopped["button"] == "Carry on downloading"
    assert stopped["line"] == "The speech model stopped downloading at 62%. It carries on from where it stopped."
    failed = wait(stt(percent=62, bytes_done=350_000_000, error="Check you're online and try again."))
    assert failed["button"] == "Try again" and failed["line"] == "Check you're online and try again."
    assert wait(stt())["line"] == "The speech model isn't downloaded yet."
    assert wait(stt())["button"] == "Download and start"
    assert stopped["steady"] == stopped["line"]
    assert wait(stt(state="ready", percent=100)) == {"waiting": False, "percent": 100, "line": "", "steady": "",
                                                     "short": "", "button": ""}
    # With a key nothing waits, whatever the model is doing.
    assert wait(stt(state="downloading", percent=10), "key")["waiting"] is False
    assert wait(stt(), "")["waiting"] is False


@needs_node
def test_progress_is_spoken_at_the_start_the_quarters_and_the_end():
    def say(prev, status):
        return js(f"L.setupDownloadSaid({json.dumps(prev)}, {json.dumps(status)})")

    assert say(None, stt()) == ""
    assert say({"state": "idle", "percent": 0}, stt(state="downloading", percent=0)) == \
        "Downloading the speech model, 574 MB."
    assert say({"state": "stopped", "percent": 42}, stt(state="downloading", percent=42, bytes_done=1)) == \
        "Carrying on with the download from 42%."
    assert say({"state": "downloading", "percent": 42}, stt(state="downloading", percent=43)) == ""
    assert say({"state": "downloading", "percent": 49}, stt(state="downloading", percent=51)) == "50% downloaded."
    assert say({"state": "downloading", "percent": 20}, stt(state="downloading", percent=80)) == "75% downloaded."
    assert say({"state": "downloading", "percent": 99}, stt(state="ready", percent=100)) == "The speech model is ready."
    assert say({"state": "downloading", "percent": 60}, stt(percent=60, bytes_done=9, cancelled=True)) == \
        "Download stopped at 60%."
    assert say({"state": "downloading", "percent": 60}, stt(percent=60, bytes_done=9, error="It failed.")) == "It failed."
    assert say({"state": "ready", "percent": 100}, stt(state="ready", percent=100)) == ""
    # The last piece can arrive between two looks: the end is still said, once.
    assert say({"state": "stopped", "percent": 96}, stt(state="ready", percent=100)) == "The speech model is ready."
    assert say({"state": "stopped", "percent": 60}, stt(percent=60, bytes_done=9, error="It failed.")) == "It failed."
    assert say({"state": "failed", "percent": 60}, stt(percent=60, bytes_done=9, error="It failed.")) == ""
    assert say({"state": "stopped", "percent": 60}, stt(percent=60, bytes_done=9)) == ""


@needs_node
def test_a_reopened_setup_carries_the_download_on_only_if_it_was_running():
    part = json.dumps(stt(mode="local", percent=40, bytes_done=1))
    # Quit at a later step (a Mac "Quit & Reopen"): it was running, so it carries on.
    assert js(f"L.setupShouldResumeDownload('local', 'permissions', {part})") is True
    assert js(f"L.setupShouldResumeDownload('local', 'try', {json.dumps(stt(mode='local'))})") is True
    # Still on the first step: it may have been stopped on purpose. The card offers it.
    assert js(f"L.setupShouldResumeDownload('local', 'connect', {part})") is False
    assert js(f"L.setupShouldResumeDownload('key', 'try', {part})") is False
    assert js(f"L.setupShouldResumeDownload('local', 'try', {json.dumps(stt(state='ready'))})") is False
    assert js(f"L.setupShouldResumeDownload('local', 'try', {json.dumps(stt(state='downloading'))})") is False
    assert js(f"L.setupShouldResumeDownload('local', 'try', {json.dumps(stt(available=False))})") is False


@needs_node
def test_the_practice_says_what_really_happens_without_a_key():
    tidy = js("L.setupTryText(false)")
    plain = js("L.setupTryText(true)")
    assert tidy["working"] == "Tidying it up." and tidy["chip"] == "Cleaning up"
    assert plain["working"] == "Writing it down." and plain["chip"] == "Writing"
    assert plain["wrote"] == "And the same words, typed, here."
    assert "sorry" in tidy["say"] and "sorry" not in plain["say"], "no clean-up to show off without a key"


# ── no "add a key" nagging for someone on the free way ───────────────────────

FREE = {"transcription_mode": "local", "transcription_backend": "mlx", "styling_backend": "none"}


@needs_node
def test_settings_does_not_ask_the_free_way_for_a_key():
    flow = js(f"L.flowView({json.dumps(FREE)})")
    assert flow["speech"] == {"by": "This Mac", "model": "Whisper"}
    assert flow["cleanup"] == {"by": "Off", "model": "typed as you said it"}
    assert js(f"L.backendsLine({json.dumps(FREE)})") == "Speech to text: on this Mac · Clean-up: off"
    uses = js(f"L.usesView({json.dumps(FREE)})")
    assert uses == {"speech": "Whisper, on this Mac", "cleanup": "no one: your words are typed as you said them"}
    assert js(f"L.privacyFlow({json.dumps(FREE)})") is None, "nothing leaves the computer"
    # The page draws the flow from that, so "Add a key" is not what it shows.
    app = read("app.js")
    assert "const stage = (x) => (x ? `${escHtml(x.by)} <small>${escHtml(x.model)}</small>` : '<small>Add a key</small>');" in app


@needs_node
def test_someone_with_a_key_or_with_nothing_sees_what_they_saw_before():
    cloud = {"transcription_mode": "cloud", "transcription_backend": "none", "styling_backend": "none"}
    assert js(f"L.flowView({json.dumps(cloud)})") == {"speech": None, "cleanup": None, "out": "Pasted"}
    assert js(f"L.backendsLine({json.dumps(cloud)})") == "Speech to text: no key yet · Clean-up: no key yet"
    assert js(f"L.usesView({json.dumps(cloud)})") == {"speech": "No key yet", "cleanup": "No key yet"}
    # On this computer with a key for the clean-up: the key's provider, as before.
    mixed = {"transcription_mode": "local", "transcription_backend": "faster", "styling_backend": "groq"}
    assert js(f"L.flowView({json.dumps(mixed)})")["cleanup"] == {"by": "Groq", "model": "gpt-oss-120b"}
    assert js(f"L.backendsLine({json.dumps(mixed)})") == "Speech to text: on this computer · Clean-up: Groq"


def test_a_dictation_without_a_key_is_typed_as_heard_with_no_message():
    """What the explainer promises: with no key the words go in as heard (the
    clean-up is treated as Off, not as failed), in a dictation and in setup's
    practice alike."""
    for name in ("_process", "_no_cleanup_key"):
        assert f"def {name}(" in _SRC
    process = _SRC[_SRC.index("    def _process("):]
    assert "_style_layer.cleanup_is_off(_settings) or self._no_cleanup_key(_settings)" in process
    practice = _SRC[_SRC.index("def _wizard_finish_practice("):_SRC.index("def _wizard_finish_practice(") + 3000]
    assert "local_speech and not any(os.getenv(k, \"\").strip() for k in (" in practice
    notes = (ROOT / "src" / "meetings" / "llm.py").read_text(encoding="utf-8")
    assert "The transcript is saved. Writing notes needs an API key" in notes


# ── the page ─────────────────────────────────────────────────────────────────

def test_the_first_screen_is_two_cards_and_a_short_explainer():
    html = setup_html()
    text = visible_text(html)
    assert re.search(r'<section class="ob-step" id="obStepConnect" data-step="connect" data-state="choose"', html)
    assert "Download the free model and get to work" in text
    for words in ("On this computer", "Free", "With your own key", "Faster",
                  "Recommended for this computer", "Download and start", "Use my own key",
                  "No key, account or internet.",
                  "Your voice never leaves this computer.",
                  "Quicker. Tidies text and writes meeting notes.",
                  "A free Groq account. Your voice goes to Groq and nowhere else.",
                  "You can change this any time in Settings."):
        assert words in text, words
    assert html.count("Download and start") == 1
    assert 'onclick="wizChooseLocal()"' in html and 'onclick="wizChooseKey()"' in html
    assert 'onclick="wizStopDownload()"' in html
    # The key screens are all still there, word for word.
    for words in ("Connect your free Groq account", "Get my free Groq key", "Groq is connected",
                  "Or paste it here", "Already have a key?"):
        assert words in text, words


def test_the_explainer_says_what_the_free_way_does_not_do_and_no_more():
    text = visible_text(setup_html())
    tldr = text[text.index("The short version."):]
    tldr = tldr[:tldr.index("You can change this any time in Settings.") + 45]
    assert "typed as you said them, with punctuation" in tldr
    assert "does not tidy them or write meeting notes" in tldr
    assert "a meeting still gets its full transcript" in tldr
    assert "need a key" in tldr
    assert len(tldr.split()) < 85, "three lines, not a manual"
    # Nothing is promised that the code does not do.
    assert "offline" not in tldr.lower() and "unlimited" not in text.lower()


def test_the_cards_are_labelled_groups_with_real_buttons_and_a_polite_status():
    html = setup_html()
    for card, title in (("obWayLocal", "obWayLocalT"), ("obWayKey", "obWayKeyT")):
        assert re.search(rf'<div class="panel ob-way[^"]*" id="{card}" role="group" aria-labelledby="{title}"', html), card
        assert re.search(rf'<h2 class="ob-way-t" id="{title}">', html), title
    assert re.search(r'<button type="button" class="btn [^"]*" id="obWayLocalBtn" onclick="wizChooseLocal\(\)">Download and start</button>', html)
    assert re.search(r'<button type="button" class="btn [^"]*" id="obWayKeyBtn" onclick="wizChooseKey\(\)">Use my own key</button>', html)
    assert re.search(r'<button type="button" class="btn [^"]*" id="obWayStop" onclick="wizStopDownload\(\)" hidden>Stop the download</button>', html)
    # Progress: a real progress bar with its value, and one polite status
    # line that speaks at the start, the quarters and the end, not every tick.
    assert re.search(r'id="obWayBar" role="progressbar" aria-label="Speech model download" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"', html)
    assert '<p class="sr-only" id="obDlLive" role="status" aria-live="polite"></p>' in html
    assert re.search(r'<h1 class="ob-title" id="obTitleChoose" tabindex="-1" data-when="choose">', html)
    app = read("app.js")
    draw = func(app, "wizDrawLocal")
    assert "WL.setupDownloadSaid(_wizSttSaid, _wizStt)" in draw and "_wizSay('obDlLive', said)" in draw
    assert "setAttribute('aria-valuenow', String(v.percent))" in draw


def test_the_recommended_card_comes_first_for_the_eye_and_for_tab():
    app = read("app.js")
    rec = func(app, "wizApplyRec")
    assert "sec.dataset.rec = v.rec;" in rec
    assert "el.hidden = el.dataset.rec !== v.rec;" in rec
    assert "ways.insertBefore(first, ways.firstElementChild);" in rec, "moved in the page, so Tab follows"
    assert "localBtn.disabled = !v.canChoose;" in rec, "no engine: the card explains and cannot be chosen"
    html = setup_html()
    assert html.index('id="obWayLocal"') < html.index('id="obWayKey"'), "free first until the machine says otherwise"
    assert re.search(r'<p class="ob-way-warn" id="obWaySlow" hidden>[^<]*It will be slow on this computer', html)
    css = read("setup.css")
    assert not re.search(r"(?<![\w-])order\s*:", css[css.index(".ob-ways"):css.index(".ob-tldr")]), \
        "CSS order would leave Tab behind"


def test_choosing_a_card():
    app = read("app.js")
    local = func(app, "wizChooseLocal")
    assert "_wizWay = 'local';" in local and "pywebview.api.local_stt_download()" in local
    assert "await wizRefreshLocal();" in local
    stop = func(app, "wizStopDownload")
    assert "pywebview.api.local_stt_cancel()" in stop
    key = func(app, "wizChooseKey")
    assert "wizSetState('connect', _wizKeyOk ? 'connected' : 'start');" in key and "wizInitConnect();" in key
    # The clipboard is only looked at once the key card was chosen.
    init = func(app, "wizInitConnect")
    assert init.index("=== 'choose'") < init.index("startWizClipboardWatch();")
    # Back from the key screens returns to the two cards.
    back = func(app, "wizBack")
    assert "wizShowChoice();" in back
    assert "if (back && step === 'connect') back.hidden = !WL.setupCanGoBack(state);" in app
    assert "if (step === 'connect') enabled = WL.setupConnectDone(state, _wizWay, _wizKeyOk, _wizStt);" in app
    # A key that passes its check makes the key the chosen way (and stops a download).
    check = func(app, "wizValidateGroqKey")
    assert "_wizChoseKey();" in check
    chose = func(app, "_wizChoseKey")
    assert "_wizWay = 'key';" in chose and "local_stt_cancel()" in chose and "wizSyncMode()" in chose


def test_the_mode_is_saved_through_settings_like_the_settings_page_does():
    app = read("app.js")
    sync = func(app, "wizSyncMode")
    assert "WL.setupModeToSave(_wizWay, _wizStt, _wizHasKey || _wizKeyOk)" in sync
    assert "pywebview.api.save_settings({ transcription_mode: want })" in sync
    refresh = func(app, "wizRefreshLocal")
    assert "pywebview.api.local_stt_status()" in refresh
    assert refresh.index("await wizSyncMode();") < refresh.index("wizDrawLocal();")
    assert "_wizSttTimer = setTimeout(wizRefreshLocal, 700);" in refresh, "polled while it downloads, as Settings does"
    # Settings shows the same choice: it reads the same status.
    hir = read("howitruns.js")
    assert "const local = s.mode === 'local';" in hir and "$('hirLocalRadio').checked = local;" in hir
    assert "if (sec === 'keys' && window.loadHowItRuns) window.loadHowItRuns();" in app


def test_setup_opens_on_the_right_screen_for_each_way():
    app = read("app.js")
    show = func(app, "showWizard")
    assert "_wizWay = WL.setupWay(status);" in show
    assert "wizSetState('connect', 'choose');" in show
    assert "WIZ_STEPS.includes(status.resume_step)" in show
    assert "WL.setupShouldResumeDownload(_wizWay, first, _wizStt)" in show
    check = func(app, "checkOnboarding")
    assert "pywebview.api.local_stt_status()" in check and "showWizard(status, stt);" in check
    again = func(app, "runSetupAgain")
    assert "transcription_mode: s.transcription_mode" in again
    assert "has_key: !!(s.groq_key_set || s.api_key_set)" in again
    stepper = func(app, "wizRenderStepper")
    assert "WL.setupStepLabel(s, _wizLabelWay())" in stepper
    assert "'Connect Groq'" not in app, "the first step is no longer always Groq"


def test_try_it_waits_for_the_model_then_starts_the_practice():
    html = setup_html()
    assert re.search(r'<div class="panel ob-modelwait ob-extra" data-when="model">', html)
    assert re.search(r'id="obModelBar" role="progressbar" aria-label="Speech model download"', html)
    assert 'id="obModelLine"' in html and 'id="obModelBtn" onclick="wizChooseLocal()" hidden' in html
    app = read("app.js")
    init = func(app, "wizInitTryItStep")
    assert "await wizRefreshLocal();" in init
    assert "wizSetState('try', 'model');" in init
    assert init.index("wizSetState('try', 'model');") < init.index("await wizStartPractice();")
    draw = func(app, "wizDrawLocal")
    assert "if (_wizardStep === 'try' && trySec && trySec.dataset.state === 'model' && !wait.waiting)" in draw
    assert "wizStartPractice();" in draw
    # Nothing starts the practice while the model is not here (a microphone or
    # hotkey change on that screen would otherwise).
    practice = func(app, "wizStartPractice")
    assert practice.index("if (WL.setupModelWait(_wizStt, _wizWay).waiting) return;") < \
        practice.index("wizard_start_hotkey_test")
    assert "model: 'Waiting for the speech model. You can talk as soon as it is here.'" in app
    # The practice itself needs no key on the free way (app.py).
    start = _SRC[_SRC.index("def wizard_start_hotkey_test"):_SRC.index("def wizard_stop_hotkey_test")]
    assert "if not openai_key and not groq_key and not _local_stt.mode_is_local():" in start


def test_the_last_step_and_done_say_so_while_the_model_is_still_coming():
    html = setup_html()
    assert '<p class="ob-small ob-anymodel" id="obAnyModel" role="status" hidden></p>' in html
    assert re.search(r'<span class="ob-dl" id="obDlFoot" hidden>', html)
    app = read("app.js")
    draw = func(app, "wizDrawLocal")
    assert "editor.disabled = wait.waiting;" in draw
    assert "put('obAnyModel', wait.waiting ? wait.steady : '');" in draw, "a status region must not tick"
    done = func(app, "wizCompleteSetup")
    assert "WL.setupModelWait(_wizStt, _wizWay)" in done
    assert "Waffler starts listening as soon as the speech model is here." in done


def test_the_cards_follow_the_theme_and_keep_readable_colours():
    css = read("setup.css")
    block = css[css.index("/* ── The two ways"):css.index("/* ── Type")]
    assert "#" not in re.sub(r"/\*.*?\*/", "", block, flags=re.S).replace("#0C0A09", ""), \
        "colours come from the theme's tokens, so both themes are covered"
    for token in ("var(--text)", "var(--text-2)", "var(--honey)", "var(--surface-2)"):
        assert token in block, token
    # The card itself is the shared .panel (var(--card)).
    assert "background: var(--card);" in read("components.css")[read("components.css").index("\n.panel {"):][:120]
    assert "var(--faint)" not in block, "--faint is not for text anyone has to read"


def _lum(hex_colour):
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _tokens(theme):
    css = read("tokens.css")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    light = css[css.index(":root,\nbody {"):]
    light = light[:light.index("\n}")]
    dark = css[css.index('body[data-theme="dark"]'):]
    dark = dark[:dark.index("\n}")]
    src = light if theme == "light" else dark
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9A-Fa-f]{6})\b", src))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_cards_text_meets_four_and_a_half_to_one(theme):
    """Every text colour the cards use, on the card and on the page."""
    t = _tokens(theme)
    for fg in ("--text", "--text-2", "--muted", "--accent-ink"):
        for bg in ("--card", "--bg"):
            assert _contrast(t[fg], t[bg]) >= 4.5, (theme, fg, bg, round(_contrast(t[fg], t[bg]), 2))
    # "Recommended for this computer": ink on honey, in both themes.
    assert _contrast("#0C0A09", t["--honey"] if "--honey" in t else _tokens("light")["--honey"]) >= 4.5
