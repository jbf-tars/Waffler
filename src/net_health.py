"""What Waffler does when a provider cannot be reached from this network.

The owner's report: Waffler "doesn't work" behind NordVPN. A full-tunnel VPN
sends every request out through the VPN company's servers, and Groq and
Cerebras often block those addresses. They answer at their edge, before any
key is looked at, with an HTTP 403, which is quick to deal with. The slow
case is a provider that says nothing: it drops the packets, or the big upload
stalls (a path-MTU problem). The first provider then used to be waited on for
the whole request timeout (60 s or more for speech to text) before the next
one was tried, and a flat 30 s pause then repeated that wait twice a minute.

This module holds the parts that make that fast, and plain to read in the log:

* ``request_timeout``: a SHORT connect allowance and a stall limit on the
  upload, but only when another provider is there to take the request.
* ``escalated`` and ``FailureStreak``: a pause that doubles while a provider
  keeps failing (30 s, 60 s, 120 s, 240 s, then 5 minutes) and starts again at
  the first success.
* ``classify``: the kind of failure (blocked, unreachable, ...) from what the
  SDKs raise, read through the whole chain of causes.
* ``Failover``, ``FAILOVER_KINDS``: the fact that a different provider did the
  work, recorded on the transcriber and the styler and shown once per provider
  per half hour (src/notifier.py).
* ``failover_line``: the one ``[net]`` line app.log gets for each failure.

No network, no keys and no transcript text anywhere in here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

try:
    from vpn_detect import describe as _describe_vpn
except ImportError:  # imported as src.net_health
    from src.vpn_detect import describe as _describe_vpn

# ── Timeouts ─────────────────────────────────────────────────────────────────

# A healthy TLS handshake to Groq, OpenAI or Cerebras takes 66 to 135 ms
# (measured 2026-10-22, with Tailscale on), so 6 s is about 50 times normal and
# still short enough to try the next provider before the person gives up.
CONNECT_TIMEOUT_FAST_S = 6.0
# With no other provider to try, giving up early is a certain failure, so the
# only option is waited on much longer (a slow hotel network still connects).
CONNECT_TIMEOUT_SOLE_S = 20.0
# An upload that has not moved a single byte for this long is stalled (a
# black hole or a path-MTU problem), whereas a healthy uplink sends a 64 KB
# piece in well under a second. Only when another provider can take the clip.
WRITE_STALL_FAST_S = 20.0
# A clip small enough to sit in the kernel's send buffer is "sent" at once even
# when a black hole eats it, so the stall shows as silence AFTER the upload.
# With another provider to take the clip, no answer for this long (plus a
# little per megabyte of audio) is given up on; the speech services answer a
# 30 s clip in 1 to 5 s, so this is five or more times the normal.
READ_STALL_FAST_BASE_S = 25.0
READ_STALL_FAST_PER_MB_S = 4.0

# ── Pauses ───────────────────────────────────────────────────────────────────

# The longest a provider is left alone however often it has failed: a switch
# of VPN server is noticed within this long at the latest, because the next
# dictation after it asks the provider again.
COOLDOWN_CAP_S = 300.0
# A failure this long after the last one starts the count again: the
# situation that caused the earlier ones has probably changed (a new network,
# a new VPN server), so the first failure of the new one is not "the fifth".
STREAK_FORGET_S = 900.0
# The pause starts at this and doubles.
ERROR_COOLDOWN_BASE_S = 30.0
AUTH_COOLDOWN_BASE_S = 60.0


class AttemptTimeout(httpx.Timeout):
    """An ``httpx.Timeout`` that still compares and converts like the number
    of seconds the answer may take (its read allowance).

    A request's timeout was a plain number until the connect allowance became
    shorter than the rest. Code that was written for the number (a log line,
    a test that checks a request waits at most the provider cap) keeps
    working this way, while the SDKs and httpx see a full ``Timeout``.
    """

    def __float__(self):
        return float(self.read)

    @staticmethod
    def _number(other):
        if isinstance(other, bool) or not isinstance(other, (int, float)):
            return None
        return float(other)

    def __eq__(self, other):
        n = self._number(other)
        if n is not None:
            return float(self) == n
        if isinstance(other, httpx.Timeout):
            return ((self.connect, self.read, self.write, self.pool)
                    == (other.connect, other.read, other.write, other.pool))
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    __hash__ = None

    def __lt__(self, other):
        n = self._number(other)
        return NotImplemented if n is None else float(self) < n

    def __le__(self, other):
        n = self._number(other)
        return NotImplemented if n is None else float(self) <= n

    def __gt__(self, other):
        n = self._number(other)
        return NotImplemented if n is None else float(self) > n

    def __ge__(self, other):
        n = self._number(other)
        return NotImplemented if n is None else float(self) >= n


def request_timeout(total_s: float, *, fallback: bool, size_mb: float = 0.0):
    """The timeout for one request that may take ``total_s`` seconds to answer.

    ``fallback``: another provider can take this request if this one fails.
    Then connecting gets ``CONNECT_TIMEOUT_FAST_S``, an upload that makes no
    progress gets ``WRITE_STALL_FAST_S`` and an answer that does not begin gets
    ``READ_STALL_FAST_BASE_S`` (plus ``READ_STALL_FAST_PER_MB_S`` for each of
    the ``size_mb`` megabytes sent), so a provider that drops packets costs
    seconds, not the whole ``total_s``. With no other provider the connect
    allowance is ``CONNECT_TIMEOUT_SOLE_S`` and the rest keeps the whole of
    ``total_s`` (it is the only chance).

    Returns a plain number when nothing is shortened (``total_s`` is already
    below the connect allowance, as for the clean-up's 15 s), so a request
    that never needed more than one number still sends exactly one.
    """
    total = max(0.1, float(total_s))
    if fallback:
        connect = min(CONNECT_TIMEOUT_FAST_S, total)
        write = min(WRITE_STALL_FAST_S, total)
        read = min(total, READ_STALL_FAST_BASE_S + READ_STALL_FAST_PER_MB_S * max(0.0, size_mb))
    else:
        connect = min(CONNECT_TIMEOUT_SOLE_S, total)
        write = read = total
    if connect >= total and write >= total and read >= total:
        return total
    return AttemptTimeout(total, connect=connect, read=read, write=write)


# ── Pauses that grow ─────────────────────────────────────────────────────────

def escalated(base_s: float, failures: int, cap_s: float = COOLDOWN_CAP_S) -> float:
    """The pause after the ``failures``-th failure in a row: ``base_s``, twice
    that, four times, ... never more than ``cap_s``. (30 s gives 30, 60, 120,
    240, 300; 60 s gives 60, 120, 240, 300.)"""
    n = max(1, int(failures))
    return min(float(cap_s), float(base_s) * (2 ** min(n - 1, 20)))


class FailureStreak:
    """How many times in a row one provider has failed.

    ``failed(now)`` counts a failure and returns the count; ``succeeded()``
    clears it and returns what it was. A failure more than ``STREAK_FORGET_S``
    after the last one begins a new streak.
    """

    __slots__ = ("count", "last_at")

    def __init__(self):
        self.count = 0
        self.last_at: Optional[float] = None

    def failed(self, now: float) -> int:
        if self.last_at is not None and now - self.last_at > STREAK_FORGET_S:
            self.count = 0
        self.count += 1
        self.last_at = now
        return self.count

    def succeeded(self) -> int:
        was = self.count
        self.count = 0
        self.last_at = None
        return was


# ── What kind of failure ─────────────────────────────────────────────────────

BLOCKED = "blocked"          # HTTP 403 / "access denied": the provider refuses this network
KEY = "key"                  # HTTP 401: the key was not accepted
RATE_LIMIT = "rate_limit"    # HTTP 429
UNREACHABLE = "unreachable"  # could not connect, the connection reset, or an upload stalled
SLOW = "slow"                # the provider took the request and did not answer in time
SERVER = "server"            # HTTP 5xx or 408
OTHER = "other"

# The kinds that mean "this network is why", and so are worth telling the
# person about when another provider had to take over.
NETWORK_KINDS = (BLOCKED, UNREACHABLE)

_CONNECT_LEVEL_TIMEOUTS = {"ConnectTimeout", "WriteTimeout", "PoolTimeout"}
_UNREACHABLE_NAMES = {
    "ConnectError", "NetworkError", "ReadError", "WriteError", "CloseError",
    "RemoteProtocolError", "LocalProtocolError", "ProxyError", "ConnectionError",
    "ConnectionResetError", "ConnectionAbortedError", "ConnectionRefusedError",
    "BrokenPipeError", "gaierror", "SSLError", "SSLEOFError", "SSLZeroReturnError",
    "NameResolutionError", "NewConnectionError",
}
_UNREACHABLE_TEXT = ("connection error", "connection reset", "connection aborted",
                     "connection failed", "connection refused", "network is unreachable",
                     "name or service not known", "nodename nor servname",
                     "getaddrinfo failed", "broken pipe")


@dataclass(frozen=True)
class Failure:
    kind: str                   # one of the constants above
    error: str                  # the class of the root cause, e.g. "ConnectTimeout"
    status: Optional[int] = None


def _chain(exc: BaseException, limit: int = 8) -> list:
    """``exc`` and what caused it, outermost first. The SDKs raise
    ``APITimeoutError`` from the httpx error, and the styler wraps what the SDK
    raised in a ``RuntimeError``, so the real cause is two steps down."""
    out, seen = [], set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen and len(out) < limit:
        out.append(cur)
        seen.add(id(cur))
        cur = cur.__cause__ or (None if cur.__suppress_context__ else cur.__context__)
    return out


def _status_of(exc: BaseException) -> Optional[int]:
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _label(chain: list) -> str:
    """The class that best says what went wrong: httpx's own error when there
    is one (``ConnectTimeout`` rather than the SDK's ``APITimeoutError`` or the
    ``TimeoutError`` three layers down), else the deepest class that is not a
    bare builtin, else the last."""
    for e in chain:
        if type(e).__module__.split(".")[0] in ("httpx", "httpcore"):
            return type(e).__name__
    for e in reversed(chain):
        if type(e).__module__ != "builtins":
            return type(e).__name__
    return type(chain[-1]).__name__


def classify(exc: BaseException) -> Failure:
    """Sort a failure into a ``Failure``. Never raises."""
    try:
        chain = _chain(exc)
        status = next((s for s in (_status_of(e) for e in chain) if s is not None), None)
        names = set()
        for e in chain:
            names.update(c.__name__ for c in type(e).__mro__)
        root = _label(chain)
        text = " ".join(str(e) for e in chain[:2]).lower()

        if status == 429 or "RateLimitError" in names:
            return Failure(RATE_LIMIT, root, status or 429)
        if status == 403 or "PermissionDeniedError" in names:
            return Failure(BLOCKED, root, status or 403)
        if status == 401 or "AuthenticationError" in names:
            return Failure(KEY, root, status or 401)
        if status is not None:
            if status >= 500 or status == 408:
                return Failure(SERVER, root, status)
            return Failure(OTHER, root, status)
        if names & _CONNECT_LEVEL_TIMEOUTS:
            return Failure(UNREACHABLE, root)
        if names & {"ReadTimeout", "APITimeoutError", "TimeoutException", "TimeoutError"}:
            return Failure(SLOW, root)
        if names & _UNREACHABLE_NAMES or "APIConnectionError" in names:
            return Failure(UNREACHABLE, root)
        # Nothing typed: the words a plain error carries (the styler's own
        # "CONNECTION: ..." and "AUTH: ..." reasons, a fake in a test).
        if "access denied" in text or "403" in text:
            return Failure(BLOCKED, root, 403 if "403" in text else None)
        if "401" in text or "unauthorized" in text:
            return Failure(KEY, root, 401 if "401" in text else None)
        if any(s in text for s in _UNREACHABLE_TEXT):
            return Failure(UNREACHABLE, root)
        if "timed out" in text or "timeout" in text:
            return Failure(SLOW, root)
        return Failure(OTHER, root)
    except Exception:
        return Failure(OTHER, "Exception")


# ── The notice ───────────────────────────────────────────────────────────────

# One notifier kind per provider, so "once per provider per 30 minutes" is the
# notifier's own gap (src/notifier.py), kept across restarts like the others.
FAILOVER_KINDS = {
    "groq": "failover_groq",
    "openai": "failover_openai",
    "cerebras": "failover_cerebras",
}

STAGE_SPEECH = "speech"
STAGE_CLEANUP = "cleanup"


def failover_dict(provider: str, used: str, kind: str, stage: str) -> dict:
    """A plain record (JSON-safe) that ``used`` did the work because
    ``provider`` could not be reached or refused this network."""
    return {"provider": provider, "used": used, "kind": kind, "stage": stage}


def notice_candidates(events) -> list:
    """The failovers worth a notice for one dictation, best first: one for
    each provider, speech before clean-up, only where the network was the
    reason and another provider did the work.

    Several can have happened (speech and clean-up, each piece of a long
    take). The caller shows ONE: the first the notifier lets through.
    """
    ranked = sorted(
        (e for e in (events or ()) if isinstance(e, dict)
         and e.get("provider") in FAILOVER_KINDS and e.get("used")
         and e.get("provider") != e.get("used") and e.get("kind") in NETWORK_KINDS),
        key=lambda e: 0 if e.get("stage") == STAGE_SPEECH else 1)
    seen, out = set(), []
    for e in ranked:
        if e["provider"] not in seen:
            seen.add(e["provider"])
            out.append(e)
    return out


# ── The log ──────────────────────────────────────────────────────────────────

def vpn_text() -> str:
    """The VPN description for a ``[net]`` line (src/vpn_detect.py reads the
    routing table at most once a minute, whatever the number of failures)."""
    try:
        return _describe_vpn().summary()
    except Exception:
        return "unknown"


def failover_line(stage: str, provider: str, failure: Failure, spent_s: float,
                  cooldown_s: Optional[float], streak: int, next_provider: Optional[str],
                  vpn: str, note: str = "") -> str:
    """The ``[net]`` line for one failed attempt:

    ``[net] speech groq failed: ConnectTimeout (unreachable) after 6.1s;
    paused 30s (failure 1 in a row); next: openai; vpn: full tunnel via utun4``

    Class names and numbers only: no key, no URL, no text from the request or
    from the provider's reply.
    """
    what = f"{failure.error} ({failure.kind}" + (f", HTTP {failure.status}" if failure.status else "") + ")"
    if cooldown_s:
        # No count for a pause the provider asked for itself (a rate limit's
        # wait, a block's hour): it is not one of a run of connection failures.
        paused = f"paused {cooldown_s:.0f}s" + (f" (failure {streak} in a row)" if streak else "")
    else:
        paused = note or "not paused"
    return (f"[net] {stage} {provider} failed: {what} after {spent_s:.1f}s; {paused}; "
            f"next: {next_provider or 'none'}; vpn: {vpn}")
