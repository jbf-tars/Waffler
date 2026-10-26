"""A network that blocks, drops, stalls and resets, without a network.

NordVPN-style full tunnels send every request out through the VPN company's
servers, and a provider can treat that address in five ways. Each is a "mode"
here, and each is a real exception raised by a real transport under the real
SDK clients, so the code under test goes through the same classes, the same
timeout object and the same error wrapping it does in use:

* ``works``          the provider answers.
* ``edge_403``       HTTP 403 at the provider's edge, before any key is read.
* ``black_hole``     the packets are dropped: the connect times out.
* ``stalled_upload`` the connection opens and the upload stops moving.
* ``silent_server``  the upload is taken and nothing comes back.
* ``reset``          the connection is reset.
* ``unavailable``    an HTTP 503 (the provider's own trouble, not the network's).

Time is a number that moves only when something waits: a mode that "times out"
adds the timeout the client was GIVEN (read from the request itself, which is
how the SDK hands httpx its limits) to the clock. So "a black-holed Groq costs
about the connect timeout" is a sum, not a sleep, and the test runs in
milliseconds.

Not a test module (no test_ prefix); imported by the tests.
"""
import time

import httpx

import groq
import openai

import transcribe_whisper as tw

GROQ_KEY = "gsk_TEST_KEY_THAT_MUST_NEVER_BE_LOGGED"
OPENAI_KEY = "sk-TEST-KEY-THAT-MUST-NEVER-BE-LOGGED"
AUDIO = b"\x00" * 4096            # the transcriber posts these bytes; the fakes never read them


class FakeNet:
    """The clock, and the two places that move it."""

    def __init__(self, monkeypatch, start=1000.0):
        self.now = start
        self.sleeps = []
        monkeypatch.setattr(time, "monotonic", lambda: self.now)
        monkeypatch.setattr(tw, "_retry_sleep", self._sleep)

    def _sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def pass_time(self, seconds):
        """Nothing is being asked; time goes by."""
        self.now += seconds


# ── how a provider behaves ───────────────────────────────────────────────────

def works(text="hello there", took=0.4):
    def mode(p, request, t):
        p.net.now += took
        return httpx.Response(200, text=text)
    mode.__name__ = "works"
    return mode


def edge_403(took=0.2):
    def mode(p, request, t):
        p.net.now += took
        return httpx.Response(403, text="Access denied. Please check your network settings.")
    mode.__name__ = "edge_403"
    return mode


def black_hole():
    def mode(p, request, t):
        p.net.now += t["connect"]
        raise httpx.ConnectTimeout("timed out", request=request)
    mode.__name__ = "black_hole"
    return mode


def stalled_upload():
    def mode(p, request, t):
        p.net.now += t["write"]
        raise httpx.WriteTimeout("timed out", request=request)
    mode.__name__ = "stalled_upload"
    return mode


def silent_server():
    def mode(p, request, t):
        p.net.now += t["read"]
        raise httpx.ReadTimeout("timed out", request=request)
    mode.__name__ = "silent_server"
    return mode


def reset(took=0.05):
    def mode(p, request, t):
        p.net.now += took
        raise httpx.ConnectError("[Errno 54] Connection reset by peer", request=request)
    mode.__name__ = "reset"
    return mode


def unavailable(status=503, took=0.3):
    def mode(p, request, t):
        p.net.now += took
        return httpx.Response(status, text="service unavailable")
    mode.__name__ = "unavailable"
    return mode


class Provider:
    """One provider's server as the network makes it behave. Change ``mode``
    between dictations to move the VPN to another server."""

    def __init__(self, net, name, mode):
        self.net, self.name, self.mode = net, name, mode
        self.calls = []                 # (when, the timeouts the client was given)

    def handler(self, request):
        timeouts = dict(request.extensions["timeout"])
        self.calls.append((self.net.now, timeouts))
        return self.mode(self, request, timeouts)

    @property
    def last_timeouts(self):
        return self.calls[-1][1]


def groq_client(provider):
    return groq.Groq(api_key=GROQ_KEY, max_retries=0,
                     http_client=httpx.Client(transport=httpx.MockTransport(provider.handler)))


def openai_client(provider):
    return openai.OpenAI(api_key=OPENAI_KEY, max_retries=0,
                         http_client=httpx.Client(transport=httpx.MockTransport(provider.handler)))


def transcriber(net, groq_mode=None, openai_mode=None, order=("groq", "openai")):
    """A WhisperTranscriber with the real request code and real SDK clients,
    over the fake network. ``None`` leaves that provider without a key.
    Returns ``(transcriber, groq_provider, openai_provider)``."""
    t = object.__new__(tw.WhisperTranscriber)
    t._backend = "groq"
    t._cloud_order = list(order)
    t._groq_skip_until = 0.0
    t.model = "gpt-4o-mini-transcribe"
    g = Provider(net, "groq", groq_mode) if groq_mode is not None else None
    o = Provider(net, "openai", openai_mode) if openai_mode is not None else None
    t._groq_client = groq_client(g) if g else None
    t.client = openai_client(o) if o else None
    return t, g, o
