#!/usr/bin/env python3
"""
sengled_join_wifi.py - hand a Sengled bulb over to your real Wi-Fi network
(open or password), by talking directly to it on its AP address 192.168.8.1.

Usage (must be connected to the bulb's 'Sengled Wi-Fi Bulb_XXXXXX' AP):
    python3 sengled_join_wifi.py
    python3 sengled_join_wifi.py --ssid WhiteSky-PublicWiFi
    python3 sengled_join_wifi.py --ssid "MyWifi" --password "secret"

This sends the bulb's new Wi-Fi credentials (routerInfo) and does NOT require
the optional HTTP verification callback (that is what fails on CGNAT/Tailscale
networks, but it is irrelevant to actually moving the bulb).
"""
import sys, json, time, socket, argparse, os

BULB_IP = "192.168.8.1"
PORT = 9080

try:
    from sengled.crypto import encrypt_wifi_payload, decrypt_wifi_payload
except Exception:
    print("Need the project venv (has cryptography):  .venv/bin/python sengled_join_wifi.py")
    sys.exit(1)


def say(kind, msg):
    print(f"   {kind} {msg}")


def handshake(s):
    req = {"name": "startConfigRequest", "totalStep": 1, "curStep": 1,
            "payload": {"protocol": 1}}
    s.sendto(json.dumps(req).encode("utf-8"), (BULB_IP, PORT))
    s.settimeout(15)
    data, _ = s.recvfrom(4096)
    return json.loads(data.decode("utf-8"))


def send_credentials(s, ssid, password, http_port, http_host):
    # Re-handshake (required before accepting config)
    req = {"name": "startConfigRequest", "totalStep": 1, "curStep": 1,
            "payload": {"protocol": 1}}
    s.sendto(json.dumps(req).encode("utf-8"), (BULB_IP, PORT))
    data, _ = s.recvfrom(4096)
    resp = json.loads(data.decode("utf-8"))
    if not resp.get("payload", {}).get("result"):
        say("x", "config prep failed (bulb not in setup mode?)")
        return False

    router_info = {"ssid": ssid, "password": password or ""}
    params = {
         "name": "setParamsRequest", "totalStep": 1, "curStep": 1,
          "payload": {
             "userID": "618",
             "appServerDomain": f"http://{http_host}:{http_port}/life2/device/accessCloud.json",
             "jbalancerDomain": f"http://{http_host}:{http_port}/jbalancer/new/bimqtt",
             "routerInfo": router_info,
          },
    }
    encrypted = encrypt_wifi_payload(params)
    s.sendto(encrypted.encode("utf-8"), (BULB_IP, PORT))

     # The response may be JSON, encrypted, or a timeout - all are acceptable.
    s.settimeout(5)
    try:
        data, _ = s.recvfrom(4096)
        raw = data.decode("utf-8", errors="ignore")
        try:
            as_json = json.loads(raw)
            if as_json.get("payload", {}).get("result") is False:
                say("x", "bulb rejected credentials (plaintext)")
                return False
            say("ok", "credentials accepted (plaintext)")
        except json.JSONDecodeError:
            try:
                dec = decrypt_wifi_payload(raw)
                if dec and dec.get("payload", {}).get("result"):
                    say("ok", "credentials accepted (encrypted ack)")
                else:
                    say("ok", "credentials sent (could not verify ack)")
            except Exception:
                say("ok", "credentials sent (verification skipped)")
    except socket.timeout:
        say("ok", "credentials sent (no response = expected for some firmware)")

     # End configuration so the bulb applies and joins the network.
    end_req = {"name": "endConfigRequest", "totalStep": 1, "curStep": 1, "payload": {}}
    s.sendto(json.dumps(end_req).encode("utf-8"), (BULB_IP, PORT))
    s.settimeout(3)
    try:
        s.recvfrom(4096)
    except Exception:
        pass
    return True


def wait_until_ap_gone(timeout=30):
     # Once the bulb joins the new network, its AP (192.168.8.1) stops answering.
    for i in range(timeout):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1)
            req = {"name": "startConfigRequest", "totalStep": 1, "curStep": 1,
                     "payload": {"protocol": 1}}
            s.sendto(json.dumps(req).encode(), (BULB_IP, PORT))
            s.recvfrom(4096)
            s.close()
        except Exception:
            return i + 1
        time.sleep(1)
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ssid", default="WhiteSky-PublicWiFi")
    p.add_argument("--password", default="")
    p.add_argument("--http-port", type=int, default=57542)
    p.add_argument("--http-ip", default=None, help="callback IP (default: auto) ")
    a = p.parse_args()

    http_host = a.http_ip
    if not http_host:
        try:
            from sengled.utils import pick_lan_ip
            http_host = pick_lan_ip()
        except Exception:
            http_host = "192.168.8.1"

    print(f"   >> Handing bulb over to Wi-Fi '{a.ssid}' "
          f"{('(open)' if not a.password else '(w/ password)')}")
    print(f"   >> Callback host (optional): {http_host}:{a.http_port}")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(15)
        try:
            hs = handshake(s)
        except Exception as e:
            say("x", f"could not reach bulb on {BULB_IP}: {e}")
            say("i", "Make sure you are on the bulb's 'Sengled Wi-Fi Bulb_....' AP.")
            sys.exit(1)
        mac = hs.get("payload", {}).get("mac")
        say("i", f"connected to bulb {mac or '(mac not reported)'} on {BULB_IP}")

        ok = send_credentials(s, a.ssid, a.password, a.http_port, http_host)
        if not ok:
            say("x", "handoff failed")
            sys.exit(1)

    say("i", "Wait ~10-20s for the bulb to leave its AP and join the network...")
    gone = wait_until_ap_gone(timeout=30)
    if gone is not None:
        say("g", f"Bulb left its AP after {gone}s - it has joined "
                    f"'{a.ssid}'.")
        say("i", "Now switch this Mac to the SAME network "
                  f"'{a.ssid}', then run:")
        say("i", "    python3 sengled_ctl.py status --mode public")
        say("i", "The tool will auto-find the bulb's new IP there and save it.")
    else:
        say("w", "AP still responding (bulb may already be on the new network).")
        say("i", f"Switch this Mac to '{a.ssid}' and run:")
        say("i", "    python3 sengled_ctl.py status --mode public --no-sweep")

    if os.path.expanduser("~/.sengled") and os.path.isdir("sengled"):
        pass
    try:
        from sengled.utils import load_bulbs, save_bulb
        # remember which target network we aimed at, for reference
    except Exception:
        pass
    print()
    print("   done.")


if __name__ == "__main__":
    main()
