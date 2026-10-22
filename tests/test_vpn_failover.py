"""Speech to text behind a VPN that blocks, drops or stalls a provider.

NordVPN-style full tunnels send every request out through the VPN company's
servers, and a provider treats those addresses in different ways (see
tests/_net_fakes.py). Before, a provider that silently dropped packets was
waited on for the whole request timeout (60 s or more) before the next was
tried, and the flat 30 s pause then repeated that wait twice a minute, so
dictation felt dead. These tests run the real request code, over the real SDK
clients, against a network that does each of those things, on a clock that only
moves when something waits.
"""
import sys
from pathlib import Path

import groq
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _net_fakes as nf  # noqa: E402
import _pipeline_harness as H  # noqa: E402
import transcribe_whisper as tw  # noqa: E402
import vpn_detect  # noqa: E402

AUDIO = nf.AUDIO


@pytest.fixture
def net(monkeypatch):
    return nf.FakeNet(monkeypatch)


@pytest.fixture
def log(monkeypatch):
    lines = []
    monkeypatch.setattr(tw, "_wlog", lines.append)
    return lines


def paused_for(t, provider="groq"):
    """Seconds from now that ``provider`` is left alone."""
    return getattr(t, f"_{provider}_skip_until") - __import__("time").monotonic()


# ── the five things a VPN can do to a provider ───────────────────────────────

def test_a_provider_that_works_is_untouched(net, log):
    t, g, o = nf.transcriber(net, nf.works("from groq"), nf.works("from openai"))
    started = net.now
    assert t._dispatch_one(AUDIO) == "from groq"
    assert net.now - started == pytest.approx(0.4)
    assert len(g.calls) == 1 and o.calls == []
    assert t._groq_skip_until == 0.0
    assert getattr(t, "last_failovers", []) == []
    assert not [line for line in log if line.startswith("[net]")], "no failure, no [net] line"
    assert t._last_cloud_provider == "groq"


def test_http_403_at_the_edge_falls_over_at_once_and_pauses_groq_a_minute(net, log):
    t, g, o = nf.transcriber(net, nf.edge_403(), nf.works("from openai"))
    started = net.now
    assert t._dispatch_one(AUDIO) == "from openai"
    assert net.now - started == pytest.approx(0.2 + 0.4), "no waiting: the refusal came back at once"
    assert paused_for(t) == pytest.approx(60.0 - 0.4)
    assert len(g.calls) == 1 and len(o.calls) == 1
    assert t._last_cloud_provider == "openai"


def test_a_black_hole_costs_the_connect_timeout_not_the_request_timeout(net, log):
    """The owner's case: Groq's packets are dropped. 6 s, then OpenAI answers
    (it used to be 60 s or more before OpenAI was even tried)."""
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.works("from openai"))
    started = net.now
    assert t._dispatch_one(AUDIO) == "from openai"
    spent = net.now - started
    assert spent == pytest.approx(6.0 + 0.4)
    assert spent < 10 < tw._upload_timeout_s(len(AUDIO)), "not the 60 s it used to be"
    assert len(g.calls) == 1, "Groq was not retried: the next provider is the quicker recovery"
    assert paused_for(t) == pytest.approx(30.0 - 0.4)


def test_a_stalled_upload_costs_the_stall_limit(net, log):
    """The connection opens, the upload stops moving (an MTU problem)."""
    t, g, o = nf.transcriber(net, nf.stalled_upload(), nf.works("from openai"))
    started = net.now
    assert t._dispatch_one(AUDIO) == "from openai"
    assert net.now - started == pytest.approx(20.0 + 0.4)
    assert paused_for(t) > 0 and t._last_cloud_provider == "openai"


def test_a_server_that_takes_the_upload_and_says_nothing_costs_about_25_seconds(net, log):
    t, g, o = nf.transcriber(net, nf.silent_server(), nf.works("from openai"))
    started = net.now
    assert t._dispatch_one(AUDIO) == "from openai"
    assert net.now - started == pytest.approx(25.0 + 0.4, abs=0.1)
    assert net.now - started < 60


def test_a_reset_connection_falls_over_at_once(net, log):
    t, g, o = nf.transcriber(net, nf.reset(), nf.works("from openai"))
    started = net.now
    assert t._dispatch_one(AUDIO) == "from openai"
    assert net.now - started == pytest.approx(0.05 + 0.4)
    assert paused_for(t) == pytest.approx(30.0 - 0.4)


def test_an_unavailable_provider_is_paused_too_but_is_not_the_networks_fault(net, log):
    t, g, o = nf.transcriber(net, nf.unavailable(), nf.works("from openai"))
    assert t._dispatch_one(AUDIO) == "from openai"
    assert paused_for(t) > 0
    assert getattr(t, "last_failovers", []) == [], "a 503 is the provider's trouble, not the network's"


@pytest.mark.parametrize("mode", [nf.black_hole, nf.stalled_upload, nf.silent_server, nf.edge_403,
                                  nf.reset, nf.unavailable])
def test_every_failover_finishes_inside_the_pipelines_own_deadline(net, log, mode):
    """The watchdog gives speech to text 45 s for a short clip, and then saves
    the recording as Not sent. With a connect that waited the whole 60 s, a
    black-holed Groq used up that deadline before OpenAI was ever asked."""
    import pipeline_watchdog as pw
    deadline = pw.transcribe_deadline_s(5.0)
    assert deadline == 45.0
    t, g, o = nf.transcriber(net, mode(), nf.works("from openai"))
    started = net.now
    assert t._dispatch_one(AUDIO) == "from openai"
    assert net.now - started < deadline - 15, "with room to spare, not just under it"


def test_the_old_wait_would_have_missed_that_deadline():
    import pipeline_watchdog as pw
    assert tw._upload_timeout_s(len(AUDIO)) > pw.transcribe_deadline_s(5.0)


# ── the timeouts the clients are given ───────────────────────────────────────

def test_the_first_provider_connects_fast_when_another_can_take_the_clip(net, log):
    t, g, o = nf.transcriber(net, nf.works(), nf.works())
    t._dispatch_one(AUDIO)
    assert g.last_timeouts["connect"] == 6.0
    assert g.last_timeouts["write"] == 20.0
    assert g.last_timeouts["read"] == pytest.approx(25.0, abs=0.1)
    assert g.last_timeouts["pool"] == 60.0


def test_the_last_provider_left_is_waited_on(net, log):
    t, g, o = nf.transcriber(net, nf.edge_403(), nf.works())
    t._dispatch_one(AUDIO)
    assert g.last_timeouts["connect"] == 6.0
    assert o.last_timeouts == {"connect": 20.0, "read": 60.0, "write": 60.0, "pool": 60.0}


def test_a_sole_provider_gets_the_generous_values(net, log):
    t, g, o = nf.transcriber(net, nf.works())          # a Groq key alone
    t._dispatch_one(AUDIO)
    assert g.last_timeouts == {"connect": 20.0, "read": 60.0, "write": 60.0, "pool": 60.0}
    t, g, o = nf.transcriber(net, None, nf.works())    # an OpenAI key alone
    t._dispatch_one(AUDIO)
    assert o.last_timeouts == {"connect": 20.0, "read": 60.0, "write": 60.0, "pool": 60.0}


def test_the_sole_provider_is_not_given_up_on_early_and_not_paused(net, log):
    t, g, o = nf.transcriber(net, nf.black_hole())
    started = net.now
    with pytest.raises(groq.APITimeoutError):
        t._dispatch_one(AUDIO)
    assert net.now - started == pytest.approx(20.0), "20 s to connect, not 6"
    assert t._groq_skip_until == 0.0, "pausing the only option is a certain failure"
    assert getattr(t, "last_failovers", []) == []
    assert any("not paused (the only speech provider)" in line for line in log)


def test_a_large_upload_scales_the_allowance_with_its_size(net, log, monkeypatch):
    monkeypatch.setattr(tw, "_GROQ_MAX_UPLOAD_BYTES", 10 * 1024 * 1024)
    t, g, o = nf.transcriber(net, nf.works(), nf.works())
    big = b"\x00" * (9 * 1024 * 1024)
    t._dispatch_one(big)
    assert g.last_timeouts["read"] == pytest.approx(25.0 + 4.0 * 9, abs=0.2)
    assert g.last_timeouts["connect"] == 6.0


def test_a_clip_over_the_groq_gate_goes_to_openai_which_is_then_the_only_option(net, log, monkeypatch):
    monkeypatch.setattr(tw, "_GROQ_MAX_UPLOAD_BYTES", 1000)
    t, g, o = nf.transcriber(net, nf.works(), nf.works("from openai"))
    assert t._dispatch_one(AUDIO) == "from openai"
    assert g.calls == []
    assert o.last_timeouts["connect"] == 20.0, "nothing else can take it"


def test_the_timeout_outside_a_dispatch_is_the_generous_one():
    assert tw._request_timeout(1000).connect == 20.0


# ── pauses that grow while a provider keeps failing ──────────────────────────

def failures_in_a_row(net, t, g, n):
    """Dictate n times, each after the last pause is over, and return the pause
    each failure set (seconds from the moment Groq failed)."""
    pauses = []
    for _ in range(n):
        before = len(g.calls)
        t._dispatch_one(AUDIO)
        assert len(g.calls) == before + 1, "Groq was asked, since its pause was over"
        begun, timeouts = g.calls[-1]
        failed_at = net.now - 0.4                      # OpenAI answered 0.4 s later
        pauses.append(round(t._groq_skip_until - failed_at, 1))
        net.pass_time(pauses[-1] + 1)
    return pauses


def test_a_black_holed_groq_is_paused_30_60_120_240_then_five_minutes(net, log):
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.works())
    assert failures_in_a_row(net, t, g, 7) == [30, 60, 120, 240, 300, 300, 300]


def test_a_blocked_groq_is_paused_60_120_240_then_five_minutes(net, log):
    t, g, o = nf.transcriber(net, nf.edge_403(), nf.works())
    assert failures_in_a_row(net, t, g, 6) == [60, 120, 240, 300, 300, 300]


def test_the_first_answer_starts_the_count_again(net, log):
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.works())
    assert failures_in_a_row(net, t, g, 4) == [30, 60, 120, 240]
    g.mode = nf.works("groq is back")                  # the VPN server changed
    assert t._dispatch_one(AUDIO) == "groq is back"
    assert t._groq_skip_until == 0.0
    assert any("[net] speech groq answered again after 4 failed tries in a row" in line for line in log)
    g.mode = nf.black_hole()
    assert failures_in_a_row(net, t, g, 2) == [30, 60], "from the start, not from 480"


def test_a_failure_long_after_the_last_is_a_new_start(net, log):
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.works())
    assert failures_in_a_row(net, t, g, 3) == [30, 60, 120]
    net.pass_time(20 * 60)                             # lunch, and a different network
    assert failures_in_a_row(net, t, g, 1) == [30]


def test_while_groq_is_paused_it_is_not_touched_and_openai_answers_at_once(net, log):
    """Stays failed over."""
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.works("from openai"))
    t._dispatch_one(AUDIO)
    g.mode = nf.works("from groq")                     # it would work now, but is left alone
    for _ in range(5):
        net.pass_time(5)
        started = net.now
        assert t._dispatch_one(AUDIO) == "from openai"
        assert net.now - started == pytest.approx(0.4), "straight to OpenAI: no wait at all"
    assert len(g.calls) == 1


def test_groq_is_asked_again_when_its_pause_ends(net, log):
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.works("from openai"))
    t._dispatch_one(AUDIO)
    g.mode = nf.works("from groq")
    net.pass_time(31)
    assert t._dispatch_one(AUDIO) == "from groq"
    assert len(g.calls) == 2


def test_openai_is_paused_as_well_when_it_is_the_one_that_fails(net, log):
    t, g, o = nf.transcriber(net, nf.works("from groq"), nf.edge_403(), order=("openai", "groq"))
    assert t._dispatch_one(AUDIO) == "from groq"
    assert paused_for(t, "openai") > 0
    o.mode = nf.works("from openai")
    assert t._dispatch_one(AUDIO) == "from groq", "OpenAI is left alone during its pause"
    assert len(o.calls) == 1


def test_a_request_at_fault_does_not_make_the_pause_grow(net, log):
    """A 400 is about that clip, not about the provider."""
    bad = nf.unavailable(400)
    t, g, o = nf.transcriber(net, bad, nf.works())
    assert failures_in_a_row(net, t, g, 4) == [30, 30, 30, 30]


# ── a pause never turns a dictation that could be done into a failure ───────

def test_when_everything_else_fails_a_paused_provider_is_asked_anyway(net, log):
    """The VPN server changed inside Groq's pause, and OpenAI is down."""
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.works())
    t._dispatch_one(AUDIO)
    assert paused_for(t) > 0
    g.mode = nf.works("groq came back")
    o.mode = nf.unavailable()
    assert t._dispatch_one(AUDIO) == "groq came back"
    assert t._groq_skip_until == 0.0, "a probe that works clears the pause"
    assert len(g.calls) == 2
    assert any("asking groq again anyway" in line for line in log)


def test_the_probe_is_asked_only_after_the_others_and_their_retries(net, log):
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.works())
    t._dispatch_one(AUDIO)
    g.mode = nf.works("groq came back")
    o.mode = nf.unavailable()
    before = len(o.calls)
    t._dispatch_one(AUDIO)
    assert len(o.calls) - before == 1 + tw._TRANSIENT_RETRIES, "OpenAI is the last active one: retried"


def test_the_error_raised_is_the_last_one_when_nothing_answers(net, log):
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.unavailable())
    with pytest.raises(Exception) as ei:
        t._dispatch_one(AUDIO)
    assert "503" in str(ei.value) or "service" in str(ei.value).lower() or ei.value.__class__.__name__ == "InternalServerError"


def test_two_paused_providers_are_both_asked_rather_than_nothing(net, log):
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.reset())
    with pytest.raises(Exception):
        t._dispatch_one(AUDIO)
    assert paused_for(t, "groq") > 0 and paused_for(t, "openai") > 0
    g.mode, o.mode = nf.works("groq"), nf.works("openai")
    assert t._dispatch_one(AUDIO) == "groq", "both paused: still better to ask than to fail"


def test_the_alternate_provider_retry_never_probes_a_paused_one(net, log):
    """That retry is a bonus (a transcript that looks incomplete); it must not
    spend time on a provider known to be unreachable."""
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.works())
    t._dispatch_one(AUDIO)
    o.mode = nf.unavailable()
    calls = len(g.calls)
    with pytest.raises(Exception):
        t._dispatch_one(AUDIO, exclude="groq")
    assert len(g.calls) == calls


# ── the Groq upload gate and the pause stay as they were ────────────────────

def test_the_constants_the_rest_of_the_code_and_the_tests_name(net):
    assert tw._GROQ_AUTH_COOLDOWN_S == 60.0 and tw._GROQ_ERROR_COOLDOWN_S == 30.0
    assert tw._TRANSIENT_RETRIES == 2


# ── what is recorded for the notice ──────────────────────────────────────────

def test_a_block_is_recorded_as_a_failover(net, log):
    t, g, o = nf.transcriber(net, nf.edge_403(), nf.works())
    t._dispatch_one(AUDIO)
    assert t.last_failovers == [{"provider": "groq", "used": "openai", "kind": "blocked",
                                 "stage": "speech"}]


@pytest.mark.parametrize("mode,kind", [(nf.black_hole, "unreachable"), (nf.stalled_upload, "unreachable"),
                                       (nf.reset, "unreachable")])
def test_no_connection_is_recorded_as_a_failover(net, log, mode, kind):
    t, g, o = nf.transcriber(net, mode(), nf.works())
    t._dispatch_one(AUDIO)
    assert [e["kind"] for e in t.last_failovers] == [kind]


@pytest.mark.parametrize("mode", [nf.silent_server, nf.unavailable])
def test_a_slow_or_broken_provider_is_not_a_network_failover(net, log, mode):
    t, g, o = nf.transcriber(net, mode(), nf.works())
    t._dispatch_one(AUDIO)
    assert getattr(t, "last_failovers", []) == []


def test_a_provider_that_fails_with_nobody_to_take_over_records_nothing(net, log):
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.black_hole())
    with pytest.raises(Exception):
        t._dispatch_one(AUDIO)
    assert getattr(t, "last_failovers", []) == [], "no provider did the work, so there is nobody to name"


def test_each_recording_starts_with_none(net, log, monkeypatch):
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.works("hello there my friend"))
    monkeypatch.setattr(tw, "load_vocab", lambda: [])
    wav = H.speech_wav(2.0)
    t.transcribe_sync(wav)
    assert len(t.last_failovers) == 1
    net.pass_time(5)                                   # Groq is still paused: nothing fails this time
    t.transcribe_sync(wav)
    assert t.last_failovers == [], "the earlier recording's failover is not this one's"


def test_the_pipeline_snapshot_carries_the_failovers():
    import re
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert re.search(r'\("last_failovers", \(\)\)', src), "_transcribe_with_provenance snapshots it"


# ── the [net] line ───────────────────────────────────────────────────────────

ROUTE_NORD = "   route to: 1.1.1.1\ndestination: 0.0.0.0\n  interface: utun5\n"


@pytest.fixture
def full_tunnel(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    asked = []

    def run(cmd, timeout):
        asked.append(cmd[0])
        return ROUTE_NORD if cmd[0] == "route" else None
    monkeypatch.setattr(vpn_detect, "_run", run)
    return asked


def test_a_failover_is_one_net_line_with_what_a_diagnosis_needs(net, log, full_tunnel):
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.works())
    t._dispatch_one(AUDIO)
    [line] = [x for x in log if x.startswith("[net]") and "failed" in x]
    assert line == ("[net] speech groq failed: ConnectTimeout (unreachable) after 6.0s; "
                    "paused 30s (failure 1 in a row); next: openai; vpn: full tunnel via utun5")


def test_a_block_line_names_the_status(net, log, full_tunnel):
    t, g, o = nf.transcriber(net, nf.edge_403(), nf.works())
    t._dispatch_one(AUDIO)
    [line] = [x for x in log if x.startswith("[net]") and "failed" in x]
    assert line == ("[net] speech groq failed: PermissionDeniedError (blocked, HTTP 403) after "
                    "0.2s; paused 60s (failure 1 in a row); next: openai; vpn: full tunnel via utun5")


def test_the_second_failure_in_a_row_says_so(net, log, full_tunnel):
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.works())
    failures_in_a_row(net, t, g, 2)
    lines = [x for x in log if x.startswith("[net]") and "failed" in x]
    assert "paused 30s (failure 1 in a row)" in lines[0]
    assert "paused 60s (failure 2 in a row)" in lines[1]


def test_the_last_provider_failing_says_there_is_no_next(net, log, full_tunnel):
    t, g, o = nf.transcriber(net, nf.works(), nf.edge_403(), order=("openai", "groq"))
    t._dispatch_one(AUDIO)          # OpenAI first: 403, then Groq answers
    assert any("speech openai failed" in x and "next: groq" in x for x in log)
    t2, g2, o2 = nf.transcriber(net, nf.black_hole(), nf.black_hole())
    with pytest.raises(Exception):
        t2._dispatch_one(AUDIO)
    assert any("speech openai failed" in x and "next: none" in x for x in log)


def test_the_vpn_is_asked_at_most_once_a_minute_however_many_failures(net, log, full_tunnel):
    t, g, o = nf.transcriber(net, nf.reset(), nf.reset())
    for _ in range(4):                       # eight failures, a fraction of a second apart
        with pytest.raises(Exception):
            t._dispatch_one(AUDIO)
        t._groq_skip_until = t._openai_skip_until = 0.0
    assert len([x for x in log if x.startswith("[net]") and "failed" in x]) == 8
    assert full_tunnel.count("route") == 1, f"route was started {full_tunnel.count('route')} times"


def test_a_minute_later_it_is_asked_again(net, log, full_tunnel):
    t, g, o = nf.transcriber(net, nf.reset(), nf.works())
    t._dispatch_one(AUDIO)
    first = full_tunnel.count("route")
    net.pass_time(61)
    t._groq_skip_until = 0.0
    t._dispatch_one(AUDIO)
    assert full_tunnel.count("route") == first + 1


def test_no_key_and_no_text_ever_reach_the_log(net, log, full_tunnel, capsys):
    t, g, o = nf.transcriber(net, nf.edge_403(), nf.black_hole())
    with pytest.raises(Exception):
        t._dispatch_one(AUDIO)
    t.model = "gpt-4o-mini-transcribe"
    everything = "\n".join(log) + capsys.readouterr().out
    assert nf.GROQ_KEY not in everything and nf.OPENAI_KEY not in everything
    assert "gsk_" not in everything and "sk-TEST" not in everything
    assert "Authorization" not in everything and "Bearer" not in everything
    assert "api.groq.com" not in "\n".join(x for x in log if x.startswith("[net]"))


def test_a_failure_in_the_bookkeeping_never_replaces_the_real_error(net, log, monkeypatch):
    monkeypatch.setattr(tw._nh, "vpn_text", lambda: 1 / 0)
    t, g, o = nf.transcriber(net, nf.black_hole(), nf.works("from openai"))
    assert t._dispatch_one(AUDIO) == "from openai"
    assert paused_for(t) > 0, "the pause was set before the line was written"
    assert any("could not log a failure" in x for x in log)
