"use client";

import { useEffect, useRef, useState } from "react";
import { api, type AppState, type Bulb, type LogLine, type WifiNetwork } from "@/lib/api";
import { Button, Spinner } from "./ui";
import { LogLines } from "./ActivityLog";

type Step = "prepare" | "network" | "running" | "done";

export function WifiSetup({
  state,
  onClose,
  onDone,
  onLog,
}: {
  state: AppState;
  onClose: () => void;
  onDone: (b: Bulb) => void;
  onLog: (lines: LogLine[], bulb?: string) => void;
}) {
  const [step, setStep] = useState<Step>("prepare");
  const [networks, setNetworks] = useState<WifiNetwork[] | null>(null);
  const [loadingNets, setLoadingNets] = useState(false);
  const [netError, setNetError] = useState<string | null>(null);
  const [ssid, setSsid] = useState("");
  const [other, setOther] = useState(false);
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [callback, setCallback] = useState(state.network.rememberedLanIp ?? "");
  // Use this Mac's address on the chosen network when we've seen it there before.
  const pickSsid = (name: string) => {
    setOther(false);
    setSsid(name);
    const known = state.network.macIpBySsid?.[name];
    if (known) setCallback(known);
  };
  const [log, setLog] = useState<LogLine[]>([]);
  const [result, setResult] = useState<{ ok: boolean; bulb: Bulb | null; remote?: string | null } | null>(null);
  // An address this computer has never had: the bulb will belong to another server (a Pi).
  const ownIps = new Set([
    ...Object.values(state.network.macIpBySsid ?? {}),
    ...state.network.interfaces.map((i) => i.cidr.split("/")[0]),
    state.network.rememberedLanIp,
  ]);
  const remote = /^\d+\.\d+\.\d+\.\d+$/.test(callback) && !ownIps.has(callback);
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    dialog.current?.showModal();
  }, []);

  const onAp = state.network.onBulbAp;

  const findNetworks = async () => {
    setLoadingNets(true);
    setNetError(null);
    try {
      const { networks } = await api.wifiNetworks();
      setNetworks(networks);
      setStep("network");
    } catch (e) {
      setNetError((e as Error).message);
    } finally {
      setLoadingNets(false);
    }
  };

  const start = async () => {
    setStep("running");
    setLog([]);
    try {
      const { job } = await api.wifiSetup(ssid, password, callback);
      for (;;) {
        await new Promise((r) => setTimeout(r, 1000));
        const j = await api.job(job);
        setLog(j.log);
        if (j.done) {
          setResult({ ok: j.ok, bulb: j.bulb, remote: j.remote });
          setStep("done");
          onLog(j.log, "Wi-Fi setup");
          if (j.ok && j.bulb) onDone(j.bulb);
          break;
        }
      }
    } catch (e) {
      setLog((l) => [...l, { kind: "err", text: (e as Error).message }]);
      setResult({ ok: false, bulb: null });
      setStep("done");
    }
  };

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      onCancel={(e) => {
        if (step === "running") e.preventDefault();
      }}
      className="m-auto w-[min(640px,calc(100vw-32px))] rounded-2xl border border-line bg-panel p-0 text-text backdrop:bg-black/70"
    >
      <div className="flex items-center justify-between border-b border-line px-5 py-4">
        <h2 className="text-lg font-semibold">Wi-Fi setup</h2>
        <button onClick={() => dialog.current?.close()} disabled={step === "running"} className="text-muted hover:text-text disabled:opacity-30" aria-label="Close">
          ✕
        </button>
      </div>

      <div className="max-h-[70vh] overflow-y-auto px-5 py-4">
        {step === "prepare" && (
          <div className="flex flex-col gap-4">
            <ol className="flex flex-col gap-3 text-sm">
              <Li n={1}>Turn the bulb off and on <b>5 times</b> until it blinks (setup mode). Only one bulb blinking at a time.</Li>
              <Li n={2}>
                On this Mac, join the <b>Sengled Wi-Fi Bulb_…</b> network from the Wi-Fi menu.
              </Li>
              <Li n={3}>Come back here and press <b>Find networks</b>.</Li>
            </ol>
            <div className={`rounded-xl border px-4 py-3 text-sm ${onAp ? "border-ok/40 bg-ok/10 text-ok" : "border-line bg-panel-2 text-muted"}`}>
              {onAp ? "✓ This Mac is on a bulb's setup network." : "Waiting for this Mac to join the bulb's network… (this updates by itself)"}
            </div>
            {netError && <p className="text-sm text-err">{netError}</p>}
            <div className="flex justify-end gap-2">
              <Button onClick={() => dialog.current?.close()}>Cancel</Button>
              <Button variant="primary" onClick={findNetworks} disabled={loadingNets}>
                {loadingNets ? <Spinner /> : null} {loadingNets ? "Asking the bulb (~5s)…" : "Find networks"}
              </Button>
            </div>
          </div>
        )}

        {step === "network" && (
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (ssid && callback) start();
            }}
          >
            <fieldset className="flex flex-col gap-1.5">
              <legend className="mb-2 text-sm text-muted">Network for the bulb (it only sees 2.4 GHz)</legend>
              {networks?.map((n) => (
                <label
                  key={n.ssid}
                  className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 text-sm ${
                    !other && ssid === n.ssid ? "border-accent/70 bg-panel-2" : "border-line hover:bg-panel-2/60"
                  }`}
                >
                  <input type="radio" name="ssid" checked={!other && ssid === n.ssid} onChange={() => pickSsid(n.ssid)} className="accent-[var(--accent)]" />
                  <span className="flex-1 truncate">{n.ssid || "(hidden)"}</span>
                  {n.aps > 1 && <span className="text-xs text-muted">{n.aps} access points</span>}
                  <Bars signal={n.signal ?? 0} />
                </label>
              ))}
              <label className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 text-sm ${other ? "border-accent/70 bg-panel-2" : "border-line"}`}>
                <input type="radio" name="ssid" checked={other} onChange={() => { setOther(true); setSsid(""); }} className="accent-[var(--accent)]" />
                {other ? (
                  <input autoFocus value={ssid} onChange={(e) => setSsid(e.target.value)} placeholder="Network name" className="flex-1 bg-transparent outline-none" />
                ) : (
                  <span className="text-muted">Other network…</span>
                )}
              </label>
            </fieldset>

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-muted">Password (blank if open)</span>
                <div className="flex rounded-lg border border-line bg-panel-2 focus-within:border-accent">
                  <input type={showPw ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} className="min-w-0 flex-1 bg-transparent px-3 py-2 outline-none" />
                  <button type="button" onClick={() => setShowPw((s) => !s)} className="px-3 text-xs text-muted hover:text-text">
                    {showPw ? "Hide" : "Show"}
                  </button>
                </div>
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="text-muted">Server address on that network</span>
                <input value={callback} onChange={(e) => setCallback(e.target.value)} placeholder="e.g. 172.20.10.3" className="rounded-lg border border-line bg-panel-2 px-3 py-2 font-mono outline-none focus:border-accent" />
              </label>
            </div>
            <p className="text-xs text-muted">
              The bulb connects to the computer at that address. For this Mac on an iPhone hotspot it&apos;s usually{" "}
              <button type="button" className="font-mono text-accent hover:underline" onClick={() => setCallback("172.20.10.3")}>
                172.20.10.3
              </button>
              ; for a network you&apos;ve set a bulb up on before, it&apos;s filled in when you pick it. For a Raspberry
              Pi hosting the bulbs&apos; Wi-Fi it&apos;s{" "}
              <button type="button" className="font-mono text-accent hover:underline" onClick={() => setCallback("10.42.0.1")}>
                10.42.0.1
              </button>
              . Open Wi-Fi that isolates devices (e.g. public WhiteSky) blocks this; password-protected ones like
              WhiteSky-Brexx work.
            </p>
            {remote && (
              <p className="rounded-lg border border-accent/40 bg-accent/10 px-3 py-2 text-xs">
                <span className="font-mono">{callback}</span> isn&apos;t this Mac, so the bulb will belong to the server
                there (e.g. your Raspberry Pi) and show up in <i>its</i> web app. This Mac stays on its own Wi-Fi.
              </p>
            )}

            <div className="flex justify-between gap-2">
              <Button type="button" variant="ghost" onClick={() => setStep("prepare")}>
                ← Back
              </Button>
              <Button type="submit" variant="primary" disabled={!ssid || !callback}>
                {remote ? "Send to bulb" : "Send to bulb & switch this Mac"}
              </Button>
            </div>
          </form>
        )}

        {(step === "running" || step === "done") && (
          <div className="flex flex-col gap-4">
            <div className="flex items-center gap-3 text-sm">
              {step === "running" ? (
                <>
                  <Spinner /> Setting up - this takes up to 2 minutes.
                  {remote ? " " : <> The Mac will switch to <b>{ssid}</b>.</>}
                </>
              ) : result?.ok && result.remote ? (
                <span className="text-ok">
                  ✓ The bulb is joining {ssid}. It shows up on the server at {result.remote} within about a minute.
                </span>
              ) : result?.ok ? (
                <span className="text-ok">✓ The bulb is on {ssid} and connected.</span>
              ) : (
                <span className="text-err">The bulb didn&apos;t come online. See the log below.</span>
              )}
            </div>
            <div className="max-h-72 overflow-y-auto rounded-xl border border-line bg-bg p-3">
              <LogLines lines={log} />
              {!log.length && <p className="text-sm text-muted">Starting…</p>}
            </div>
            {step === "done" && (
              <div className="flex justify-end gap-2">
                {!result?.ok && (
                  <Button onClick={() => setStep("prepare")}>Try again</Button>
                )}
                <Button variant="primary" onClick={() => dialog.current?.close()}>
                  {result?.ok ? "Done" : "Close"}
                </Button>
              </div>
            )}
          </div>
        )}
      </div>
    </dialog>
  );
}

function Li({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent/15 text-xs font-semibold text-accent">{n}</span>
      <span className="pt-0.5">{children}</span>
    </li>
  );
}

function Bars({ signal }: { signal: number }) {
  return (
    <span className="flex items-end gap-0.5" aria-label={`signal ${signal} of 3`}>
      {[0, 1, 2].map((i) => (
        <span key={i} className={`w-1 rounded-sm ${i <= signal ? "bg-text" : "bg-line"}`} style={{ height: 5 + i * 4 }} />
      ))}
    </span>
  );
}
