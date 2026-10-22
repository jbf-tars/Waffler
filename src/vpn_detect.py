"""Does traffic to the public internet go through a tunnel?

User report: "Waffler doesn't work or is very slow with a VPN. We need to sort
that out. Having this issue with NordVPN." What matters for that is not whether
a VPN program is running or a tunnel interface exists, but whether requests to
Groq, OpenAI and Cerebras leave through the VPN company's servers. Those are
the requests that get blocked (HTTP 403 before any key is read), dropped or
stalled.

The first version of this module called any ``utun`` interface with an IPv4
address a VPN. Tailscale has one, always, and it is not a VPN in this sense:
it only carries traffic for the user's own devices, and the default route is
still the Wi-Fi or Ethernet one. It was reported as ``[vpn] on (providers may
be slower or block requests)``, which was wrong and sent the reader the wrong
way. The question is now put to the system:

* macOS: ``route -n get 1.1.1.1`` names the interface a public address would
  use. A full tunnel is when that is a ``utun``/``ipsec``/``ppp``/``tun``/
  ``tap`` interface. (NordLynx and WireGuard split the default route in two,
  ``0/1`` and ``128.0/1``, both via the tunnel. OpenVPN-style clients put the
  default on the tunnel and keep a scoped default on ``en0``. Tailscale without
  an exit node leaves the real default on ``en0`` and adds only a scoped
  default on its ``utun``, which ``route get`` does not pick.)
* Windows: ``Find-NetRoute -RemoteIPAddress 1.1.1.1`` names the adapter; it is
  a full tunnel when its name or description matches a VPN client's. Tailscale
  is a full tunnel only when an exit node makes it the adapter for the
  internet. If PowerShell fails, the old scan of ``ipconfig /all`` for VPN
  adapters is used (Tailscale excluded: that scan cannot tell).

``describe()`` gives the whole answer as a ``VpnState``; ``is_vpn_active()`` and
``vpn_label()`` keep their old meaning (True: internet traffic goes through a
VPN). The pure ``describe_macos`` and ``describe_windows`` take the text a
command printed, so they can be tested without one.

Diagnostic only: nothing here changes how Waffler behaves. Every command has a
short timeout and any error gives "unknown", never an exception. The answer is
kept for a minute so that a run of failures does not start a process each.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

# Any public address does; this one answers everywhere and is not blocked.
PROBE_ADDRESS = "1.1.1.1"
# route, ifconfig and ipconfig answer in milliseconds; 2 s is for a wedged system.
QUICK_TIMEOUT_S = 2.0
# PowerShell is slow to start (a second on a cold machine).
WINDOWS_TIMEOUT_S = 3.0
# A run of failures must not start a process each.
CACHE_S = 60.0

_CREATE_NO_WINDOW = 0x08000000

# macOS interface-name prefixes of tunnels.
_TUNNEL_PREFIXES = ("utun", "ipsec", "ppp", "tun", "tap")

# Per-platform substring signatures for VPN adapters. Add more here as
# new VPN clients surface — false positives cost nothing because the
# detector is diagnostic-only.
_WIN_VPN_SIGNATURES = (
    "nordlynx",
    "nordvpn",
    "wireguard",
    "tap-windows",
    "tap adapter",
    "tun adapter",
    "openvpn",
    "expressvpn",
    "surfshark",
    "mullvad",
    "protonvpn",
    "ivacy",
    "private internet access",
    "pia adapter",
)
# A name that is a full tunnel only when the route to the internet goes
# through it (an exit node). Present alone it is the user's own network.
_WIN_ROUTED_ONLY_SIGNATURES = ("tailscale",)


@dataclass(frozen=True)
class VpnState:
    """What is known about tunnels on this computer.

    ``full_tunnel``: requests to the public internet go through a tunnel.
    ``tunnel_present``: a tunnel interface exists (whether or not the internet
    uses it). ``interface``: the tunnel involved, when there is one (the one
    the internet uses, else the one that is present but unused).
    ``route_interface``: what the internet really uses. ``known`` is False when
    the system could not be asked, and then nothing else is claimed.
    """

    full_tunnel: bool = False
    interface: Optional[str] = None
    tunnel_present: bool = False
    route_interface: Optional[str] = None
    known: bool = True
    method: str = ""

    def log_line(self) -> str:
        """The startup line for app.log."""
        if not self.known:
            return "[vpn] unknown (could not read the routing table)"
        if self.full_tunnel:
            return (f"[vpn] full tunnel via {self.interface or 'a tunnel'}: "
                    f"providers may be slower or block requests")
        if self.tunnel_present:
            return (f"[vpn] a tunnel ({self.interface or '?'}) is present but internet "
                    f"traffic does not use it (Tailscale?)")
        return "[vpn] off"

    def summary(self) -> str:
        """A short phrase for the middle of another log line."""
        if not self.known:
            return "unknown"
        if self.full_tunnel:
            return f"full tunnel via {self.interface or 'a tunnel'}"
        if self.tunnel_present:
            return f"tunnel {self.interface or '?'} present, not used for internet"
        return "off"


UNKNOWN = VpnState(known=False)


# ── macOS: parsing ───────────────────────────────────────────────────────────

def is_tunnel_name(name: Optional[str]) -> bool:
    """Is this macOS interface name a tunnel's (utun4, ipsec0, ppp0, ...)?"""
    n = (name or "").strip().lower()
    return bool(n) and n.startswith(_TUNNEL_PREFIXES)


def parse_route_interface(text: str) -> Optional[str]:
    """The ``interface:`` line of ``route -n get <address>``, or None."""
    m = re.search(r"^\s*interface:\s*(\S+)", text or "", re.MULTILINE)
    return m.group(1) if m else None


def parse_netstat_default(text: str) -> Optional[str]:
    """The interface of the default route in ``netstat -rn -f inet`` that
    applies to everything: a ``default`` row WITHOUT the ``I`` (interface
    scope) flag. Tailscale's own row is ``default link#22 UCSIg utun4``; the
    real one is ``default 10.128.128.128 UGScg en0``."""
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "default":
            flags, netif = parts[2], parts[3]
            if "I" in flags:
                continue
            return netif
    return None


def parse_ifconfig_tunnels(text: str) -> list:
    """Names of the tunnel interfaces in ``ifconfig`` output that are UP and
    have an IPv4 address. macOS keeps several ``utun`` interfaces of its own
    (iCloud, AirDrop) with only IPv6 link-local addresses; those are not
    counted."""
    found = []
    name, block = None, []

    def close():
        if name and is_tunnel_name(name):
            joined = "\n".join(block)
            if "<UP," in joined and "inet " in joined:
                found.append(name)

    for line in (text or "").splitlines():
        if line and not line.startswith((" ", "\t")):
            close()
            name, block = line.split(":")[0], [line]
        else:
            block.append(line)
    close()
    return found


def describe_macos(route_text: Optional[str], ifconfig_text: Optional[str] = None,
                   netstat_text: Optional[str] = None) -> VpnState:
    """The state from the text of ``route -n get 1.1.1.1`` and, to tell a
    tunnel that is present from none at all, of ``ifconfig``. ``netstat_text``
    (``netstat -rn -f inet``) is only used when ``route`` printed no
    interface."""
    iface = parse_route_interface(route_text or "")
    if iface is None and netstat_text:
        iface = parse_netstat_default(netstat_text)
    if iface is None:
        return UNKNOWN
    if is_tunnel_name(iface):
        return VpnState(full_tunnel=True, interface=iface, tunnel_present=True,
                        route_interface=iface, method="route")
    tunnels = parse_ifconfig_tunnels(ifconfig_text or "")
    return VpnState(full_tunnel=False, interface=tunnels[0] if tunnels else None,
                    tunnel_present=bool(tunnels), route_interface=iface, method="route")


# ── Windows: parsing ─────────────────────────────────────────────────────────

# Printed by the PowerShell command below: "<alias>|<description>".
_WIN_ROUTE_SCRIPT = (
    "try{[Console]::OutputEncoding=[System.Text.Encoding]::UTF8}catch{};"
    "$r=Find-NetRoute -RemoteIPAddress " + PROBE_ADDRESS +
    " | Where-Object { $_.InterfaceAlias } | Select-Object -First 1;"
    "if($r){$a=Get-NetAdapter -InterfaceIndex $r.InterfaceIndex -ErrorAction SilentlyContinue;"
    "Write-Output ($r.InterfaceAlias+'|'+$a.InterfaceDescription)}"
)


def parse_windows_route(text: str) -> Optional[tuple]:
    """``(alias, description)`` from the PowerShell output, or None."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            alias, _, desc = line.partition("|")
            alias = alias.strip()
            return (alias, desc.strip()) if alias else None
    return None


def _win_signature(text: str, *, routed: bool) -> Optional[str]:
    low = text.lower()
    sigs = _WIN_VPN_SIGNATURES + (_WIN_ROUTED_ONLY_SIGNATURES if routed else ())
    return next((s for s in sigs if s in low), None)


def parse_ipconfig_tunnels(text: str) -> list:
    """``(adapter name, full_tunnel_signature)`` for each adapter in
    ``ipconfig /all`` that has a VPN client's signature and an IPv4 address
    and is connected. Tailscale is listed with ``False``: present, but not
    something this scan can call a full tunnel."""
    found = []
    header, block = None, []

    def close():
        if header is None:
            return
        joined = "\n".join(block)
        low = (header + "\n" + joined).lower()
        if "media disconnected" in low or "ipv4" not in low:
            return
        name = header.split("adapter", 1)[-1].strip().rstrip(":").strip() or header.strip()
        if _win_signature(low, routed=False):
            found.append((name, True))
        elif _win_signature(low, routed=True):
            found.append((name, False))

    for line in (text or "").splitlines():
        if line and not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            close()
            header, block = line.strip(), []
        elif header is not None:
            block.append(line)
    close()
    return found


def describe_windows(route_text: Optional[str], ipconfig_text: Optional[str] = None) -> VpnState:
    """The state from the PowerShell route lookup (``route_text``) and, to
    find a tunnel that is present but unused, ``ipconfig /all``. With no
    route answer the old signature scan decides alone."""
    parsed = parse_windows_route(route_text or "")
    tunnels = parse_ipconfig_tunnels(ipconfig_text or "") if ipconfig_text else []
    if parsed is None:
        full = [t for t in tunnels if t[1]]
        if full:
            return VpnState(full_tunnel=True, interface=full[0][0], tunnel_present=True,
                            method="signatures")
        if tunnels:
            return VpnState(interface=tunnels[0][0], tunnel_present=True, method="signatures")
        if ipconfig_text is not None:
            return VpnState(method="signatures")
        return UNKNOWN
    alias, desc = parsed
    if _win_signature(f"{alias} {desc}", routed=True):
        # The internet goes out through this adapter: a VPN client's, or
        # Tailscale with an exit node.
        return VpnState(full_tunnel=True, interface=alias, tunnel_present=True,
                        route_interface=alias, method="route")
    return VpnState(full_tunnel=False, interface=tunnels[0][0] if tunnels else None,
                    tunnel_present=bool(tunnels), route_interface=alias, method="route")


# ── Asking the system ────────────────────────────────────────────────────────

def _run(cmd: list, timeout: float) -> Optional[str]:
    """The command's output, or None when it cannot be run, fails or is slow.
    Never raises."""
    try:
        kwargs = {}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, **kwargs)
        if r.returncode != 0:
            return None
        return r.stdout or ""
    except Exception:
        return None


def _describe_now() -> VpnState:
    if sys.platform == "darwin":
        route = _run(["route", "-n", "get", PROBE_ADDRESS], QUICK_TIMEOUT_S)
        netstat = None
        if parse_route_interface(route or "") is None:
            netstat = _run(["netstat", "-rn", "-f", "inet"], QUICK_TIMEOUT_S)
        iface = parse_route_interface(route or "") or parse_netstat_default(netstat or "")
        ifconfig = None
        if iface is not None and not is_tunnel_name(iface):
            ifconfig = _run(["ifconfig"], QUICK_TIMEOUT_S)
        return describe_macos(route, ifconfig, netstat)
    if sys.platform.startswith("win"):
        route = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                      _WIN_ROUTE_SCRIPT], WINDOWS_TIMEOUT_S)
        ipconfig = _run(["ipconfig", "/all"], QUICK_TIMEOUT_S)
        return describe_windows(route, ipconfig)
    return UNKNOWN


_lock = threading.Lock()
_cached: Optional[tuple] = None        # (taken at, VpnState)


def describe(max_age_s: float = CACHE_S) -> VpnState:
    """The state now, or the one read less than ``max_age_s`` seconds ago.
    ``max_age_s=0`` always asks. Never raises."""
    global _cached
    try:
        with _lock:
            now = time.monotonic()
            if _cached is not None and max_age_s > 0 and now - _cached[0] < max_age_s:
                return _cached[1]
            state = _describe_now()
            _cached = (time.monotonic(), state)
            return state
    except Exception:
        return UNKNOWN


def is_vpn_active() -> bool:
    """True when traffic to the public internet goes through a VPN tunnel.
    A tunnel that is present but not used for the internet (Tailscale) is not
    a VPN here. Best-effort; any error gives False."""
    return describe().full_tunnel


def vpn_label() -> str:
    """Return ``"vpn-on"`` / ``"vpn-off"`` for inclusion in log lines."""
    return "vpn-on" if is_vpn_active() else "vpn-off"
