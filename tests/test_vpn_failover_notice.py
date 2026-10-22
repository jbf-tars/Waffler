"""The plain sentence that says another provider did the work.

When speech to text or the clean-up had to use a different provider than the
first choice because the first could not be reached from this network (a
block, or no connection; not a rate limit, which has its own message), the
person is told once, after the paste: "Groq can't be reached from this
network, so Waffler used OpenAI instead. A VPN is often the reason."

The transcriber and the styler only RECORD the fact (`last_failovers`, the
clean-up's `failovers`); app.py shows it once the words are pasted, through the
notifier, one kind per provider with a 30 minute gap. No toast on the hot
path, no raw error, no text from the dictation.
"""
import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _pipeline_harness as H  # noqa: E402
import net_health as nh  # noqa: E402
import notifier as N  # noqa: E402
import user_messages as um  # noqa: E402

HALF_HOUR = 30 * 60
WORDS = "ok so ship it on monday"


# ── the words ────────────────────────────────────────────────────────────────

def test_the_sentence_for_a_provider_that_cannot_be_reached():
    assert um.failover_message("groq", "openai", "unreachable") == (
        "Used OpenAI instead",
        "Groq can't be reached from this network, so Waffler used OpenAI instead. "
        "A VPN is often the reason.")


def test_the_sentence_for_a_provider_that_refuses_this_network():
    assert um.failover_message("groq", "openai", "blocked") == (
        "Used OpenAI instead",
        "Groq is blocking this network, so Waffler used OpenAI instead. "
        "A VPN is often the reason.")


@pytest.mark.parametrize("provider,used", [("groq", "openai"), ("openai", "groq"),
                                           ("cerebras", "groq"), ("groq", "cerebras")])
@pytest.mark.parametrize("kind", ["unreachable", "blocked"])
def test_every_wording_is_plain(provider, used, kind):
    heading, body = um.failover_message(provider, used, kind)
    for text in (heading, body):
        assert chr(0x2014) not in text and chr(0x2013) not in text, "no dashes"
        for jargon in ("HTTP 403", "fallback", "provider key", "Error code", "Traceback", "429",
                       "timeout", "Timeout", "exception", "API"):
            assert jargon not in text, (jargon, text)
    assert body.endswith(".") and "A VPN is often the reason." in body
    assert um._PROVIDER_NAMES[provider] in body and um._PROVIDER_NAMES[used] in body


def test_an_unknown_name_still_reads_as_a_sentence():
    heading, body = um.failover_message("mystery", "other")
    assert body.endswith("A VPN is often the reason.") and "Your provider" in body


# ── the governor ─────────────────────────────────────────────────────────────

def test_one_kind_for_each_provider_information_with_a_half_hour_gap():
    for provider, kind_id in nh.FAILOVER_KINDS.items():
        kind = N.KINDS[kind_id]
        assert kind.severity == N.INFO, "information: it is not an error and not a tip"
        assert kind.min_gap_s == HALF_HOUR
        assert kind.max_times is None, "no cap over a lifetime: the gap is the rule"
        assert kind.sound is None, "never a sound for this"


def test_the_notifier_lets_one_through_per_provider_per_half_hour():
    now = [1000.0]
    n = N.Notifier(store=N.MemoryStore(), clock=lambda: now[0])
    assert n.request("failover_groq").show is True
    now[0] += 60
    d = n.request("failover_groq")
    assert d.show is False and d.reason == "gap"
    assert n.request("failover_openai").show is True, "another provider has its own gap"
    now[0] += HALF_HOUR - 61
    assert n.request("failover_groq").show is False, "29 minutes 59 seconds"
    now[0] += 2
    assert n.request("failover_groq").show is True, "half an hour on"


def test_a_muted_kind_is_not_shown():
    n = N.Notifier(store=N.MemoryStore())
    n.mute("failover_groq")
    d = n.request("failover_groq")
    assert d.show is False and d.reason == "muted"


def test_it_does_not_wait_for_a_quiet_moment_like_a_tip_does():
    """An information message has no quiet-moment rule (it comes after the
    paste, when the dictation is over), but it keeps its gap and its mute."""
    n = N.Notifier(store=N.MemoryStore(),
                   context=lambda: N.Context(recording=True, processing=True, setup_done=False,
                                             tips_enabled=False))
    assert n.request("failover_groq").show is True


def test_the_gap_is_kept_across_restarts():
    store = N.MemoryStore()
    clock = [5000.0]
    N.Notifier(store=store, clock=lambda: clock[0]).request("failover_groq")
    clock[0] += 120                                    # the app was restarted
    assert N.Notifier(store=store, clock=lambda: clock[0]).request("failover_groq").show is False


# ── through the pipeline ─────────────────────────────────────────────────────

class FailedOverTranscriber(H.FakeTranscriber):
    """Groq could not be reached, so OpenAI did the speech to text."""

    def __init__(self, events, **kw):
        super().__init__(**kw)
        self.events = events
        self.client = object()
        self.last_failovers = []

    def transcribe_sync(self, audio_bytes):
        text = super().transcribe_sync(audio_bytes)
        self.last_failovers = list(self.events)
        self._last_cloud_provider = "openai"
        return text


class FailedOverStyler(H.FakeStyler):
    def __init__(self, events, **kw):
        super().__init__(**kw)
        self.events = events

    def style(self, transcript, app_context=None):
        styled, usage = super().style(transcript, app_context)
        if self.events:
            usage = dict(usage, failovers=list(self.events))
        return styled, usage


def speech_event(provider="groq", used="openai", kind="unreachable"):
    return nh.failover_dict(provider, used, kind, nh.STAGE_SPEECH)


def cleanup_event(provider="groq", used="openai", kind="unreachable"):
    return nh.failover_dict(provider, used, kind, nh.STAGE_CLEANUP)


@pytest.fixture
def limits(monkeypatch):
    H.fast_limits(monkeypatch)


def dictate(p, generation=1):
    t = H.run_process(p, generation)
    t.join(8)
    assert not t.is_alive()
    # The toast, if any, is shown on the pipeline thread before it ends.
    return p


def next_press(p, generation):
    p._processing_id = p._current_press_id = generation
    p._recording_start_time = __import__("time").time() - 3.0


def notices(p):
    return [t for t in p.overlay.toasts() if t.get("style") == "info"]


def test_a_dictation_that_needed_another_provider_says_so_once_after_the_paste(tmp_path, limits):
    clip = H.FakeClipboard()
    p = H.make_pipeline(tmp_path, transcriber=FailedOverTranscriber([speech_event()]),
                        clipboard=clip)
    when_shown = []
    original = p.overlay.show_toast

    def spy(**kw):
        when_shown.append(len(clip.pastes))
        return original(**kw)
    p.overlay.show_toast = spy
    dictate(p)
    [toast] = notices(p)
    assert toast == {"style": "info", "heading": "Used OpenAI instead",
                     "body": "Groq can't be reached from this network, so Waffler used OpenAI "
                             "instead. A VPN is often the reason.", "buttons": None,
                     "timeout": 8, "reposition": False}
    assert when_shown == [1], "shown after the words were pasted, never before"
    assert p._notifier.snapshot()["failover_groq"]["shown"] == 1
    assert any("[net] told the person: groq unreachable, openai used for the speech" in x
               for x in p.logged)


def test_a_block_is_worded_as_a_block(tmp_path, limits):
    p = H.make_pipeline(tmp_path, transcriber=FailedOverTranscriber([speech_event(kind="blocked")]))
    dictate(p)
    [toast] = notices(p)
    assert toast["body"].startswith("Groq is blocking this network, so Waffler used OpenAI instead")


def test_the_cleanup_can_be_the_one_that_failed_over(tmp_path, limits):
    p = H.make_pipeline(tmp_path, styler=FailedOverStyler([cleanup_event("cerebras", "groq")]))
    dictate(p)
    [toast] = notices(p)
    assert toast["body"].startswith("Cerebras can't be reached from this network, so Waffler used Groq")
    assert "failover_cerebras" in p._notifier.snapshot()


def test_speech_and_cleanup_failing_over_for_the_same_provider_is_one_notice(tmp_path, limits):
    p = H.make_pipeline(tmp_path, transcriber=FailedOverTranscriber([speech_event()]),
                        styler=FailedOverStyler([cleanup_event()]))
    dictate(p)
    assert len(notices(p)) == 1


def test_two_dictations_in_a_row_say_it_once(tmp_path, limits):
    p = H.make_pipeline(tmp_path, transcriber=FailedOverTranscriber([speech_event()]))
    dictate(p, 1)
    next_press(p, 2)
    dictate(p, 2)
    assert len(notices(p)) == 1
    assert any("failover_groq not shown (gap)" in x for x in p.logged)


def test_half_an_hour_later_it_is_said_again(tmp_path, limits):
    now = [10_000.0]
    p = H.make_pipeline(tmp_path, transcriber=FailedOverTranscriber([speech_event()]))
    p._notifier = N.Notifier(store=N.MemoryStore(), clock=lambda: now[0],
                             context=p._notifier_context, log=p.logged.append)
    dictate(p, 1)
    now[0] += HALF_HOUR - 5
    next_press(p, 2)
    dictate(p, 2)
    assert len(notices(p)) == 1
    now[0] += 10
    next_press(p, 3)
    dictate(p, 3)
    assert len(notices(p)) == 2


def test_another_provider_failing_gets_its_own_notice(tmp_path, limits):
    p = H.make_pipeline(tmp_path, transcriber=FailedOverTranscriber([speech_event("groq", "openai")]))
    dictate(p, 1)
    p.transcriber.events = [speech_event("openai", "groq")]
    next_press(p, 2)
    dictate(p, 2)
    heads = [t["heading"] for t in notices(p)]
    assert heads == ["Used OpenAI instead", "Used Groq instead"]


def test_a_dictation_that_needed_nobody_else_says_nothing(tmp_path, limits):
    p = H.make_pipeline(tmp_path)
    dictate(p)
    assert notices(p) == [] and p.overlay.toasts() == []
    assert "failover_groq" not in p._notifier.snapshot()


@pytest.mark.parametrize("event", [
    nh.failover_dict("groq", "openai", "slow", "speech"),
    nh.failover_dict("groq", "openai", "server", "speech"),
    nh.failover_dict("groq", "openai", "rate_limit", "speech"),
    nh.failover_dict("groq", "groq", "unreachable", "speech"),
])
def test_what_is_not_the_networks_doing_says_nothing(tmp_path, limits, event):
    p = H.make_pipeline(tmp_path, transcriber=FailedOverTranscriber([event]))
    dictate(p)
    assert notices(p) == []


def test_a_notifier_that_breaks_does_not_break_the_dictation(tmp_path, limits):
    p = H.make_pipeline(tmp_path, transcriber=FailedOverTranscriber([speech_event()]))

    def boom(kind_id):
        raise RuntimeError("state file locked")
    p._notifier.request = boom
    dictate(p)
    assert H.history(p), "the words were saved"
    assert p.clipboard.pastes == ["Ok so ship it on monday."]


def test_the_notice_never_carries_the_words(tmp_path, limits):
    p = H.make_pipeline(tmp_path, transcriber=FailedOverTranscriber([speech_event()]))
    dictate(p)
    [toast] = notices(p)
    shown = f"{toast['heading']} {toast['body']} " + " ".join(p.logged)
    assert "monday" not in shown.lower() and "ship it" not in shown.lower()


def test_a_newer_recording_has_the_pill_so_no_notice_is_put_over_it(tmp_path):
    p = H.make_pipeline(tmp_path)
    run = type("Run", (), {"generation": 1})()
    p._processing_id = 2                                # a newer dictation has begun
    p._announce_failover(run, {"last_failovers": [speech_event()]}, {})
    assert p.overlay.toasts() == []
    assert "failover_groq" not in p._notifier.snapshot(), "not counted as said"
    p._processing_id = 1
    p.is_recording = True                               # or the key is down again
    p._announce_failover(run, {"last_failovers": [speech_event()]}, {})
    assert p.overlay.toasts() == []


def test_a_notice_that_fails_to_show_is_swallowed(tmp_path):
    p = H.make_pipeline(tmp_path)
    run = type("Run", (), {"generation": 1})()
    p._show_toast_safe = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("overlay gone"))
    p._announce_failover(run, {"last_failovers": [speech_event()]}, {})
    assert any("[net] notice skipped (RuntimeError)" in x for x in p.logged)


def test_malformed_events_are_ignored(tmp_path):
    p = H.make_pipeline(tmp_path)
    run = type("Run", (), {"generation": 1})()
    p._announce_failover(run, {"last_failovers": ["junk", None, 4, {"provider": "groq"}]},
                         {"failovers": "not a list"})
    p._announce_failover(run, None, None)
    assert p.overlay.toasts() == []


# ── where it sits in app.py ──────────────────────────────────────────────────

APP = (ROOT / "app.py").read_text(encoding="utf-8")


def test_the_notice_is_raised_after_the_paste_and_the_history_save():
    process = APP[APP.index("    def _process(self"):]
    paste = process.index("self.clipboard.paste_text")
    saved = process.index("append_history_safely(item)")
    notice = process.index("self._announce_failover(run, _asr_info, gpt_usage)")
    assert paste < saved < notice, "after the words are pasted and saved"


def test_the_transcriber_and_the_styler_never_show_anything():
    for name in ("transcribe_whisper.py", "style_openai.py", "net_health.py"):
        src = (ROOT / "src" / name).read_text(encoding="utf-8")
        assert "show_toast" not in src and "overlay" not in src.lower().replace("overlap", ""), name


def test_every_kind_is_named_in_the_source_so_none_is_left_over():
    src = (ROOT / "src" / "net_health.py").read_text(encoding="utf-8")
    for kind_id in nh.FAILOVER_KINDS.values():
        assert f'"{kind_id}"' in src


def test_the_message_function_lives_with_the_other_user_messages():
    assert re.search(r"def failover_message\(provider", (ROOT / "src" / "user_messages.py")
                     .read_text(encoding="utf-8"))
    assert threading  # imported for the harness; keeps the linter quiet
