"""
MAC -> IP resolver for Sengled bulbs over a local network.

Resolution strategy, in order of preference:
   1. cache     - a previously seen MAC->IP mapping on disk (~/.sengled/lan.json)
   2. broadcast - send `search_devices` to 255.255.255.255 + /24 broadcast and
                  match the MAC in each bulb's reply (no firewall/AP isolation)
   3. sweep      - ping the whole /24 one host at a time (works when the router
                   blocks broadcasts or enables client/AP isolation)
   4. arp        - read the OS ARP table as a last resort

The bulb speaks plain UDP/JSON on port 9080; a bulb that is still in AP /
provisioning mode answers at 192.168.8.1 with reported ip "0.0.0.0".
"""

import json
import re
import socket
import time
import subprocess
import ipaddress
from typing import Optional

from sengled.constants import BULB_IP, BULB_PORT
from .utils import normalize_mac_address, get_local_ip, get_config_dir


def _load_lan_cache() -> dict:
    f = get_config_dir() / "lan.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            return {}
    return {}


def _save_lan_cache(mac: str, ip: str) -> None:
    cfg = get_config_dir()
    cfg.mkdir(exist_ok=True)
    cache = _load_lan_cache()
    key = normalize_mac_address(mac)
    cache[key] = {"ip": ip, "ap": ip == BULB_IP}
    (cfg / "lan.json").write_text(json.dumps(cache, indent=2))


def _query(ip: str, func: str, param: Optional[dict] = None,
           timeout: float = 1.5, attempts: int = 2):
    for i in range(attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                s.sendto(
                    json.dumps({"func": func, "param": param or {}}).encode(),
                    (ip, BULB_PORT),
                 )
                data, _ = s.recvfrom(2048)
                return json.loads(data.decode(errors="ignore"))
        except socket.timeout:
            pass
        except Exception:
            pass
    return None


def _reachable(ip: str, timeout: float = 0.4) -> bool:
    try:
        out = subprocess.run(
            ["ping", "-c", "1", f"-t", str(int(timeout * 1000)), ip],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
        )
        return out.returncode == 0
    except Exception:
        return False


def _mac_match(candidate: str, target: str) -> bool:
    try:
        return normalize_mac_address(candidate) == normalize_mac_address(target)
    except ValueError:
        return False


def _broadcast_find(target_mac: str, timeout: float = 2.0) -> Optional[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.4)
    targets = ["255.255.255.255"]
    try:
        local = get_local_ip()
        if local != "127.0.0.1":
            parts = local.split(".")
            targets.append(".".join(parts[:3] + ["255"]))
    except Exception:
        pass
    msg = json.dumps({"func": "search_devices", "param": {}}).encode()
    try:
        for _ in range(2):
            for addr in targets:
                try:
                    sock.sendto(msg, (addr, BULB_PORT))
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
                    payload = json.loads(data.decode(errors="ignore"))
                except Exception:
                    continue
                res = payload.get("result", {})
                if not res or not res.get("mac"):
                    continue
                if _mac_match(res["mac"], target_mac):
                    ip = res.get("ip")
                    if ip and ip not in ("0.0.0.0", ""):
                        return ip
                    if addr[0] and addr[0] != "0.0.0.0":
                        return addr[0]
    finally:
        sock.close()
    return None


def _sweep_find(target_mac: str, subnet: ipaddress.IPv4Network,
                skip: str) -> Optional[str]:
    for host in subnet.hosts():
        h = str(host)
        if h == skip:
            continue
        if not _reachable(h):
            continue
        resp = _query(h, "search_devices", timeout=0.15, attempts=1)
        if resp and resp.get("result", {}).get("mac"):
            if _mac_match(resp["result"]["mac"], target_mac):
                ip = resp["result"].get("ip")
                return ip or h
    return None


def _arp_find(target_mac: str) -> Optional[str]:
    rows = []
    try:
        out = subprocess.run(["arp", "-an"], capture_output=True, text=True, timeout=3)
        for m in re.finditer(r"\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-fA-F:]{17})", out.stdout):
            rows.append((m.group(1), m.group(2)))
    except Exception:
        return None
    for ip, mac in rows:
        if _mac_match(mac, target_mac):
            return ip
    return None


def resolve_mac(target_mac: str, use_cache: bool = True,
                broadcast: bool = True, sweep: bool = True,
                arp: bool = True) -> dict:
    """Resolve a bulb MAC to its reachable IP.

    Always returns a dict with keys: ip, method ("cache"|"broadcast"|"sweep"|"arp"|"none"),
    ap (bool, True when the IP is the bulb's own AP address 192.168.8.1), version.
    `ip` is None only when nothing was found.
    """
    target = normalize_mac_address(target_mac)

    if use_cache:
        cache = _load_lan_cache().get(target, {})
        cip = cache.get("ip")
        if cip:
            resp = _query(cip, "search_devices")
            if resp and (resp.get("result", {}).get("ret") == 0
                         or _mac_match(resp.get("result", {}).get("mac", ""), target)):
                return {"ip": cip, "method": "cache", "ap": cip == BULB_IP,
                        "version": resp.get("result", {}).get("version")}
            if _reachable(cip):
                return {"ip": cip, "method": "cache", "ap": cip == BULB_IP,
                        "version": None}

    if broadcast:
        ip = _broadcast_find(target)
        if ip:
            version = None
            if ip != BULB_IP:
                resp = _query(ip, "search_devices")
                version = resp.get("result", {}).get("version") if resp else None
            return {"ip": ip, "method": "broadcast", "ap": ip == BULB_IP, "version": version}

    if sweep:
        try:
            net = ipaddress.ip_network(f"{get_local_ip()}/24", strict=False)
            ip = _sweep_find(target, net, get_local_ip())
            if ip:
                return {"ip": ip, "method": "sweep", "ap": ip == BULB_IP, "version": None}
        except Exception:
            pass

    if arp:
        ip = _arp_find(target)
        if ip:
            return {"ip": ip, "method": "arp", "ap": ip == BULB_IP, "version": None}

    return {"ip": None, "method": "none", "ap": False, "version": None}


def remember(mac: str, ip: str) -> None:
    _save_lan_cache(mac, ip)
