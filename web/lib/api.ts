// Client for the local Python API (sengled_ctl.py api), proxied at /api by next.config.ts.

export type RGB = [number, number, number];

export interface Bulb {
  id: string;
  name: string | null;
  mac: string | null;
  ip: string | null;
  via: "udp" | "mqtt";
  model: string | null;
  version: string | null;
  on: boolean | null;
  brightness: number | null;
  temp: number | null;
  rgb: RGB | null;
  mode: "color" | "white" | null;
  effect: string | null;
  cycle: CycleSettings | null;
  apMode: boolean;
  connected: boolean;
  /** Wi-Fi network the bulb is on (recorded at setup / from its subnet). */
  ssid: string | null;
  /** Wi-Fi signal in dBm, as the bulb reported when it last connected. */
  rssi: number | null;
}

export interface CycleSettings {
  palette: string;
  period: number;
  spread: boolean;
}

export interface AppState {
  network: {
    interfaces: { name: string; cidr: string }[];
    ssid: string | null;
    onBulbAp: boolean;
    ipv6Only: boolean;
    lanIp: string | null;
    rememberedLanIp: string | null;
    /** This Mac's last IP on each Wi-Fi network a bulb was set up on. */
    macIpBySsid?: Record<string, string>;
  };
  mqtt: { running: boolean; available: boolean; peers: string[] };
  bulbs: Bulb[];
  presets: Record<string, RGB>;
  effects: { id: string; label: string }[];
  palettes: { id: string; label: string; preview: RGB[] }[];
  cycleLimits: { min: number; max: number };
}

export interface LogLine {
  kind: "ok" | "err" | "info" | "warn";
  text: string;
}

export interface ControlResult {
  id: string;
  ok: boolean;
  log: LogLine[];
  bulb: Bulb;
}

export interface WifiNetwork {
  ssid: string;
  signal: number | null;
  aps: number;
}

export interface Job {
  done: boolean;
  ok: boolean;
  log: LogLine[];
  bulb: Bulb | null;
  /** Set when the bulb was pointed at another server (e.g. a Raspberry Pi): its address. */
  remote?: string | null;
}

export type Action =
  | { action: "power"; value: boolean }
  | { action: "color"; value: RGB }
  | { action: "brightness"; value: number }
  | { action: "temp"; value: number }
  | { action: "effect"; value: string | null }
  | { action: "cycle"; value: CycleSettings | null }
  | {
      action: "fade";
      value: { from: number; to: number; seconds: number; tempFrom?: number; tempTo?: number; offAtEnd?: boolean };
    }
  | { action: "raw"; value: string }
  | { action: "reboot" }
  | { action: "reset" };

async function call<T>(path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new Error("Can't reach the bulb service. Is `npm run dev` running?");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(
      data.error ??
        (res.status >= 500
          ? "The bulb service isn't running (python3 ../sengled_ctl.py api)"
          : `HTTP ${res.status}`),
    );
  }
  return data as T;
}

export interface BulbStats {
  mac: string | null;
  ip: string | null;
  via: "udp" | "mqtt";
  ssid: string | null;
  connected: boolean;
  device: Record<string, string>;
  wifi: { rssi: number | null; quality: string | null; min: number | null; max: number | null; history: [number, number][] };
  uptime: { seconds: number | null; bootedAt: number | null; rebootsSeen: number; rebootTimes: number[] };
  connection: {
    connectedSince: number | null;
    lastHeard: number | null;
    reconnectsLastHour: number;
    reconnects24h: number;
    medianReconnectInterval: number | null;
    closedSockets24h: number;
    history: number[];
    openSockets?: number;
    staleSockets?: number;
    ports?: number[];
  };
  commands: {
    count: number;
    ok: number;
    timeouts: number;
    successRate: number | null;
    lastMs: number | null;
    avgMs: number | null;
    p50Ms: number | null;
    p95Ms: number | null;
    maxMs: number | null;
    minMs: number | null;
    history: [number, number | null][];
  };
  pending: Record<string, string>;
}

export interface PingResult {
  method: "mqtt" | "udp";
  rounds: (number | null)[];
  loss: number;
  minMs: number | null;
  avgMs: number | null;
  maxMs: number | null;
  icmp: { loss: number; minMs: number | null; avgMs: number | null; maxMs: number | null } | null;
}

export const api = {
  state: () => call<AppState>("state"),
  scan: () => call<AppState & { found: number }>("scan", {}),
  add: (ip: string) => call<Bulb>("add", { ip }),
  control: (ids: string[], a: Action) =>
    call<{ results: ControlResult[] }>("control", { ids, ...a }),
  rename: (id: string, name: string) => call<Bulb>("rename", { id, name }),
  forget: (id: string) => call<{ ok: boolean }>("forget", { id }),
  wifiNetworks: () => call<{ networks: WifiNetwork[] }>("wifi/networks"),
  wifiSetup: (ssid: string, password: string, callbackHost: string) =>
    call<{ job: string }>("wifi/setup", { ssid, password, callbackHost }),
  job: (id: string) => call<Job>(`jobs/${id}`),
  stats: (id: string) => call<BulbStats>(`stats?id=${encodeURIComponent(id)}`),
  ping: (id: string, count = 5) => call<PingResult>("ping", { id, count }),
};
