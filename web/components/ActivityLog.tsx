"use client";

import type { LogLine } from "@/lib/api";

export type LogEntry = LogLine & { t: number; bulb?: string };

const mark: Record<LogLine["kind"], { icon: string; cls: string }> = {
  ok: { icon: "✓", cls: "text-ok" },
  err: { icon: "✕", cls: "text-err" },
  warn: { icon: "!", cls: "text-warn" },
  info: { icon: "·", cls: "text-muted" },
};

export function LogLines({ lines }: { lines: (LogLine & { bulb?: string; t?: number })[] }) {
  return (
    <ul className="flex flex-col gap-1 font-mono text-xs">
      {lines.map((l, i) => (
        <li key={i} className="flex gap-2">
          <span className={`w-3 shrink-0 ${mark[l.kind].cls}`}>{mark[l.kind].icon}</span>
          {l.t && <span className="shrink-0 text-muted">{new Date(l.t).toLocaleTimeString()}</span>}
          {l.bulb && <span className="shrink-0 text-accent">{l.bulb}</span>}
          <span className={l.kind === "err" ? "text-err" : "text-text"}>{l.text}</span>
        </li>
      ))}
    </ul>
  );
}

export function ActivityLog({ entries, onClear }: { entries: LogEntry[]; onClear: () => void }) {
  if (!entries.length) return null;
  return (
    <details className="group rounded-2xl border border-line bg-panel" open>
      <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-semibold tracking-wide text-muted uppercase select-none">
        <span>
          <span className="mr-2 inline-block transition group-open:rotate-90">›</span>Activity
        </span>
        <button
          onClick={(e) => {
            e.preventDefault();
            onClear();
          }}
          className="text-xs font-normal normal-case text-muted hover:text-text"
        >
          Clear
        </button>
      </summary>
      <div className="max-h-56 overflow-y-auto border-t border-line px-4 py-3">
        <LogLines lines={entries} />
      </div>
      <p className="border-t border-line px-4 py-2 text-[11px] text-muted">
        Everything is also saved, with bulb connections and replies, to{" "}
        <code className="font-mono">~/.sengled/logs/sengled.log</code>
      </p>
    </details>
  );
}
