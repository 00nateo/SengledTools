"use client";

import { useState } from "react";
import type { Action, Bulb, CycleSettings, RGB } from "@/lib/api";
import { rgbCss } from "@/lib/color";
import { Button, Panel } from "./ui";

type Palette = { id: string; label: string; preview: RGB[] };

// Slider 0..100 maps logarithmically onto seconds-per-loop, slow (left) to fast (right).
const toPeriod = (x: number, min: number, max: number) => Math.round(max * Math.pow(min / max, x / 100));
const toSlider = (p: number, min: number, max: number) => Math.round((Math.log(p / max) / Math.log(min / max)) * 100);

const fmt = (s: number) => (s >= 60 ? `${+(s / 60).toFixed(s % 60 ? 1 : 0)} min` : `${s} s`);

export function CyclePanel({
  bulbs,
  palettes,
  limits,
  disabled,
  onAction,
}: {
  bulbs: Bulb[];
  palettes: Palette[];
  limits: { min: number; max: number };
  disabled: boolean;
  onAction: (a: Action, ids?: string[], guess?: Partial<Bulb>) => Promise<void>;
}) {
  const running = bulbs[0].cycle;
  const [palette, setPalette] = useState(running?.palette ?? "rainbow");
  const [slider, setSlider] = useState(toSlider(running?.period ?? 60, limits.min, limits.max));
  const [spread, setSpread] = useState(running?.spread ?? false);
  const period = toPeriod(slider, limits.min, limits.max);
  const ids = bulbs.map((b) => b.id);

  const send = (s: CycleSettings) => onAction({ action: "cycle", value: s }, ids, { cycle: s, effect: null, on: true });
  // While running, palette/speed/spread changes apply live.
  const apply = (patch: Partial<CycleSettings>) => {
    if (running) send({ palette, period, spread, ...patch });
  };

  if (!palettes.length) return null;
  return (
    <Panel
      title="Custom color cycle"
      right={
        running && (
          <Button size="sm" onClick={() => onAction({ action: "cycle", value: null }, ids, { cycle: null })}>
            ■ Stop
          </Button>
        )
      }
    >
      <div className={disabled ? "pointer-events-none opacity-50" : ""}>
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          {palettes.map((p) => (
            <button
              key={p.id}
              aria-pressed={palette === p.id}
              onClick={() => {
                setPalette(p.id);
                apply({ palette: p.id });
              }}
              className={`flex flex-col items-center gap-1.5 rounded-xl border p-2 text-xs transition ${
                palette === p.id ? "border-accent/70 bg-panel-2" : "border-line hover:border-[#4a4a58]"
              }`}
            >
              <span
                className={`h-7 w-full rounded-md ${running && running.palette === p.id ? "animate-pulse" : ""}`}
                style={{ background: `linear-gradient(90deg, ${[...p.preview, p.preview[0]].map((c) => rgbCss(c)).join(", ")})` }}
                aria-hidden
              />
              {p.label}
            </button>
          ))}
        </div>

        <div className="mt-5">
          <div className="mb-2 flex items-baseline justify-between text-sm">
            <label htmlFor="cycle-speed" className="text-muted">Speed</label>
            <span className="font-mono text-xs tabular-nums">one loop every {fmt(period)}</span>
          </div>
          <input
            id="cycle-speed"
            type="range"
            min={0}
            max={100}
            value={slider}
            onChange={(e) => setSlider(Number(e.target.value))}
            onPointerUp={() => apply({ period })}
            onKeyUp={() => apply({ period })}
            className="slider"
            style={{ background: "linear-gradient(90deg, #2a2a40, #f5b544)" }}
          />
          <div className="mt-1 flex justify-between text-[11px] text-muted">
            <span>Slow ({fmt(limits.max)})</span>
            <span>Fast ({fmt(limits.min)})</span>
          </div>
        </div>

        {bulbs.length > 1 && (
          <label className="mt-4 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={spread}
              onChange={(e) => {
                setSpread(e.target.checked);
                apply({ spread: e.target.checked });
              }}
              className="accent-[var(--accent)]"
            />
            Spread colors across the {bulbs.length} bulbs (instead of all the same color)
          </label>
        )}

        <div className="mt-4 flex items-center justify-between gap-3">
          <p className="text-xs text-muted">
            Runs from this Mac, so keep the app running. Picking a color, white, an effect or a fade stops it.
          </p>
          {running ? (
            <span className="shrink-0 text-sm text-accent">Cycling…</span>
          ) : (
            <Button variant="primary" className="shrink-0" onClick={() => send({ palette, period, spread })}>
              Start cycle
            </Button>
          )}
        </div>
      </div>
    </Panel>
  );
}
