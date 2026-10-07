"""First-run setup's screens (3.15, OB1, OB2, OB4, OB6, OB7, OB9).

Setup was four or five screens that each opened "Welcome to Waffler",
spent a whole step on the hotkey, offered a provider choice, and threw the
practice dictation away. It is now three steps on Windows (Connect Groq,
Try it, Use it anywhere) and four on a Mac (Permissions after Connect).
These tests read ui/index.html, ui/app.js and ui/logic.js (logic.js runs in
Node where it is installed) and check the decisions the plan asked for. No
network; nothing is launched.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="needs Node.js to run ui/logic.js")


def read(name):
    return (UI / name).read_text(encoding="utf-8")


def js(expr):
    code = (f"const L = require({json.dumps(str(UI / 'logic.js'))});\n"
            f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def setup_html():
    html = read("index.html")
    a = html.index('<div class="wizard-overlay ob" id="wizardOverlay"')
    return html[a:html.index("<!-- Toast -->", a)]


def visible_text(html):
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<(script|style|svg)[^>]*>.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# ── the steps ────────────────────────────────────────────────────────────────

@needs_node
def test_three_steps_on_windows_four_on_a_mac():
    assert js("L.setupSteps(false)") == ["connect", "try", "anywhere"]
    assert js("L.setupSteps(true)") == ["connect", "permissions", "try", "anywhere"]


def test_every_step_says_its_task_and_nobody_is_welcomed_twice():
    html = setup_html()
    titles = re.findall(r'<h1 class="ob-title"[^>]*>(.*?)</h1>', html, flags=re.S)
    text = [re.sub(r"<[^>]+>", "", t).strip() for t in titles]
    assert "Connect your free Groq account" in text
    assert "Groq is connected" in text
    assert "Let Waffler listen and type for you" in text
    assert "Now use it anywhere" in text
    assert any(t.startswith("Hold ") and t.endswith(" and talk") for t in text)
    assert "Welcome to Waffler" not in html and "quick steps" not in html


def test_the_hotkey_step_is_merged_into_try_it():
    html = setup_html()
    assert html.count('class="ob-step"') == 4
    assert "Listening for hotkey press" not in html
    assert 'id="obHoldKey"' in html and "Pick another key" in html


# ── Connect ──────────────────────────────────────────────────────────────────

def test_connect_has_one_button_and_groqs_four_steps_in_order():
    html = setup_html()
    assert html.count("Get my free Groq key") == 1
    assert "wizOpenGroq()" in html
    items = re.findall(r'<li><span class="ob-n">(\d)</span><span>(.*?)</span></li>', html)
    assert [n for n, _ in items] == ["1", "2", "3", "4"]
    steps = [re.sub(r"<[^>]+>", "", t) for _, t in items]
    assert steps == ["Sign in with Google or email", "Click Create API Key",
                     "Name it Waffler and click Submit", "Click Copy, then come back here"]
    assert "Groq shows the key once. Click Copy before you close that box." in html
    assert "Already have a key?" in html and "Paste it here" in html
    assert "Or paste it here" in html


def test_no_provider_choice_in_setup():
    """OpenAI and Cerebras are added in Settings, not chosen during setup."""
    html = setup_html()
    assert "wiz-prov-tab" not in html
    assert 'placeholder="sk-' not in html and 'placeholder="csk-' not in html
    assert "Add an OpenAI key later in Settings." in read("app.js")
    settings = read("index.html")
    assert 'id="apiKeyInput"' in settings and 'id="cerebrasKeyInput"' in settings


def test_only_verified_claims():
    """"Free for roughly 30 cleaned dictations a day" is verified; "No card
    needed" was not checked against Groq's current sign-up, so it is not said."""
    text = visible_text(setup_html())
    assert "Free for roughly 30 cleaned dictations a day." in text
    assert "No card" not in text and "no card" not in text
    assert "usually takes a second" not in text


def test_plain_words_and_no_em_dashes():
    text = visible_text(setup_html())
    assert chr(0x2014) not in text
    assert "API key" not in text.replace("Create API Key", "")
    assert "token" not in text.lower()
    for word in ("Accessibility", "Input Monitoring"):
        assert word not in text, f"setup says '{word}'; it says what the permission does instead"


def test_the_key_is_picked_up_on_focus_and_checked_by_itself():
    app = read("app.js")
    watch = app[app.index("function startWizClipboardWatch()"):]
    watch = watch[:watch.index("\n}")]
    assert "addEventListener('focus'" in watch and "_wizCheckClipboardOnce(false)" in watch
    once = app[app.index("async function _wizCheckClipboardOnce("):]
    once = once[:once.index("\n}")]
    assert "wizValidateGroqKey(r.key, true)" in once
    assert "r.provider !== 'groq'" in once


def test_a_busy_groq_is_retried_by_itself_a_few_times():
    app = read("app.js")
    body = app[app.index("async function wizValidateGroqKey("):]
    body = body[:body.index("\n}")]
    assert "r.kind === 'rate_limited' && _wizKeyRetries < 3" in body
    assert "Trying again in a few seconds." in body
    assert "wizRetryKeyCheckSoon(key)" in body


@needs_node
def test_what_the_key_box_says_before_asking_groq():
    views = js("['', 'gsk_short', 'gsk_' + 'a'.repeat(30), 'sk-proj-abc', 'csk-abc', 'hello']"
               ".map((v) => L.keyInputView(v).kind)")
    assert views == ["empty", "short", "ok", "openai", "cerebras", "bad"]
    msg = js("L.keyInputView('sk-proj-abc').message")
    assert msg == "That's an OpenAI key. Setup needs a free Groq key. You can add OpenAI later in Settings."


@needs_node
def test_connected_lists_each_job_with_a_plain_chip():
    rows = js("L.serviceRows([{name:'Speech to text',provider:'Groq',model:'whisper-large-v3',status:'ok'},"
              "{name:'Clean-up',provider:'Groq',model:'gpt-oss-120b',status:'missing'}])")
    assert [(r["title"], r["chip"]) for r in rows] == [("Speech to text", "Ready"), ("Clean-up", "Not listed")]
    assert rows[0]["desc"] == "Groq · whisper-large-v3"
    assert rows[1]["desc"] == "Groq · gpt-oss-120b. Groq didn't list this model for your key."
    assert [r["chip"] for r in js("L.serviceRows(null)")] == ["Key saved", "Key saved"]


# ── Try it ───────────────────────────────────────────────────────────────────

@needs_node
def test_you_said_strikes_what_the_clean_up_left_out():
    parts = js("L.saidDiff('Send it to John, sorry, James, by Wednesday at three.', "
               "'Send it to James by Wednesday at three.')")
    assert parts == [{"text": "Send it to ", "cut": False}, {"text": "John, sorry,", "cut": True},
                     {"text": " James, by Wednesday at three.", "cut": False}]
    # The pieces always join back to exactly what was said.
    said = "Hi, Sam. Um, so, yeah, for the review, can we do Thursday at two?"
    joined = js(f"L.saidDiff({json.dumps(said)}, 'Hi Sam, for the review, can we do Thursday at two?')"
                ".map((p) => p.text).join('')")
    assert joined == said


@needs_node
def test_a_rewrite_strikes_nothing():
    assert js("L.saidDiff('hello there my friend', 'Totally different words')") == [
        {"text": "hello there my friend", "cut": False}]
    assert js("L.saidDiff('Testing one two three.', 'Testing one two three.')") == [
        {"text": "Testing one two three.", "cut": False}]
    assert js("L.saidDiff('', 'x')") == []


def test_the_practice_shows_said_next_to_wrote_and_saves_it():
    html = setup_html()
    assert 'id="obSaid"' in html and 'id="obWrote"' in html
    assert "What you said" in html and "Waffler wrote" in html
    assert "Saved as your first Journal entry" in html
    app = read("app.js")
    for fn in ("window.wizOnRecordingStart", "window.wizOnLevel", "window.wizOnRecordingStop",
               "window.wizOnCleaning", "window.wizOnPracticeResult", "window.wizOnSilentRecording",
               "window.wizOnPracticeError"):
        assert fn + " = function" in app, fn
    # Each of those is what app.py calls.
    py = (ROOT / "app.py").read_text(encoding="utf-8")
    for name in ("wizOnRecordingStart", "wizOnRecordingStop", "wizOnSilentRecording", "wizOnLevel",
                 "wizOnCleaning", "wizOnPracticeResult", "wizOnPracticeError"):
        assert name in py, name


def test_silent_denied_and_failed_practices_each_say_what_to_do():
    html = setup_html()
    assert "Waffler heard silence." in html
    assert "Waffler isn't allowed to use the microphone." in html
    assert "wizAllow('microphone')" in html
    assert 'data-when="error"' in html and 'id="obPracticeError"' in html


# ── Mac permissions ──────────────────────────────────────────────────────────

def test_three_allow_rows_call_the_real_prompts():
    html = setup_html()
    for name in ("microphone", "input_monitoring", "accessibility"):
        assert f"wizAllow('{name}')" in html
    app = read("app.js")
    allow = app[app.index("async function wizAllow("):]
    assert "pywebview.api.request_permission(name)" in allow[:allow.index("\n}")]
    tick = app[app.index("async function wizPermTick()"):]
    tick = tick[:tick.index("\n}")]
    assert "microphone: !!r.mic_granted" in tick
    assert "wizNext()" in tick                          # moves on by itself
    assert "Your Mac may ask you to quit and reopen Waffler. That's normal." in html


def test_the_fn_key_warning_is_shown_never_fixed():
    html = read("index.html")
    assert 'id="obFnWarn"' in html and 'id="settingsFnWarn"' in html
    assert "Open Keyboard settings" in html
    app = read("app.js")
    assert "get_fn_key_conflict()" in app
    py = (ROOT / "app.py").read_text(encoding="utf-8")
    body = py[py.index("def get_fn_key_conflict"):py.index("def open_keyboard_settings")]
    assert "defaults" not in body.replace("_mac_perms.read_fn_usage()", "")


# ── Use it anywhere ──────────────────────────────────────────────────────────

def test_the_last_step_opens_an_editor_and_starts_at_sign_in_by_default():
    html = setup_html()
    assert "Open Notepad and try it" in html
    assert re.search(r'<input type="checkbox" class="tog" role="switch" id="wizStartAtLogin" checked', html)
    app = read("app.js")
    assert "'Open TextEdit and try it'" in app
    done = app[app.index("async function wizCompleteSetup()"):]
    done = done[:done.index("\n}")]
    assert "set_start_at_login(tog.checked)" in done
    assert done.index("set_start_at_login") < done.index("complete_setup()")
    assert "let _wizLoginChoice = true;" in app
    assert "Closing this window doesn't stop it." in html


def test_settings_has_the_off_switch_read_from_the_system():
    html = read("index.html")
    assert re.search(r'id="startAtLoginToggle"[^>]*onchange="setStartAtLogin\(this.checked\)"', html)
    app = read("app.js")
    load = app[app.index("async function loadStartAtLogin()"):]
    assert "get_start_at_login()" in load[:load.index("\n}")]
    assert "await loadStartAtLogin();" in app


def test_back_is_hidden_once_the_real_hotkey_is_listening():
    """Back to Try it would start the practice listener beside the real one."""
    app = read("app.js")
    assert "back.hidden = i <= 0 || (step === 'anywhere' && _wizDictationLive);" in app
    py = (ROOT / "app.py").read_text(encoding="utf-8")
    start = py[py.index("def wizard_start_hotkey_test"):py.index("def wizard_stop_hotkey_test")]
    assert "if _pipeline_running_or_starting():" in start
    setup = py[py.index("def start_dictation_for_setup"):py.index("def open_practice_editor")]
    assert "if _pipeline_running_or_starting():" in setup


# ── Existing users, and resuming ─────────────────────────────────────────────

def test_setup_opens_only_when_it_is_needed_and_once():
    app = read("app.js")
    check = app[app.index("async function checkOnboarding()"):]
    check = check[:check.index("\n}")]
    assert "if (status.needs_setup)" in check and "if (_wizardShown) return;" in check


def test_a_reopened_setup_carries_on_where_it_was():
    app = read("app.js")
    show = app[app.index("function showWizard("):]
    show = show[:show.index("\n}")]
    assert "WIZ_STEPS.includes(status.resume_step)" in show
    assert "pywebview.api.save_setup_step(step)" in app
    py = (ROOT / "app.py").read_text(encoding="utf-8")
    status = py[py.index("def get_onboarding_status"):py.index("def save_setup_step")]
    assert '"resume_step": resume' in status and "if needs_setup:" in status
    done = py[py.index("def complete_setup"):py.index("# ── Snippets API")]
    assert 'stored.pop("setup_step", None)' in done


def test_setup_follows_the_theme():
    css = read("setup.css")
    assert "!important" not in css.replace(".ob [hidden] { display: none !important; }", "") \
        .replace("0 0 0 7px rgba(217, 164, 65, 0.18) !important", "")
    assert 'body[data-theme="dark"] .ob-art' in css
    style = read("style.css")
    assert "#FFFBF3 0%, #FBF7EB 100%) !important" not in style
