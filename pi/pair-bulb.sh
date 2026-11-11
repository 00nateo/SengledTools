#!/usr/bin/env bash
# Pair a Sengled bulb with this Pi - no other computer needed.
#   bash ~/SengledTools/pi/pair-bulb.sh            (pair the bulb in setup mode)
#   bash ~/SengledTools/pi/pair-bulb.sh EFB8       (pick one by the end of its name, if several)
#
# A bulb in setup mode (new, or factory reset: flick its power 5+ times until it
# flashes) hosts its own open network, 'Sengled_Wi-Fi Bulb_XXXX', at 192.168.8.1.
# The Pi's only Wi-Fi radio is busy hosting the bulbs' network, so this:
#   1. looks for the bulb's setup network (the bulbs' network stays up meanwhile),
#   2. pauses the bulbs' network for ~20 s (the other bulbs reconnect by themselves),
#   3. joins the bulb's network and gives it the bulbs' network name and password,
#   4. brings the bulbs' network back and waits for the bulb to connect.
# If anything goes wrong - even the SSH session dropping - a timer brings the
# bulbs' network back within 4 minutes.
set -uo pipefail
export PATH="$PATH:/usr/sbin:/sbin"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
IF=wlan0
CONF=/etc/hostapd/hostapd.conf
PI_IP=10.42.0.1          # the Pi on the bulbs' network: the bulb's server
ME=192.168.8.200         # the Pi on a bulb's setup network (the bulb is 192.168.8.1)
LOG="$HOME/.sengled/logs/sengled.log"
FALLBACK=sengled-ap-restore
WANT="${1:-}"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓ %s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Run it as your normal user, without sudo. It asks for sudo itself."
sudo true || exit 1
sudo grep -qx "# sengled-ap" "$CONF" 2>/dev/null ||
  die "The bulbs' network isn't set up on this Pi yet. Run: bash $REPO/pi/bulb-wifi.sh"
[ -x "$REPO/.venv/bin/python" ] || die "No $REPO/.venv - run pi/install.sh first."
read -r _ _ ssh_ip _ <<<"${SSH_CONNECTION:-- - - -}"
[ "$ssh_ip" != "$PI_IP" ] ||
  die "You're connected over the bulbs' network, which this pauses. Connect over Ethernet or Tailscale."

NET_SSID="$(sudo sed -n 's/^ssid=//p' "$CONF" | head -1)"
NET_PW="$(sudo sed -n 's/^wpa_passphrase=//p' "$CONF" | head -1)"

is_ap() { iw dev "$IF" info 2>/dev/null | grep -q "type AP"; }

# Setup networks in range, strongest first: "signal<TAB>freq<TAB>bssid<TAB>open|secured<TAB>ssid".
setup_networks() {
  local how=""; is_ap && how="ap-force"  # scanning while hosting needs ap-force
  sudo timeout 25 iw dev "$IF" scan $how 2>/dev/null | awk '
    function out() { if (b != "" && tolower(s) ~ /^sengled/) printf "%s\t%d\t%s\t%s\t%s\n", sig, f, b, (p ? "secured" : "open"), s }
    /^BSS / { out(); b = $2; sub(/\(.*/, "", b); s = ""; sig = -100; f = 0; p = 0 }
    /^\tfreq:/ { f = $2 }
    /^\tsignal:/ { sig = $2 }
    /^\tcapability:/ { p = ($0 ~ /Privacy/) }
    /^\tSSID:/ { s = $0; sub(/^\tSSID: ?/, "", s) }
    END { out() }' | sort -t$'\t' -k1,1 -g -r
}

# --- 1. find the bulb in setup mode ------------------------------------------
say "Looking for a bulb in setup mode"
while :; do
  mapfile -t NETS < <(setup_networks | grep -i -- "${WANT:-.}")
  [ ${#NETS[@]} -gt 0 ] && break
  warn "No bulb in setup mode in range${WANT:+ matching '$WANT'}."
  info "Put it in setup mode: flick its power off and on 5+ times, quickly, until it"
  info "flashes. Keep it within a room or two of the Pi."
  [ -t 0 ] || exit 1
  read -rp "  Press Enter to look again (Ctrl-C to quit) " _ || exit 1
done
if [ ${#NETS[@]} -gt 1 ]; then
  echo "  Several bulbs are in setup mode:"
  for i in "${!NETS[@]}"; do
    IFS=$'\t' read -r sig _ _ _ name <<<"${NETS[$i]}"
    printf '   %d) %s  (%s dBm)\n' $((i + 1)) "$(printf '%b' "$name")" "${sig%.*}"
  done
  [ -t 0 ] || die "Pick one: bash $0 <end of its name>"
  read -rp "  Which one? [1] " n
  n="${n:-1}"; [[ "$n" =~ ^[0-9]+$ && $n -ge 1 && $n -le ${#NETS[@]} ]] || die "No bulb $n."
  NETS=("${NETS[$((n - 1))]}")
fi
IFS=$'\t' read -r SIG FREQ BSSID SEC RAW <<<"${NETS[0]}"
BULB_NET="$(printf '%b' "$RAW")"  # iw escapes non-ASCII bytes as \xNN
ok "Found '$BULB_NET' (${SIG%.*} dBm, ${FREQ} MHz)"
[ "$SEC" = open ] || die "'$BULB_NET' asks for a password - setup networks are normally open. Reset the bulb and try again."
[ "${SIG%.*}" -ge -80 ] 2>/dev/null || warn "Weak signal - if this fails, bring the bulb closer to the Pi for setup."

# --- 2. pause the bulbs' network, with a safety net ---------------------------
paused=""
restore() {
  [ -n "$paused" ] || return 0
  sudo iw dev "$IF" disconnect 2>/dev/null
  sudo ip -4 addr flush dev "$IF"
  sudo systemctl start hostapd sengled-ap-net
  sudo systemctl stop "$FALLBACK.timer" 2>/dev/null
  sudo systemctl reset-failed "$FALLBACK.service" "$FALLBACK.timer" 2>/dev/null
  paused=""
  for _ in $(seq 10); do is_ap && systemctl is-active -q sengled-ap-net && break; sleep 1; done
  if is_ap && systemctl is-active -q sengled-ap-net; then
    ok "'$NET_SSID' is back up"
  else
    warn "'$NET_SSID' didn't come back. Run: sudo systemctl restart hostapd sengled-ap-net"
  fi
}
trap restore EXIT
trap 'exit 130' INT TERM HUP

say "Pausing '$NET_SSID' for about 20 seconds"
sudo systemctl stop "$FALLBACK.timer" 2>/dev/null
sudo systemctl reset-failed "$FALLBACK.service" "$FALLBACK.timer" 2>/dev/null
sudo systemd-run --quiet --unit="$FALLBACK" --on-active=4min /bin/sh -c \
  "iw dev $IF disconnect; ip -4 addr flush dev $IF; systemctl restart hostapd sengled-ap-net" ||
  die "Couldn't set up the safety timer, so '$NET_SSID' was left alone."
paused=1
sudo systemctl stop hostapd sengled-ap-net
sudo ip -4 addr flush dev "$IF"
if ! sudo iw dev "$IF" set type managed 2>/dev/null; then
  sudo ip link set "$IF" down && sudo iw dev "$IF" set type managed
fi
sudo ip link set "$IF" up

# --- 3. join the bulb's setup network and hand it the bulbs' network ----------
say "Joining '$BULB_NET'"
joined=""
for _ in 1 2 3; do
  sudo timeout 15 iw dev "$IF" connect -w "$BULB_NET" "${FREQ%.*}" "$BSSID" >/dev/null 2>&1
  if iw dev "$IF" link | grep -q "^Connected"; then joined=1; break; fi
  sleep 2
done
[ -n "$joined" ] || die "Couldn't join '$BULB_NET'. Bring the bulb closer and run this again."
sudo ip addr add "$ME/24" dev "$IF"
ok "Joined - the Pi is $ME on the bulb's network"

MAC_FILE="$(mktemp)"
NET_SSID="$NET_SSID" NET_PW="$NET_PW" PI_IP="$PI_IP" MAC_FILE="$MAC_FILE" REPO="$REPO" \
  "$REPO/.venv/bin/python" - <<'PY'
import os, sys, time
sys.path.insert(0, os.environ["REPO"])
import sengled_ctl as s
s.LOG_ROLE = "pair"
for attempt in range(4):  # the bulb's setup service can take a few seconds to answer
    if s.probe(s.AP_IP, timeout=1.0):
        break
    time.sleep(2)
mac = s.send_credentials(os.environ["NET_SSID"], os.environ["NET_PW"], s.AP_IP, os.environ["PI_IP"])
with open(os.environ["MAC_FILE"], "w") as f:
    f.write(mac or "")
sys.exit(0 if mac else 1)
PY
sent=$?
MAC="$(tr '[:upper:]' '[:lower:]' <"$MAC_FILE")"; rm -f "$MAC_FILE"
[ $sent -eq 0 ] || die "The bulb didn't take the settings. Reset it (5+ flicks) and run this again."
sleep 2  # let it apply them before its setup network disappears

# --- 4. bring the bulbs' network back and wait for the bulb -------------------
say "Bringing '$NET_SSID' back"
restore

say "Waiting for the bulb to join '$NET_SSID' (up to 2 minutes)"
since="$(date '+%F %T')"
step=""
for _ in $(seq 60); do
  if [ -n "$MAC" ] && [ "$MAC" != "?" ]; then
    if [ -z "$step" ] && sudo iw dev "$IF" station dump | grep -qi "^Station $MAC"; then
      ok "Joined '$NET_SSID'"; step=joined
    fi
    if awk -v t="$since" '$1" "$2 >= t' "$LOG" 2>/dev/null | grep -i "$MAC" | grep -q "connected to MQTT"; then
      ip="$(awk -v m="$MAC" '$2 == m {print $3}' /var/lib/misc/sengled-ap.leases 2>/dev/null)"
      ok "Connected to the Pi${ip:+ at $ip} - it's in the web app now (${MAC^^})."
      exit 0
    fi
  fi
  sleep 2
done
warn "The bulb hasn't connected yet."
info "Watch for it:  bash $REPO/pi/watch.sh"
info "If it's flashing again (back in setup mode), run this again with the bulb closer to the Pi."
exit 1
