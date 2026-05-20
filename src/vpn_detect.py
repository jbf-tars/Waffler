"""Best-effort VPN detection for diagnostic logging.

User report: "Waffler doesn't work or is very slow with a VPN. We need
to sort that out. Is there any way around that? Having this issue with
NordVPN." The underlying causes vary — VPN exit IPs blocked by API
providers (Groq / Cerebras), added latency on every request, MTU
fragmentation on large Whisper uploads — and they aren't always fixable
from inside Waffler.

What we can do without overreach: detect whether a VPN tunnel is active
at startup and write a single `[vpn]` line into ``app.log``. Future
"why is Waffler slow?" investigations then have a one-grep answer to
the first question ("is the user on VPN right now?"), and the user
themselves can correlate "Waffler is slow today" with "oh, I forgot
NordVPN is on".

Detection is heuristic — we look for VPN-adapter signatures in the
output of platform tools:

* macOS: ``ifconfig`` — every common VPN (NordVPN, ExpressVPN, Surfshark,
  Mullvad, WireGuard, OpenVPN) creates ``utun*`` interfaces. We check
  for any ``utun*`` that is UP and has an IPv4 address. Pure WiFi /
  Ethernet users have only ``en0``/``en1``/``lo0``.
* Windows: ``ipconfig /all`` and ``Get-NetAdapter`` — VPNs install named
  adapters that contain ``NordLynx`` / ``WireGuard`` / ``TAP`` / ``TUN`` /
  ``OpenVPN`` / ``ExpressVPN`` / ``NordVPN``. We search for those
  substrings in the device descriptions.

Both checks have a 2 s subprocess timeout. We never block startup on
this — any error returns False ("don't know, assume no VPN").

No behaviour changes — Waffler runs identically whether VPN is detected
or not. The function exists only to enrich the log.
"""

from __future__ import annotations

import subprocess
import sys


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


def is_vpn_active() -> bool:
    """Return True iff we detect a VPN-style network interface that's
    UP. Best-effort; any error returns False so we never block startup.
    """
    try:
        if sys.platform == "darwin":
            return _is_vpn_active_macos()
        if sys.platform.startswith("win"):
            return _is_vpn_active_windows()
    except Exception:
        pass
    return False


def _is_vpn_active_macos() -> bool:
    r = subprocess.run(
        ["ifconfig"],
        capture_output=True,
        text=True,
        timeout=2,
    )
    text = r.stdout
    # ifconfig groups by interface. Each interface block starts at
    # column 0 with the interface name, followed by indented lines.
    current_iface = None
    iface_lines: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line and not line.startswith((" ", "\t")):
            if current_iface is not None:
                blocks.append((current_iface, iface_lines))
            current_iface = line.split(":")[0]
            iface_lines = [line]
        else:
            iface_lines.append(line)
    if current_iface is not None:
        blocks.append((current_iface, iface_lines))

    for iface, lines in blocks:
        if not iface.startswith("utun"):
            continue
        block = "\n".join(lines)
        # Tunnel UP + an IPv4 address attached → live tunnel.
        if "<UP," in block and "inet " in block:
            return True
    return False


def _is_vpn_active_windows() -> bool:
    # Use ipconfig /all because it includes the description line where
    # VPN-product names typically appear. The 2 s timeout is plenty;
    # ipconfig is sub-second on every machine I've seen.
    r = subprocess.run(
        ["ipconfig", "/all"],
        capture_output=True,
        text=True,
        timeout=2,
    )
    text = r.stdout.lower()
    if not any(sig in text for sig in _WIN_VPN_SIGNATURES):
        return False
    # We saw a signature — but it might be a non-active adapter. The
    # ipconfig output has "Media State . . . . . . . . . . . : Media
    # disconnected" for inactive adapters. As a cheap heuristic, only
    # claim VPN-active if there's at least one signature on the same
    # adapter block as an IPv4 address line.
    blocks = text.split("\n\n")
    for block in blocks:
        if not any(sig in block for sig in _WIN_VPN_SIGNATURES):
            continue
        if "ipv4 address" in block or "ipv4 . . " in block:
            return True
    return False


def vpn_label() -> str:
    """Return ``"vpn-on"`` / ``"vpn-off"`` for inclusion in log lines."""
    return "vpn-on" if is_vpn_active() else "vpn-off"
