#!/usr/bin/env bash
# Turn a Raspberry Pi into the always-on server for Sengled Wi-Fi bulbs:
#   - its built-in Wi-Fi hosts a private 2.4 GHz network for the bulbs (Pi = 10.42.0.1)
#   - the bulb service + web app start at boot (http://<hostname>.local:3000)
#   - the Pi reaches your building's network over Ethernet
#
# Run ON THE PI, as your normal user, from the copied SengledTools folder:
#   bash pi/install.sh
# Safe to re-run, e.g. after copying over a newer version of the tools.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE=sengled-lights
BULB_IF=wlan0
CON=SengledNet          # NetworkManager's name for the bulbs' network
PI_IP=10.42.0.1

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*"; exit 1; }

[ "$(uname -s)" = Linux ] || die "Run this on the Raspberry Pi (over SSH), not on the Mac."
[ "$(id -u)" -ne 0 ] || die "Run it as your normal user, without sudo. It asks for sudo itself."
[ -f "$REPO/sengled_ctl.py" ] || die "Can't find sengled_ctl.py next to pi/ - copy the whole SengledTools folder."
[ "$(uname -m)" = aarch64 ] || warn "Not 64-bit Raspberry Pi OS: some packages may have to compile (slow)."
command -v nmcli >/dev/null || die "NetworkManager (nmcli) not found. Use Raspberry Pi OS Bookworm or newer."

# --------------------------------------------------------------------------
say "1/5  System packages"
sudo apt-get update -q
sudo apt-get install -y -q python3-venv python3-pip iw dnsmasq-base curl ca-certificates rsync

major="$(node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/' || true)"
if [ "${major:-0}" -lt 20 ] || ! command -v npm >/dev/null; then
  say "Installing Node.js 24 LTS (the web app needs 20.9 or newer)"
  curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
  sudo apt-get install -y -q nodejs
fi
ok "Node $(node -v), Python $(python3 -V | cut -d' ' -f2)"

# --------------------------------------------------------------------------
say "2/5  Bulb service (Python)"
cd "$REPO"
if [ -d .venv ] && ! .venv/bin/python -c 1 2>/dev/null; then
  warn "Removing a .venv copied from another computer"
  rm -rf .venv
fi
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
ok "Python packages installed"

# --------------------------------------------------------------------------
say "3/5  Web app (takes a few minutes on a Pi)"
if systemctl is-active -q $SERVICE 2>/dev/null; then
  sudo systemctl stop $SERVICE  # re-run: free memory for the build; restarted at the end
fi
cd "$REPO/web"
npm ci --no-audit --no-fund
npm run build
ok "Web app built"

# --------------------------------------------------------------------------
say "4/5  Wi-Fi network for the bulbs"
if nmcli -t -f NAME connection show | grep -qx "$CON"; then
  SSID="$(nmcli -g 802-11-wireless.ssid connection show "$CON")"
  ok "'$SSID' is already set up - keeping it."
  echo "  (To change it: sudo nmcli connection delete $CON, then run this again.)"
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
  COUNTRY="$(echo "${COUNTRY:-US}" | tr '[:lower:]' '[:upper:]')"
  sudo raspi-config nonint do_wifi_country "$COUNTRY" >/dev/null 2>&1 || sudo iw reg set "$COUNTRY" || true
  sudo rfkill unblock wifi || true

  read -rp "  Name for the bulbs' Wi-Fi [SengledNet]: " SSID
  SSID="${SSID:-SengledNet}"
  while :; do
    read -rsp "  Password for it (8-63 letters and numbers): " PW; echo
    if [[ ${#PW} -ge 8 && ${#PW} -le 63 && "$PW" =~ ^[A-Za-z0-9]+$ ]]; then break; fi
    warn "Use 8-63 letters and numbers only - bulbs can choke on symbols."
  done

  # 2.4 GHz (band bg), WPA2-AES, no PMF: what the bulbs support.
  sudo nmcli connection add type wifi ifname "$BULB_IF" con-name "$CON" ssid "$SSID" \
    802-11-wireless.mode ap 802-11-wireless.band bg \
    ipv4.method shared ipv4.addresses "$PI_IP/24" ipv6.method disabled \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PW" wifi-sec.proto rsn \
    wifi-sec.pairwise ccmp wifi-sec.group ccmp wifi-sec.pmf disable \
    connection.autoconnect yes connection.autoconnect-priority 100 >/dev/null
  sudo nmcli connection up "$CON" >/dev/null
  ok "Hosting '$SSID' on $BULB_IF - the Pi is $PI_IP on it"
fi

# --------------------------------------------------------------------------
say "5/5  Start at boot"
NPM="$(command -v npm)"
sudo tee /etc/systemd/system/$SERVICE.service >/dev/null <<EOF
[Unit]
Description=Sengled lights: bulb service (MQTT :8883) + web app (:3000)
Wants=network-online.target
After=network-online.target NetworkManager.service

[Service]
User=$(id -un)
WorkingDirectory=$REPO/web
Environment=NODE_ENV=production
Environment=PATH=$(dirname "$NPM"):/usr/local/bin:/usr/bin:/bin
ExecStart=$NPM start
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE >/dev/null 2>&1
sudo systemctl restart $SERVICE

printf '  waiting for the web app'
for _ in $(seq 60); do
  if curl -fs -o /dev/null http://127.0.0.1:3000; then break; fi
  printf '.'; sleep 1
done
echo
if curl -fs -o /dev/null http://127.0.0.1:3000; then
  ok "Running, and starts by itself after every reboot"
else
  warn "Not answering yet. Check: journalctl -u $SERVICE -n 50"
fi

# Everything is worked out before the text block: bash 5.2 (Raspberry Pi OS) rejects
# a quote inside ${...} in a here-document ("bad substitution: no closing }").
LAN_IP="$(ip -4 -o addr show | awk '$2 ~ /^(eth|end|enx)/ {split($4, a, "/"); print a[1]; exit}')"
WEB="http://$(hostname).local:3000"
[ -n "$LAN_IP" ] && WEB="$WEB   or   http://$LAN_IP:3000"
GREEN=$'\033[1;32m'
PLAIN=$'\033[0m'
cat <<EOF

${GREEN}All set.${PLAIN}

  Web app:     $WEB
  Bulb Wi-Fi:  $SSID  (server address for bulbs: $PI_IP)

  Pair each bulb from the Mac: put the bulb in setup mode, join its
  "Sengled Wi-Fi Bulb_..." network on the Mac, then in the SengledTools folder:

    python3 sengled_ctl.py wifi --ssid "$SSID" --password 'THE-PASSWORD' --callback $PI_IP

  Logs:  journalctl -u $SERVICE -f      tail -f ~/.sengled/logs/sengled.log
EOF
