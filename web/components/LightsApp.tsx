"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Action, type AppState, type Bulb, type LogLine } from "@/lib/api";
import { BulbList } from "./BulbList";
import { ControlPanel } from "./ControlPanel";
import { TopBar } from "./TopBar";
import { WifiSetup } from "./WifiSetup";
import { ActivityLog, type LogEntry } from "./ActivityLog";
import { EmptyState } from "./EmptyState";
import type { FadeSpec, FadeTimers } from "./FadePanel";

const POLL_MS = 2500;
const SELECTION_KEY = "sengled.selected";

export function bulbLabel(b: Pick<Bulb, "name" | "mac" | "ip">) {
  if (b.name) return b.name;
  if (b.mac) return `Bulb ${b.mac.slice(-5).replace(":", "")}`;
  return `Bulb ${b.ip ?? "?"}`;
}

export default function LightsApp() {
  const [state, setState] = useState<AppState | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [optimistic, setOptimistic] = useState<Record<string, Partial<Bulb>>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [wifiOpen, setWifiOpen] = useState(false);
  const [fades, setFades] = useState<FadeTimers>({});
  const restored = useRef(false);

  const addLog = useCallback((lines: LogLine[], bulb?: string) => {
    if (!lines.length) return;
    const t = Date.now();
    setLog((prev) => [...lines.map((l) => ({ ...l, t, bulb })), ...prev].slice(0, 80));
  }, []);

  const refresh = useCallback(async () => {
    try {
      const s = await api.state();
      setState(s);
      setApiError(null);
    } catch (e) {
      setApiError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    // Polling an external service: fetch now, then on an interval.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  const bulbs = useMemo(
    () => (state?.bulbs ?? []).map((b) => ({ ...b, ...optimistic[b.id] })),
    [state, optimistic],
  );

  // Restore the last selection once bulbs are known; otherwise pick the first bulb.
  useEffect(() => {
    if (restored.current || !bulbs.length) return;
    restored.current = true;
    let saved: string[] = [];
    try {
      saved = JSON.parse(localStorage.getItem(SELECTION_KEY) ?? "[]");
    } catch {}
    const valid = saved.filter((id) => bulbs.some((b) => b.id === id));
    setSelected(valid.length ? valid : [bulbs[0].id]);
  }, [bulbs]);

  useEffect(() => {
    try {
      localStorage.setItem(SELECTION_KEY, JSON.stringify(selected));
    } catch {}
  }, [selected]);

  const selectedBulbs = bulbs.filter((b) => selected.includes(b.id));

  const control = useCallback(
    async (a: Action, ids: string[] = selected, guess?: Partial<Bulb>) => {
      if (!ids.length) return;
      // Any other command to a bulb ends its fade (the bulb drops it).
      setFades((f) => {
        const next = { ...f };
        ids.forEach((id) => delete next[id]);
        return next;
      });
      if (guess) {
        setOptimistic((o) => ({ ...o, ...Object.fromEntries(ids.map((id) => [id, { ...o[id], ...guess }])) }));
      }
      try {
        const { results } = await api.control(ids, a);
        setState((s) =>
          s && {
            ...s,
            bulbs: s.bulbs.map((b) => results.find((r) => r.id === b.id)?.bulb ?? b),
          },
        );
        for (const r of results) {
          const b = bulbs.find((x) => x.id === r.id);
          addLog(r.log, b ? bulbLabel(b) : r.id);
        }
      } catch (e) {
        addLog([{ kind: "err", text: (e as Error).message }]);
      } finally {
        if (guess) {
          setOptimistic((o) => {
            const next = { ...o };
            ids.forEach((id) => delete next[id]);
            return next;
          });
        }
      }
    },
    [selected, bulbs, addLog],
  );

  const startFade = useCallback(
    async (spec: FadeSpec, ids: string[]) => {
      await control({ action: "fade", value: spec }, ids, { on: true, effect: null, cycle: null });
      const start = Date.now();
      setFades((f) => ({
        ...f,
        ...Object.fromEntries(ids.map((id) => [id, { start, end: start + spec.seconds * 1000 }])),
      }));
    },
    [control],
  );

  const scan = useCallback(async () => {
    setBusy("scan");
    try {
      const s = await api.scan();
      setState(s);
      addLog([
        {
          kind: s.found ? "ok" : "info",
          text: s.found
            ? `Scan found ${s.found} bulb${s.found > 1 ? "s" : ""} over UDP`
            : "Scan found no UDP bulbs (bulbs on Wi-Fi show up via MQTT on their own)",
        },
      ]);
    } catch (e) {
      addLog([{ kind: "err", text: (e as Error).message }]);
    } finally {
      setBusy(null);
    }
  }, [addLog]);

  const addByIp = useCallback(
    async (ip: string) => {
      try {
        const b = await api.add(ip);
        addLog([{ kind: "ok", text: `Added bulb at ${ip}` }]);
        await refresh();
        setSelected([b.id]);
      } catch (e) {
        addLog([{ kind: "err", text: (e as Error).message }]);
      }
    },
    [addLog, refresh],
  );

  const rename = useCallback(
    async (id: string, name: string) => {
      try {
        await api.rename(id, name);
        await refresh();
      } catch (e) {
        addLog([{ kind: "err", text: (e as Error).message }]);
      }
    },
    [addLog, refresh],
  );

  const forget = useCallback(
    async (id: string) => {
      await api.forget(id).catch(() => {});
      setSelected((s) => s.filter((x) => x !== id));
      await refresh();
    },
    [refresh],
  );

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-5 px-4 py-5 sm:px-6">
      <TopBar
        state={state}
        apiError={apiError}
        scanning={busy === "scan"}
        onScan={scan}
        onAdd={addByIp}
        onWifi={() => setWifiOpen(true)}
      />

      {apiError && !state ? (
        <ServiceDown message={apiError} />
      ) : !state ? (
        <div className="py-20 text-center text-muted">Connecting to the bulb service…</div>
      ) : bulbs.length === 0 ? (
        <EmptyState state={state} scanning={busy === "scan"} onScan={scan} onWifi={() => setWifiOpen(true)} />
      ) : (
        <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          <BulbList
            bulbs={bulbs}
            selected={selected}
            onSelect={setSelected}
            onPower={(b, on) => control({ action: "power", value: on }, [b.id], { on })}
            onRename={rename}
          />
          <ControlPanel
            bulbs={selectedBulbs}
            totalBulbs={bulbs.length}
            effects={state.effects ?? []}
            palettes={state.palettes ?? []}
            cycleLimits={state.cycleLimits ?? { min: 3, max: 3600 }}
            fades={fades}
            onFade={startFade}
            onAction={control}
            onForget={forget}
          />
        </div>
      )}

      <ActivityLog entries={log} onClear={() => setLog([])} />

      {wifiOpen && state && (
        <WifiSetup
          state={state}
          onClose={() => setWifiOpen(false)}
          onDone={(b) => {
            addLog([{ kind: "ok", text: `${b.mac ?? "Bulb"} joined Wi-Fi and is connected` }]);
            refresh().then(() => setSelected([b.id]));
          }}
          onLog={addLog}
        />
      )}
    </div>
  );
}

function ServiceDown({ message }: { message: string }) {
  return (
    <div className="mx-auto mt-10 max-w-lg rounded-2xl border border-err/40 bg-err/10 p-6">
      <h2 className="text-lg font-semibold text-err">Bulb service not running</h2>
      <p className="mt-2 text-sm text-muted">{message}</p>
      <p className="mt-4 text-sm">
        Start everything from the <code className="font-mono">web/</code> folder with{" "}
        <code className="rounded bg-panel-2 px-1.5 py-0.5 font-mono">npm run dev</code>, or run the
        service alone with{" "}
        <code className="rounded bg-panel-2 px-1.5 py-0.5 font-mono">python3 sengled_ctl.py api</code>.
      </p>
    </div>
  );
}
