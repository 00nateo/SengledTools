"use client";

import { useState } from "react";
import type { Bulb } from "@/lib/api";
import { bulbDisplayColor, rgbCss } from "@/lib/color";
import { bulbLabel } from "./LightsApp";
import { Panel } from "./ui";

export function BulbList({
  bulbs,
  selected,
  onSelect,
  onPower,
  onRename,
}: {
  bulbs: Bulb[];
  selected: string[];
  onSelect: (ids: string[]) => void;
  onPower: (b: Bulb, on: boolean) => void;
  onRename: (id: string, name: string) => void;
}) {
  const allSelected = bulbs.length > 0 && bulbs.every((b) => selected.includes(b.id));
  const toggle = (id: string) =>
    onSelect(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);

  return (
    <Panel
      title={`Bulbs · ${bulbs.length}`}
      right={
        bulbs.length > 1 && (
          <button
            onClick={() => onSelect(allSelected ? bulbs.slice(0, 1).map((b) => b.id) : bulbs.map((b) => b.id))}
            className="text-xs text-accent hover:underline"
          >
            {allSelected ? "Select one" : "Select all"}
          </button>
        )
      }
    >
      <ul className="flex flex-col gap-2.5">
        {bulbs.map((b) => (
          <BulbCard
            key={b.id}
            bulb={b}
            selected={selected.includes(b.id)}
            multi={selected.length > 1}
            onClick={() => onSelect([b.id])}
            onToggle={() => toggle(b.id)}
            onPower={(on) => onPower(b, on)}
            onRename={(name) => onRename(b.id, name)}
          />
        ))}
      </ul>
      {bulbs.length > 1 && (
        <p className="mt-3 text-xs text-muted">Click a bulb to control it alone, or tick several to control them together.</p>
      )}
    </Panel>
  );
}

function BulbCard({
  bulb: b,
  selected,
  multi,
  onClick,
  onToggle,
  onPower,
  onRename,
}: {
  bulb: Bulb;
  selected: boolean;
  multi: boolean;
  onClick: () => void;
  onToggle: () => void;
  onPower: (on: boolean) => void;
  onRename: (name: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(b.name ?? "");
  const reachable = b.via === "udp" || b.connected;
  const color = reachable ? bulbDisplayColor(b) : null;
  const lastKnown = bulbDisplayColor(b);
  const status = b.apMode
    ? { text: "Setup mode", cls: "text-warn" }
    : reachable
      ? { text: b.via === "mqtt" ? "Online · Wi-Fi" : "Online", cls: "text-ok" }
      : { text: "Offline", cls: "text-muted" };

  return (
    <li
      onClick={onClick}
      className={`group relative cursor-pointer overflow-hidden rounded-xl border p-3 transition ${
        selected ? "border-accent/70 bg-panel-2" : "border-line bg-panel-2/40 hover:border-[#3a3a48]"
      }`}
      style={{
        backgroundImage: color
          ? `radial-gradient(120% 140% at 0% 50%, ${rgbCss(color, 0.28)}, transparent 60%)`
          : undefined,
      }}
    >
      <div className="flex items-center gap-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          onClick={(e) => e.stopPropagation()}
          aria-label={`Select ${bulbLabel(b)}`}
          className={`h-4 w-4 accent-[var(--accent)] ${multi || selected ? "" : "opacity-40 group-hover:opacity-100"}`}
        />
        <div
          className="h-10 w-10 shrink-0 rounded-full border border-white/10"
          style={{
            background: color ? rgbCss(color) : lastKnown ? rgbCss(lastKnown, 0.25) : "#2a2a33",
            boxShadow: color ? `0 0 22px ${rgbCss(color, 0.65)}` : "none",
          }}
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          {editing ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                onRename(name);
                setEditing(false);
              }}
              onClick={(e) => e.stopPropagation()}
            >
              <input
                autoFocus
                value={name}
                placeholder={bulbLabel({ ...b, name: null })}
                onChange={(e) => setName(e.target.value)}
                onBlur={() => {
                  onRename(name);
                  setEditing(false);
                }}
                className="w-full rounded-md border border-accent bg-bg px-2 py-1 text-sm outline-none"
              />
            </form>
          ) : (
            <div className="flex items-center gap-1.5">
              <span className="truncate font-medium">{bulbLabel(b)}</span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setName(b.name ?? "");
                  setEditing(true);
                }}
                className="text-xs text-muted opacity-0 transition group-hover:opacity-100 hover:text-text"
                aria-label="Rename"
                title="Rename"
              >
                ✎
              </button>
            </div>
          )}
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted">
            <span className={status.cls}>● {status.text}</span>
            <span className="font-mono">{b.ip ?? "?"}</span>
            <span className="rounded bg-black/30 px-1 font-mono text-[10px] uppercase">{b.via}</span>
            {b.model && <span>{b.model}</span>}
            {!b.apMode && (b.ssid || b.rssi !== null) && <WifiInfo ssid={b.ssid} rssi={b.rssi} />}
            {reachable && b.cycle && <span className="text-accent">↻ cycling</span>}
            {reachable && !b.cycle && b.effect && (
              <span className="inline-flex items-center gap-1 text-accent">
                <span className={`fx-${b.effect} h-2.5 w-2.5 rounded-full`} aria-hidden />
                effect
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onPower(!b.on);
            }}
            disabled={!reachable}
            role="switch"
            aria-checked={reachable && !!b.on}
            aria-label={`Power ${bulbLabel(b)}`}
            className={`relative h-6 w-11 rounded-full transition disabled:opacity-40 ${reachable && b.on ? "bg-accent" : "bg-[#33333f]"}`}
          >
            <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition ${reachable && b.on ? "left-[22px]" : "left-0.5"}`} />
          </button>
          {reachable && b.on && b.brightness !== null && <span className="text-[11px] text-muted">{b.brightness}%</span>}
        </div>
      </div>
    </li>
  );
}

function signal(rssi: number) {
  if (rssi >= -60) return { bars: 4, label: "strong", cls: "text-ok" };
  if (rssi >= -67) return { bars: 3, label: "good", cls: "text-ok" };
  if (rssi >= -75) return { bars: 2, label: "fair", cls: "text-warn" };
  return { bars: 1, label: "weak", cls: "text-err" };
}

function WifiInfo({ ssid, rssi }: { ssid: string | null; rssi: number | null }) {
  const sig = rssi !== null ? signal(rssi) : null;
  return (
    <span
      className="inline-flex items-center gap-1"
      title={
        sig
          ? `Wi-Fi signal ${rssi} dBm (${sig.label}) when the bulb last connected${sig.bars < 2 ? " - weak signal can delay or drop commands" : ""}`
          : undefined
      }
    >
      {sig && (
        <span className={`inline-flex items-end gap-px ${sig.cls}`} aria-label={`signal ${sig.label}`}>
          {[1, 2, 3, 4].map((n) => (
            <span
              key={n}
              className={`w-[3px] rounded-sm ${n <= sig.bars ? "bg-current" : "bg-white/15"}`}
              style={{ height: 3 + n * 2 }}
            />
          ))}
        </span>
      )}
      <span className="max-w-[9rem] truncate">{ssid ?? "unknown Wi-Fi"}</span>
      {sig && sig.bars < 2 && <span className={sig.cls}>weak</span>}
    </span>
  );
}
