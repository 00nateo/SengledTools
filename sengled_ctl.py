#!/usr/bin/env python3
"""
sengled_ctl.py - one tool for Sengled Wi-Fi bulbs: scan, control, Wi-Fi setup.

    python3 sengled_ctl.py                      # interactive TUI (recommended)
    python3 sengled_ctl.py scan                 # list every bulb you can reach
    python3 sengled_ctl.py on | off | status
    python3 sengled_ctl.py red                  # presets: see `colors`
    python3 sengled_ctl.py color 255 80 0       # or: color "#ff5000"
    python3 sengled_ctl.py brightness 40
    python3 sengled_ctl.py temp 20              # 0 = warm .. 100 = cool
    python3 sengled_ctl.py effect "color cycle" # built-in effects (see `effects`); `effect off` stops
    python3 sengled_ctl.py fade 1 100 600 0 75  # bulb-side fade: brightness 1->100% over 600 s,
                                                #   white 0->75 (optional); add `off` to switch off at the end
    python3 sengled_ctl.py cycle sunset 30      # custom color cycle at your speed (seconds per loop);
                                                #   palettes: rainbow sunset ocean aurora candy fire
    python3 sengled_ctl.py wifi --ssid "MyWifi" --password "secret"
    python3 sengled_ctl.py serve                # keep the MQTT server up for bulbs on Wi-Fi
    python3 sengled_ctl.py api                  # JSON API for the web app (cd web && npm run dev)
    python3 sengled_ctl.py raw '...' | reboot | reset
    python3 sengled_ctl.py --all off            # every bulb at once (also: red, brightness 50, ...)

Target selection: --ip IP, --mac MAC, otherwise the last bulb used, otherwise
whatever answers (if exactly one bulb does).

How bulbs are reached:
  * UDP :9080  - a bulb in setup mode (its 'Sengled Wi-Fi Bulb_XXXXXX' network,
                 always 192.168.8.1), or on a network that allows it.
  * MQTT :8883 - a bulb set up onto your Wi-Fi with this tool connects back to
                 an MQTT server on this Mac. The TUI (or `serve`) runs that server;
                 the bulb only obeys while it runs, and reconnects ~1 min after it
                 starts. One-shot commands use it if it's already running.

MQTT needs the repo's .venv (paho-mqtt, amqtt); the script relaunches itself with
.venv/bin/python when plain python3 lacks them. UDP features need nothing.
"""

import argparse
import base64
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 9080
MQTT_PORT = 8883
CALLBACK_PORT = 57542
AP_IP = "192.168.8.1"
AP_NET = ipaddress.ip_network("192.168.8.0/24")
HOME_DIR = os.path.expanduser("~/.sengled")
CACHE = os.path.join(HOME_DIR, "lan.json")
LOG_FILE = os.path.join(HOME_DIR, "logs", "sengled.log")
LOG_MAX_BYTES = 5_000_000  # then it's moved to sengled.log.1
SEND_WAIT = 6.0  # seconds to wait for a bulb to confirm; weak Wi-Fi can delay it 4-5 s
MAX_SWEEP_HOSTS = 1024  # sweep at most a /22 per interface
# iPhone Personal Hotspot hands IPv4 clients (like bulbs) 172.20.10.2-14.
HOTSPOT_NET = ipaddress.ip_network("172.20.10.0/28")
# macOS on an IPv6-only network (newer iPhone hotspots) shows only this placeholder
# IPv4 address; it cannot reach IPv4-only devices on the same hotspot.
CLAT_NET = ipaddress.ip_network("192.0.0.0/29")

PRESETS = {
    "white": (255, 255, 255), "red": (255, 0, 0), "green": (0, 255, 0),
    "blue": (0, 0, 255), "yellow": (255, 255, 0), "cyan": (0, 255, 255),
    "magenta": (255, 0, 255), "orange": (255, 128, 0), "pink": (255, 105, 180),
    "purple": (128, 0, 128), "teal": (0, 128, 128), "lime": (128, 255, 0),
}

# Built-in light effects (MQTT only). Names and effectStatus numbers come from the
# W31-N15 stock firmware and were confirmed on a real bulb: send {type: <name>, value: 1}
# to start, {type: <name>, value: 0} to stop; any color/white command also stops it.
EFFECTS = {
    "colorCycle": ("Color cycle", 1),     # slowly fades through every color
    "randomColor": ("Random colors", 2),
    "rhythm": ("Rhythm", 3),
    "christmas": ("Christmas", 4),
    "halloween": ("Halloween", 5),
    "festival": ("Festival", 6),
}
EFFECT_BY_STATUS = {n: name for name, (_, n) in EFFECTS.items()}

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

TTY = sys.stdout.isatty()


def c(text, code):
    return f"\033[{code}m{text}\033[0m" if TTY else str(text)


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_sink = threading.local()  # the web API collects messages per request/job thread


_log_lock = threading.Lock()
LOG_ROLE = "cli"  # which front end wrote a log line: cli, tui, api or serve


def log(kind, msg, path=None):
    """Append a line to ~/.sengled/logs/sengled.log (shared by the TUI, CLI and web API)."""
    path = path or LOG_FILE
    text = kind if msg is None else f"{kind:<5} {ANSI.sub('', str(msg)).strip()}"
    line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} "
            f"{LOG_ROLE:<5} {text}\n")
    with _log_lock:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
                os.replace(path, path + ".1")
            with open(path, "a") as f:
                f.write(line)
        except OSError:
            pass


def _emit(kind, msg):
    log(kind, msg)
    lines = getattr(_sink, "lines", None)
    if lines is not None:
        lines.append({"kind": kind, "text": ANSI.sub("", str(msg)).strip()})
    else:
        mark = {"ok": c("✓", "32"), "err": c("✗", "31"), "info": c("·", "36"), "warn": c("!", "33")}
        print(f"  {mark[kind]} {msg}")


def ok_(msg):
    _emit("ok", msg)


def err(msg):
    _emit("err", msg)


def info(msg):
    _emit("info", msg)


def warn(msg):
    _emit("warn", msg)


def swatch(rgb, width=4):
    if not TTY or not rgb:
        return ""
    r, g, b = rgb
    return f"\033[48;2;{r};{g};{b}m{' ' * width}\033[0m"


# ---------------------------------------------------------------------------
# Small utils
# ---------------------------------------------------------------------------

def norm_mac(mac):
    h = re.sub(r"[^0-9A-Fa-f]", "", mac or "").upper()
    if len(h) != 12 or h == "0" * 12:  # AP-mode get_device_mac reports all zeros
        return None
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def parse_rgb(text):
    """'255:0:0' | 'FF0000' -> (r, g, b) or None"""
    text = str(text or "").strip()
    try:
        if ":" in text:
            rgb = tuple(int(x) for x in text.split(":"))
            return rgb if len(rgb) == 3 else None
        if re.fullmatch(r"[0-9A-Fa-f]{6}", text):
            return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        pass
    return None


def parse_color(args):
    """['red'] | ['#ff8800'] | ['255', '136', '0'] -> (r, g, b)"""
    if len(args) == 1:
        a = args[0].strip().lower()
        if a in PRESETS:
            return PRESETS[a]
        h = a.lstrip("#")
        if re.fullmatch(r"[0-9a-f]{6}", h):
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        args = re.split(r"[\s,]+", a)
    if len(args) == 3:
        rgb = tuple(int(x) for x in args)
        if all(0 <= v <= 255 for v in rgb):
            return rgb
    raise ValueError("color must be a preset, #RRGGBB, or R G B (0-255)")


def to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Cache: ~/.sengled/lan.json  {MAC: {...}, "_last": {"mac"}, "_lan_ip": "..."}
# ---------------------------------------------------------------------------

def _read_cache_file():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except Exception:
        return {}


def _write_cache_file(data):
    try:
        os.makedirs(HOME_DIR, exist_ok=True)
        with open(CACHE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def load_cache():
    return {norm_mac(k): v for k, v in _read_cache_file().items()
            if norm_mac(k) and isinstance(v, dict)}


def last_mac():
    return norm_mac(_read_cache_file().get("_last", {}).get("mac"))


def last_lan_ip():
    return _read_cache_file().get("_lan_ip")


def current_lan_ip():
    return next((str(i.ip) for _, i in interfaces()
                 if i.ip not in AP_NET and i.ip not in CLAT_NET), None)


def remember_lan_ip():
    """Store this Mac's address on the current non-bulb network. Wi-Fi setup uses it
    as the callback host, because the bulb calls home on the network it joins."""
    ip = current_lan_ip()
    if ip:
        data = _read_cache_file()
        data["_lan_ip"] = ip
        net = next((str(i.network) for _, i in interfaces() if str(i.ip) == ip), None)
        if net:  # per network, so setup for another network can use the right address
            data.setdefault("_mac_ips", {})[net] = ip
        _write_cache_file(data)
    if IS_LINUX:  # e.g. a Raspberry Pi hosting the bulbs' own network: learn its SSID
        ips = {n: str(i.ip) for n, i in interfaces()}
        for name, (ssid, _) in linux_wifi().items():
            if name in ips:
                remember_network(ssid, ips[name])


def mac_ip_for_ssid(ssid=None):
    """This Mac's last address on Wi-Fi `ssid` (from networks a bulb was set up on), or
    {ssid: ip} for all known networks when no ssid is given."""
    data = _read_cache_file()
    ips = data.get("_mac_ips", {})
    known = {name: ips[net] for net, name in data.get("_networks", {}).items() if net in ips}
    return known if ssid is None else known.get(ssid)


def save_cache(bulb):
    mac = bulb.get("mac")
    if not mac:
        return
    data = _read_cache_file()
    ip = bulb.get("ip") if is_ip(bulb.get("ip")) else data.get(mac, {}).get("ip")
    data[mac] = {**data.get(mac, {}), "ip": ip, "via": bulb.get("via", "udp"),
                 "ap": ip == AP_IP, "model": bulb.get("model"),
                 "ts": int(time.time())}
    _release_ip(data, mac, ip)
    data["_last"] = {"mac": mac}
    _write_cache_file(data)


def is_ip(s):
    try:
        ipaddress.IPv4Address(str(s))
        return True
    except ValueError:
        return False


def _release_ip(data, mac, ip):
    """An IP belongs to one bulb: drop it from any other bulb's cache entry (every bulb in
    setup mode shares the AP address, so that one is exempt)."""
    if not ip or ip == AP_IP:
        return
    for m, v in data.items():
        if m != mac and isinstance(v, dict) and v.get("ip") == ip:
            v["ip"] = None


def remember_network(ssid, ip):
    """Note which Wi-Fi network the subnet containing `ip` belongs to (macOS hides the
    current SSID from scripts, and bulbs don't report theirs)."""
    if not ssid or not is_ip(ip):
        return
    net = next((str(i.network) for _, i in interfaces()
                if ipaddress.IPv4Address(ip) in i.network and i.network.prefixlen < 31), None)
    if net:
        data = _read_cache_file()
        data.setdefault("_networks", {})[net] = ssid
        _write_cache_file(data)


def ssid_for_ip(ip):
    if not is_ip(ip):
        return None
    for net, ssid in _read_cache_file().get("_networks", {}).items():
        try:
            if ipaddress.IPv4Address(ip) in ipaddress.IPv4Network(net):
                return ssid
        except ValueError:
            continue
    return None


def bulb_ssid(b):
    """The Wi-Fi network a bulb is on: from its subnet, else what we set it up with."""
    if b.get("ap_mode"):
        return None
    return ssid_for_ip(b.get("ip")) or load_cache().get(b.get("mac"), {}).get("ssid")


def remember_bulb_ssid(b, ssid):
    remember_network(ssid, b.get("ip"))
    if b.get("mac"):
        data = _read_cache_file()
        data.setdefault(b["mac"], {})["ssid"] = ssid
        _write_cache_file(data)
    b["ssid"] = ssid


def signal_label(rssi):
    if rssi is None:
        return None
    return ("strong" if rssi >= -60 else "good" if rssi >= -67 else
            "fair" if rssi >= -75 else "weak")


def claim_ip(mac, ip):
    """Record that `mac` is the bulb at `ip` (without changing the last-used bulb)."""
    data = _read_cache_file()
    data.setdefault(mac, {"via": "mqtt"})["ip"] = ip
    _release_ip(data, mac, ip)
    _write_cache_file(data)


def cached_ips():
    """{mac: ip} from the cache, ignoring IPs that several bulbs claim (ambiguous)."""
    ips = {m: v.get("ip") for m, v in load_cache().items() if is_ip(v.get("ip")) and v.get("ip") != AP_IP}
    dupes = {ip for ip in ips.values() if list(ips.values()).count(ip) > 1}
    return {m: ip for m, ip in ips.items() if ip not in dupes}


# ---------------------------------------------------------------------------
# Network info
# ---------------------------------------------------------------------------

IS_LINUX = sys.platform.startswith("linux")
# Loopback, VPNs and containers: never where bulbs are.
SKIP_IFACES = ("lo", "utun", "tailscale", "wg", "zt", "docker", "veth", "br-", "virbr")


def parse_ip_addr(text):
    """[(ifname, IPv4Interface)] from Linux `ip -o -4 addr show`."""
    out = []
    for m in re.finditer(r"^\d+:\s+([^\s@:]+)\S*\s+inet\s+(\d+\.\d+\.\d+\.\d+/\d+)", text, re.M):
        out.append((m.group(1), ipaddress.ip_interface(m.group(2))))
    return out


def parse_ifconfig(text):
    """[(ifname, IPv4Interface)] from ifconfig (macOS: hex netmask; Linux: dotted)."""
    out, name = [], None
    for line in text.splitlines():
        m = re.match(r"^([\w.]+):", line)
        if m:
            name = m.group(1)
            continue
        m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+(0x[0-9a-f]+|[\d.]+)", line)
        if m and name:
            mask = m.group(2)
            if mask.startswith("0x"):
                mask = str(ipaddress.IPv4Address(int(mask, 16)))
            out.append((name, ipaddress.ip_interface(f"{m.group(1)}/{mask}")))
    return out


def interfaces():
    """[(ifname, IPv4Interface)] for every up, non-loopback, non-VPN IPv4 address."""
    cmds = [(["ifconfig"], parse_ifconfig)]
    if IS_LINUX:  # `ip` is always there; ifconfig often isn't
        cmds.insert(0, (["ip", "-o", "-4", "addr", "show"], parse_ip_addr))
    for cmd, parse in cmds:
        try:
            text = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
        except Exception:
            continue
        return [(n, i) for n, i in parse(text)
                if not n.startswith(SKIP_IFACES) and not i.ip.is_link_local and not i.ip.is_loopback]
    return []


def parse_iw_dev(text):
    """{ifname: (ssid, type)} from `iw dev`; type is 'managed' (joined) or 'AP' (hosting)."""
    out = {}
    for block in re.split(r"^\s*Interface\s+", text, flags=re.M)[1:]:
        name = block.split()[0]
        ssid = re.search(r"^\s*ssid\s+(.+)$", block, re.M)
        kind = re.search(r"^\s*type\s+(\S+)", block, re.M)
        if ssid:
            out[name] = (ssid.group(1).strip(), kind.group(1) if kind else "?")
    return out


def linux_wifi():
    """Linux: {ifname: (ssid, type)} for each Wi-Fi interface that's joined or hosting a network."""
    try:
        return parse_iw_dev(subprocess.run(["iw", "dev"], capture_output=True,
                                           text=True, timeout=3).stdout)
    except Exception:
        return {}


def wifi_device():
    try:
        text = subprocess.run(["networksetup", "-listallhardwareports"],
                              capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"Hardware Port: Wi-Fi\nDevice: (\w+)", text)
        return m.group(1) if m else "en0"
    except Exception:
        return "en0"


def wifi_ssid():
    if IS_LINUX:  # a joined network first; else the one this machine hosts for the bulbs
        nets = sorted(linux_wifi().values(), key=lambda v: v[1] != "managed")
        return nets[0][0] if nets else ssid_for_ip(current_lan_ip())
    try:
        text = subprocess.run(["networksetup", "-getairportnetwork", wifi_device()],
                              capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"Current Wi-Fi Network: (.+)", text)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return ssid_for_ip(current_lan_ip())


def ipv6_only():
    """True when this Mac has no real IPv4 address on the LAN (iPhone hotspot, IPv6-only mode)."""
    return any(i.ip in CLAT_NET for _, i in interfaces())


def ipv6_only_help():
    warn("this Mac is on an IPv6-only connection (192.0.0.2) - typical of iPhone hotspots.")
    info("The bulb only speaks IPv4, so the Mac can't see it. Fix (one time):")
    info("  System Settings › Wi-Fi › Details… (on the hotspot) › TCP/IP ›")
    info("  Configure IPv6: Link-Local Only › OK, then rejoin the hotspot.")
    info("  or in a terminal:  networksetup -setv6linklocal Wi-Fi")
    info("The Mac should then get a 172.20.10.x address. Undo later with:")
    info("  networksetup -setv6automatic Wi-Fi")


def on_bulb_ap():
    return any(i.ip in AP_NET for _, i in interfaces())


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def join_wifi(ssid, password):
    """Switch this Mac to `ssid` (macOS). Returns True once it has a usable IPv4 address."""
    dev = wifi_device()
    info(f"switching this Mac ({dev}) to '{ssid}' ...")
    cmd = ["networksetup", "-setairportnetwork", dev, ssid] + ([password] if password else [])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
        msg = (out.stdout + out.stderr).strip()
        if msg:
            warn(msg)
    except Exception as e:
        warn(f"could not switch Wi-Fi automatically: {e}")
        return False
    for _ in range(20):
        if current_lan_ip():
            return True
        time.sleep(1)
    return False


# ---------------------------------------------------------------------------
# UDP protocol (port 9080)
# ---------------------------------------------------------------------------

def udp(ip, func, param=None, timeout=1.5, attempts=2):
    """Send one command; returns the bulb's `result` dict or None."""
    msg = json.dumps({"func": func, "param": param or {}}).encode()
    for _ in range(attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                s.sendto(msg, (ip, PORT))
                data, _ = s.recvfrom(4096)
                return json.loads(data.decode(errors="ignore")).get("result")
        except (OSError, ValueError):
            continue
    return None


def succeeded(result):
    return isinstance(result, dict) and result.get("ret") == 0


def bulb_from_status(ip, res):
    """Normalize a UDP search_devices result into a bulb dict."""
    return {
        "via": "udp",
        "ip": ip,
        "mac": norm_mac(res.get("mac")),
        "model": res.get("MN"),
        "version": res.get("version"),
        "on": res.get("switch") == 1,
        "brightness": res.get("brightness"),
        "temp": res.get("colorTemperature"),
        "rgb": parse_rgb(res.get("color")),
        "mode": udp_mode(res),
        "ap_mode": ip == AP_IP or res.get("ip") in ("0.0.0.0", "", None),
    }


def udp_mode(res):
    """'white' or 'color' from the live channel levels in a search_devices reply."""
    def level(k):
        v = res.get(k) if k in res else res.get(k.upper())
        v = v.get("value") if isinstance(v, dict) else v
        return to_int(v) or 0
    if level("w") > 0 and not any(level(k) for k in "rgb"):
        return "white"
    if any(level(k) for k in "rgb"):
        return "color"
    return None


def probe(ip, timeout=1.0):
    res = udp(ip, "search_devices", timeout=timeout, attempts=2)
    return bulb_from_status(ip, res) if succeeded(res) else None


def neighbors():
    """Linux: IPv4 hosts the kernel has recently heard from (`ip neigh`), e.g. bulbs on
    the Pi's own Wi-Fi. Elsewhere: []."""
    if not IS_LINUX:
        return []
    try:
        text = subprocess.run(["ip", "-4", "neigh", "show"], capture_output=True,
                              text=True, timeout=3).stdout
    except Exception:
        return []
    return [ln.split()[0] for ln in text.splitlines()
            if ln.strip() and not ln.rstrip().endswith(("FAILED", "INCOMPLETE"))]


def scan(window=2.5, verbose=True):
    """Find UDP-reachable bulbs: the setup AP, cached IPs, broadcasts, and a unicast
    sweep of every local subnet (all fired at once, then listen). Returns {mac: bulb}.

    Likely hosts go first and a send never waits. On Linux each packet for an absent
    host sits in the ARP queue for ~3 s and counts against the socket's send buffer,
    so once that fills, waiting sends stalled a /22 sweep (a Pi's Ethernet) past a minute."""
    targets, bcasts, nets, mine = [AP_IP], ["255.255.255.255"], [], set()
    targets += [v["ip"] for v in load_cache().values() if v.get("ip")]
    targets += neighbors()
    for _, iface in interfaces():
        mine.add(str(iface.ip))
        net = iface.network
        if net.num_addresses > MAX_SWEEP_HOSTS:
            net = ipaddress.ip_network(f"{iface.ip}/22", strict=False)
        nets.append(net)
        if net.prefixlen < 31:
            bcasts.append(str(iface.network.broadcast_address))
    for net in sorted(nets, key=lambda n: n.num_addresses):  # small (bulb) networks first
        targets += [str(h) for h in net.hosts()]
    targets += [str(h) for h in HOTSPOT_NET.hosts()]
    targets = [t for t in dict.fromkeys(targets) if t not in mine]
    if verbose:
        info(f"scanning {len(targets)} addresses on {', '.join(map(str, nets)) or 'no network'} ...")

    msg = json.dumps({"func": "search_devices", "param": {}}).encode()
    found = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        def drain(until):
            while (left := until - time.time()) > 0:
                s.settimeout(min(0.05, left))
                try:
                    data, addr = s.recvfrom(4096)
                    res = json.loads(data.decode(errors="ignore")).get("result")
                except (OSError, ValueError):
                    continue
                if succeeded(res):
                    b = bulb_from_status(addr[0], res)
                    found[b["mac"] or addr[0]] = b
            s.setblocking(False)  # back to sending: skip a send rather than wait for room

        s.setblocking(False)
        for _ in range(2):
            for i, t in enumerate(bcasts + targets):
                try:
                    s.sendto(msg, (t, PORT))
                except OSError:
                    pass  # no route, no ARP answer, or send buffer full: skip it
                if i % 64 == 63:
                    drain(time.time() + 0.01)
            drain(time.time() + window / 2)
    for b in found.values():
        save_cache(b)
    return found


# ---------------------------------------------------------------------------
# Call-home HTTP server (port 57542) - what a bulb contacts right after joining Wi-Fi
# ---------------------------------------------------------------------------

class CallbackServer:
    """Answers the two HTTP calls a freshly-joined bulb makes (same replies as
    sengled/http_server.py): accessCloud, then bimqtt = "where is your MQTT server".
    Every request also tells us the bulb's IP on its new network."""

    def __init__(self, mqtt_host, port=CALLBACK_PORT):
        hits = self.hits = []  # (ip, path, time)
        host = mqtt_host if callable(mqtt_host) else (lambda: mqtt_host)

        class Handler(BaseHTTPRequestHandler):
            def _reply(self):
                n = int(self.headers.get("Content-Length", 0) or 0)
                if n:
                    self.rfile.read(n)
                path = self.path.split("?")[0]
                if path == "/life2/device/accessCloud.json":
                    body = {"messageCode": "200", "info": "OK", "description": "正常", "success": True}
                elif path == "/jbalancer/new/bimqtt":
                    # The MQTT server is on this machine: answer with the address the bulb
                    # just reached us at. Right even with several networks (e.g. a Pi
                    # hosting the bulbs' Wi-Fi and wired to the building's network).
                    here = self.connection.getsockname()[0]
                    here = here if is_ip(here) and not here.startswith(("127.", "0.")) else None
                    body = {"protocal": "mqtt", "host": here or host() or "127.0.0.1",
                            "port": MQTT_PORT}
                else:
                    self.send_error(404)
                    return
                hits.append((self.client_address[0], path, time.time()))
                log("conn", f"call-home from {self.client_address[0]}: {path}"
                            + (f" -> MQTT {body['host']}:{MQTT_PORT}" if "host" in body else ""))
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = _reply

            def log_message(self, *a):
                pass

        try:
            self.httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        except OSError as e:
            self.httpd = None
            self.error = str(e)
            return
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def bulb_ip(self, since=0.0):
        return next((ip for ip, _, t in self.hits
                     if t >= since and ip not in ("127.0.0.1", "::1")), None)

    def close(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


# ---------------------------------------------------------------------------
# MQTT hub (port 8883) - for bulbs that live on Wi-Fi
# ---------------------------------------------------------------------------

def mqtt_available():
    try:
        import paho.mqtt.client  # noqa: F401
        import amqtt  # noqa: F401
        return True
    except ImportError:
        return False


def parse_ss(text, port=MQTT_PORT):
    """[(peer_ip, peer_port)] from Linux `ss -Htn state established sport = :PORT`."""
    out = []
    for line in text.splitlines():
        pairs = re.findall(r"(\d+\.\d+\.\d+\.\d+)\]?:(\d+)", line)  # local, then peer
        if len(pairs) >= 2 and pairs[0][1] == str(port):
            out.append((pairs[1][0], int(pairs[1][1])))
    return out


def parse_lsof(text, port=MQTT_PORT):
    """[(peer_ip, peer_port)] from `lsof -nP -iTCP:PORT -sTCP:ESTABLISHED`."""
    return [(m.group(1), int(m.group(2)))
            for m in re.finditer(rf":{port}->\[?(?:::ffff:)?(\d+\.\d+\.\d+\.\d+)\]?:(\d+)", text)]


def mqtt_sockets():
    """[(ip, port)] of established TCP connections to our MQTT port, in listing order.
    Linux: ss (always installed, fast). macOS: lsof (netstat/arp print nothing there)."""
    cmds = [(["lsof", "-nP", f"-iTCP:{MQTT_PORT}", "-sTCP:ESTABLISHED"], parse_lsof)]
    if IS_LINUX:
        cmds.insert(0, (["ss", "-Htn", "state", "established", "sport", "=", f":{MQTT_PORT}"],
                        parse_ss))
    for cmd, parse in cmds:
        try:
            text = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
        except Exception:
            continue
        return [(ip, p) for ip, p in parse(text) if not ip.startswith("127.")]
    return []


def mqtt_conns():
    """{(ip, port)} of bulbs' TCP connections to the MQTT port."""
    return set(mqtt_sockets())


def mqtt_peers():
    """IPs with an established TCP connection to our MQTT port (= connected bulbs)."""
    return list(dict.fromkeys(ip for ip, _ in mqtt_sockets()))


class Hub:
    """MQTT server + call-home server + our own MQTT client, for bulbs on Wi-Fi.
    Reuses a server that's already running (e.g. `sengled_ctl.py serve`)."""

    def __init__(self, own_servers=True, quiet=False):
        import paho.mqtt.client as mqtt
        import ssl

        self.broker = None
        self.callback = None
        self.state = {}       # mac -> {attr: value}
        self.seen = {}        # mac -> last message time
        self.ips = {}         # mac -> IP of its MQTT connection, learned this session
        self.uptime = {}      # mac -> (seconds since boot, when we heard it)
        self.pending = {}     # mac -> {type: (value, sent at)} - unconfirmed, resent on reconnect
        self.events = []      # (time, mac, type, value)
        self.lock = threading.Lock()
        say = (lambda *_: None) if quiet else info

        if not port_in_use(MQTT_PORT):
            if not own_servers:
                raise RuntimeError("no MQTT server running")
            if HERE not in sys.path:
                sys.path.insert(0, HERE)
            from pathlib import Path
            import logging
            logging.getLogger("sengled").setLevel(logging.WARNING)
            from sengled.mqtt_broker import EmbeddedBroker
            self.broker = EmbeddedBroker(Path(HOME_DIR) / "certs")
            self._log_broker()
            self.broker.start()
            threading.Thread(target=self._watch_connections, daemon=True).start()
            say(f"MQTT server started on :{MQTT_PORT} - bulbs on Wi-Fi reconnect within ~1 min")
        if own_servers and not port_in_use(CALLBACK_PORT):
            self.callback = CallbackServer(lambda: current_lan_ip() or last_lan_ip())
            if not self.callback.httpd:
                self.callback = None

        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                      client_id=f"sengled-ctl-{os.getpid()}")
        except AttributeError:  # paho-mqtt < 2
            self.client = mqtt.Client(client_id=f"sengled-ctl-{os.getpid()}")
        self.client.tls_set(cert_reqs=ssl.CERT_NONE)
        self.client.tls_insecure_set(True)
        self.client.on_message = self._on_message
        self.client.on_connect = lambda cl, *a, **k: cl.subscribe("wifielement/+/status")
        self.client.connect("127.0.0.1", MQTT_PORT, keepalive=30)
        self.client.loop_start()
        time.sleep(0.5)

    def _on_message(self, client, userdata, msg):
        mac = norm_mac(msg.topic.split("/")[1]) if msg.topic.count("/") >= 2 else None
        try:
            payload = json.loads(msg.payload.decode(errors="ignore"))
        except ValueError:
            return
        items = payload if isinstance(payload, list) else [payload]
        now = time.time()
        self._log_status(mac, items)
        with self.lock:
            for it in items:
                if not isinstance(it, dict):
                    continue
                m = norm_mac(it.get("dn")) or mac
                if not m:
                    continue
                st = self.state.setdefault(m, {})
                if "type" in it:
                    st[it["type"]] = it.get("value")
                    self.events.append((now, m, it["type"], it.get("value")))
                else:
                    st.update({k: v for k, v in it.items() if k not in ("dn", "time")})
                self.seen[m] = now
            if len(self.events) > 4000:
                del self.events[:2000]
            macs = {norm_mac(it.get("dn")) or mac for it in items if isinstance(it, dict)} - {None}
            # A bulb sends supportAttributes right after it (re)connects.
            reconnected = any(isinstance(it, dict) and it.get("type") == "supportAttributes" for it in items)
        self._clear_pending(items, mac)
        if reconnected:
            for m in macs:
                self._redeliver(m)
        for m in macs:
            reported = self.state.get(m, {}).get("ip")
            if reconnected and is_ip(reported):  # the bulb's own report beats guessing
                self.ips[m] = reported
                claim_ip(m, reported)
            elif reconnected or m not in self.ips:
                self._learn_ip(m, reconnected)

    def _clear_pending(self, items, mac):
        with self.lock:
            for it in items:
                if not isinstance(it, dict):
                    continue
                m = norm_mac(it.get("dn")) or mac
                pend = self.pending.get(m)
                if not pend:
                    continue
                k, v = it.get("type"), str(it.get("value"))
                if k == "effectStatus":
                    for e in [e for e in pend if e in EFFECTS]:
                        del pend[e]
                elif k in pend and str(pend[k][0]) == v:
                    del pend[k]

    def _redeliver(self, mac):
        """Resend commands the bulb missed while its connection was down (last 5 min)."""
        with self.lock:
            pend = {k: v for k, (v, t) in self.pending.get(mac, {}).items() if time.time() - t < 300}
        if pend:
            log("send", f"{mac} resending after reconnect: " + " ".join(f"{k}={v}" for k, v in pend.items()))
            ts = int(time.time() * 1000)
            self.client.publish(f"wifielement/{mac}/update", json.dumps(
                [{"dn": mac, "type": k, "value": str(v), "time": ts} for k, v in pend.items()]), qos=1)

    def _log_status(self, mac, items):
        if not self.broker:  # the process running the MQTT server logs bulb traffic (once)
            return
        types = {it.get("type"): it.get("value") for it in items if isinstance(it, dict)}
        if mac and "supportAttributes" in types:
            up = to_int(next((it.get("time") for it in items if isinstance(it, dict)), None))
            prev = self.uptime.get(mac)
            note = ""
            if up is not None:
                if prev and up < prev[0] + (time.time() - prev[1]) - 30:
                    note = " - it REBOOTED since we last heard it"
                self.uptime[mac] = (up, time.time())
            log("conn", f"{mac} (re)connected to MQTT, up {fmt_duration(up) if up is not None else '?'}{note}"
                        f" [uptime={up if up is not None else '?'}]")
        if mac and "deviceRssi" in types:
            r = to_int(types["deviceRssi"])
            log("conn", f"{mac} ip {types.get('ip', '?')}, Wi-Fi signal {r} dBm ({signal_label(r)})")
        cycling = bool(mac) and any(mac in g.bulbs for g in CYCLES.groups)  # skip its color echoes
        shown = {k: v for k, v in types.items()
                 if not (cycling and k in ("color", "colorMode"))}
        if shown:
            log("bulb", f"{mac} " + " ".join(f"{k}={v}" for k, v in shown.items()))

    def _log_broker(self):
        """Send the MQTT server's own messages (connects, timeouts, errors) to the log file."""
        import logging

        class ToLog(logging.Handler):
            def emit(self, record):
                msg = record.getMessage()
                if record.levelno < logging.INFO:  # packet-level detail: its own file, so
                    if "Input stream read timeout" not in msg:  # it can't push out history
                        log(f"broker debug: {msg}", None, LOG_FILE.replace(".log", "-mqtt-debug.log"))
                    return
                log("broker", f"{record.levelname.lower()}: {msg}")

        lg = logging.getLogger("amqtt")
        # SENGLED_MQTT_DEBUG=1 logs every MQTT packet (CONNECT, PINGREQ/PINGRESP, ...)
        lg.setLevel(logging.DEBUG if os.environ.get("SENGLED_MQTT_DEBUG") else logging.INFO)
        lg.addHandler(ToLog())
        aio = logging.getLogger("asyncio")  # TLS handshake failures are reported here
        aio.setLevel(logging.WARNING)
        aio.addHandler(ToLog())

    def _watch_connections(self):
        """Log every bulb connection that opens or closes (the broker owner only)."""
        prev = set()
        while True:
            now = mqtt_conns()
            names = {ip: m for m, ip in {**cached_ips(), **self.ips}.items()}
            for ip, port in sorted(now - prev):
                n = sum(1 for i, _ in now if i == ip)
                log("conn", f"{names.get(ip, ip)} opened connection {ip}:{port}"
                            f"{f' ({n} open from this IP - older ones are stale)' if n > 1 else ''}")
            for ip, port in sorted(prev - now):
                log("conn", f"{names.get(ip, ip)} connection {ip}:{port} closed")
            prev = now
            time.sleep(3)

    def ip_of(self, mac):
        return self.ips.get(mac) or cached_ips().get(mac)

    def _learn_ip(self, mac, reconnected=False):
        """Pair a bulb's MAC with the IP of its MQTT connection: the one connected IP that
        no other bulb is known to have. (lsof only shows IPs, and bulbs don't say theirs.)"""
        peers = mqtt_peers()
        if not reconnected and self.ip_of(mac) in peers:
            return
        known = {**cached_ips(), **self.ips}
        free = [ip for ip in peers if not any(m != mac and v == ip for m, v in known.items())]
        if len(free) == 1:
            self.ips[mac] = free[0]
            for m in [m for m, v in self.ips.items() if m != mac and v == free[0]]:
                del self.ips[m]
            claim_ip(mac, free[0])

    def known_macs(self):
        """MACs heard on MQTT this session, plus cached MQTT bulbs."""
        macs = list(self.state)
        macs += [m for m, v in load_cache().items() if v.get("via") == "mqtt" and m not in macs]
        return macs

    def bulb(self, mac):
        mac = norm_mac(mac)
        cached = load_cache().get(mac, {})
        st = {**cached.get("state", {}), **self.state.get(mac, {})}
        peers = mqtt_peers()
        ip = self.ip_of(mac)
        if ip not in peers and mac in self.state:  # heard from it this session
            self._learn_ip(mac)
            ip = self.ip_of(mac)
        rssi = to_int(st.get("deviceRssi"))
        shown_ip = ip or cached.get("ip")
        return {
            "via": "mqtt", "mac": mac, "ip": shown_ip or "?",
            "ssid": bulb_ssid({"mac": mac, "ip": shown_ip}),
            "rssi": rssi,
            "model": st.get("typeCode") or cached.get("model"),
            "version": st.get("version") or st.get("softwareVersion"),
            "on": str(st.get("switch")) == "1" if "switch" in st else None,
            "brightness": to_int(st.get("brightness")),
            "temp": to_int(st.get("colorTemperature")),
            "rgb": parse_rgb(st.get("color")),
            "mode": {"1": "color", "2": "white"}.get(str(st.get("colorMode"))),
            "effect": EFFECT_BY_STATUS.get(to_int(st.get("effectStatus"))),
            "ap_mode": False,
            "connected": ip in peers,
        }

    def bulbs(self):
        return [self.bulb(m) for m in self.known_macs()]

    def send(self, mac, items, label, wait=SEND_WAIT, confirm=()):
        """Publish [(type, value), ...] and wait for the bulb to report the last item's new
        value (or any type in `confirm`) on /status. Matching the value matters: turning a
        bulb on makes it report its *old* color, which isn't a confirmation of a new one."""
        ts = int(time.time() * 1000)
        payload = [{"dn": mac, "type": t, "value": str(v), "time": ts} for t, v in items]
        start = time.time()
        self.client.publish(f"wifielement/{mac}/update", json.dumps(payload), qos=1)
        log("send", f"{mac} " + " ".join(f"{t}={v}" for t, v in items))
        want_type, want_value = items[-1][0], str(items[-1][1])
        other = None
        while time.time() - start < wait:
            with self.lock:
                for t, m, k, v in self.events:
                    if t < start or m != mac:
                        continue
                    # confirm: types that count whatever their value, or (type, value) pairs
                    if (k in confirm or (k, str(v)) in confirm
                            or (k == want_type and str(v) == want_value)):
                        took = time.time() - start
                        log("rtt", f"{mac} {want_type} {took * 1000:.0f} ms")
                        ok_(f"{label}: ok" + (f" (slow: {took:.1f}s)" if took > 2 else ""))
                        return True
                    if k == want_type:
                        other = v
            time.sleep(0.1)
        log("rtt", f"{mac} {want_type} timeout")
        with self.lock:
            pend = self.pending.setdefault(mac, {})
            for t, v in items:
                pend[t] = (v, time.time())
        if not mqtt_peers():
            err(f"{label}: no bulb is connected to the MQTT server yet "
                "(it reconnects within ~1 min of the server starting)")
        else:
            warn(f"{label}: sent, but the bulb didn't confirm within {wait:.0f}s"
                 + (f" (it still reports {want_type}={other})" if other is not None else
                    " - it may still change late if its Wi-Fi signal is weak")
                 + "; it will be resent when the bulb reconnects")
        return False

    def publish(self, mac, items, quiet=False):
        """Fire-and-forget publish, for commands the bulb never acknowledges (e.g. `still`)."""
        ts = int(time.time() * 1000)
        if not quiet:
            log("send", f"{mac} " + " ".join(f"{t}={json.dumps(v) if isinstance(v, dict) else v}"
                                             for t, v in items))
        payload = [{"dn": mac, "type": t, "value": v if isinstance(v, dict) else str(v), "time": ts}
                   for t, v in items]
        return self.client.publish(f"wifielement/{mac}/update", json.dumps(payload), qos=1).rc == 0

    def changed_since(self, mac, since):
        with self.lock:
            return any(t >= since and m == mac for t, m, _, _ in self.events)

    def wait_for_bulb(self, mac=None, timeout=90):
        """Wait until a bulb (optionally `mac`) is connected. Returns its MAC or None."""
        start = time.time()
        while time.time() - start < timeout:
            if mac and mac in self.state:
                return mac
            if mqtt_peers():
                macs = self.known_macs()
                if mac in macs or (not mac and len(macs) == 1):
                    return mac or macs[0]
                if not mac and self.state:
                    return next(iter(self.state))
            time.sleep(1)
        return None

    def save_state(self):
        """Remember each bulb's last reported attributes (the bulb only reports changes)."""
        data = _read_cache_file()
        with self.lock:
            for mac, st in self.state.items():
                entry = data.setdefault(mac, {"via": "mqtt"})
                entry["state"] = {**entry.get("state", {}), **st}
        _write_cache_file(data)

    def close(self):
        self.save_state()
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass
        if self.callback:
            self.callback.close()
        if self.broker:
            self.broker.stop()


HUB = None  # the running Hub, if any


# ---------------------------------------------------------------------------
# Stats for nerds: built from the shared log file (so the TUI, CLI and web app all
# see the same history), plus live socket counts and the bulb's own reports.
# ---------------------------------------------------------------------------

STATS_LOG_BYTES = 3_000_000  # how much of the log tail to read
_LOG_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.(\d{3}) (\S+)\s+(\S+)\s+(.*)$")


def _log_tail():
    """[(time, kind, message)] from the last few MB of the log (and its rolled-over part)."""
    chunks = []
    for path in (LOG_FILE + ".1", LOG_FILE):
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - STATS_LOG_BYTES))
                chunks.append(f.read().decode(errors="ignore"))
        except OSError:
            pass
    out = []
    for line in "".join(chunks).splitlines():
        m = _LOG_LINE.match(line)
        if m:
            t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) + int(m.group(2)) / 1000
            out.append((t, m.group(4), m.group(5)))
    return out


def _pct(values, p):
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def bulb_stats(b):
    """Everything we know about one bulb's connection and health."""
    mac, now = b.get("mac"), time.time()
    st = {**load_cache().get(mac, {}).get("state", {}), **(HUB.state.get(mac, {}) if HUB else {})}
    out = {
        "mac": mac, "ip": b.get("ip"), "via": b.get("via"), "ssid": b.get("ssid") or bulb_ssid(b),
        "connected": b.get("connected", b.get("via") == "udp"),
        "device": {k: v for k, v in {
            "model": st.get("typeCode") or b.get("model"), "firmware": st.get("version") or b.get("version"),
            "chip": st.get("identifyNO"), "product": st.get("productCode"),
            "supports": st.get("supportAttributes"), "colorMode": st.get("colorMode"),
            "effectStatus": st.get("effectStatus"), "saveFlag": st.get("saveFlag"),
            "timeZone": st.get("timeZone"), "update": st.get("update"),
        }.items() if v is not None},
    }
    connects, closes, rssi, rtt, last_heard = [], [], [], [], None
    for t, kind, msg in _log_tail():
        if not mac or not msg.startswith(mac):
            continue
        if kind == "conn" and "(re)connected" in msg:
            m = re.search(r"\[uptime=(\d+)\]", msg)
            h = re.search(r"up (?:(\d+) min)? ?(?:(\d+) s)?", msg)  # older lines: "up 8 min 53 s"
            up = (int(m.group(1)) if m else
                  int(h.group(1) or 0) * 60 + int(h.group(2) or 0) if h and (h.group(1) or h.group(2)) else None)
            connects.append((t, up, "REBOOTED" in msg))
        elif kind == "conn" and msg.endswith("closed"):
            closes.append(t)
        elif kind == "conn" and "Wi-Fi signal" in msg:
            m = re.search(r"signal (-?\d+) dBm", msg)
            if m:
                rssi.append((t, int(m.group(1))))
        elif kind == "rtt":
            m = re.search(r" (\d+) ms$", msg)
            rtt.append((t, int(m.group(1)) if m else None, msg.split()[1]))
        if kind in ("bulb", "conn") and "closed" not in msg and "opened" not in msg:
            last_heard = t

    live_rssi = to_int(st.get("deviceRssi"))
    cur = rssi[-1][1] if rssi else live_rssi
    vals = [v for _, v in rssi[-200:]]
    out["wifi"] = {"rssi": cur, "quality": signal_label(cur),
                   "min": min(vals) if vals else cur, "max": max(vals) if vals else cur,
                   "history": [[round(t), v] for t, v in rssi[-40:]]}

    # Uptime: the bulb reports seconds since boot when it connects; extrapolate.
    boots, up_now = [], None
    for t, up, _ in connects:
        if up is None:
            continue
        boot = t - up
        if not boots or abs(boot - boots[-1]) > 30:
            boots.append(boot)
    if connects and connects[-1][1] is not None:
        up_now = connects[-1][1] + (now - connects[-1][0])
    out["uptime"] = {"seconds": round(up_now) if up_now is not None else None,
                     "bootedAt": round(boots[-1]) if boots else None,
                     "rebootsSeen": max(0, len(boots) - 1),
                     "rebootTimes": [round(x) for x in boots[1:][-10:]]}

    times = [t for t, _, _ in connects]
    gaps = [b2 - a2 for a2, b2 in zip(times, times[1:])]
    out["connection"] = {
        "connectedSince": round(times[-1]) if times else None,
        "lastHeard": round(last_heard) if last_heard else None,
        "reconnectsLastHour": sum(1 for t in times if now - t < 3600),
        "reconnects24h": sum(1 for t in times if now - t < 86400),
        "medianReconnectInterval": _pct(gaps[-30:], 50),
        "closedSockets24h": sum(1 for t in closes if now - t < 86400),
        "history": [round(t) for t in times[-60:]],
    }
    if b.get("via") == "mqtt" and is_ip(b.get("ip")):
        socks = [port for ip, port in mqtt_conns() if ip == b["ip"]]
        out["connection"].update(openSockets=len(socks), staleSockets=max(0, len(socks) - 1),
                                 ports=sorted(socks))

    recent = rtt[-50:]
    ms = [v for _, v, _ in recent if v is not None]
    out["commands"] = {
        "count": len(recent), "ok": len(ms), "timeouts": len(recent) - len(ms),
        "successRate": round(len(ms) / len(recent), 3) if recent else None,
        "lastMs": recent[-1][1] if recent else None,
        "avgMs": round(sum(ms) / len(ms)) if ms else None,
        "p50Ms": _pct(ms, 50), "p95Ms": _pct(ms, 95), "maxMs": max(ms) if ms else None,
        "minMs": min(ms) if ms else None,
        "history": [[round(t), v] for t, v, _ in recent[-40:]],
    }
    pend = HUB.pending.get(mac, {}) if HUB and mac else {}
    out["pending"] = {k: v for k, (v, _) in pend.items()}
    return out


def _ago(sec):
    return f"{sec:.0f} s" if sec < 60 else f"{sec / 60:.0f} min" if sec < 3600 else f"{sec / 3600:.1f} h"


def _spark(values):
    """Unicode sparkline; timeouts (None) shown as a red ×."""
    bars = "▁▂▃▄▅▆▇█"
    nums = [v for v in values if v is not None]
    if not nums:
        return ""
    lo, hi = min(nums), max(nums)
    return "".join(c("×", "31") if v is None else bars[int((v - lo) / (hi - lo or 1) * 7)] for v in values)


def print_stats(b):
    s = bulb_stats(b)
    now = time.time()
    w, u, cn, cm = s["wifi"], s["uptime"], s["connection"], s["commands"]
    row = lambda k, v: print(f"    {c(k.ljust(16), '90')} {v}")  # noqa: E731
    print()
    print(f"  {c('stats for nerds', '1')}  {s['mac'] or s['ip']}")
    sig = f"{w['rssi']} dBm ({w['quality']})" if w["rssi"] is not None else "?"
    row("network", f"{s['ssid'] or '?'}   {s['ip']}   via {s['via'].upper()}")
    row("signal", f"{c(sig, '31' if (w['rssi'] or 0) < -75 else '33' if (w['rssi'] or 0) < -67 else '32')}"
                  f"   range {w['min']}..{w['max']} dBm   {_spark([v for _, v in w['history']])}")
    if u["seconds"] is not None:
        boot = time.strftime("%H:%M", time.localtime(u["bootedAt"])) if u["bootedAt"] else "?"
        reboots = c(f"   {u['rebootsSeen']} reboot(s) seen", "33") if u["rebootsSeen"] else ""
        row("uptime", f"{fmt_duration(u['seconds'])} (booted {boot}){reboots}")
    if s["via"] == "mqtt":
        flap = cn["reconnectsLastHour"] >= 6
        since = f"up {_ago(now - cn['connectedSince'])}" if cn["connectedSince"] else "?"
        row("connection", f"{c(since, '33' if flap else '32') if s['connected'] else c('not connected', '31')}"
                          f"   {cn['reconnectsLastHour']} reconnects/h"
                          + (f", every ~{cn['medianReconnectInterval']:.0f} s" if cn["medianReconnectInterval"] else ""))
        if "openSockets" in cn:
            row("tcp sockets", f"{cn['openSockets']} open"
                               + (c(f" ({cn['staleSockets']} stale)", "33") if cn["staleSockets"] else "")
                               + (f"   ports {', '.join(map(str, cn['ports']))}" if cn["ports"] else ""))
        if cn["lastHeard"]:
            row("last heard", f"{_ago(now - cn['lastHeard'])} ago")
    if cm["count"]:
        row("response time", f"avg {cm['avgMs']} ms   p50 {cm['p50Ms']}   p95 {cm['p95Ms']}   max {cm['maxMs']} ms"
                             f"   {cm['ok']}/{cm['count']} confirmed   {_spark([v for _, v in cm['history']])}")
    else:
        row("response time", c("no commands measured yet - ping it", "90"))
    if s["pending"]:
        row("pending", c(" ".join(f"{k}={v}" for k, v in s["pending"].items()) + "  (resent on reconnect)", "33"))
    for k, v in s["device"].items():
        row(k, v)
    print(c(f"    history from {LOG_FILE}", "90"))


def print_ping(b, count=5):
    info(f"pinging {b.get('mac') or b.get('ip')} {count}x over {b.get('via', 'udp').upper()} ...")
    try:
        r = ping_bulb(b, count)
    except ValueError as e:
        err(str(e))
        return False
    print("    " + "  ".join(c("timeout", "31") if x is None else
                             c(f"{x} ms", "32" if x < 300 else "33" if x < 1500 else "31") for x in r["rounds"]))
    print(f"    min/avg/max {r['minMs']}/{r['avgMs']}/{r['maxMs']} ms   loss "
          f"{c(str(r['loss']) + '%', '31' if r['loss'] else '32')}")
    if r["icmp"]:
        i = r["icmp"]
        print(c("    system ping: no reply (often blocked on shared Wi-Fi - not conclusive)", "90")
              if i["loss"] >= 100 else f"    system ping: avg {i['avgMs']} ms, {i['loss']:.0f}% loss")
    return r["loss"] < 100


def icmp_ping(ip, count=3):
    """System ping. Many shared/public networks block it, so 100% loss isn't conclusive."""
    try:
        wait = "1" if IS_LINUX else "1000"  # per-reply timeout: Linux in s, macOS in ms
        text = subprocess.run(["ping", "-c", str(count), "-i", "0.3", "-W", wait, ip],
                              capture_output=True, text=True, timeout=count * 2 + 3).stdout
    except Exception:
        return None
    m = re.search(r"([\d.]+)% packet loss", text)
    r = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)", text)
    return {"loss": float(m.group(1)) if m else 100.0,
            "minMs": float(r.group(1)) if r else None, "avgMs": float(r.group(2)) if r else None,
            "maxMs": float(r.group(3)) if r else None}


def ping_bulb(b, count=5):
    """Time real round trips to the bulb. On MQTT: re-send its current power state (a no-op
    the bulb still answers). Over UDP: ask for its MAC. Plus a system ping."""
    rounds = []
    if b.get("via") == "mqtt":
        if not HUB:
            raise ValueError("the MQTT server isn't running")
        mac = b["mac"]
        sw = (HUB.state.get(mac) or load_cache().get(mac, {}).get("state", {})).get("switch")
        if str(sw) not in ("0", "1"):
            raise ValueError("power state unknown - turn the bulb on or off once, then ping")
        for i in range(count):
            start = time.time()
            HUB.client.publish(f"wifielement/{mac}/update", json.dumps(
                [{"dn": mac, "type": "switch", "value": str(sw), "time": int(start * 1000)}]), qos=1)
            took = None
            while time.time() - start < 5:
                with HUB.lock:
                    if any(t >= start and m == mac and k == "switch" for t, m, k, _ in HUB.events):
                        took = (time.time() - start) * 1000
                        break
                time.sleep(0.02)
            rounds.append(round(took) if took is not None else None)
            log("rtt", f"{mac} ping {f'{took:.0f} ms' if took is not None else 'timeout'}")
            if i < count - 1:
                time.sleep(0.4)
    else:
        for i in range(count):
            start = time.time()
            res = udp(b["ip"], "get_device_mac", timeout=2.0, attempts=1)
            rounds.append(round((time.time() - start) * 1000) if succeeded(res) else None)
            time.sleep(0.2)
    ok = [r for r in rounds if r is not None]
    result = {"method": "mqtt" if b.get("via") == "mqtt" else "udp", "rounds": rounds,
              "loss": round(100 * (1 - len(ok) / len(rounds))) if rounds else 100,
              "minMs": min(ok) if ok else None, "avgMs": round(sum(ok) / len(ok)) if ok else None,
              "maxMs": max(ok) if ok else None,
              "icmp": icmp_ping(b["ip"]) if is_ip(b.get("ip")) else None}
    return result


def start_hub(own_servers=True, quiet=False):
    global HUB
    if HUB:
        return HUB
    if not mqtt_available():
        return None
    try:
        HUB = Hub(own_servers=own_servers, quiet=quiet)
    except Exception as e:
        if own_servers:
            warn(f"MQTT unavailable: {e}")
        HUB = None
    return HUB


# ---------------------------------------------------------------------------
# Bulb commands (UDP or MQTT, depending on how the bulb was reached)
# ---------------------------------------------------------------------------

def report(label, res):
    good = succeeded(res)
    (ok_ if good else err)(f"{label}: {'ok' if good else (res or 'no response')}")
    return good


def _mqtt(b):
    if b.get("via") != "mqtt":
        return None
    if not HUB:
        raise ValueError("this bulb is on MQTT, but the MQTT server isn't running")
    return HUB


def set_power(b, on):
    if not on:
        CYCLES.release(b)
    label = "on" if on else "off"
    if _mqtt(b):
        return HUB.send(b["mac"], [("switch", 1 if on else 0)], label)
    return report(label, udp(b["ip"], "set_device_switch", {"switch": 1 if on else 0}))


def set_color(b, rgb):
    CYCLES.release(b)
    r, g, bl = rgb
    label = f"color {r},{g},{bl} {swatch(rgb)}"
    if _mqtt(b):
        return HUB.send(b["mac"], [("switch", 1), ("color", f"{r}:{g}:{bl}")], label)
    udp(b["ip"], "set_device_switch", {"switch": 1})
    return report(label, udp(b["ip"], "set_device_color", {"red": r, "green": g, "blue": bl}))


def set_brightness(b, n):
    if not 0 <= n <= 100:
        raise ValueError("brightness must be 0-100")
    if _mqtt(b):
        return HUB.send(b["mac"], [("switch", 1), ("brightness", n)], f"brightness {n}%")
    udp(b["ip"], "set_device_switch", {"switch": 1})
    return report(f"brightness {n}%", udp(b["ip"], "set_device_brightness", {"brightness": n}))


def set_temp(b, n):
    if not 0 <= n <= 100:
        raise ValueError("temp must be 0 (warm) - 100 (cool)")
    CYCLES.release(b)
    if _mqtt(b):
        return HUB.send(b["mac"], [("switch", 1), ("colorTemperature", n)], f"white temp {n}")
    udp(b["ip"], "set_device_switch", {"switch": 1})
    return report(f"white temp {n}", udp(b["ip"], "set_device_colortemp", {"colorTemperature": n}))


def send_raw(b, text):
    obj = json.loads(text)
    if _mqtt(b):
        # MQTT: {"type": "...", "value": "..."} or a list of them
        items = obj if isinstance(obj, list) else [obj]
        return HUB.send(b["mac"], [(i["type"], i.get("value", "")) for i in items], "raw")
    res = udp(b["ip"], obj.get("func", ""), obj.get("param", {}), timeout=3)
    print(f"  {json.dumps(res)}")
    return res is not None


def reboot(b):
    if _mqtt(b):
        err("reboot isn't available over MQTT - power-cycle the bulb instead")
        return False
    return report("reboot", udp(b["ip"], "reboot"))


def set_effect(b, name):
    """Start a built-in effect (see EFFECTS), or stop the running one with name=None."""
    if not _mqtt(b):
        raise ValueError("effects need the bulb on Wi-Fi (MQTT); bulbs in setup mode can't run them")
    CYCLES.release(b)
    if name is None:
        running = b.get("effect")
        targets = [running] if running else list(EFFECTS)  # unknown: stop them all
        return HUB.send(b["mac"], [(n, 0) for n in targets], "effect off", confirm=(("effectStatus", "0"),))
    name = effect_name(name)
    return HUB.send(b["mac"], [(name, 1)], f"effect {EFFECTS[name][0]}",
                    confirm=(("effectStatus", str(EFFECTS[name][1])),))


# ---------------------------------------------------------------------------
# Custom color cycle: driven from this computer, so its speed is adjustable
# (the bulb's own colorCycle effect runs at a fixed speed). Runs only while this
# process (TUI / web service / `cycle` command) is running.
# ---------------------------------------------------------------------------

PALETTES = {
    "rainbow": ("Rainbow", None),  # full hue sweep
    "sunset": ("Sunset", [(255, 40, 0), (255, 120, 0), (255, 0, 90), (180, 0, 160)]),
    "ocean": ("Ocean", [(0, 60, 255), (0, 200, 255), (0, 255, 170), (0, 120, 200)]),
    "aurora": ("Aurora", [(0, 255, 120), (0, 180, 255), (140, 0, 255), (255, 0, 200)]),
    "candy": ("Candy", [(255, 0, 140), (255, 120, 200), (170, 80, 255), (90, 200, 255)]),
    "fire": ("Fire", [(255, 0, 0), (255, 90, 0), (255, 170, 0), (255, 40, 0)]),
}
CYCLE_MIN_PERIOD, CYCLE_MAX_PERIOD = 3, 3600   # seconds per full loop
CYCLE_MIN_STEP = 0.25                          # don't send more than 4 colors / s / bulb


def palette_color(palette, pos):
    """Color at `pos` (0..1, wraps) along a palette loop."""
    import colorsys
    pos %= 1.0
    anchors = PALETTES[palette][1]
    if anchors is None:
        r, g, b = colorsys.hsv_to_rgb(pos, 1.0, 1.0)
        return (round(r * 255), round(g * 255), round(b * 255))
    x = pos * len(anchors)
    i = int(x)
    f = x - i
    a, b = anchors[i % len(anchors)], anchors[(i + 1) % len(anchors)]
    f = f * f * (3 - 2 * f)  # ease between anchors
    return tuple(round(a[k] + (b[k] - a[k]) * f) for k in range(3))


def bulb_key(b):
    return b.get("mac") or b.get("ip")


class CycleGroup:
    """One running cycle over one or more bulbs (kept in sync, or spread apart)."""

    def __init__(self, bulbs, palette, period, spread):
        self.bulbs = {bulb_key(b): b for b in bulbs}
        self.palette, self.period, self.spread = palette, float(period), bool(spread)
        self.pos = 0.0
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _send(self, b, rgb):
        r, g, bl = rgb
        if b.get("via") == "mqtt":
            if HUB:
                HUB.publish(b["mac"], [("color", f"{r}:{g}:{bl}")], quiet=True)
        else:
            udp(b["ip"], "set_device_color", {"red": r, "green": g, "blue": bl}, timeout=0.5, attempts=1)

    def _run(self):
        for b in list(self.bulbs.values()):  # make sure they're on
            if b.get("via") == "mqtt" and HUB:
                HUB.publish(b["mac"], [("switch", 1)])
            elif b.get("via") != "mqtt":
                udp(b["ip"], "set_device_switch", {"switch": 1}, timeout=0.5, attempts=1)
        last = {}
        t = time.time()
        while not self.stopped.is_set():
            with self.lock:
                bulbs = list(self.bulbs.values())
                period, palette, spread = self.period, self.palette, self.spread
            if not bulbs:
                break
            # One step every ~0.5 degree of a full loop, but never faster than CYCLE_MIN_STEP.
            step = max(CYCLE_MIN_STEP, period / 720)
            now = time.time()
            self.pos = (self.pos + (now - t) / period) % 1.0
            t = now
            for n, b in enumerate(bulbs):
                offset = n / len(bulbs) if spread else 0.0
                rgb = palette_color(palette, self.pos + offset)
                if last.get(bulb_key(b)) != rgb:
                    try:
                        self._send(b, rgb)
                    except Exception:
                        pass
                    last[bulb_key(b)] = rgb
            self.stopped.wait(step)

    def update(self, palette=None, period=None, spread=None):
        with self.lock:
            if palette is not None:
                self.palette = palette
            if period is not None:
                self.period = float(period)
            if spread is not None:
                self.spread = bool(spread)


class CycleManager:
    def __init__(self):
        self.groups = []
        self.lock = threading.Lock()

    def start(self, bulbs, palette="rainbow", period=60, spread=False):
        if palette not in PALETTES:
            raise ValueError(f"unknown palette {palette!r} - choose from: {', '.join(PALETTES)}")
        period = float(period)
        if not CYCLE_MIN_PERIOD <= period <= CYCLE_MAX_PERIOD:
            raise ValueError(f"cycle speed must be {CYCLE_MIN_PERIOD}-{CYCLE_MAX_PERIOD} seconds per loop")
        keys = {bulb_key(b) for b in bulbs}
        with self.lock:
            # Same bulbs already cycling together: just change speed/palette, no restart.
            for g in self.groups:
                if set(g.bulbs) == keys and not g.stopped.is_set():
                    g.update(palette, period, spread)
                    return g
        for b in bulbs:
            self.release(b)
            if b.get("via") == "mqtt" and HUB and b.get("effect"):
                HUB.publish(b["mac"], [(b["effect"], 0)])  # stop a built-in effect first
        g = CycleGroup(bulbs, palette, period, spread)
        with self.lock:
            self.groups.append(g)
        g.thread.start()
        return g

    def release(self, b):
        """Take a bulb out of whatever cycle it's in (stops the cycle if it was the last)."""
        key = bulb_key(b)
        with self.lock:
            for g in list(self.groups):
                with g.lock:
                    g.bulbs.pop(key, None)
                    empty = not g.bulbs
                if empty:
                    g.stopped.set()
                    self.groups.remove(g)

    def of(self, b):
        key = bulb_key(b)
        with self.lock:
            for g in self.groups:
                if key in g.bulbs and not g.stopped.is_set():
                    return {"palette": g.palette, "period": g.period, "spread": g.spread}
        return None

    def stop_all(self):
        with self.lock:
            for g in self.groups:
                g.stopped.set()
            self.groups.clear()


CYCLES = CycleManager()


def start_cycle(bulbs, palette="rainbow", period=60, spread=False):
    g = CYCLES.start(bulbs, palette, period, spread)
    names = ", ".join(b.get("mac") or b.get("ip") for b in bulbs)
    ok_(f"custom cycle '{PALETTES[g.palette][0]}' every {fmt_duration(g.period)} on {names}"
        f"{' (spread)' if g.spread and len(bulbs) > 1 else ''}")
    return True


def stop_cycle(b):
    if CYCLES.of(b):
        CYCLES.release(b)
        ok_("custom cycle stopped")
        return True
    info("no custom cycle running on this bulb")
    return True


FADE_MAX_SECONDS = 3600


def set_fade(b, start, end, seconds, temp_start=None, temp_end=None, off_at_end=False):
    """Smooth brightness (and optionally white-temperature) fade done by the bulb itself.

    Uses the firmware's `still` command (W31-N15: handler 0x402239c0). The bulb never
    acknowledges it and doesn't update its reported brightness, so once the fade should
    be over we re-send the end state - unless something else changed the bulb meanwhile."""
    if not _mqtt(b):
        raise ValueError("fades need the bulb on Wi-Fi (MQTT); bulbs in setup mode can't run them")
    if end is None or seconds is None:
        raise ValueError("fade needs an end brightness and a duration in seconds")
    CYCLES.release(b)
    start, end, seconds = int(start), int(end), int(seconds)
    if not (0 <= start <= 100 and 0 <= end <= 100):
        raise ValueError("fade brightness must be 0-100")
    if start == end:
        raise ValueError("fade start and end brightness must differ (the bulb rejects equal values)")
    if not 1 <= seconds <= FADE_MAX_SECONDS:
        raise ValueError(f"fade duration must be 1-{FADE_MAX_SECONDS} seconds")
    for t in (temp_start, temp_end):
        if t is not None and not 0 <= int(t) <= 100:
            raise ValueError("fade temperature must be 0 (warm) - 100 (cool)")
    spec = {"beginBrightness": max(start, 1), "endBrightness": max(end, 1), "interval": seconds,
            "beginColorTemperature": -1 if temp_start is None else max(int(temp_start), 1),
            "endColorTemperature": -1 if temp_end is None else max(int(temp_end), 1)}
    mac, hub = b["mac"], HUB
    # Make sure it's on and in white mode, so the fade is visible from the first second.
    first = [("switch", 1)] + ([("colorTemperature", temp_start)] if temp_start is not None else [])
    hub.publish(mac, first)
    time.sleep(0.3)
    if not hub.publish(mac, [("still", spec)]):
        err("fade: couldn't send")
        return False
    started = time.time()

    def finish():
        if hub is not HUB or hub.changed_since(mac, started + 2):
            return  # something else changed the bulb during the fade; leave it alone
        final = [("brightness", max(end, 1))]
        if temp_end is not None:
            final.append(("colorTemperature", temp_end))
        if off_at_end or end == 0:
            final.append(("switch", 0))
        hub.publish(mac, final)

    timer = threading.Timer(seconds + 1, finish)
    timer.daemon = True
    timer.start()
    temps = f", white {temp_start}->{temp_end}" if temp_start is not None and temp_end is not None else ""
    ok_(f"fade {start}% -> {end}%{temps} over {fmt_duration(seconds)}"
        f"{' then off' if off_at_end or end == 0 else ''} (the bulb doesn't confirm fades)")
    return True


def fmt_duration(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m} min {s} s" if m and s else f"{m} min" if m else f"{s} s"


def effect_name(text):
    """'colorCycle' | 'color cycle' | 'color-cycle' | 'cycle' -> 'colorCycle'"""
    key = re.sub(r"[^a-z]", "", str(text).lower())
    for name, (label, _) in EFFECTS.items():
        if key in (name.lower(), re.sub(r"[^a-z]", "", label.lower())):
            return name
    matches = [n for n, (label, _) in EFFECTS.items() if key and key in label.lower().replace(" ", "")]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"unknown effect {text!r} - choose from: {', '.join(EFFECTS)}")


def factory_reset(b):
    if _mqtt(b):
        return HUB.send(b["mac"], [("reset", 1)], "factory reset")
    return report("factory reset", udp(b["ip"], "factory_reset"))


def refresh(b):
    if not b:
        return None
    if b.get("via") == "mqtt":
        return HUB.bulb(b["mac"]) if HUB else b
    return probe(b["ip"]) or b


def print_bulb(b):
    def val(v, suffix=""):
        return "?" if v is None else f"{v}{suffix}"

    if b.get("via") == "mqtt":
        mode = c("on Wi-Fi · MQTT", "32") if b.get("connected") else c("MQTT · not connected", "33")
    else:
        mode = c("setup/AP mode · UDP", "33") if b["ap_mode"] else c("on Wi-Fi · UDP", "32")
    power = "?" if b["on"] is None else (c("ON", "32") if b["on"] else c("off", "90"))
    color = ":".join(map(str, b["rgb"])) if b["rgb"] else "?"
    print(f"  {c(b['mac'] or '??:??', '1')}  {b['ip'] or '?':<15} {b['model'] or '?':<10} "
          f"fw {b['version'] or '?':<9} {mode}")
    ssid = b.get("ssid") or bulb_ssid(b)
    rssi = b.get("rssi")
    if ssid or rssi is not None:
        sig = (c(f"{rssi} dBm ({signal_label(rssi)})", "33" if rssi < -75 else "32")
               if rssi is not None else "?")
        print(f"    Wi-Fi {c(repr(ssid) if ssid else 'unknown network', '1')}   signal {sig}"
              f"{c('  - weak signal can cause dropped commands; move it closer to the router', '90') if rssi is not None and rssi < -75 else ''}")
    cyc = CYCLES.of(b)
    fx = (f"   custom cycle {c(PALETTES[cyc['palette']][0], '35')} every {fmt_duration(cyc['period'])}" if cyc
          else f"   effect {c(EFFECTS[b['effect']][0], '35')}" if b.get("effect") else "")
    print(f"    power {power}   brightness {val(b['brightness'], '%')}   temp {val(b['temp'])}   "
          f"color {color} {swatch(b['rgb'])}{fx}")


# ---------------------------------------------------------------------------
# Wi-Fi setup (bulb must be in setup mode and this Mac joined to its network)
# ---------------------------------------------------------------------------

RC4_KEY = b"MTlCaWppbmdTaGFuZ2hhaVdpU2VuZ2xlZEZpMjBBQUJBU0U2NA=="  # from the Sengled app


def rc4(data, key=RC4_KEY):
    S, j = list(range(256)), 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) % 256
        S[i], S[j] = S[j], S[i]
    i = j = 0
    out = bytearray()
    for byte in data:
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        out.append(byte ^ S[(S[i] + S[j]) % 256])
    return bytes(out)


def config_msg(name, payload=None):
    return json.dumps({"name": name, "totalStep": 1, "curStep": 1,
                       "payload": payload or {}}).encode()


def config_req(s, ip, name, payload=None, timeout=5):
    s.settimeout(timeout)
    s.sendto(config_msg(name, payload), (ip, PORT))
    data, _ = s.recvfrom(8192)
    return json.loads(data.decode(errors="ignore"))


def bulb_wifi_list(ip=AP_IP):
    """Ask the bulb which networks it can see (it only does 2.4 GHz)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        config_req(s, ip, "startConfigRequest", {"protocol": 1})
        s.sendto(config_msg("scanWifiRequest"), (ip, PORT))
        time.sleep(5)
        resp = config_req(s, ip, "getAPListRequest", timeout=8)
    log("wifi", f"bulb at {ip} sees: {json.dumps(resp.get('payload', {}).get('routers', []))}")
    # Big networks (apartments, offices) show up once per access point; keep the
    # strongest of each name. Only the name is sent to the bulb, so which one doesn't matter.
    best = {}
    for r in resp.get("payload", {}).get("routers", []):
        name = r.get("ssid", "")
        r = {**r, "aps": best.get(name, {}).get("aps", 0) + 1}
        if name not in best or int(r.get("signal", 0) or 0) > int(best[name].get("signal", 0) or 0):
            best[name] = r
        else:
            best[name]["aps"] = r["aps"]
    return sorted(best.values(), key=lambda r: -int(r.get("signal", 0) or 0))


def send_credentials(ssid, password, ip, callback_host):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            hs = config_req(s, ip, "startConfigRequest", {"protocol": 1})
        except (OSError, ValueError) as e:
            err(f"bulb not answering at {ip} ({e}).")
            info("Put it in setup mode and join its 'Sengled Wi-Fi Bulb_…' network first.")
            return None
        mac = norm_mac(hs.get("payload", {}).get("mac"))
        info(f"bulb {mac or '?'} ready for setup")
        log("wifi", f"sending ssid {ssid!r}, password length {len(password)}"
                    f"{', has non-letter/digit characters' if re.search(r'[^A-Za-z0-9]', password) else ''}")
        info(f"call-home address: http://{callback_host}:{CALLBACK_PORT}")
        params = {"name": "setParamsRequest", "totalStep": 1, "curStep": 1, "payload": {
            "userID": "618",
            "appServerDomain": f"http://{callback_host}:{CALLBACK_PORT}/life2/device/accessCloud.json",
            "jbalancerDomain": f"http://{callback_host}:{CALLBACK_PORT}/jbalancer/new/bimqtt",
            "timeZone": time.tzname[0] if time.tzname else "UTC",
            "routerInfo": {"ssid": ssid, "password": password or ""},
        }}
        s.sendto(base64.b64encode(rc4(json.dumps(params).encode())), (ip, PORT))
        s.settimeout(5)
        try:
            raw = s.recvfrom(8192)[0].decode(errors="ignore")
            try:
                resp = json.loads(raw)
            except ValueError:
                resp = json.loads(rc4(base64.b64decode(raw)).decode(errors="ignore"))
            if resp.get("payload", {}).get("result") is False:
                err("bulb rejected the credentials")
                return None
            ok_("credentials accepted")
        except socket.timeout:
            ok_("credentials sent (no ack - normal on some firmware)")
        except Exception:
            ok_("credentials sent (ack unreadable)")
        try:
            config_req(s, ip, "endConfigRequest", timeout=3)
        except Exception:
            pass
    return mac or "?"


def wait_left_setup(ip):
    """After sending credentials: True once the bulb stops answering on its setup network."""
    info("waiting for the bulb to leave setup mode ...")
    misses = 0
    for sec in range(40):
        misses = 0 if probe(ip, timeout=0.8) else misses + 1
        if misses >= 4:  # one missed reply can just be the bulb busy applying config
            ok_(f"bulb left its setup network after ~{sec + 1}s.")
            return True
        time.sleep(1)
    warn("bulb is still in setup mode - wrong password, 5 GHz-only network, or out of range?")
    return False


def is_own_ip(host):
    """True if `host` is one of this computer's addresses, now or on a network seen before."""
    if not is_ip(host):
        return True  # a hostname: can't tell, so treat it as this machine (old behavior)
    data = _read_cache_file()
    own = {str(i.ip) for _, i in interfaces()} | set(data.get("_mac_ips", {}).values())
    return host.startswith("127.") or host in own or host == data.get("_lan_ip")


def handoff_to_server(ssid, password, ip, server):
    """Wi-Fi setup for a bulb that will belong to another computer running this tool
    (e.g. a Raspberry Pi): point the bulb at it, confirm it leaves setup mode, done."""
    info(f"{server} isn't this computer, so the bulb will connect to the server there "
         "(e.g. a Raspberry Pi running this tool).")
    try:
        mac = send_credentials(ssid, password, ip, server)
        if not mac or not wait_left_setup(ip):
            return None
    except KeyboardInterrupt:
        print()
        return None
    ok_(f"the bulb is joining '{ssid}' and will connect to the server at {server} within ~1 min.")
    info("Look for it in that server's web app. This computer goes back to its usual Wi-Fi "
         "by itself (or rejoin it from the Wi-Fi menu).")
    return {"mac": mac, "remote": server, "ssid": ssid}


def wifi_handoff(ssid, password="", ip=AP_IP, callback_host=None, switch_back=True, wait=120):
    """Give a bulb in setup mode new Wi-Fi credentials, then follow it onto that network.
    Returns the bulb dict once it's reachable, else None.

    callback_host must be this Mac's IP *on the target network*: right after joining,
    the bulb calls http://callback_host:57542/... to ask for its MQTT server. That call
    is also how we learn the bulb's new IP.

    callback_host can also be another computer running this tool (e.g. a Raspberry Pi
    hosting the bulbs' Wi-Fi at 10.42.0.1). Then the bulb is that server's: this Mac
    sends the credentials, stays on its own network, and returns {"mac", "remote", "ssid"}."""
    callback_host = callback_host or mac_ip_for_ssid(ssid) or last_lan_ip()
    if not callback_host:
        warn("don't know this Mac's address on the target network - the bulb's call-home will fail.")
        info("Fix: join the target network first and open this tool once, then come back.")
    if callback_host and not is_own_ip(callback_host):
        return handoff_to_server(ssid, password, ip, callback_host)
    hub = start_hub(quiet=True)
    own_cb = None
    if not (hub and hub.callback) and not port_in_use(CALLBACK_PORT):
        own_cb = CallbackServer(callback_host or "127.0.0.1")
    callback = (hub.callback if hub and hub.callback else own_cb)
    since = time.time()
    try:
        mac = send_credentials(ssid, password, ip, callback_host or "127.0.0.1")
        if not mac:
            return None

        if not wait_left_setup(ip):
            return None

        if not switch_back:
            info(f"Switch this Mac to '{ssid}', then open this tool there.")
            return None
        if not join_wifi(ssid, password):
            warn(f"join '{ssid}' on this Mac yourself.")
        now_ip = current_lan_ip()
        if callback_host and now_ip and callback_host != now_ip:
            warn(f"this Mac is now {now_ip}, not {callback_host} - the bulb's call-home will miss. "
                 "Run Wi-Fi setup again from here if it doesn't show up.")
        remember_lan_ip()

        info(f"watching for the bulb on '{ssid}' (up to {wait}s; Ctrl-C to stop) ...")
        start, announced = time.time(), False
        while time.time() - start < wait:
            hit = callback.bulb_ip(since) if callback else None
            if hit and not announced:
                ok_(f"bulb called home from {hit} - it's on '{ssid}'.")
                announced = True
                if hub:
                    info("waiting for it to connect to the MQTT server ...")
            # Only signs of life from after the setup count: the bulb was probably
            # connected (on its old network) earlier in this session.
            fresh = hub and mac != "?" and hub.changed_since(mac, since)
            if hub and mac != "?" and (fresh or (hit and hit in mqtt_peers())):
                b = hub.bulb(mac)
                b["ip"] = hit or b["ip"]
                ok_("bulb is connected and ready.")
                save_cache(b)
                remember_bulb_ssid(b, ssid)
                return b
            if hit:
                b = probe(hit, timeout=1.0)
                if b:
                    ok_(f"bulb answers UDP at {hit}.")
                    save_cache(b)
                    remember_bulb_ssid(b, ssid)
                    return b
            time.sleep(2)
        if announced and not hub:
            warn("the bulb joined but needs an MQTT server to be controlled.")
            info("Run this tool with the repo's .venv so it can start one.")
        else:
            err(f"the bulb didn't show up on '{ssid}' within {wait}s.")
            info("If its setup network 'Sengled Wi-Fi Bulb_…' is back, it couldn't join:")
            info("check the password (case-sensitive) and that the network is 2.4 GHz WPA2.")
        return None
    except KeyboardInterrupt:
        print()
        return None
    finally:
        if own_cb:
            own_cb.close()


# ---------------------------------------------------------------------------
# Target resolution (CLI one-shots)
# ---------------------------------------------------------------------------

def quick_find(mac=None):
    """Cheap UDP checks only (no sweep): last-known IP, then the setup AP."""
    mac = norm_mac(mac) or last_mac()
    cached = load_cache().get(mac, {}) if mac else {}
    ips = [cached.get("ip")] if cached.get("via", "udp") == "udp" else []
    for ip in filter(None, ips + [AP_IP]):
        b = probe(ip, timeout=0.6)
        if b and (not mac or b["mac"] in (None, mac) or ip == AP_IP):
            return b
    return None


def resolve(ip=None, mac=None):
    """Pick the bulb a CLI command should talk to. Returns a bulb dict or None."""
    if ip:
        b = probe(ip)
        if not b:
            err(f"no bulb answered at {ip}:{PORT}")
        return b
    want = norm_mac(mac) or last_mac()
    cached = load_cache().get(want, {}) if want else {}
    # A bulb we last reached over MQTT: use the running MQTT server if there is one.
    hub = start_hub(own_servers=False, quiet=True) if port_in_use(MQTT_PORT) else None
    if hub and want and cached.get("via") == "mqtt":
        return hub.bulb(want)
    b = quick_find(norm_mac(mac))
    if b and (not mac or b["mac"] == norm_mac(mac)):
        return b
    # Not on UDP, but a bulb is connected to the MQTT server: address it by MAC.
    if hub and mqtt_peers():
        macs = hub.known_macs()
        target = norm_mac(mac) or (macs[0] if len(macs) == 1 else None)
        if target:
            return hub.bulb(target)
        warn("a bulb is connected over MQTT - say which one with --mac")
    found = scan()
    if mac and norm_mac(mac) in found:
        return found[norm_mac(mac)]
    if not mac and len(found) == 1:
        return next(iter(found.values()))
    if found:
        warn("several bulbs answered - pick one with --mac or --ip:")
        for f in found.values():
            print_bulb(f)
        return None
    if cached.get("via") == "mqtt":
        err(f"{want} is set up for MQTT, but no MQTT server is running.")
        info("Start one in another terminal:  python3 sengled_ctl.py serve")
        info("(or just use the TUI: python3 sengled_ctl.py)")
        return None
    explain_nothing_found()
    return None


def explain_nothing_found():
    err("no bulb answered.")
    ifs = interfaces()
    if not ifs:
        info("this computer has no IPv4 network address.")
        return
    for name, i in ifs:
        info(f"this computer: {name} {i}")
    if ipv6_only():
        ipv6_only_help()
    elif not on_bulb_ap():
        info("Bulb in setup mode (blinking)? Join its 'Sengled Wi-Fi Bulb_…' Wi-Fi network.")
        info("Bulb set up on Wi-Fi with this tool? It connects to the MQTT server here")
        info("within ~1 min - keep the TUI open (or run `serve`) and press [r].")
        info("Public/apartment Wi-Fi (e.g. WhiteSky) blocks device-to-device traffic;")
        info("use a home router or phone hotspot that both devices join.")


# ---------------------------------------------------------------------------
# TUI
# ---------------------------------------------------------------------------

def key(k):
    return c(f"[{k}]", "1;36")


MENU = f"""
  {key('s')} scan for bulbs     {key('1-9')} pick bulb       {key('i')} enter IP manually
  {key('o')} on   {key('f')} off       {key('c')} color             {key('b')} brightness
  {key('t')} white temperature  {key('e')} effects (color cycle…)  {key('x')} raw command
  {key('w')} Wi-Fi setup (bulb in setup mode)     {key('R')} reboot   {key('Z')} factory reset
  {key('d')} fade / sunrise     {key('a')} all bulbs at once   {key('n')} stats & ping
  {key('r')} refresh   {key('q')} quit
"""


def header(bulb, found):
    remember_lan_ip()
    if TTY:
        print("\033[2J\033[H", end="")
    print(c(" Sengled control ", "1;7") + "  " + c("local only · no cloud", "90"))
    ssid = wifi_ssid()
    nets = ", ".join(f"{n} {i}" for n, i in interfaces()) or "no network"
    print(f"  network : {nets}{f' ({ssid})' if ssid else ''}"
          f"{c('  ← bulb setup AP', '33') if on_bulb_ap() else ''}"
          f"{c('  ← IPv6-only, cannot see bulbs (scan for help)', '33') if ipv6_only() else ''}")
    if HUB:
        peers = mqtt_peers()
        state = (c(f"{len(peers)} bulb(s) connected ({', '.join(peers)})", "32") if peers
                 else c("waiting for bulbs (they reconnect within ~1 min)", "90"))
        print(f"  mqtt    : server on :{MQTT_PORT} · {state}")
    else:
        print(f"  mqtt    : {c('off - run inside the repo with its .venv to control bulbs on Wi-Fi', '90')}")
    if found:
        print("  bulbs   :")
        for n, b in enumerate(found, 1):
            mark = c("▶", "32") if bulb and b["mac"] == bulb["mac"] and b["via"] == bulb["via"] else " "
            off = b["via"] == "mqtt" and not b.get("connected")
            net = "setup mode" if b.get("ap_mode") else (b.get("ssid") or bulb_ssid(b) or "?")
            print(f"   {mark} {n}. {b['mac'] or '?':<17}  {b['ip'] or '?':<15} {b['via'].upper():<5} "
                  f"{b['model'] or '':<8} {net[:20]:<20}{c('  offline', '90') if off else ''}")
    print()
    if bulb:
        print_bulb(bulb)
    else:
        info(f"no bulb selected - press {key('s')} to scan")
    print(MENU)


def ask(prompt):
    try:
        return input(f"  {prompt}").strip()
    except EOFError:
        raise KeyboardInterrupt


def merge(found, new):
    """Add/replace bulbs in the list, keyed by (mac or ip, via)."""
    k = lambda b: (b["mac"] or b["ip"], b["via"])  # noqa: E731
    out = {k(b): b for b in found}
    out.update({k(b): b for b in new})
    return list(out.values())


def tui(ip=None, mac=None):
    print(c("  looking for bulbs ...", "90"))
    start_hub()
    found = HUB.bulbs() if HUB else []
    b = probe(ip) if ip else quick_find(mac)
    if b:
        found = merge(found, [b])
    want = norm_mac(mac) or last_mac()
    bulb = b or next((x for x in found if x["mac"] == want), found[0] if found else None)
    pause = False
    try:
        while True:
            if pause:
                ask(c("press Enter ", "90"))
            if HUB:
                found = merge(found, HUB.bulbs())
            bulb = refresh(bulb) if bulb and bulb["via"] == "mqtt" else bulb
            header(bulb, found)
            choice = ask("> ")
            pause = True
            try:
                if choice in ("q", "quit", "exit"):
                    return 0
                if choice in ("", "r"):
                    bulb = refresh(bulb)
                    pause = False
                elif choice == "s":
                    udp_found = list(scan().values())
                    found = merge(found, udp_found + (HUB.bulbs() if HUB else []))
                    if found:
                        bulb = next((x for x in found if bulb and x["mac"] == bulb["mac"]), found[0])
                        save_cache(bulb)
                        pause = False
                    else:
                        explain_nothing_found()
                elif choice.isdigit():
                    n = int(choice)
                    if 1 <= n <= len(found):
                        bulb = refresh(found[n - 1])
                        save_cache(bulb)
                        pause = False
                    else:
                        err("no bulb with that number")
                elif choice == "i":
                    target = ask(f"bulb IP [{AP_IP}]: ") or AP_IP
                    nb = probe(target)
                    if nb:
                        bulb, found = nb, merge(found, [nb])
                        save_cache(nb)
                        pause = False
                    else:
                        err(f"no bulb answered at {target}:{PORT}")
                elif choice == "n":
                    if not bulb:
                        err("pick a bulb first")
                    else:
                        print_stats(bulb)
                        if ask(f"  ping it? {key('Y')}/n: ").lower() in ("", "y", "yes"):
                            print_ping(bulb)
                elif choice == "w":
                    nb = wifi_menu()
                    if nb:
                        bulb, found = nb, merge(found, [nb])
                elif choice == "a":
                    k = ask("all bulbs - [o]n [f]off [c]olor [b]rightness [t]emp [e]ffect fa[d]e: ")
                    if k not in ("o", "f", "c", "b", "t", "e", "d"):
                        raise ValueError(f"unknown choice {k!r}")
                    control_all([x for x in found if x["via"] == "udp" or x.get("connected")], k)
                    bulb = refresh(bulb)
                elif not bulb:
                    err(f"no bulb selected - {key('s')} scan or {key('i')} enter IP")
                elif choice in ("o", "f", "c", "b", "t", "e", "d", "x", "R", "Z"):
                    control(bulb, choice)
                    if choice not in ("R", "Z"):
                        bulb = refresh(bulb)
                else:
                    err(f"unknown choice {choice!r}")
            except ValueError as e:
                err(str(e))
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        CYCLES.stop_all()
        if HUB:
            HUB.close()


def action(choice, via="udp"):
    """Ask for any value a control key needs, then return fn(bulb) that applies it.
    Splitting the prompt from the action lets [a] apply one choice to every bulb."""
    if choice == "o":
        return lambda b: set_power(b, True)
    if choice == "f":
        return lambda b: set_power(b, False)
    if choice == "c":
        print("  " + "  ".join(f"{swatch(v, 2)} {k}" for k, v in PRESETS.items()))
        rgb = parse_color(ask("preset, #RRGGBB or R G B: ").split())
        return lambda b: set_color(b, rgb)
    if choice == "b":
        n = int(ask("brightness 0-100: "))
        return lambda b: set_brightness(b, n)
    if choice == "t":
        n = int(ask("temperature 0 (warm) - 100 (cool): "))
        return lambda b: set_temp(b, n)
    if choice == "e":
        names = list(EFFECTS)
        for n, name in enumerate(names, 1):
            print(f"   {n}. {EFFECTS[name][0]}")
        print(f"   {len(names) + 1}. Custom color cycle (adjustable speed, runs from this computer)")
        print("   0. stop the running effect / custom cycle")
        pick = ask("effect: ")
        if pick == "0":
            def stop(b):
                return stop_cycle(b) if CYCLES.of(b) else set_effect(b, None)
            return stop
        if pick == str(len(names) + 1):
            pals = list(PALETTES)
            print("   palettes: " + "  ".join(f"{i}. {PALETTES[p][0]}" for i, p in enumerate(pals, 1)))
            pp = ask("palette [1]: ") or "1"
            palette = pals[int(pp) - 1] if pp.isdigit() and 1 <= int(pp) <= len(pals) else pp.lower()
            period = float(ask(f"seconds per full loop ({CYCLE_MIN_PERIOD}-{CYCLE_MAX_PERIOD}, "
                               "lower = faster) [60]: ") or 60)
            spread = ask("several bulbs: spread colors across them? [y/N] ").lower() == "y"

            def cycle(b):
                return start_cycle([b], palette, period, spread)
            cycle.group = lambda bulbs: start_cycle(bulbs, palette, period, spread)
            return cycle
        name = names[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(names) else effect_name(pick)
        return lambda b: set_effect(b, name)
    if choice == "d":
        print("   presets: 1. Sunrise (1→100%, warm→daylight, 10 min)   2. Bedtime (→1% warm, 15 min, then off)")
        print("            3. Quick fade out (→1%, 30 s)   or Enter for custom")
        pick = ask("fade: ")
        if pick == "1":
            return lambda b: set_fade(b, 1, 100, 600, 0, 75)
        if pick == "2":
            return lambda b: set_fade(b, b.get("brightness") or 100, 1, 900, 0, 0, off_at_end=True)
        if pick == "3":
            return lambda b: set_fade(b, b.get("brightness") or 100, 1, 30)
        start = int(ask("start brightness 0-100: "))
        end = int(ask("end brightness 0-100: "))
        secs = int(ask("duration in seconds: "))
        t = ask("white temperature start end (0-100 0-100, blank = keep): ").split()
        t1, t2 = (int(t[0]), int(t[1])) if len(t) == 2 else (None, None)
        off = ask("turn off at the end? [y/N] ").lower() == "y"
        return lambda b: set_fade(b, start, end, secs, t1, t2, off)
    if choice == "x":
        hint = ('{"type":"switch","value":"1"}' if via == "mqtt"
                else '{"func":"get_device_mac","param":{}}')
        text = ask(f"JSON, e.g. {hint} : ")
        return lambda b: send_raw(b, text)
    if choice == "R":
        return reboot
    if choice == "Z":
        if ask(c("factory reset - the bulb forgets its Wi-Fi. Type RESET: ", "31")) == "RESET":
            return factory_reset
        return None
    raise ValueError(f"unknown choice {choice!r}")


def control(b, choice):
    fn = action(choice, b["via"])
    if fn:
        fn(b)


def control_all(bulbs, choice):
    """Apply one control to every bulb; returns how many succeeded."""
    if not bulbs:
        err("no bulbs to control")
        return 0
    fn = action(choice, bulbs[0]["via"])
    if fn is not None and hasattr(fn, "group"):  # custom cycle: one synced group
        good = fn.group(bulbs)
        return len(bulbs) if good else 0
    done = 0
    for b in bulbs:
        print(f"  {c(b['mac'] or b['ip'], '1')}")
        done += bool(fn and fn(b))
    (ok_ if done == len(bulbs) else warn)(f"{done}/{len(bulbs)} bulbs confirmed")
    return done


def wifi_menu():
    print()
    if not on_bulb_ap():
        warn("this computer is not on a bulb's setup network (192.168.8.x).")
        info("Put the bulb in setup mode (it blinks), join 'Sengled Wi-Fi Bulb_…', then retry.")
        if ask("try anyway? [y/N] ").lower() != "y":
            return None
    ssid = ask("Wi-Fi name for the bulb (blank = list what the bulb can see): ")
    if not ssid:
        try:
            info("asking the bulb to scan (~5s) ...")
            routers = bulb_wifi_list()
        except Exception as e:
            err(f"bulb didn't answer: {e}")
            return None
        if not routers:
            err("the bulb sees no networks")
            return None
        for n, r in enumerate(routers, 1):
            aps = f"  ({r['aps']} access points)" if r.get("aps", 1) > 1 else ""
            print(f"   {n:2}. {r.get('ssid') or '(hidden)':<32} signal {r.get('signal', '?')}{aps}")
        pick = ask("number: ")
        if not pick.isdigit() or not 1 <= int(pick) <= len(routers):
            return None
        ssid = routers[int(pick) - 1].get("ssid", "")
    password = ask(f"password for '{ssid}' (blank = open network): ")
    known = mac_ip_for_ssid(ssid) or last_lan_ip()
    info("Server address: this computer's IP on that network, or a Raspberry Pi's (10.42.0.1).")
    cb = ask(f"server address on '{ssid}' [{known or 'unknown'}]: ") or known
    then = ("then switch this computer to it" if not cb or is_own_ip(cb)
            else f"for the server at {cb}")
    if ask(f"send '{ssid}' to the bulb, {then}? [y/N] ").lower() == "y":
        b = wifi_handoff(ssid, password, callback_host=cb)
        return None if b and b.get("remote") else b  # another server's bulb: not ours
    return None


# ---------------------------------------------------------------------------
# serve: keep the MQTT + call-home servers up and log what bulbs report
# ---------------------------------------------------------------------------

def serve():
    if not start_hub():
        err("MQTT needs the repo's .venv (paho-mqtt, amqtt).")
        return 1
    if not HUB.broker:
        warn(f"an MQTT server is already running on :{MQTT_PORT} - just watching it.")
    info("Ctrl-C to stop. Bulbs on Wi-Fi only obey while this runs.")
    shown, peers = 0, None
    try:
        while True:
            now = mqtt_peers()
            if now != peers:
                info(f"connected bulbs: {', '.join(now) or 'none'}")
                peers = now
            with HUB.lock:
                new = HUB.events[shown:]
                shown = len(HUB.events)
            for t, m, k, v in new:
                print(f"  {time.strftime('%H:%M:%S', time.localtime(t))}  {m}  {k} = {v}")
                if k in ("switch", "color", "brightness", "typeCode"):
                    save_cache(HUB.bulb(m))
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        HUB.close()


API_PORT = 8765


class Api:
    """Local JSON API behind the web app (web/). Binds to 127.0.0.1 only.

    GET  /api/state                  network, MQTT server and every known bulb
    POST /api/scan                   UDP scan (setup-mode bulbs, UDP-reachable bulbs)
    POST /api/add      {ip}          add a bulb by IP
    POST /api/control  {ids, action, value}
                       action: power(bool) color([r,g,b]) brightness(n) temp(n)
                               raw(str) reboot reset
    POST /api/rename   {id, name}    nickname (stored in ~/.sengled/lan.json)
    POST /api/forget   {id}
    GET  /api/wifi/networks          networks the bulb can see (Mac on its setup AP)
    POST /api/wifi/setup {ssid, password, callbackHost}  -> {job}
    GET  /api/jobs/<id>              {done, ok, log, bulb}
    """

    def __init__(self):
        self.bulbs = {}   # id -> bulb dict (UDP bulbs we've seen; MQTT bulbs come from HUB)
        self.jobs = {}
        self.lock = threading.Lock()

    @staticmethod
    def bulb_id(b):
        return f"{b['via']}:{b['mac'] or b['ip']}"

    def all_bulbs(self):
        with self.lock:
            out = {self.bulb_id(b): b for b in self.bulbs.values()}
        if HUB:
            out.update({self.bulb_id(b): b for b in HUB.bulbs()})
        return out

    def serialize(self, b):
        cached = load_cache().get(b["mac"], {}) if b.get("mac") else {}
        return {
            "id": self.bulb_id(b), "name": cached.get("name"), "mac": b["mac"], "ip": b["ip"],
            "via": b["via"], "model": b["model"], "version": b["version"], "on": b["on"],
            "brightness": b["brightness"], "temp": b["temp"],
            "rgb": list(b["rgb"]) if b["rgb"] else None,
            "mode": b.get("mode"),
            "effect": b.get("effect"),
            "cycle": CYCLES.of(b),
            "apMode": b["ap_mode"],
            "connected": b.get("connected", True) if b["via"] == "mqtt" else True,
            "ssid": b.get("ssid") or bulb_ssid(b),
            "rssi": b.get("rssi"),
        }

    def state(self):
        return {
            "network": {
                "interfaces": [{"name": n, "cidr": str(i)} for n, i in interfaces()],
                "ssid": wifi_ssid(),
                "onBulbAp": on_bulb_ap(),
                "ipv6Only": ipv6_only(),
                "lanIp": current_lan_ip(),
                "rememberedLanIp": last_lan_ip(),
                "macIpBySsid": mac_ip_for_ssid(),
            },
            "mqtt": {"running": HUB is not None, "available": mqtt_available(),
                     "peers": mqtt_peers() if HUB else []},
            "bulbs": [self.serialize(b) for b in self.all_bulbs().values()],
            "presets": {k: list(v) for k, v in PRESETS.items()},
            "effects": [{"id": n, "label": label} for n, (label, _) in EFFECTS.items()],
            "palettes": [{"id": n, "label": label,
                          "preview": [list(palette_color(n, i / 6)) for i in range(6)]}
                         for n, (label, _) in PALETTES.items()],
            "cycleLimits": {"min": CYCLE_MIN_PERIOD, "max": CYCLE_MAX_PERIOD},
        }

    def remember(self, b):
        save_cache(b)
        if b["via"] == "udp":
            with self.lock:
                self.bulbs[self.bulb_id(b)] = b

    # -- actions -----------------------------------------------------------

    def scan(self):
        remember_lan_ip()
        found = list(scan(verbose=False).values())
        for b in found:
            self.remember(b)
        return {"found": len(found), **self.state()}

    def add(self, body):
        b = probe(str(body.get("ip", "")).strip())
        if not b:
            raise ValueError(f"no bulb answered at {body.get('ip')}:{PORT}")
        self.remember(b)
        return self.serialize(b)

    def control(self, body):
        bulbs = self.all_bulbs()
        ids = body.get("ids") or []
        targets = [bulbs[i] for i in ids if i in bulbs]
        if not targets:
            raise ValueError("no matching bulbs")
        action, value = body.get("action"), body.get("value")
        if action == "cycle":
            return self.cycle(targets, value)
        fns = {
            "power": lambda b: set_power(b, bool(value)),
            "color": lambda b: set_color(b, tuple(int(x) for x in value)),
            "brightness": lambda b: set_brightness(b, int(value)),
            "temp": lambda b: set_temp(b, int(value)),
            "effect": lambda b: set_effect(b, value or None),
            "fade": lambda b: set_fade(
                b, (value or {}).get("from", b.get("brightness") or 100), (value or {}).get("to"),
                (value or {}).get("seconds"), (value or {}).get("tempFrom"), (value or {}).get("tempTo"),
                bool((value or {}).get("offAtEnd"))),
            "raw": lambda b: send_raw(b, str(value)),
            "reboot": reboot,
            "reset": factory_reset,
        }
        if action not in fns:
            raise ValueError(f"unknown action {action!r}")
        if action in ("color",) and (not isinstance(value, list) or len(value) != 3):
            raise ValueError("color needs [r, g, b]")

        results = [None] * len(targets)

        def run(n, b):  # one thread per bulb, so 'all bulbs' changes together
            _sink.lines = []
            try:
                good = bool(fns[action](b))
            except Exception as e:
                err(str(e))
                good = False
            nb = refresh(b) if action not in ("reboot", "reset") else b
            if nb and nb["via"] == "udp":
                self.remember(nb)
            results[n] = {"id": self.bulb_id(b), "ok": good, "log": _sink.lines,
                          "bulb": self.serialize(nb or b)}

        threads = [threading.Thread(target=run, args=(n, b)) for n, b in enumerate(targets)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return {"results": results}

    def cycle(self, targets, value):
        _sink.lines = []
        try:
            if value:
                good = start_cycle(targets, value.get("palette", "rainbow"), value.get("period", 60),
                                   value.get("spread", False))
            else:
                good = all([stop_cycle(b) for b in targets])
        except ValueError as e:
            err(str(e))
            good = False
        log = _sink.lines
        _sink.lines = None
        return {"results": [{"id": self.bulb_id(b), "ok": good, "log": log if n == 0 else [],
                             "bulb": self.serialize(refresh(b) or b)} for n, b in enumerate(targets)]}

    def rename(self, body):
        b = self.all_bulbs().get(body.get("id"))
        if not b or not b.get("mac"):
            raise ValueError("unknown bulb")
        data = _read_cache_file()
        entry = data.setdefault(b["mac"], {"via": b["via"], "ip": b["ip"]})
        name = str(body.get("name") or "").strip()[:40]
        if name:
            entry["name"] = name
        else:
            entry.pop("name", None)
        _write_cache_file(data)
        return self.serialize(b)

    def forget(self, body):
        bid = body.get("id")
        b = self.all_bulbs().get(bid)
        with self.lock:
            self.bulbs.pop(bid, None)
        if b and b.get("mac"):
            data = _read_cache_file()
            data.pop(b["mac"], None)
            if data.get("_last", {}).get("mac") == b["mac"]:
                data.pop("_last")
            _write_cache_file(data)
            if HUB:
                with HUB.lock:
                    HUB.state.pop(b["mac"], None)
        return {"ok": True}

    def wifi_networks(self):
        try:
            routers = bulb_wifi_list()
        except Exception as e:
            raise ValueError(f"the bulb didn't answer ({e}) - is this Mac on its "
                             "'Sengled Wi-Fi Bulb_…' network?")
        return {"networks": [{"ssid": r.get("ssid", ""), "signal": r.get("signal"),
                              "aps": r.get("aps", 1)} for r in routers]}

    def wifi_setup(self, body):
        ssid = str(body.get("ssid") or "").strip()
        if not ssid:
            raise ValueError("ssid required")
        job_id = f"{int(time.time() * 1000)}"
        job = self.jobs[job_id] = {"done": False, "ok": False, "log": [], "bulb": None, "remote": None}

        def run():
            _sink.lines = job["log"]
            try:
                b = wifi_handoff(ssid, str(body.get("password") or ""),
                                 callback_host=str(body.get("callbackHost") or "") or None)
                if b and b.get("remote"):  # set up for another server (e.g. a Raspberry Pi)
                    job["remote"] = b["remote"]
                elif b:
                    self.remember(b)
                    job["bulb"] = self.serialize(b)
                job["ok"] = bool(b)
            except Exception as e:
                err(str(e))
            finally:
                job["done"] = True

        threading.Thread(target=run, daemon=True).start()
        return {"job": job_id}

    def job(self, job_id):
        if job_id not in self.jobs:
            raise ValueError("unknown job")
        return self.jobs[job_id]

    # -- HTTP --------------------------------------------------------------

    def _one(self, bulb_id):
        b = self.all_bulbs().get(bulb_id)
        if not b:
            raise ValueError(f"unknown bulb {bulb_id!r}")
        return b

    def stats(self, path):
        from urllib.parse import parse_qs, urlparse
        return bulb_stats(self._one((parse_qs(urlparse(path).query).get("id") or [""])[0]))

    def ping(self, body):
        return ping_bulb(self._one(str(body.get("id", ""))), int(body.get("count") or 5))

    def handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _route(self, method):
                path = self.path.split("?")[0].rstrip("/")
                body = {}
                if method == "POST":
                    n = int(self.headers.get("Content-Length", 0) or 0)
                    try:
                        body = json.loads(self.rfile.read(n) or b"{}")
                    except ValueError:
                        return self._send(400, {"error": "invalid JSON"})
                routes = {
                    ("GET", "/api/state"): lambda: api.state(),
                    ("POST", "/api/scan"): lambda: api.scan(),
                    ("POST", "/api/add"): lambda: api.add(body),
                    ("POST", "/api/control"): lambda: api.control(body),
                    ("POST", "/api/rename"): lambda: api.rename(body),
                    ("POST", "/api/forget"): lambda: api.forget(body),
                    ("GET", "/api/wifi/networks"): lambda: api.wifi_networks(),
                    ("POST", "/api/wifi/setup"): lambda: api.wifi_setup(body),
                    ("GET", "/api/stats"): lambda: api.stats(self.path),
                    ("POST", "/api/ping"): lambda: api.ping(body),
                }
                fn = routes.get((method, path))
                if not fn and method == "GET" and path.startswith("/api/jobs/"):
                    fn = lambda: api.job(path.rsplit("/", 1)[1])  # noqa: E731
                if not fn:
                    return self._send(404, {"error": "not found"})
                try:
                    code, result = 200, fn()
                except ValueError as e:
                    code, result = 400, {"error": str(e)}
                except Exception as e:
                    code, result = 500, {"error": f"{type(e).__name__}: {e}"}
                try:
                    self._send(code, result)
                except (BrokenPipeError, ConnectionResetError):
                    # The web app gave up waiting (its proxy allows 30 s): nobody to answer.
                    log("api", f"{path}: finished after the web app stopped waiting")

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def log_message(self, *a):
                pass

        return Handler


def serve_api(port=API_PORT):
    remember_lan_ip()
    if not start_hub():
        warn("MQTT unavailable - only setup-mode / UDP bulbs will work "
             "(run with the repo's .venv for Wi-Fi bulbs)")
    api = Api()
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), api.handler())
    except OSError as e:
        err(f"can't listen on 127.0.0.1:{port}: {e} (is the API already running?)")
        return 1
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ok_(f"API on http://127.0.0.1:{port}  (web app: cd web && npm run dev)")
    b = quick_find()
    if b:
        api.remember(b)
    try:
        while True:  # persist what bulbs report, so state survives restarts
            time.sleep(30)
            if HUB:
                HUB.save_state()
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        CYCLES.stop_all()
        httpd.shutdown()
        if HUB:
            HUB.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def relaunch_with_venv():
    """MQTT needs paho-mqtt + amqtt; if this python lacks them, re-run with the repo's .venv."""
    venv = os.path.join(HERE, ".venv", "bin", "python")
    if (not mqtt_available() and os.path.exists(venv)
            and os.path.realpath(sys.executable) != os.path.realpath(venv)
            and not os.environ.get("SENGLED_CTL_NO_VENV")):
        os.environ["SENGLED_CTL_NO_VENV"] = "1"
        os.execv(venv, [venv, os.path.abspath(__file__)] + sys.argv[1:])


def run_cycle(bulbs, args):
    """`cycle [PALETTE] [SECONDS_PER_LOOP] [spread]` - runs in the foreground until Ctrl-C."""
    palette, period, spread = "rainbow", 60.0, False
    for x in args:
        if x.lower() == "spread":
            spread = True
        elif re.fullmatch(r"\d+(\.\d+)?", x):
            period = float(x)
        else:
            palette = x.lower()
    mqtt_bulbs = [b for b in bulbs if b.get("via") == "mqtt"]
    if mqtt_bulbs and not HUB:
        start_hub()
        if HUB and HUB.broker:
            info("started the MQTT server; Wi-Fi bulbs reconnect within ~1 min ...")
            HUB.wait_for_bulb(mqtt_bulbs[0]["mac"], timeout=90)
    start_cycle(bulbs, palette, period, spread)
    info("cycling - Ctrl-C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        CYCLES.stop_all()
        ok_("stopped")
    return 0


def fade_args(args):
    """fade START END SECONDS [TEMP_START TEMP_END] [off]"""
    off = bool(args) and args[-1].lower() == "off"
    nums = [int(x) for x in (args[:-1] if off else args)]
    if len(nums) not in (3, 5):
        raise ValueError("usage: fade START END SECONDS [TEMP_START TEMP_END] [off]")
    t1, t2 = (nums[3], nums[4]) if len(nums) == 5 else (None, None)
    return nums[0], nums[1], nums[2], t1, t2, off


def run_all(cmd, args):
    """`--all`: every bulb on the running MQTT server plus every bulb answering UDP."""
    bulbs = list(scan(verbose=False).values())
    if port_in_use(MQTT_PORT) and start_hub(own_servers=False, quiet=True):
        bulbs += [b for b in HUB.bulbs() if b["connected"]]
    elif any(v.get("via") == "mqtt" for v in load_cache().values()):
        warn("no MQTT server running - Wi-Fi bulbs skipped (start the TUI or `serve` first)")
    if not bulbs:
        explain_nothing_found()
        return 1
    try:
        if cmd in ("on", "off"):
            fn = lambda b: set_power(b, cmd == "on")  # noqa: E731
        elif cmd in PRESETS or cmd == "color":
            rgb = PRESETS[cmd] if cmd in PRESETS else parse_color(args)
            fn = lambda b: set_color(b, rgb)  # noqa: E731
        elif cmd == "brightness":
            n = int(args[0])
            fn = lambda b: set_brightness(b, n)  # noqa: E731
        elif cmd == "temp":
            n = int(args[0])
            fn = lambda b: set_temp(b, n)  # noqa: E731
        elif cmd == "effect":
            arg = " ".join(args)
            name = None if arg.lower() in ("off", "stop", "none", "0") else effect_name(arg)
            fn = lambda b: set_effect(b, name)  # noqa: E731
        elif cmd == "fade":
            fa = fade_args(args)
            fn = lambda b: set_fade(b, *fa)  # noqa: E731
        elif cmd == "cycle":
            return run_cycle(bulbs, args)
        elif cmd == "status":
            for b in bulbs:
                print_bulb(b)
            return 0
        else:
            err(f"--all supports on, off, <preset>, color, brightness, temp, effect, fade, cycle, status (not {cmd!r})")
            return 2
        done = 0
        for b in bulbs:
            print(f"  {c(b['mac'] or b['ip'], '1')}")
            done += bool(fn(b))
        (ok_ if done == len(bulbs) else warn)(f"{done}/{len(bulbs)} bulbs confirmed")
        return 0 if done == len(bulbs) else 1
    except IndexError:
        err(f"{cmd}: missing value, e.g. `{cmd} 50`")
        return 2
    except ValueError as e:
        err(f"{cmd}: {e}")
        return 2
    finally:
        if HUB:
            HUB.close()


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Scan, control and set up Sengled Wi-Fi bulbs locally (UDP + MQTT).",
        epilog="No command = interactive TUI. Commands: scan, status, on, off, colors, "
               "<preset>, color R G B|#hex, brightness N, temp N, effect NAME|off, effects, "
               "fade START END SECONDS [T1 T2] [off], cycle [PALETTE] [SECONDS] [spread], "
               "raw JSON, reboot, reset, stats, ping [N], "
               "wifi --ssid NAME [--password PW], serve")
    p.add_argument("command", nargs="?")
    p.add_argument("args", nargs="*")
    p.add_argument("--ip", help="bulb IP (UDP, skip discovery)")
    p.add_argument("--mac", help="bulb MAC, to pick one when several answer")
    p.add_argument("--all", action="store_true",
                   help="apply the command to every bulb (MQTT server must be running for Wi-Fi bulbs)")
    p.add_argument("--ssid", help="wifi: network name to give the bulb")
    p.add_argument("--password", default="", help="wifi: network password (blank = open)")
    p.add_argument("--callback", help="wifi: the bulb's server address on the target network: "
                                      "this computer's IP (default: the last one seen, e.g. "
                                      "172.20.10.3) or a Raspberry Pi's (10.42.0.1)")
    a = p.parse_args(argv)
    cmd = (a.command or "").lower()
    global LOG_ROLE
    LOG_ROLE = (cmd if cmd in ("api", "serve") else
                "tui" if not cmd and sys.stdin.isatty() else "cli")
    log("start", f"sengled_ctl {' '.join(argv if argv is not None else sys.argv[1:]) or '(tui)'} "
                 f"pid {os.getpid()}")

    if not cmd:
        if sys.stdin.isatty():
            return tui(a.ip, a.mac)
        cmd = "status"
    if cmd == "effects":
        for name, (label, _) in EFFECTS.items():
            print(f"  {name:<12} {label}")
        return 0
    if cmd == "colors":
        for k, v in PRESETS.items():
            print(f"  {swatch(v)} {k}")
        return 0
    remember_lan_ip()
    if cmd == "serve":
        return serve()
    if cmd == "api":
        return serve_api(int(a.args[0]) if a.args else API_PORT)
    if cmd == "scan":
        found = list(scan().values())
        if start_hub(own_servers=False, quiet=True):
            found += HUB.bulbs()
        if not found:
            explain_nothing_found()
            return 1
        for b in found:
            print_bulb(b)
        return 0
    if cmd == "wifi":
        ssid = a.ssid or (a.args[0] if a.args else None)
        if not ssid:
            err("wifi needs --ssid NAME [--password PW]")
            return 2
        try:
            return 0 if wifi_handoff(ssid, a.password, ip=a.ip or AP_IP,
                                     callback_host=a.callback) else 1
        finally:
            if HUB:
                HUB.close()

    if a.all:
        return run_all(cmd, a.args)
    try:
        bulb = resolve(a.ip, a.mac)
        if not bulb:
            return 1
        save_cache(bulb)
        if cmd == "status":
            print_bulb(bulb)
            return 0
        if cmd == "stats":
            print_stats(bulb)
            return 0
        if cmd == "ping":
            return 0 if print_ping(bulb, int(a.args[0]) if a.args else 5) else 1
        if cmd in ("on", "off"):
            good = set_power(bulb, cmd == "on")
        elif cmd in PRESETS:
            good = set_color(bulb, PRESETS[cmd])
        elif cmd == "color":
            good = set_color(bulb, parse_color(a.args))
        elif cmd == "brightness":
            good = set_brightness(bulb, int(a.args[0]))
        elif cmd == "temp":
            good = set_temp(bulb, int(a.args[0]))
        elif cmd == "effect":
            arg = " ".join(a.args)
            good = set_effect(bulb, None if arg.lower() in ("off", "stop", "none", "0") else arg)
        elif cmd == "fade":
            good = set_fade(bulb, *fade_args(a.args))
        elif cmd == "cycle":
            return run_cycle([bulb], a.args)
        elif cmd == "raw":
            good = send_raw(bulb, " ".join(a.args))
        elif cmd == "reboot":
            good = reboot(bulb)
        elif cmd == "reset":
            good = factory_reset(bulb)
        else:
            err(f"unknown command {cmd!r}")
            p.print_help()
            return 2
        return 0 if good else 1
    except IndexError:
        err(f"{cmd}: missing value, e.g. `{cmd} 50`")
        return 2
    except ValueError as e:
        err(f"{cmd}: {e}")
        return 2
    finally:
        if HUB:
            HUB.close()


if __name__ == "__main__":
    relaunch_with_venv()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
