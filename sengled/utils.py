"""
Utility functions for Sengled tool.
Provides IP/MAC address detection, configuration file management,
MAC address normalization, and timestamp generation.
"""

import json
import socket
import time
import re
import ipaddress
from pathlib import Path
from typing import Dict, Optional

# Ranges that are NOT reachable from a device on your real local Wi-Fi,
# e.g. Tailscale's CGNAT overlay (100.64.0.0/10) or an APIPA self-assign
# (169.254.0.0/16). Handing one of these to the bulb is what breaks the
# "bulb did not contact required endpoints" verification.
_BAD_IP_RANGES = [
     ipaddress.ip_network("100.64.0.0/10"),
     ipaddress.ip_network("169.254.0.0/16"),
]


def _is_reachable_lan_ip(ip: str) -> bool:
    """True if this IP could plausibly be reached by a bulb on the real LAN."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.is_loopback:
        return False
    for net in _BAD_IP_RANGES:
        if addr in net:
            return False
    return True


def get_local_ip() -> str:
    """Get the local IP address of this computer."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except OSError:
        return "127.0.0.1"


def pick_lan_ip() -> str:
    """Choose the LAN IP to hand to the bulb, skipping Tailscale/CGNAT overlays.

    Enumerates every non-loopback IPv4 and returns the most usable real-LAN
    address (skips 100.64.0.0/10 Tailscale and 169.254.0.0/16 APIPA).
    Prefers 192.168.x, then 10.x, then 172.x, then anything else reachable.
    Falls back to get_local_ip() when no usable address is found.
    """
    try:
        import psutil
        candidates: list = []
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family != socket.AF_INET:  # psutil has no AF_INET of its own
                    continue
                if a.address == "127.0.0.1":
                    continue
                if not _is_reachable_lan_ip(a.address):
                    continue
                candidates.append(a.address)

        def _rank(ip: str) -> int:
            p = ip.split(".")
            if p[0] == "192" and p[1] == "168":
                return 0
            if p[0] == "10":
                return 1
            if p[0] == "172":
                return 2
            return 3

        candidates.sort(key=_rank)
        if candidates:
            return candidates[0]
    except Exception:
        pass
    return get_local_ip()


def local_ipv4s() -> list:
    """Every non-loopback IPv4 address of this computer (falls back to the default-route one)."""
    try:
        import psutil
        ips = [a.address for addrs in psutil.net_if_addrs().values() for a in addrs
               if a.family == socket.AF_INET and not a.address.startswith("127.")]
        if ips:
            return ips
    except Exception:
        pass
    return [get_local_ip()]

def get_mac_address(ip: Optional[str] = None, interface: Optional[str] = None) -> Optional[str]:
    """Get MAC address using psutil (cross-platform, more reliable than getmac).
    
    Args:
        ip: IP address to find MAC for (not fully supported, use interface instead)
        interface: Network interface name (e.g., 'eth0', 'wlan0')
    
    Returns:
        MAC address string or None if not found
    """
    try:
        import psutil
        
        if interface:
            # Get MAC for specific interface
            addrs = psutil.net_if_addrs().get(interface, [])
            for addr in addrs:
                if addr.family == psutil.AF_LINK:
                    return addr.address
        else:
            # Get first non-loopback interface MAC
            for name, addrs in psutil.net_if_addrs().items():
                if name != 'lo' and addrs:
                    for addr in addrs:
                        if addr.family == psutil.AF_LINK:
                            return addr.address
        
        return None
    except ImportError:
        return None

def get_config_dir() -> Path:
    """Gets the configuration directory for the application."""
    return Path.home() / ".sengled"

def load_bulbs() -> Dict[str, Dict]:
    """Loads bulb information from the configuration file."""
    config_dir = get_config_dir()
    bulbs_file = config_dir / "bulbs.json"
    if not bulbs_file.exists():
        return {}
    with open(bulbs_file, "r") as f:
        return json.load(f)

def save_bulb(mac: str, broker_ip: str):
    """Saves a bulb's MAC address and broker IP to the configuration file."""
    config_dir = get_config_dir()
    config_dir.mkdir(exist_ok=True)
    bulbs_file = config_dir / "bulbs.json"
    bulbs = load_bulbs()
    bulbs[mac] = {"broker": broker_ip}
    with open(bulbs_file, "w") as f:
        json.dump(bulbs, f, indent=2)

def get_bulb_broker(mac: str) -> Optional[str]:
    """Retrieves the broker IP for a given bulb MAC address."""
    bulbs = load_bulbs()
    return bulbs.get(mac, {}).get("broker")

def normalize_mac_address(mac: str) -> str:
    """Return MAC in canonical uppercase colon-delimited form (XX:XX:XX:XX:XX:XX).

    Raises ValueError if input is empty or not 12 hex digits (colons/hyphens optional).
    """
    if not mac:
        raise ValueError("MAC address cannot be empty")

    candidate = mac.strip().upper()
    # remove common separators
    hex_only = re.sub(r"[:-]", "", candidate)

    if not re.fullmatch(r"[0-9A-F]{12}", hex_only):
        raise ValueError(f"Invalid MAC address format: {mac}")

    return ":".join(hex_only[i:i+2] for i in range(0, 12, 2))

def get_current_epoch_ms() -> int:
    """Returns the current time in milliseconds since the epoch."""
    return int(time.time() * 1000)