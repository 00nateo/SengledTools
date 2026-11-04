"use client";

import { useEffect, useState } from "react";
import { HexColorPicker } from "react-colorful";
import type { Action, Bulb, RGB } from "@/lib/api";
import {
  COLOR_PRESETS,
  WHITE_PRESETS,
  bulbDisplayColor,
  fromHex,
  rgbCss,
  tempToKelvin,
  tempToRgb,
  toHex,
} from "@/lib/color";
import { bulbLabel } from "./LightsApp";
import { Button, Panel } from "./ui";
import { FadePanel, type FadeSpec, type FadeTimers } from "./FadePanel";
import { CyclePanel } from "./CyclePanel";
import { StatsPanel } from "./StatsPanel";

const RECENT_KEY = "sengled.recentColors";

type OnAction = (a: Action, ids?: string[], guess?: Partial<Bulb>) => Promise<void>;

export function ControlPanel({
  bulbs,
  totalBulbs,
  effects,
  palettes,
  cycleLimits,
  fades,
  onFade,
  onAction,
  onForget,
}: {
  bulbs: Bulb[];
  totalBulbs: number;
  effects: { id: string; label: string }[];
  palettes: { id: string; label: string; preview: RGB[] }[];
  cycleLimits: { min: number; max: number };
  fades: FadeTimers;
  onFade: (spec: FadeSpec, ids: string[]) => void;
  onAction: OnAction;
  onForget: (id: string) => void;
}) {
  const lead = bulbs[0];
  const [tab, setTab] = useState<"color" | "white">(lead?.mode === "white" ? "white" : "color");

  if (!lead) {
    return (
      <Panel className="grid place-items-center text-center">
        <p className="py-16 text-muted">Pick a bulb on the left to control it.</p>
      </Panel>
    );
  }

  const ids = bulbs.map((b) => b.id);
  const reachable = bulbs.some((b) => b.via === "udp" || b.connected);
  const act: OnAction = (a, only, guess) => onAction(a, only ?? ids, guess);
  const title = bulbs.length === 1 ? bulbLabel(lead) : bulbs.length === totalBulbs ? `All bulbs (${bulbs.length})` : `${bulbs.length} bulbs`;
  const glow = reachable ? bulbDisplayColor(lead) : null;

  return (
    <div className="flex flex-col gap-4">
      <Panel>
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <h2 className="truncate text-xl font-semibold">{title}</h2>
            <p className="mt-0.5 text-sm text-muted">
              {!reachable
                ? lead.via === "mqtt"
                  ? "Not connected - it reconnects to this Mac within ~1 min of the service starting."
                  : "Not answering."
                : bulbs.length > 1
                  ? bulbs.map(bulbLabel).join(", ")
                  : lead.on === null
                    ? "State unknown until it reports"
                    : lead.on
                      ? `On · ${lead.brightness ?? "?"}%${
                          lead.cycle
                            ? ` · ${palettes.find((p) => p.id === lead.cycle?.palette)?.label ?? "Custom"} cycle`
                            : lead.effect
                            ? ` · ${effects.find((e) => e.id === lead.effect)?.label ?? lead.effect}`
                            : lead.mode === "white" && lead.temp !== null
                              ? ` · ${tempToKelvin(lead.temp)} K`
                              : ""
                        }`
                      : "Off"}
            </p>
          </div>
          <div className="flex gap-2">
            <Button size="lg" disabled={!reachable} onClick={() => act({ action: "power", value: false }, undefined, { on: false })} className={lead.on === false ? "ring-1 ring-white/20" : ""}>
              Off
            </Button>
            <Button
              size="lg"
              variant="primary"
              disabled={!reachable}
              onClick={() => act({ action: "power", value: true }, undefined, { on: true })}
              style={glow ? { boxShadow: `0 0 24px ${rgbCss(glow, 0.5)}` } : undefined}
            >
              On
            </Button>
          </div>
        </div>

        <Brightness bulb={lead} disabled={!reachable} onCommit={(n) => act({ action: "brightness", value: n }, undefined, { brightness: n, on: true })} />
      </Panel>

      <Panel
        title={
          <div className="flex gap-1 rounded-lg bg-panel-2 p-1 normal-case tracking-normal" role="tablist">
            {(["color", "white"] as const).map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={tab === t}
                onClick={() => setTab(t)}
                className={`rounded-md px-4 py-1.5 text-sm font-medium transition ${tab === t ? "bg-bg text-text" : "text-muted hover:text-text"}`}
              >
                {t === "color" ? "Color" : "White"}
              </button>
            ))}
          </div>
        }
      >
        {tab === "color" ? (
          <ColorTab
            key={lead.id}
            bulb={lead}
            disabled={!reachable}
            onCommit={(rgb) => act({ action: "color", value: rgb }, undefined, { rgb, mode: "color", on: true, effect: null, cycle: null })}
          />
        ) : (
          <WhiteTab
            key={lead.id}
            bulb={lead}
            disabled={!reachable}
            onCommit={(t) => act({ action: "temp", value: t }, undefined, { temp: t, mode: "white", on: true, effect: null, cycle: null })}
          />
        )}
      </Panel>

      <Effects bulbs={bulbs} effects={effects} disabled={!reachable} onAction={act} />

      <CyclePanel key={`cycle-${ids.join(",")}`} bulbs={bulbs} palettes={palettes} limits={cycleLimits} disabled={!reachable} onAction={act} />

      <FadePanel key={lead.id} bulbs={bulbs} disabled={!reachable} timers={fades} onStart={onFade} />

      <StatsPanel key={`stats-${lead.id}`} bulb={lead} />

      <Advanced bulbs={bulbs} disabled={!reachable} onAction={act} onForget={onForget} />
    </div>
  );
}

/** A slider that follows the bulb's reported value until you drag, then sends on release. */
function useCommitSlider(remote: number | null, fallback: number) {
  const [value, setValue] = useState(remote ?? fallback);
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    // Keep in sync with what the bulb reports, except mid-drag.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!dragging && remote !== null) setValue(remote);
  }, [remote, dragging]);
  return { value, setValue, dragging, setDragging };
}

function Brightness({ bulb, disabled, onCommit }: { bulb: Bulb; disabled: boolean; onCommit: (n: number) => void }) {
  const s = useCommitSlider(bulb.brightness, 100);
  const commit = () => {
    s.setDragging(false);
    if (s.value !== bulb.brightness) onCommit(s.value);
  };
  return (
    <div className="mt-5">
      <div className="mb-2 flex items-baseline justify-between text-sm">
        <label htmlFor="brightness" className="text-muted">Brightness</label>
        <span className="font-mono tabular-nums">{s.value}%</span>
      </div>
      <input
        id="brightness"
        type="range"
        min={1}
        max={100}
        value={s.value}
        disabled={disabled}
        className="slider"
        style={{ background: `linear-gradient(90deg, #222 0%, #f5e6c4 ${s.value}%, #2a2a33 ${s.value}%)` }}
        onPointerDown={() => s.setDragging(true)}
        onChange={(e) => s.setValue(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
      />
      <div className="mt-2 flex gap-1.5">
        {[10, 25, 50, 75, 100].map((n) => (
          <button
            key={n}
            disabled={disabled}
            onClick={() => {
              s.setValue(n);
              onCommit(n);
            }}
            className="flex-1 rounded-md border border-line bg-panel-2 py-1 text-xs text-muted hover:text-text disabled:opacity-40"
          >
            {n}%
          </button>
        ))}
      </div>
    </div>
  );
}

function ColorTab({ bulb, disabled, onCommit }: { bulb: Bulb; disabled: boolean; onCommit: (rgb: RGB) => void }) {
  const [hex, setHex] = useState(bulb.rgb ? toHex(bulb.rgb) : "#ff8000");
  const [hexInput, setHexInput] = useState(hex);
  const [recent, setRecent] = useState<string[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      return JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    } catch {
      return [];
    }
  });

  const send = (h: string) => {
    const rgb = fromHex(h);
    if (!rgb) return;
    onCommit(rgb);
    const next = [h, ...recent.filter((x) => x !== h)].slice(0, 10);
    setRecent(next);
    try {
      localStorage.setItem(RECENT_KEY, JSON.stringify(next));
    } catch {}
  };

  const pick = (h: string) => {
    setHex(h);
    setHexInput(h);
  };

  return (
    <div className={`grid gap-5 md:grid-cols-2 ${disabled ? "pointer-events-none opacity-50" : ""}`}>
      <div>
        {/* Sends when you let go, so dragging doesn't flood the bulb. */}
        <div className="picker" onPointerUp={() => send(hex)}>
          <HexColorPicker color={hex} onChange={pick} />
        </div>
        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const rgb = fromHex(hexInput);
            if (rgb) {
              const h = toHex(rgb);
              pick(h);
              send(h);
            }
          }}
        >
          <span className="h-9 w-9 shrink-0 rounded-lg border border-white/10" style={{ background: hex }} aria-hidden />
          <input
            value={hexInput}
            onChange={(e) => setHexInput(e.target.value)}
            aria-label="Hex color"
            className="min-w-0 flex-1 rounded-lg border border-line bg-panel-2 px-3 font-mono text-sm outline-none focus:border-accent"
          />
          <Button type="submit">Set</Button>
        </form>
      </div>

      <div>
        <p className="mb-2 text-xs text-muted">Presets</p>
        <div className="grid grid-cols-6 gap-2">
          {COLOR_PRESETS.map((p) => (
            <button
              key={p.name}
              title={p.name}
              aria-label={p.name}
              onClick={() => {
                const h = toHex(p.rgb);
                pick(h);
                send(h);
              }}
              className="aspect-square rounded-lg border border-white/10 transition hover:scale-110 focus-visible:scale-110 focus-visible:outline-2 focus-visible:outline-accent"
              style={{ background: rgbCss(p.rgb) }}
            />
          ))}
        </div>
        {recent.length > 0 && (
          <>
            <p className="mt-4 mb-2 text-xs text-muted">Recent</p>
            <div className="flex flex-wrap gap-2">
              {recent.map((h) => (
                <button
                  key={h}
                  title={h}
                  aria-label={`Recent color ${h}`}
                  onClick={() => {
                    pick(h);
                    send(h);
                  }}
                  className="h-8 w-8 rounded-full border border-white/10 transition hover:scale-110"
                  style={{ background: h }}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function WhiteTab({ bulb, disabled, onCommit }: { bulb: Bulb; disabled: boolean; onCommit: (t: number) => void }) {
  const s = useCommitSlider(bulb.temp, 30);
  const commit = () => {
    s.setDragging(false);
    onCommit(s.value);
  };
  const stops = [0, 25, 50, 75, 100].map((t) => `${rgbCss(tempToRgb(t))} ${t}%`).join(", ");

  return (
    <div className={disabled ? "pointer-events-none opacity-50" : ""}>
      <div className="mb-4 flex items-center gap-4">
        <div
          className="h-16 w-16 shrink-0 rounded-full border border-white/10"
          style={{ background: rgbCss(tempToRgb(s.value)), boxShadow: `0 0 28px ${rgbCss(tempToRgb(s.value), 0.6)}` }}
          aria-hidden
        />
        <div>
          <div className="font-mono text-2xl tabular-nums">{tempToKelvin(s.value)} K</div>
          <div className="text-sm text-muted">{s.value <= 20 ? "Warm" : s.value <= 60 ? "Neutral" : "Cool"} white · {s.value}</div>
        </div>
      </div>
      <label htmlFor="temp" className="sr-only">White temperature</label>
      <input
        id="temp"
        type="range"
        min={0}
        max={100}
        value={s.value}
        className="slider"
        style={{ background: `linear-gradient(90deg, ${stops})` }}
        onPointerDown={() => s.setDragging(true)}
        onChange={(e) => s.setValue(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
      />
      <div className="mt-1 flex justify-between text-[11px] text-muted">
        <span>2700 K</span>
        <span>6500 K</span>
      </div>
      <div className="mt-4 grid grid-cols-3 gap-2 sm:grid-cols-6">
        {WHITE_PRESETS.map((p) => (
          <button
            key={p.name}
            onClick={() => {
              s.setValue(p.temp);
              onCommit(p.temp);
            }}
            className={`flex flex-col items-center gap-1.5 rounded-lg border px-2 py-2 text-xs transition hover:border-[#4a4a58] ${
              bulb.mode === "white" && bulb.temp === p.temp ? "border-accent/70 bg-panel-2" : "border-line"
            }`}
          >
            <span className="h-6 w-6 rounded-full" style={{ background: rgbCss(tempToRgb(p.temp)) }} aria-hidden />
            {p.name}
          </button>
        ))}
      </div>
    </div>
  );
}

function Effects({
  bulbs,
  effects,
  disabled,
  onAction,
}: {
  bulbs: Bulb[];
  effects: { id: string; label: string }[];
  disabled: boolean;
  onAction: OnAction;
}) {
  const wifiBulbs = bulbs.filter((b) => b.via === "mqtt");
  const wifiIds = wifiBulbs.map((b) => b.id);
  const active = bulbs[0].effect;
  const unsupported = wifiBulbs.length === 0;
  return (
    <Panel
      title="Effects"
      right={
        active && (
          <Button size="sm" onClick={() => onAction({ action: "effect", value: null }, wifiIds, { effect: null })}>
            ■ Stop effect
          </Button>
        )
      }
    >
      {unsupported ? (
        <p className="text-sm text-muted">
          Effects run on the bulb itself, but it only accepts them once it&apos;s on Wi-Fi (connected over MQTT) - not in setup mode.
        </p>
      ) : (
        <>
          <div className={`grid grid-cols-2 gap-2 sm:grid-cols-3 ${disabled ? "pointer-events-none opacity-50" : ""}`}>
            {effects.map((fx) => {
              const on = active === fx.id;
              return (
                <button
                  key={fx.id}
                  aria-pressed={on}
                  onClick={() =>
                    on
                      ? onAction({ action: "effect", value: null }, wifiIds, { effect: null })
                      : onAction({ action: "effect", value: fx.id }, wifiIds, { effect: fx.id, on: true, cycle: null })
                  }
                  className={`flex items-center gap-3 rounded-xl border p-2.5 text-left text-sm transition ${
                    on ? "border-accent/70 bg-panel-2" : "border-line hover:border-[#4a4a58] hover:bg-panel-2/60"
                  }`}
                >
                  <span className={`fx-${fx.id} h-9 w-9 shrink-0 rounded-full`} aria-hidden />
                  <span className="flex flex-col">
                    <span className="font-medium">{fx.label}</span>
                    <span className={`text-xs ${on ? "text-accent" : "text-muted"}`}>{on ? "Running · tap to stop" : "Tap to start"}</span>
                  </span>
                </button>
              );
            })}
          </div>
          <p className="mt-3 text-xs text-muted">
            Runs on the bulb by itself until you stop it or pick a color.
            {wifiBulbs.length < bulbs.length && " Bulbs in setup mode are skipped."}
          </p>
        </>
      )}
    </Panel>
  );
}

function Advanced({
  bulbs,
  disabled,
  onAction,
  onForget,
}: {
  bulbs: Bulb[];
  disabled: boolean;
  onAction: OnAction;
  onForget: (id: string) => void;
}) {
  const [raw, setRaw] = useState("");
  const lead = bulbs[0];
  const single = bulbs.length === 1;
  return (
    <details className="group rounded-2xl border border-line bg-panel">
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold tracking-wide text-muted uppercase select-none">
        <span className="mr-2 inline-block transition group-open:rotate-90">›</span>Advanced
      </summary>
      <div className="flex flex-col gap-4 border-t border-line p-4">
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (raw.trim()) onAction({ action: "raw", value: raw.trim() });
          }}
        >
          <label htmlFor="raw" className="text-xs text-muted">
            Raw command ({lead.via === "mqtt" ? "MQTT item" : "UDP JSON"})
          </label>
          <div className="flex gap-2">
            <input
              id="raw"
              value={raw}
              onChange={(e) => setRaw(e.target.value)}
              placeholder={lead.via === "mqtt" ? '{"type":"switch","value":"1"}' : '{"func":"get_device_mac","param":{}}'}
              className="min-w-0 flex-1 rounded-lg border border-line bg-panel-2 px-3 py-2 font-mono text-sm outline-none focus:border-accent"
            />
            <Button type="submit" disabled={disabled}>
              Send
            </Button>
          </div>
        </form>
        <div className="flex flex-wrap gap-2">
          <Button disabled={disabled || lead.via === "mqtt"} onClick={() => onAction({ action: "reboot" })} title={lead.via === "mqtt" ? "Not available over MQTT - power-cycle the bulb" : undefined}>
            Reboot
          </Button>
          <Button
            variant="danger"
            disabled={disabled}
            onClick={() => {
              const n = bulbs.length;
              const answer = window.prompt(
                `Factory reset ${n === 1 ? bulbLabel(lead) : `${n} bulbs`}? ${n === 1 ? "It" : "They"} will forget ${n === 1 ? "its" : "their"} Wi-Fi.\nType RESET to confirm.`,
              );
              if (answer === "RESET") onAction({ action: "reset" });
            }}
          >
            Factory reset
          </Button>
          {single && (
            <Button variant="ghost" onClick={() => onForget(lead.id)} title="Remove from this list (doesn't change the bulb)">
              Forget bulb
            </Button>
          )}
        </div>
        {single && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs text-muted">
            <dt>MAC</dt>
            <dd className="font-mono text-text">{lead.mac ?? "?"}</dd>
            <dt>IP</dt>
            <dd className="font-mono text-text">{lead.ip ?? "?"}</dd>
            <dt>Connection</dt>
            <dd className="text-text">{lead.via === "mqtt" ? "MQTT via this Mac (port 8883)" : "UDP (port 9080)"}</dd>
            <dt>Model / firmware</dt>
            <dd className="text-text">
              {lead.model ?? "?"} / {lead.version ?? "?"}
            </dd>
          </dl>
        )}
      </div>
    </details>
  );
}
