"use client";

import { useState } from "react";
import type { AppState } from "@/lib/api";
import { Button, Chip, Spinner } from "./ui";

export function TopBar({
  state,
  apiError,
  scanning,
  onScan,
  onAdd,
  onWifi,
}: {
  state: AppState | null;
  apiError: string | null;
  scanning: boolean;
  onScan: () => void;
  onAdd: (ip: string) => void;
  onWifi: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [ip, setIp] = useState("192.168.8.1");
  const net = state?.network;
  const mqtt = state?.mqtt;
  const netLabel = net?.interfaces.map((i) => i.cidr.split("/")[0]).join(", ") || "no network";

  return (
    <header className="flex flex-col gap-3 rounded-2xl border border-line bg-panel px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-center gap-3">
        <div className="grid h-9 w-9 place-items-center rounded-xl bg-accent/15 text-lg" aria-hidden>
          💡
        </div>
        <div>
          <h1 className="text-base font-semibold leading-tight">Sengled Lights</h1>
          <p className="text-xs text-muted">Local control · no cloud</p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {apiError && state ? (
          <Chip tone="err">Service unreachable</Chip>
        ) : net ? (
          <>
            <Chip tone={net.ipv6Only ? "err" : net.onBulbAp ? "warn" : "neutral"} title={net.interfaces.map((i) => `${i.name} ${i.cidr}`).join("\n")}>
              {net.onBulbAp
                ? "On bulb setup network"
                : net.ipv6Only
                  ? "IPv6-only network - bulbs invisible"
                  : `${net.ssid ? net.ssid + " · " : ""}${netLabel}`}
            </Chip>
            {mqtt?.running ? (
              <Chip tone={mqtt.peers.length ? "ok" : "neutral"} title="MQTT server on :8883 - bulbs on Wi-Fi connect here">
                {mqtt.peers.length
                  ? `${mqtt.peers.length} on Wi-Fi connected`
                  : "Waiting for Wi-Fi bulbs"}
              </Chip>
            ) : (
              <Chip tone="warn" title="Run the service with the repo's .venv to enable MQTT">
                MQTT off
              </Chip>
            )}
          </>
        ) : null}

        <span className="mx-1 hidden h-6 w-px bg-line sm:block" />

        {adding ? (
          <form
            className="flex items-center gap-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              onAdd(ip.trim());
              setAdding(false);
            }}
          >
            <input
              autoFocus
              value={ip}
              onChange={(e) => setIp(e.target.value)}
              aria-label="Bulb IP address"
              className="w-32 rounded-lg border border-line bg-panel-2 px-2 py-1.5 font-mono text-sm outline-none focus:border-accent"
            />
            <Button type="submit" size="sm" variant="primary">
              Add
            </Button>
            <Button type="button" size="sm" onClick={() => setAdding(false)}>
              Cancel
            </Button>
          </form>
        ) : (
          <>
            <Button size="sm" onClick={onScan} disabled={scanning}>
              {scanning ? <Spinner /> : "⟳"} {scanning ? "Scanning…" : "Scan"}
            </Button>
            <Button size="sm" onClick={() => setAdding(true)}>
              + IP
            </Button>
            <Button size="sm" variant="primary" onClick={onWifi}>
              Wi-Fi setup
            </Button>
          </>
        )}
      </div>
    </header>
  );
}
