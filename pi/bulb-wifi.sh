#!/usr/bin/env bash
# The bulbs' own Wi-Fi network, hosted by the Pi's built-in Wi-Fi (wlan0):
#   hostapd (WPA2-PSK, 2.4 GHz) + dnsmasq (DHCP/DNS) + internet sharing over Ethernet.
#   The Pi is 10.42.0.1 on it; bulbs get 10.42.0.10-254.
#
# Why hostapd and not NetworkManager's hotspot: NetworkManager always advertises
# WPA-PSK-SHA256 next to WPA-PSK, even with PMF off. The Pi 3's Wi-Fi firmware builds
# a beacon that doesn't match what the handshake then sends, and Apple devices
# disconnect at step 3 of 4 ("could not be joined").
#
# Run by install.sh, and safe to run again. Keeps an existing network's name and
# password, including one NetworkManager hosted (earlier versions of install.sh).
set -euo pipefail
export PATH="$PATH:/usr/sbin:/sbin"

BULB_IF=wlan0
PI_IP=10.42.0.1
CONF=/etc/hostapd/hostapd.conf
MARK="# sengled-ap"
OLD_CON=SengledNet  # NetworkManager's hotspot from earlier versions of install.sh

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Run it as your normal user, without sudo. It asks for sudo itself."
command -v hostapd >/dev/null || die "hostapd isn't installed: sudo apt-get install hostapd"
setting() { sudo sed -n "s/^$1=//p" "$CONF" 2>/dev/null | head -1; }

# --- name, password, country: keep what's there, else ask --------------------
if sudo grep -qx "$MARK" "$CONF" 2>/dev/null; then
  SSID="$(setting ssid)"; PW="$(setting wpa_passphrase)"
  COUNTRY="$(setting country_code)"; CHANNEL="$(setting channel)"
  ok "'$SSID' is already set up - keeping its name and password."
  echo "  (To change them: sudo rm $CONF, then run this again.)"
elif nmcli -t -f NAME connection show 2>/dev/null | grep -qx "$OLD_CON"; then
  SSID="$(nmcli -g 802-11-wireless.ssid connection show "$OLD_CON")"
  PW="$(sudo nmcli -s -g 802-11-wireless-security.psk connection show "$OLD_CON")"
  COUNTRY="$(iw reg get | awk '/^country/ {sub(":", "", $2); print $2; exit}')"
  CHANNEL="$(iw dev "$BULB_IF" info 2>/dev/null | awk '/channel/ {print $2; exit}')"
  say "Moving '$SSID' to hostapd (same name and password, so nothing changes for the bulbs)"
else
  # wlan0 is about to become the bulbs' network: don't cut off our only connection.
  if ! nmcli -t -f TYPE,STATE device | grep -q '^ethernet:connected'; then
    die "No Ethernet connection. Plug the Pi into the building's network first:
  its Wi-Fi is about to become the bulbs' network."
  fi
  read -r _ _ ssh_ip _ <<<"${SSH_CONNECTION:-- - - -}"
  if ip -4 -o addr show dev "$BULB_IF" 2>/dev/null | grep -q " $ssh_ip/"; then
    eth_ip="$(ip -4 -o addr show | awk '$2 ~ /^(eth|end|enx)/ {split($4, a, "/"); print a[1]; exit}')"
    die "You're connected to the Pi over its Wi-Fi, which this step takes over.
  Reconnect over Ethernet and run this again:  ssh $(id -un)@$eth_ip"
  fi
  read -rp "  Your 2-letter country code, for Wi-Fi rules [US]: " COUNTRY
  read -rp "  Name for the bulbs' Wi-Fi [SengledNet]: " SSID
  SSID="${SSID:-SengledNet}"
  while :; do
    read -rsp "  Password for it (8-63 letters and numbers): " PW; echo
    if [[ ${#PW} -ge 8 && ${#PW} -le 63 && "$PW" =~ ^[A-Za-z0-9]+$ ]]; then break; fi
    warn "Use 8-63 letters and numbers only - bulbs can choke on symbols."
  done
  CHANNEL=""
fi
COUNTRY="$(echo "${COUNTRY:-US}" | tr '[:lower:]' '[:upper:]')"
[[ "${CHANNEL:-}" =~ ^([1-9]|1[0-3])$ ]] || CHANNEL=11
sudo raspi-config nonint do_wifi_country "$COUNTRY" >/dev/null 2>&1 || sudo iw reg set "$COUNTRY" || true
sudo rfkill unblock wifi || true

# --- hand wlan0 from NetworkManager to hostapd --------------------------------
sudo tee /etc/NetworkManager/conf.d/90-sengled-ap.conf >/dev/null <<EOF
# The bulbs' Wi-Fi is hosted by hostapd (pi/bulb-wifi.sh); NetworkManager keeps Ethernet.
[keyfile]
unmanaged-devices=interface-name:$BULB_IF
EOF
if nmcli -t -f NAME connection show 2>/dev/null | grep -qx "$OLD_CON"; then
  sudo nmcli connection delete "$OLD_CON" >/dev/null
fi
sudo nmcli general reload conf 2>/dev/null || true
sudo nmcli device set "$BULB_IF" managed no 2>/dev/null || true

# --- hostapd: plain WPA2-PSK/CCMP, what the bulbs (and the Pi 3's firmware) handle -
sudo mkdir -p /etc/hostapd
sudo sh -c "umask 077; cat > $CONF" <<EOF
$MARK
# Written by pi/bulb-wifi.sh: run it again instead of editing this by hand.
interface=$BULB_IF
driver=nl80211
ssid=$SSID
country_code=$COUNTRY
ieee80211d=1
hw_mode=g
channel=$CHANNEL
ieee80211n=1
wmm_enabled=1
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
ieee80211w=0
wpa_passphrase=$PW
# Joins, leaves and handshakes go to the journal (pi/watch.sh shows them live).
logger_syslog=-1
logger_syslog_level=2
EOF

sudo mkdir -p /etc/systemd/system/hostapd.service.d
sudo tee /etc/systemd/system/hostapd.service.d/sengled-ap.conf >/dev/null <<EOF
[Unit]
After=NetworkManager.service
[Service]
ExecStartPre=-/usr/sbin/rfkill unblock wifi
RestartSec=5
EOF

# --- address, DHCP/DNS and internet sharing ------------------------------------
sudo tee /etc/sengled-ap.nft >/dev/null <<EOF
# Internet for devices on the bulbs' Wi-Fi, through the Pi (pi/bulb-wifi.sh).
table ip sengled_ap
delete table ip sengled_ap
table ip sengled_ap {
  chain postrouting {
    type nat hook postrouting priority srcnat; policy accept;
    ip saddr ${PI_IP%.*}.0/24 oifname != "$BULB_IF" masquerade
  }
}
EOF
echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/90-sengled-ap.conf >/dev/null
sudo sysctl -q -p /etc/sysctl.d/90-sengled-ap.conf

sudo tee /etc/systemd/system/sengled-ap-net.service >/dev/null <<EOF
[Unit]
Description=Bulb Wi-Fi: address $PI_IP, DHCP/DNS and internet sharing on $BULB_IF
Requires=hostapd.service
After=hostapd.service
PartOf=hostapd.service

[Service]
# Flush first: an address NetworkManager left behind is 'noprefixroute', and without
# the 10.42.0.0/24 route replies to the bulbs would leave through Ethernet.
ExecStartPre=/usr/sbin/ip -4 addr flush dev $BULB_IF
ExecStartPre=/usr/sbin/ip addr add $PI_IP/24 dev $BULB_IF
ExecStartPre=/usr/sbin/nft -f /etc/sengled-ap.nft
ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file=/dev/null --log-facility=- \\
  --interface=$BULB_IF --except-interface=lo --bind-interfaces \\
  --dhcp-range=${PI_IP%.*}.10,${PI_IP%.*}.254,12h --dhcp-leasefile=/var/lib/misc/sengled-ap.leases
ExecStopPost=-/usr/sbin/nft delete table ip sengled_ap
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl unmask hostapd >/dev/null 2>&1 || true
sudo systemctl daemon-reload
sudo systemctl enable hostapd sengled-ap-net >/dev/null 2>&1
sudo systemctl restart hostapd
sudo systemctl restart sengled-ap-net

for _ in $(seq 10); do
  systemctl is-active -q sengled-ap-net && iw dev "$BULB_IF" info 2>/dev/null | grep -q "type AP" && break
  sleep 1
done
if iw dev "$BULB_IF" info 2>/dev/null | grep -q "type AP" && systemctl is-active -q sengled-ap-net; then
  ok "Hosting '$SSID' on $BULB_IF (channel $CHANNEL) - the Pi is $PI_IP on it"
else
  die "The bulbs' Wi-Fi didn't start. Check: journalctl -u hostapd -u sengled-ap-net -n 30"
fi
