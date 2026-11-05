#!/usr/bin/env bash
# Live view of everything connecting to the Pi: devices joining the bulbs' Wi-Fi,
# addresses handed out, bulbs checking in and connecting to MQTT, commands sent.
#   bash ~/SengledTools/pi/watch.sh        (Ctrl-C to stop)
set -uo pipefail
export PATH="$PATH:/usr/sbin:/sbin"
LOG="$HOME/.sengled/logs/sengled.log"
LEASES=/var/lib/misc/sengled-ap.leases
head() { printf '\033[1;36m%s\033[0m\n' "$*"; }

sudo -v || exit 1
trap 'kill 0' EXIT

head "On the bulbs' Wi-Fi now"
sudo iw dev wlan0 station dump 2>/dev/null |
  awk '/^Station/ {mac=$2} /signal:/ {sig=$2} /connected time/ {print mac, sig, $3}' |
  while read -r mac sig secs; do
    ip="$(awk -v m="$mac" '$2 == m {print $3}' "$LEASES" 2>/dev/null)"
    printf '  %s  %-15s  %s dBm  for %s min\n' "$mac" "${ip:-?}" "$sig" "$((secs / 60))"
  done
echo "  MQTT connections from bulbs: $(ss -Htn state established '( sport = :8883 )' | grep -vc '127\.0\.0\.1')"
echo
head "Live  (Ctrl-C to stop)"

# [wifi]  joins, leaves and addresses (hostapd, DHCP)
sudo journalctl -f -n 0 -o short -u hostapd -u sengled-ap-net 2>/dev/null |
  sed -u -E 's/^\S+ \S+ (\S+) \S+ [^:]+: (dnsmasq-dhcp\[[0-9]+\]: )?/\1 [wifi]  /' &
# [bulbs] check-ins, MQTT connections, commands and replies (the bulb service's log)
tail -n 0 -F "$LOG" 2>/dev/null |
  grep --line-buffered -vE 'broker info: (Listener|Connection from 127)' |
  sed -u -E 's/^[0-9-]+ ([0-9:]+)\.[0-9]+ \S+ +/\1 [bulbs] /'
