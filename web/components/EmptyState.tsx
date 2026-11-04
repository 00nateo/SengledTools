"use client";

import type { AppState } from "@/lib/api";
import { Button, Spinner } from "./ui";

export function EmptyState({
  state,
  scanning,
  onScan,
  onWifi,
}: {
  state: AppState;
  scanning: boolean;
  onScan: () => void;
  onWifi: () => void;
}) {
  const { network, mqtt } = state;
  return (
    <div className="mx-auto mt-6 flex w-full max-w-2xl flex-col gap-4 rounded-2xl border border-line bg-panel p-6">
      <h2 className="text-lg font-semibold">No bulbs yet</h2>
      {network.ipv6Only ? (
        <p className="text-sm text-warn">
          This Mac is on an IPv6-only connection (192.0.0.2), typical of iPhone hotspots, so it can&apos;t see bulbs. Fix once:{" "}
          <code className="font-mono">networksetup -setv6linklocal Wi-Fi</code>, then rejoin the hotspot.
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-xl border border-line bg-panel-2 p-4 text-sm">
          <p className="font-medium">Bulb blinking (setup mode)?</p>
          <p className="mt-1 text-muted">
            Join its <b>Sengled Wi-Fi Bulb_…</b> network on this Mac, then Scan. You can control it right away, or use Wi-Fi setup to put it on
            your network.
          </p>
          <Button className="mt-3" onClick={onScan} disabled={scanning}>
            {scanning && <Spinner />} {network.onBulbAp ? "Scan now" : "Scan"}
          </Button>
        </div>
        <div className="rounded-xl border border-line bg-panel-2 p-4 text-sm">
          <p className="font-medium">Bulb already set up on Wi-Fi?</p>
          <p className="mt-1 text-muted">
            {mqtt.running
              ? "It connects to this Mac by itself within about a minute - keep this page open. This Mac must be on the same network."
              : "The MQTT server isn't running. Start the service with the repo's .venv so Wi-Fi bulbs can connect."}
          </p>
          <Button className="mt-3" variant="primary" onClick={onWifi}>
            Wi-Fi setup
          </Button>
        </div>
      </div>
    </div>
  );
}
