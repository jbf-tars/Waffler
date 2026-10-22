"""Does traffic to the public internet go through a tunnel?

The first detector called any `utun` interface with an IPv4 address a VPN. The
owner has Tailscale on all the time, and it has one: `[vpn] on (providers may
be slower or block requests)` was in the log for a computer whose requests to
Groq, OpenAI and Cerebras leave through Wi-Fi like anyone's (measured TLS
handshakes of 66 to 135 ms). The question that matters is where a request to
a public address goes, so it is put to the system (`route -n get 1.1.1.1` on a
Mac, `Find-NetRoute` on Windows) and the tunnels that merely exist are told
apart.

Every case below is the text a command printed (the Tailscale one is the
owner's own routing table), so nothing here starts a process; the tests that
do reach `subprocess` replace it.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import vpn_detect as vd  # noqa: E402

# conftest replaces `vd._run` for every test (no test starts a process); the
# ones that exercise it, with `subprocess.run` replaced instead, want this.
_REAL_RUN = vd._run

# ── what `route -n get 1.1.1.1` prints ───────────────────────────────────────

ROUTE_WIFI = """\
   route to: 1.1.1.1
destination: default
       mask: default
    gateway: 192.168.1.1
  interface: en0
      flags: <UP,GATEWAY,DONE,STATIC,PRCLONING,GLOBAL>
 recvpipe  sendpipe  ssthresh  rtt,msec    rttvar  hopcount      mtu     expire
       0         0         0         0         0         0      1500         0
"""

# The owner's Mac with Tailscale on and no exit node: the real default is
# en0; Tailscale adds only a SCOPED default (`I` flag) on its utun.
ROUTE_TAILSCALE_ONLY = """\
   route to: 1.1.1.1
destination: default
       mask: default
    gateway: 10.128.128.128
  interface: en0
      flags: <UP,GATEWAY,DONE,STATIC,PRCLONING,GLOBAL>
 recvpipe  sendpipe  ssthresh  rtt,msec    rttvar  hopcount      mtu     expire
       0         0         0         0         0         0      1500         0
"""
NETSTAT_TAILSCALE_ONLY = """\
Routing tables

Internet:
Destination        Gateway            Flags               Netif Expire
default            10.128.128.128     UGScg                 en0
default            link#22            UCSIg               utun4
10.128.128/17      link#12            UCS                   en0      !
100.64/10          link#22            UCS                 utun4
127                127.0.0.1          UCS                   lo0
"""

# NordLynx / WireGuard: the default route is split in two halves, both via the
# tunnel, so `route get` for 1.1.1.1 finds the 0/1 half.
ROUTE_NORDLYNX = """\
   route to: 1.1.1.1
destination: 0.0.0.0
       mask: 128.0.0.0
    gateway: 10.5.0.2
  interface: utun5
      flags: <UP,GATEWAY,DONE,STATIC>
 recvpipe  sendpipe  ssthresh  rtt,msec    rttvar  hopcount      mtu     expire
       0         0         0         0         0         0      1420         0
"""
NETSTAT_NORDLYNX = """\
Internet:
Destination        Gateway            Flags               Netif Expire
0/1                10.5.0.2           UGSc                utun5
default            10.128.128.128     UGScg                 en0
128.0/1            10.5.0.2           UGSc                utun5
"""

# OpenVPN style: the default route itself is on the tunnel, and the real one
# stays behind as a scoped default on en0.
ROUTE_OPENVPN = """\
   route to: 1.1.1.1
destination: default
       mask: default
    gateway: 10.8.0.1
  interface: utun3
      flags: <UP,GATEWAY,DONE,STATIC,PRCLONING,GLOBAL>
 recvpipe  sendpipe  ssthresh  rtt,msec    rttvar  hopcount      mtu     expire
       0         0         0         0         0         0      1500         0
"""
NETSTAT_OPENVPN = """\
Internet:
Destination        Gateway            Flags               Netif Expire
default            10.8.0.1           UGSg                utun3
default            10.128.128.128     UGScIg                en0
"""

ROUTE_NO_ROUTE = "route: writing to routing socket: not in table\n"

# ── what `ifconfig` prints ───────────────────────────────────────────────────

IFCONFIG_SYSTEM_ONLY = """\
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.1.20 netmask 0xffffff00 broadcast 192.168.1.255
awdl0: flags=8943<UP,BROADCAST,RUNNING,PROMISC,SIMPLEX,MULTICAST> mtu 1500
\tinet6 fe80::1c2b:3dff:fe4a:5b6c%awdl0 prefixlen 64 scopeid 0xb
utun0: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380
\tinet6 fe80::7a1c:3e4f:9a2b:1c0d%utun0 prefixlen 64 scopeid 0xe
utun1: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 2000
\tinet6 fe80::a1b2:c3d4:e5f6:0718%utun1 prefixlen 64 scopeid 0xf
"""
IFCONFIG_TAILSCALE = IFCONFIG_SYSTEM_ONLY + """\
utun4: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1280
\tinet 100.101.102.103 --> 100.101.102.103 netmask 0xffffffff
\tinet6 fe80::ce81:b1c:bd2c:69e%utun4 prefixlen 64 scopeid 0x16
"""
IFCONFIG_NORDLYNX = IFCONFIG_SYSTEM_ONLY + """\
utun5: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1420
\tinet 10.5.0.2 --> 10.5.0.2 netmask 0xffff0000
"""
IFCONFIG_TUNNEL_DOWN = IFCONFIG_SYSTEM_ONLY + """\
utun6: flags=8010<POINTOPOINT,MULTICAST> mtu 1380
\tinet 10.9.0.2 --> 10.9.0.2 netmask 0xffffffff
"""


class FakeSystem:
    """Stands in for `vpn_detect._run`: the text of each command, by program
    name (None: the command failed), and a record of what was asked."""

    def __init__(self, **outputs):
        self.outputs = outputs
        self.asked = []

    def __call__(self, cmd, timeout):
        self.asked.append((list(cmd), timeout))
        return self.outputs.get(cmd[0])


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(vd, "_cached", None)

    def install(**outputs):
        fake = FakeSystem(**outputs)
        monkeypatch.setattr(vd, "_run", fake)
        return fake
    return install


# ── macOS: the four shapes of a routing table ───────────────────────────────

def test_plain_wifi_is_off(mac):
    mac(route=ROUTE_WIFI, ifconfig=IFCONFIG_SYSTEM_ONLY)
    state = vd.describe(0)
    assert (state.full_tunnel, state.tunnel_present, state.interface) == (False, False, None)
    assert state.route_interface == "en0"
    assert state.log_line() == "[vpn] off"
    assert vd.is_vpn_active() is False and vd.vpn_label() == "vpn-off"


def test_the_system_utuns_with_only_ipv6_are_not_tunnels(mac):
    """macOS keeps utun0 to utun3 for iCloud and AirDrop; they have no IPv4."""
    assert vd.parse_ifconfig_tunnels(IFCONFIG_SYSTEM_ONLY) == []


def test_tailscale_alone_is_a_tunnel_that_the_internet_does_not_use(mac):
    """The owner's Mac. The old detector said `[vpn] on` here."""
    mac(route=ROUTE_TAILSCALE_ONLY, ifconfig=IFCONFIG_TAILSCALE)
    state = vd.describe(0)
    assert state.full_tunnel is False
    assert state.tunnel_present is True
    assert state.interface == "utun4"
    assert state.route_interface == "en0"
    assert state.log_line() == ("[vpn] a tunnel (utun4) is present but internet traffic "
                                "does not use it (Tailscale?)")
    assert vd.is_vpn_active() is False, "Tailscale is not a VPN for this question"
    assert vd.vpn_label() == "vpn-off"


def test_the_owners_real_netstat_has_a_scoped_default_on_the_tunnel():
    """`default link#22 UCSIg utun4` has the I flag (scoped to the interface):
    it is not where an unscoped request goes. The real default is en0."""
    assert vd.parse_netstat_default(NETSTAT_TAILSCALE_ONLY) == "en0"
    # And with `route` having printed nothing, the table decides the same.
    state = vd.describe_macos(None, IFCONFIG_TAILSCALE, NETSTAT_TAILSCALE_ONLY)
    assert state.full_tunnel is False and state.tunnel_present and state.interface == "utun4"


def test_nordlynx_splits_the_default_route_and_is_a_full_tunnel(mac):
    fake = mac(route=ROUTE_NORDLYNX, ifconfig=IFCONFIG_NORDLYNX)
    state = vd.describe(0)
    assert state.full_tunnel is True and state.tunnel_present is True
    assert state.interface == "utun5" and state.route_interface == "utun5"
    assert state.log_line() == ("[vpn] full tunnel via utun5: providers may be slower or "
                                "block requests")
    assert vd.is_vpn_active() is True and vd.vpn_label() == "vpn-on"
    assert [c[0][0] for c in fake.asked] == ["route"], "ifconfig is not needed to answer this"


def test_nordlynx_routes_in_the_netstat_would_have_misled_a_naive_reader():
    """Documented so nobody 'simplifies' the detector to read the default row:
    here `default` is on en0 and the internet still goes through utun5."""
    assert vd.parse_netstat_default(NETSTAT_NORDLYNX) == "en0"
    assert vd.describe_macos(ROUTE_NORDLYNX).full_tunnel is True


def test_openvpn_puts_the_default_on_the_tunnel_with_a_scoped_default_on_en0(mac):
    mac(route=ROUTE_OPENVPN, ifconfig="")
    state = vd.describe(0)
    assert state.full_tunnel is True and state.interface == "utun3"
    assert vd.parse_netstat_default(NETSTAT_OPENVPN) == "utun3", "the scoped en0 row is skipped"


@pytest.mark.parametrize("name,tunnel", [
    ("utun4", True), ("ipsec0", True), ("ppp0", True), ("tun0", True), ("tap0", True),
    ("en0", False), ("en5", False), ("lo0", False), ("bridge0", False), ("awdl0", False),
    ("", False), (None, False),
])
def test_which_interface_names_are_tunnels(name, tunnel):
    assert vd.is_tunnel_name(name) is tunnel


def test_a_tunnel_that_is_down_is_not_present():
    assert vd.parse_ifconfig_tunnels(IFCONFIG_TUNNEL_DOWN) == []


def test_ipsec_is_a_full_tunnel():
    text = ROUTE_OPENVPN.replace("utun3", "ipsec0")
    assert vd.describe_macos(text).full_tunnel is True


# ── a command that fails, hangs or is missing ────────────────────────────────

def test_a_failing_route_command_is_unknown_and_never_a_vpn(mac):
    mac(route=None, netstat=None, ifconfig=None)
    state = vd.describe(0)
    assert state.known is False and state.full_tunnel is False and state.interface is None
    assert state.log_line() == "[vpn] unknown (could not read the routing table)"
    assert state.summary() == "unknown"
    assert vd.is_vpn_active() is False and vd.vpn_label() == "vpn-off"


def test_a_route_that_prints_no_interface_is_unknown(mac):
    mac(route=ROUTE_NO_ROUTE, netstat="", ifconfig="")
    assert vd.describe(0).known is False


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired(cmd="route", timeout=2),
    FileNotFoundError("route"),
    PermissionError("no"),
    OSError("pipe"),
])
def test_a_command_that_cannot_run_returns_none_and_never_raises(monkeypatch, failure):
    def boom(*a, **k):
        raise failure
    monkeypatch.setattr(vd, "_run", _REAL_RUN)
    monkeypatch.setattr(vd.subprocess, "run", boom)
    assert vd._run(["route", "-n", "get", "1.1.1.1"], 2.0) is None


def test_a_command_that_exits_with_an_error_is_none(monkeypatch):
    done = subprocess.CompletedProcess(["route"], 1, stdout="partial", stderr="bad")
    monkeypatch.setattr(vd, "_run", _REAL_RUN)
    monkeypatch.setattr(vd.subprocess, "run", lambda *a, **k: done)
    assert vd._run(["route"], 2.0) is None


def test_commands_have_a_short_timeout_and_no_shell(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")
    monkeypatch.setattr(vd, "_run", _REAL_RUN)
    monkeypatch.setattr(vd.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert vd._run(["route", "-n", "get", "1.1.1.1"], vd.QUICK_TIMEOUT_S) == "ok"
    assert seen["timeout"] == 2.0
    assert seen["cmd"] == ["route", "-n", "get", "1.1.1.1"] and not seen.get("shell")
    assert "creationflags" not in seen, "only Windows hides a console window"
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"


def test_describe_never_raises_even_if_everything_is_broken(monkeypatch):
    monkeypatch.setattr(vd, "_cached", None)
    monkeypatch.setattr(vd, "_describe_now", lambda: 1 / 0)
    assert vd.describe(0).known is False
    assert vd.is_vpn_active() is False


def test_an_unsupported_platform_is_unknown(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(vd, "_cached", None)
    assert vd.describe(0).known is False


# ── asked at most once a minute ──────────────────────────────────────────────

def test_the_answer_is_kept_for_a_minute(mac, monkeypatch):
    fake = mac(route=ROUTE_WIFI, ifconfig=IFCONFIG_SYSTEM_ONLY)
    now = [5000.0]
    monkeypatch.setattr(vd.time, "monotonic", lambda: now[0])
    for _ in range(10):
        vd.describe()
        vd.is_vpn_active()
    asked = len(fake.asked)
    assert asked == 2, "route and ifconfig, once, for ten questions"
    now[0] += 59
    vd.describe()
    assert len(fake.asked) == asked
    now[0] += 2
    vd.describe()
    assert len(fake.asked) == asked * 2, "a minute on, it is asked again"
    vd.describe(0)
    assert len(fake.asked) == asked * 3, "max_age_s=0 always asks"


def test_a_vpn_connected_after_start_up_is_seen_within_a_minute(mac, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(vd.time, "monotonic", lambda: now[0])
    mac(route=ROUTE_WIFI, ifconfig=IFCONFIG_SYSTEM_ONLY)
    assert vd.describe().full_tunnel is False
    mac(route=ROUTE_NORDLYNX, ifconfig=IFCONFIG_NORDLYNX)
    assert vd.describe().full_tunnel is False, "still the answer from a few seconds ago"
    now[0] += 61
    assert vd.describe().full_tunnel is True


# ── Windows ──────────────────────────────────────────────────────────────────

IPCONFIG_WIFI_ONLY = """\
Windows IP Configuration

   Host Name . . . . . . . . . . . . : DESKTOP-1

Wireless LAN adapter Wi-Fi:

   Connection-specific DNS Suffix  . :
   Description . . . . . . . . . . . : Intel(R) Wi-Fi 6 AX201 160MHz
   IPv4 Address. . . . . . . . . . . : 192.168.1.40(Preferred)
   Default Gateway . . . . . . . . . : 192.168.1.1

Ethernet adapter Bluetooth Network Connection:

   Media State . . . . . . . . . . . : Media disconnected
   Description . . . . . . . . . . . : Bluetooth Device (Personal Area Network)
"""
IPCONFIG_NORDLYNX = IPCONFIG_WIFI_ONLY + """
Unknown adapter NordLynx:

   Description . . . . . . . . . . . : NordLynx Tunnel
   IPv4 Address. . . . . . . . . . . : 10.5.0.2(Preferred)
   Default Gateway . . . . . . . . . : 0.0.0.0
"""
IPCONFIG_TAILSCALE = IPCONFIG_WIFI_ONLY + """
Unknown adapter Tailscale:

   Description . . . . . . . . . . . : Tailscale Tunnel
   IPv4 Address. . . . . . . . . . . : 100.101.102.103(Preferred)
"""
IPCONFIG_DISCONNECTED_VPN = IPCONFIG_WIFI_ONLY + """
Ethernet adapter Ethernet 3:

   Media State . . . . . . . . . . . : Media disconnected
   Description . . . . . . . . . . . : TAP-Windows Adapter V9
"""


@pytest.mark.parametrize("route_text,expected_full,expected_interface", [
    ("Wi-Fi|Intel(R) Wi-Fi 6 AX201 160MHz\n", False, None),
    ("Ethernet|Realtek PCIe GbE Family Controller\n", False, None),
    ("NordLynx|NordLynx Tunnel\n", True, "NordLynx"),
    ("Ethernet 3|TAP-Windows Adapter V9\n", True, "Ethernet 3"),
    ("wg0|WireGuard Tunnel\n", True, "wg0"),
    ("mullvad-se1|Mullvad Tunnel\n", True, "mullvad-se1"),
    # An exit node makes Tailscale the way to the internet.
    ("Tailscale|Tailscale Tunnel\n", True, "Tailscale"),
])
def test_windows_route_lookup(route_text, expected_full, expected_interface):
    state = vd.describe_windows(route_text, IPCONFIG_WIFI_ONLY)
    assert state.full_tunnel is expected_full
    assert state.interface == expected_interface
    assert state.known is True and state.method == "route"


def test_windows_tailscale_present_but_not_routing_is_not_a_full_tunnel():
    state = vd.describe_windows("Wi-Fi|Intel(R) Wi-Fi 6 AX201 160MHz\n", IPCONFIG_TAILSCALE)
    assert state.full_tunnel is False
    assert state.tunnel_present is True and state.interface == "Tailscale"
    assert state.log_line() == ("[vpn] a tunnel (Tailscale) is present but internet traffic "
                                "does not use it (Tailscale?)")


def test_windows_a_vpn_adapter_that_is_present_but_not_routing():
    state = vd.describe_windows("Wi-Fi|Intel(R) Wi-Fi 6\n", IPCONFIG_NORDLYNX)
    assert state.full_tunnel is False and state.tunnel_present and state.interface == "NordLynx"


def test_windows_when_powershell_fails_the_signature_scan_decides():
    state = vd.describe_windows(None, IPCONFIG_NORDLYNX)
    assert state.full_tunnel is True and state.interface == "NordLynx"
    assert state.method == "signatures"


def test_windows_the_signature_scan_never_calls_tailscale_a_full_tunnel():
    state = vd.describe_windows(None, IPCONFIG_TAILSCALE)
    assert state.full_tunnel is False and state.tunnel_present is True
    assert vd.describe_windows(None, IPCONFIG_WIFI_ONLY).log_line() == "[vpn] off"


def test_windows_a_disconnected_vpn_adapter_is_not_counted():
    assert vd.parse_ipconfig_tunnels(IPCONFIG_DISCONNECTED_VPN) == []


def test_windows_when_nothing_answers_it_is_unknown():
    assert vd.describe_windows(None, None).known is False
    assert vd.describe_windows("", None).known is False


def test_windows_route_output_parsing():
    assert vd.parse_windows_route("NordLynx|NordLynx Tunnel\r\n") == ("NordLynx", "NordLynx Tunnel")
    assert vd.parse_windows_route("\n\nWi-Fi|\n") == ("Wi-Fi", "")
    assert vd.parse_windows_route("") is None
    assert vd.parse_windows_route("|only a description") is None


def test_windows_asks_powershell_without_a_profile_a_hidden_window_and_a_short_timeout(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(vd, "_cached", None)
    asked = []

    def fake_run(cmd, **kw):
        asked.append((cmd, kw))
        out = "NordLynx|NordLynx Tunnel\n" if cmd[0] == "powershell" else IPCONFIG_NORDLYNX
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    monkeypatch.setattr(vd, "_run", _REAL_RUN)
    monkeypatch.setattr(vd.subprocess, "run", fake_run)

    state = vd.describe(0)
    assert state.full_tunnel is True and state.interface == "NordLynx"
    ps_cmd, ps_kw = next((c, k) for c, k in asked if c[0] == "powershell")
    assert "-NoProfile" in ps_cmd and "-NonInteractive" in ps_cmd and "-Command" in ps_cmd
    script = ps_cmd[ps_cmd.index("-Command") + 1]
    assert "Find-NetRoute" in script and "-RemoteIPAddress 1.1.1.1" in script
    assert ps_kw["timeout"] == 3.0
    assert ps_kw["creationflags"] == 0x08000000, "CREATE_NO_WINDOW: no console flashes up"
    assert all(k["creationflags"] == 0x08000000 for _, k in asked)
    assert all(k["timeout"] <= 3.0 for _, k in asked)


def test_windows_powershell_failing_falls_back_to_the_signature_scan(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(vd, "_cached", None)
    monkeypatch.setattr(vd, "_run", FakeSystem(powershell=None, ipconfig=IPCONFIG_NORDLYNX))
    state = vd.describe(0)
    assert state.full_tunnel is True and state.method == "signatures"


# ── the public API and the startup line ──────────────────────────────────────

def test_is_vpn_active_means_internet_traffic_goes_through_a_vpn(mac):
    for route, ifconfig, expected in ((ROUTE_WIFI, IFCONFIG_SYSTEM_ONLY, False),
                                      (ROUTE_TAILSCALE_ONLY, IFCONFIG_TAILSCALE, False),
                                      (ROUTE_NORDLYNX, IFCONFIG_NORDLYNX, True),
                                      (ROUTE_OPENVPN, "", True)):
        mac(route=route, ifconfig=ifconfig)
        assert vd.describe(0).full_tunnel is expected
        assert vd.is_vpn_active() is expected
        assert vd.vpn_label() == ("vpn-on" if expected else "vpn-off")


def test_the_summary_for_the_middle_of_a_net_line():
    assert vd.describe_macos(ROUTE_WIFI, IFCONFIG_SYSTEM_ONLY).summary() == "off"
    assert vd.describe_macos(ROUTE_NORDLYNX).summary() == "full tunnel via utun5"
    assert vd.describe_macos(ROUTE_TAILSCALE_ONLY, IFCONFIG_TAILSCALE).summary() == \
        "tunnel utun4 present, not used for internet"
    assert vd.UNKNOWN.summary() == "unknown"


def test_the_three_startup_lines_are_exactly_these():
    assert vd.describe_macos(ROUTE_WIFI, IFCONFIG_SYSTEM_ONLY).log_line() == "[vpn] off"
    assert vd.describe_macos(ROUTE_TAILSCALE_ONLY, IFCONFIG_TAILSCALE).log_line() == (
        "[vpn] a tunnel (utun4) is present but internet traffic does not use it (Tailscale?)")
    assert vd.describe_macos(ROUTE_NORDLYNX).log_line() == (
        "[vpn] full tunnel via utun5: providers may be slower or block requests")


def test_app_py_logs_the_described_line_not_the_old_guess():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "_describe_vpn().log_line()" in src
    assert "VPN tunnel detected" not in src, "the line that called Tailscale a VPN"


def test_the_module_imports_nothing_that_is_missing_on_windows():
    """No pwd, fcntl or other POSIX-only import: the same file runs on both."""
    import ast
    tree = ast.parse((ROOT / "src" / "vpn_detect.py").read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"pwd", "fcntl", "termios", "grp", "resource"}, imported
