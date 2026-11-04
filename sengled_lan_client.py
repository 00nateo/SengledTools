#!/usr/bin/env python3
"""
sengled_lan_client.py - control a Sengled bulb on your LAN by MAC address.

No cloud, no MQTT broker, no TLS. Finds the bulb's IP from its MAC
(cache -> broadcast -> subnet sweep -> ARP) and sends plain UDP/JSON
commands to port 9080.

Examples:
    python sengled_lan_client.py 7C:87:CE:AC:EF:B8 on
    python sengled_lan_client.py 7C:87:CE:AC:EF:B8 off
    python sengled_lan_client.py 7C:87:CE:AC:EF:B8 --green
    python sengled_lan_client.py 7C:87:CE:AC:EF:B8 --color 0 255 0
    python sengled_lan_client.py 7C:87:CE:AC:EF:B8 --brightness 50
    python sengled_lan_client.py 7C:87:CE:AC:EF:B8 --temp 30
    python sengled_lan_client.py 7C:87:CE:AC:EF:B8 status
    python sengled_lan_client.py 7C:87:CE:AC:EF:B8 raw '{"func":"set_device_switch","param":{"switch":1}}'

Presets: white red green blue yellow cyan magenta
"""

import argparse
import ipaddress
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

PORT = 9080
AP_IP = "192.168.8.1"
HOME = Path.home() / ".sengled"
PRESETS = {
     "white": (255, 255, 255), "red": (255, 0, 0), "green": (0, 255, 0),
     "blue": (0, 0, 255), "yellow": (255, 255, 0), "cyan": (0, 255, 255),
     "magenta": (255, 0, 255),
}


def norm(mac: str) -> str:
    h = re.sub(r"[:-]", "", mac.strip().upper())
    if not re.fullmatch(r"[0-9A-F]{12}", h):
        raise SystemExit(f"invalid MAC: {mac}")
    return ":".join(h[i:i+2] for i in range(0, 12, 2))


def mac_match(a: str, b: str) -> bool:
    try:
        return norm(a) == norm(b)
    except Exception:
        return False


def cache_load() -> dict:
    f = HOME / "lan.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            return {}
    return {}


def cache_save(mac: str, ip: str) -> None:
    HOME.mkdir(exist_ok=True, parents=True)
    c = cache_load()
    c[norm(mac)] = {"ip": ip, "ap": ip == AP_IP}
    (HOME / "lan.json").write_text(json.dumps(c, indent=2))


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def query(ip: str, func: str, param=None, timeout=1.5, attempts=2) -> Optional[dict]:
    for _ in range(attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                s.sendto(json.dumps({"func": func, "param": param or {}}).encode(),
                         (ip, PORT))
                data, _ = s.recvfrom(2048)
                return json.loads(data.decode(errors="ignore"))
        except socket.timeout:
            pass
        except Exception:
            pass
    return None


def reachable(ip: str, ms=400) -> bool:
    try:
        r = subprocess.run(["ping", "-c", "1", "-t", str(ms), ip],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        return r.returncode == 0
    except Exception:
        return False


def broadcast_find(mac: str, timeout=2.0) -> Optional[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.4)
    li = local_ip()
    targets = ["255.255.255.255", ".".join(li.split(".")[:3] + ["255"])]
    msg = json.dumps({"func": "search_devices", "param": {}}).encode()
    try:
        for _ in range(2):
            for t in targets:
                try:
                    sock.sendto(msg, (t, PORT))
                except Exception:
                    pass
            end = time.time() + timeout
            while time.time() < end:
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                except Exception:
                    continue
                try:
                    p = json.loads(data.decode(errors="ignore"))
                except Exception:
                    continue
                res = p.get("result", {})
                if res.get("mac") and mac_match(res["mac"], mac):
                    ip = res.get("ip")
                    if ip and ip not in ("0.0.0.0", ""):
                        return ip
                    if addr[0] and addr[0] != "0.0.0.0":
                        return addr[0]
    finally:
        sock.close()
    return None


def sweep_find(mac: str, net, skip) -> Optional[str]:
    for host in net.hosts():
        h = str(host)
        if h == skip or not reachable(h):
            continue
        r = query(h, "search_devices", timeout=0.15, attempts=1)
        if r and r.get("result", {}).get("mac") and mac_match(r["result"]["mac"], mac):
            return r["result"].get("ip") or h
    return None


def arp_find(mac: str) -> Optional[str]:
    try:
        out = subprocess.run(["arp", "-an"], capture_output=True, text=True, timeout=3)
        for m in re.finditer(r"\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-fA-F:]{17})", out.stdout):
            if mac_match(m.group(2), mac):
                return m.group(1)
    except Exception:
        pass
    return None


def resolve(mac, use_cache=True, use_broadcast=True, use_sweep=True, use_arp=True) -> dict:
    mac = norm(mac)
    if use_cache:
        cip = cache_load().get(mac, {}).get("ip")
        if cip:
            r = query(cip, "search_devices")
            if r and (r.get("result", {}).get("ret") == 0
                      or mac_match(r.get("result", {}).get("mac", ""), mac)):
                return {"ip": cip, "method": "cache", "ap": cip == AP_IP,
                         "version": r.get("result", {}).get("version")}
            if reachable(cip):
                return {"ip": cip, "method": "cache", "ap": cip == AP_IP, "version": None}
    if use_broadcast:
        ip = broadcast_find(mac)
        if ip:
            ver = None
            if ip != AP_IP:
                r = query(ip, "search_devices")
                ver = r.get("result", {}).get("version") if r else None
            return {"ip": ip, "method": "broadcast", "ap": ip == AP_IP, "version": ver}
    if use_sweep:
        try:
            net = ipaddress.ip_network(f"{local_ip()}/24", strict=False)
            ip = sweep_find(mac, net, local_ip())
            if ip:
                return {"ip": ip, "method": "sweep", "ap": ip == AP_IP, "version": None}
        except Exception:
            pass
    if use_arp:
        ip = arp_find(mac)
        if ip:
            return {"ip": ip, "method": "arp", "ap": ip == AP_IP, "version": None}
    return {"ip": None, "method": "none", "ap": False, "version": None}


def send(ip, func, param=None, timeout=3) -> None:
    r = query(ip, func, param, timeout=timeout, attempts=3)
    if r is None:
        print(f"     [x] {func}: no response from {ip}")
    else:
        print(f"     [ok] {func} -> {r.get('result')}")


def main():
    ap = argparse.ArgumentParser(
                 description="Control a Sengled bulb on your LAN by MAC address.")
    ap.add_argument("mac", help="bulb MAC, e.g. 7C:87:CE:AC:EF:B8")
    ap.add_argument("action", nargs="*", default=[],
                      help="on | off | status | reset | <color preset>")
    ap.add_argument("--ip", help="skip resolution and use this IP directly")
    ap.add_argument("--color", nargs=3, type=int, metavar="R G B")
    ap.add_argument("--brightness", type=int)
    ap.add_argument("--temp", type=int, help="color temp 0..100")
    ap.add_argument("--raw", help="send a raw JSON payload")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--no-sweep", action="store_true", help="skip the slow subnet sweep")
    a = ap.parse_args()
    mac = norm(a.mac)

    if a.ip:
        info = {"ip": a.ip, "method": "direct", "ap": a.ip == AP_IP, "version": None}
    else:
        info = resolve(mac, use_cache=not a.no_cache, use_sweep=not a.no_sweep)
    ip = info["ip"]
    if not ip:
        print("could not find the bulb. Make sure it is on your Wi-Fi "
              "(or on its own AP and you are joined to it).")
        sys.exit(1)
    info["ip"] = ip
    if info["method"] != "cache" and not a.no_cache:
        cache_save(mac, ip)
    ap_note = " (AP/provisioning mode)" if info["ap"] else ""
    ver_note = f"  fw {info['version']}" if info.get("version") else ""
    print(f"target {mac} -> {ip}   [{info['method']}]{ap_note}{ver_note}")

    did = False
    presets = [p for p in a.action if p in PRESETS]
    actions = [p for p in a.action if p not in PRESETS]

    if a.raw:
        try:
            obj = json.loads(a.raw)
            send(ip, obj.get("func", ""), param=obj.get("param", {}))
            did = True
        except Exception as e:
            print(f"     [x] raw: {e}")
            did = True

    if a.color:
        send(ip, "set_device_color", {"red": a.color[0], "green": a.color[1], "blue": a.color[2]})
        did = True

    for p in presets:
        r, g, b = PRESETS[p]
        send(ip, "set_device_color", {"red": r, "green": g, "blue": b})
        did = True

    if a.brightness is not None:
        if not 0 <= a.brightness <= 100:
            print("     [x] brightness must be 0..100"); sys.exit(1)
        send(ip, "set_device_switch", {"switch": 1})
        send(ip, "set_device_brightness", {"brightness": a.brightness})
        did = True

    if a.temp is not None:
        if not 0 <= a.temp <= 100:
            print("     [x] temp must be 0..100"); sys.exit(1)
        send(ip, "set_device_switch", {"switch": 1})
        send(ip, "set_device_colortemp", {"colorTemperature": a.temp})
        did = True

    for act in actions:
        if act == "on":
            send(ip, "set_device_switch", {"switch": 1}); did = True
        elif act == "off":
            send(ip, "set_device_switch", {"switch": 0}); did = True
        elif act == "status":
            st = query(ip, "search_devices")
            print("   status:", st.get("result") if st else "no response"); did = True
        elif act == "reset":
            send(ip, "set_factory_mode", {}); did = True
        else:
            print(f"     [x] unknown action: {act}")

    if not did and not a.raw:
        ap.print_help()


if __name__ == "__main__":
    main()
