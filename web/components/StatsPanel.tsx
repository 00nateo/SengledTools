"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Bulb, type BulbStats, type PingResult } from "@/lib/api";
import { Button, Spinner } from "./ui";

const POLL_MS = 3000;

/** "Stats for nerds": connection health, latency and device details for one bulb. */
export function StatsPanel({ bulb }: { bulb: Bulb }) {
  const [open, setOpen] = useState(false);
  const [stats, setStats] = useState<BulbStats | null>(null);
  const [now, setNow] = useState(0); // when the stats were fetched (s)
  const [error, setError] = useState<string | null>(null);
  const [ping, setPing] = useState<PingResult | null>(null);
  const [pinging, setPinging] = useState(false);
  const [pingError, setPingError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStats(await api.stats(bulb.id));
      setNow(Date.now() / 1000);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [bulb.id]);

  useEffect(() => {
    if (!open) return;
    // Polling an external service while the panel is open.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [open, load]);

  const runPing = async () => {
    setPinging(true);
    setPingError(null);
    try {
      setPing(await api.ping(bulb.id, 5));
      load();
    } catch (e) {
      setPingError((e as Error).message);
    } finally {
      setPinging(false);
    }
  };

  const s = stats;
  const c = s?.connection;
  const cmd = s?.commands;
  const flapping = c && c.reconnectsLastHour >= 6 && c.medianReconnectInterval !== null;

  return (
    <details className="group rounded-2xl border border-line bg-panel" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-semibold tracking-wide text-muted uppercase select-none">
        <span>
          <span className="mr-2 inline-block transition group-open:rotate-90">›</span>Stats for nerds
        </span>
        {open && !s && !error && <Spinner />}
      </summary>
      <div className="flex flex-col gap-4 border-t border-line p-4">
        {error && <p className="text-sm text-err">{error}</p>}

        {/* Ping */}
        <div className="rounded-xl border border-line bg-panel-2/50 p-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">Ping</p>
              <p className="text-xs text-muted">
                {bulb.via === "mqtt"
                  ? "5 round trips over MQTT (re-sends the current power state; the bulb answers without changing)"
                  : "5 round trips over UDP"}
              </p>
            </div>
            <Button size="sm" onClick={runPing} disabled={pinging}>
              {pinging ? <Spinner /> : "Ping"}
            </Button>
          </div>
          {pingError && <p className="mt-2 text-sm text-err">{pingError}</p>}
          {ping && (
            <div className="mt-3 flex flex-col gap-2 font-mono text-xs">
              <div className="flex flex-wrap gap-1.5">
                {ping.rounds.map((r, i) => (
                  <span
                    key={i}
                    className={`rounded-md border px-2 py-0.5 tabular-nums ${r === null ? "border-err/40 text-err" : latencyTone(r)}`}
                  >
                    {r === null ? "timeout" : `${r} ms`}
                  </span>
                ))}
              </div>
              <p className="text-muted">
                min/avg/max{" "}
                <span className="text-text">
                  {ms(ping.minMs)} / {ms(ping.avgMs)} / {ms(ping.maxMs)}
                </span>{" "}
                · loss <span className={ping.loss ? "text-err" : "text-ok"}>{ping.loss}%</span>
              </p>
              {ping.icmp && (
                <p className="text-muted">
                  system ping (ICMP):{" "}
                  {ping.icmp.loss >= 100 ? (
                    <span>no reply (often blocked on shared / public Wi-Fi, so not conclusive)</span>
                  ) : (
                    <span className="text-text">
                      {ms(ping.icmp.avgMs)} avg · {ping.icmp.loss}% loss
                    </span>
                  )}
                </p>
              )}
            </div>
          )}
        </div>

        {s && (
          <>
            {flapping && (
              <p className="rounded-xl border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
                Reconnecting about every {Math.round(c!.medianReconnectInterval!)} s ({c!.reconnectsLastHour} times in
                the last hour). Commands sent while it&apos;s reconnecting are delayed or resent.
              </p>
            )}

            <div className="grid gap-3 sm:grid-cols-2">
              <Stat
                label="Wi-Fi signal"
                value={s.wifi.rssi !== null ? `${s.wifi.rssi} dBm` : "—"}
                sub={
                  s.wifi.rssi !== null
                    ? `${s.wifi.quality} · range ${s.wifi.min} to ${s.wifi.max} dBm`
                    : "reported when the bulb connects"
                }
                tone={rssiTone(s.wifi.rssi)}
              >
                <Spark values={s.wifi.history.map(([, v]) => v)} min={-90} max={-40} />
              </Stat>

              <Stat
                label="Response time"
                value={cmd?.avgMs != null ? `${cmd.avgMs} ms avg` : "—"}
                sub={
                  cmd && cmd.count
                    ? `p95 ${ms(cmd.p95Ms)} · max ${ms(cmd.maxMs)} · ${Math.round((cmd.successRate ?? 0) * 100)}% of ${cmd.count} confirmed`
                    : "send a command or ping to measure"
                }
                tone={cmd?.avgMs != null ? latencyTone(cmd.avgMs) : undefined}
              >
                <Spark values={(cmd?.history ?? []).map(([, v]) => v)} min={0} />
              </Stat>

              <Stat
                label="Uptime"
                value={s.uptime.seconds !== null ? duration(s.uptime.seconds) : "—"}
                sub={
                  s.uptime.bootedAt
                    ? `booted ${clock(s.uptime.bootedAt)}${s.uptime.rebootsSeen ? ` · ${s.uptime.rebootsSeen} reboot${s.uptime.rebootsSeen > 1 ? "s" : ""} seen` : ""}`
                    : "seconds since power-on, from the bulb"
                }
                tone={s.uptime.rebootsSeen ? "text-warn" : undefined}
              />

              <Stat
                label="Connection"
                value={s.connected ? `up ${c?.connectedSince ? ago(now - c.connectedSince) : "?"}` : "not connected"}
                sub={
                  c
                    ? `${c.reconnectsLastHour} reconnects / h${c.medianReconnectInterval ? ` · every ~${Math.round(c.medianReconnectInterval)} s` : ""}${c.openSockets !== undefined ? ` · ${c.openSockets} socket${c.openSockets === 1 ? "" : "s"} open${c.staleSockets ? ` (${c.staleSockets} stale)` : ""}` : ""}`
                    : ""
                }
                tone={!s.connected ? "text-err" : flapping ? "text-warn" : "text-ok"}
              >
                <Timeline times={c?.history ?? []} now={now} />
              </Stat>
            </div>

            {Object.keys(s.pending).length > 0 && (
              <p className="text-xs text-warn">
                Waiting to resend on reconnect:{" "}
                <span className="font-mono">
                  {Object.entries(s.pending)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(" ")}
                </span>
              </p>
            )}

            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-xs">
              {[
                ["mac", s.mac],
                ["ip", s.ip],
                ["network", s.ssid],
                ["transport", s.via === "mqtt" ? "MQTT over TLS :8883" : "UDP :9080"],
                ["last heard", c?.lastHeard ? `${ago(now - c.lastHeard)} ago` : null],
                ["tcp ports", c?.ports?.join(", ")],
                ["closed sockets 24h", c ? String(c.closedSockets24h) : null],
                ["reconnects 24h", c ? String(c.reconnects24h) : null],
                ...Object.entries(s.device),
              ]
                .filter(([, v]) => v)
                .map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-muted">{k}</dt>
                    <dd className="break-all">{v}</dd>
                  </div>
                ))}
            </dl>
            <p className="text-[11px] text-muted">
              History comes from <code className="font-mono">~/.sengled/logs/sengled.log</code>, shared with the TUI.
            </p>
          </>
        )}
      </div>
    </details>
  );
}

function Stat({
  label,
  value,
  sub,
  tone,
  children,
}: {
  label: string;
  value: string;
  sub: string;
  tone?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-line bg-panel-2/50 p-3">
      <p className="text-xs text-muted">{label}</p>
      <p className={`mt-0.5 font-mono text-lg tabular-nums ${tone ?? ""}`}>{value}</p>
      <p className="mt-0.5 text-[11px] leading-snug text-muted">{sub}</p>
      {children && <div className="mt-2">{children}</div>}
    </div>
  );
}

/** Tiny line chart; null values are drawn as red ticks (timeouts). */
function Spark({ values, min, max }: { values: (number | null)[]; min: number; max?: number }) {
  if (values.length < 2) return null;
  const nums = values.filter((v): v is number => v !== null);
  const hi = max ?? Math.max(min + 1, ...nums);
  const w = 200;
  const h = 32;
  const x = (i: number) => (i / (values.length - 1)) * w;
  const y = (v: number) => h - ((Math.min(hi, Math.max(min, v)) - min) / (hi - min)) * (h - 4) - 2;
  const path = values
    .map((v, i) => (v === null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`))
    .filter(Boolean)
    .join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-8 w-full" preserveAspectRatio="none" aria-hidden>
      <polyline points={path} fill="none" stroke="var(--accent)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      {values.map((v, i) => (v === null ? <line key={i} x1={x(i)} x2={x(i)} y1={0} y2={h} stroke="var(--err)" strokeWidth="1" vectorEffect="non-scaling-stroke" /> : null))}
    </svg>
  );
}

/** Reconnects over the last hour as ticks. */
function Timeline({ times, now }: { times: number[]; now: number }) {
  const recent = times.filter((t) => now - t < 3600);
  if (!recent.length) return null;
  return (
    <div>
      <div className="relative h-3 rounded bg-black/30">
        {recent.map((t, i) => (
          <span key={i} className="absolute top-0 h-3 w-px bg-warn" style={{ left: `${100 - ((now - t) / 3600) * 100}%` }} />
        ))}
      </div>
      <div className="mt-0.5 flex justify-between text-[10px] text-muted">
        <span>1 h ago</span>
        <span>now</span>
      </div>
    </div>
  );
}

const ms = (v: number | null) => (v === null ? "—" : `${Math.round(v)} ms`);

function latencyTone(v: number) {
  return v < 300 ? "border-ok/40 text-ok" : v < 1500 ? "border-warn/40 text-warn" : "border-err/40 text-err";
}

function rssiTone(v: number | null) {
  if (v === null) return undefined;
  return v >= -67 ? "text-ok" : v >= -75 ? "text-warn" : "text-err";
}

function duration(sec: number) {
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m ${Math.floor(sec % 60)}s`;
}

function ago(sec: number) {
  return sec < 60 ? `${Math.round(sec)} s` : sec < 3600 ? `${Math.round(sec / 60)} min` : `${(sec / 3600).toFixed(1)} h`;
}

function clock(t: number) {
  return new Date(t * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
