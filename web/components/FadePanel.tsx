"use client";

import { useEffect, useState } from "react";
import type { Bulb } from "@/lib/api";
import { rgbCss, tempToKelvin, tempToRgb } from "@/lib/color";
import { Button, Panel } from "./ui";

export interface FadeSpec {
  from: number;
  to: number;
  seconds: number;
  tempFrom?: number;
  tempTo?: number;
  offAtEnd?: boolean;
}

/** When a running fade started and ends (ms since epoch), per bulb id. */
export type FadeTimers = Record<string, { start: number; end: number }>;

const DURATIONS: { label: string; s: number }[] = [
  { label: "30 s", s: 30 },
  { label: "1 min", s: 60 },
  { label: "5 min", s: 300 },
  { label: "10 min", s: 600 },
  { label: "15 min", s: 900 },
  { label: "30 min", s: 1800 },
  { label: "1 h", s: 3600 },
];

const PRESETS: { name: string; hint: string; spec: (b: Bulb) => FadeSpec }[] = [
  { name: "Sunrise", hint: "1 → 100%, warm → daylight, 10 min", spec: () => ({ from: 1, to: 100, seconds: 600, tempFrom: 0, tempTo: 75 }) },
  { name: "Gentle wake", hint: "1 → 60%, warm → soft, 20 min", spec: () => ({ from: 1, to: 60, seconds: 1200, tempFrom: 0, tempTo: 35 }) },
  { name: "Bedtime", hint: "→ 1% warm over 15 min, then off", spec: (b) => ({ from: b.brightness ?? 100, to: 1, seconds: 900, tempFrom: b.temp ?? 30, tempTo: 0, offAtEnd: true }) },
  { name: "Quick dim", hint: "→ 15% over 30 s", spec: (b) => ({ from: b.brightness ?? 100, to: 15, seconds: 30 }) },
];

const fmt = (s: number) => {
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return m ? (r ? `${m} min ${r} s` : `${m} min`) : `${r} s`;
};

export function FadePanel({
  bulbs,
  disabled,
  timers,
  onStart,
}: {
  bulbs: Bulb[];
  disabled: boolean;
  timers: FadeTimers;
  onStart: (spec: FadeSpec, ids: string[]) => void;
}) {
  const lead = bulbs[0];
  const wifiIds = bulbs.filter((b) => b.via === "mqtt").map((b) => b.id);
  const [from, setFrom] = useState(lead.brightness ?? 100);
  const [to, setTo] = useState(1);
  const [seconds, setSeconds] = useState(600);
  const [customMin, setCustomMin] = useState("");
  const [useTemp, setUseTemp] = useState(false);
  const [tempFrom, setTempFrom] = useState(0);
  const [tempTo, setTempTo] = useState(75);
  const [offAtEnd, setOffAtEnd] = useState(false);
  const running = timers[lead.id];
  const now = useNow(running ? 1000 : null);

  if (!wifiIds.length) {
    return (
      <Panel title="Fade">
        <p className="text-sm text-muted">Fades run on the bulb itself, which only accepts them once it&apos;s on Wi-Fi (MQTT), not in setup mode.</p>
      </Panel>
    );
  }

  const start = (spec: FadeSpec) => {
    if (spec.from === spec.to) return;
    onStart(spec, wifiIds);
  };
  const remaining = running ? Math.max(0, (running.end - now) / 1000) : 0;
  const progress = running ? Math.min(1, (now - running.start) / (running.end - running.start)) : 0;
  const same = from === to;

  return (
    <Panel title="Fade · sunrise & bedtime">
      <div className={disabled ? "pointer-events-none opacity-50" : ""}>
        {running && remaining > 0 && (
          <div className="mb-4 rounded-xl border border-accent/40 bg-accent/10 p-3">
            <div className="flex justify-between text-sm">
              <span className="text-accent">Fading…</span>
              <span className="font-mono tabular-nums text-muted">{fmt(remaining)} left</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-black/40">
              <div className="h-full rounded-full bg-accent transition-[width] duration-1000 ease-linear" style={{ width: `${progress * 100}%` }} />
            </div>
            <p className="mt-2 text-xs text-muted">Any other change to the bulb ends the fade.</p>
          </div>
        )}

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {PRESETS.map((p) => (
            <button
              key={p.name}
              onClick={() => start(p.spec(lead))}
              className="flex flex-col rounded-xl border border-line p-2.5 text-left transition hover:border-[#4a4a58] hover:bg-panel-2/60"
            >
              <span className="text-sm font-medium">{p.name}</span>
              <span className="mt-0.5 text-[11px] leading-snug text-muted">{p.hint}</span>
            </button>
          ))}
        </div>

        <details className="group mt-4">
          <summary className="cursor-pointer list-none text-sm text-muted select-none hover:text-text">
            <span className="mr-1.5 inline-block transition group-open:rotate-90">›</span>Custom fade
          </summary>
          <div className="mt-3 flex flex-col gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Range label="From" value={from} onChange={setFrom} />
              <Range label="To" value={to} onChange={setTo} />
            </div>

            <div>
              <p className="mb-2 text-xs text-muted">Duration</p>
              <div className="flex flex-wrap gap-1.5">
                {DURATIONS.map((d) => (
                  <button
                    key={d.s}
                    onClick={() => {
                      setSeconds(d.s);
                      setCustomMin("");
                    }}
                    className={`rounded-md border px-2.5 py-1 text-xs ${seconds === d.s && !customMin ? "border-accent/70 bg-panel-2 text-text" : "border-line text-muted hover:text-text"}`}
                  >
                    {d.label}
                  </button>
                ))}
                <label className="flex items-center gap-1 rounded-md border border-line px-2 text-xs text-muted focus-within:border-accent">
                  <input
                    inputMode="numeric"
                    value={customMin}
                    placeholder="—"
                    aria-label="Custom duration in minutes"
                    onChange={(e) => {
                      const v = e.target.value.replace(/[^0-9.]/g, "");
                      setCustomMin(v);
                      const s = Math.round(parseFloat(v) * 60);
                      if (s >= 1) setSeconds(Math.min(s, 3600));
                    }}
                    className="w-10 bg-transparent py-1 text-right text-text outline-none"
                  />
                  min
                </label>
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={useTemp} onChange={(e) => setUseTemp(e.target.checked)} className="accent-[var(--accent)]" />
              Also shift white temperature
            </label>
            {useTemp && (
              <div className="grid gap-4 sm:grid-cols-2">
                <TempRange label="From" value={tempFrom} onChange={setTempFrom} />
                <TempRange label="To" value={tempTo} onChange={setTempTo} />
              </div>
            )}

            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={offAtEnd} onChange={(e) => setOffAtEnd(e.target.checked)} className="accent-[var(--accent)]" />
              Turn off at the end
            </label>

            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-muted">
                {same ? "From and To must differ." : `${from}% → ${to}% over ${fmt(seconds)}${useTemp ? `, ${tempToKelvin(tempFrom)} K → ${tempToKelvin(tempTo)} K` : ""}${offAtEnd ? ", then off" : ""}`}
              </p>
              <Button
                variant="primary"
                disabled={same}
                onClick={() => start({ from, to, seconds, offAtEnd, ...(useTemp ? { tempFrom, tempTo } : {}) })}
              >
                Start fade
              </Button>
            </div>
          </div>
        </details>
      </div>
    </Panel>
  );
}

function Range({ label, value, onChange }: { label: string; value: number; onChange: (n: number) => void }) {
  return (
    <label className="flex flex-col gap-2 text-sm">
      <span className="flex justify-between text-muted">
        {label} brightness <span className="font-mono tabular-nums text-text">{value}%</span>
      </span>
      <input
        type="range"
        min={1}
        max={100}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="slider"
        style={{ background: `linear-gradient(90deg, #222 0%, #f5e6c4 ${value}%, #2a2a33 ${value}%)` }}
      />
    </label>
  );
}

function TempRange({ label, value, onChange }: { label: string; value: number; onChange: (n: number) => void }) {
  const stops = [0, 50, 100].map((t) => `${rgbCss(tempToRgb(t))} ${t}%`).join(", ");
  return (
    <label className="flex flex-col gap-2 text-sm">
      <span className="flex justify-between text-muted">
        {label} white <span className="font-mono tabular-nums text-text">{tempToKelvin(value)} K</span>
      </span>
      <input
        type="range"
        min={0}
        max={100}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="slider"
        style={{ background: `linear-gradient(90deg, ${stops})` }}
      />
    </label>
  );
}

/** Current time, refreshed every `ms` while non-null (for countdowns). */
function useNow(ms: number | null) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (ms === null) return;
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}
