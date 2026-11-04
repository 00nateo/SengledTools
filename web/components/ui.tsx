"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

type Tone = "ok" | "warn" | "err" | "neutral";

const toneClass: Record<Tone, string> = {
  ok: "border-ok/40 bg-ok/10 text-ok",
  warn: "border-warn/40 bg-warn/10 text-warn",
  err: "border-err/40 bg-err/10 text-err",
  neutral: "border-line bg-panel-2 text-muted",
};

export function Chip({ tone = "neutral", title, children }: { tone?: Tone; title?: string; children: ReactNode }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${toneClass[tone]}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
      {children}
    </span>
  );
}

export function Button({
  variant = "default",
  size = "md",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "danger" | "ghost";
  size?: "sm" | "md" | "lg";
}) {
  const v = {
    default: "border-line bg-panel-2 hover:bg-[#252530] text-text",
    primary: "border-accent/60 bg-accent text-black hover:brightness-110 font-medium",
    danger: "border-err/50 bg-err/10 text-err hover:bg-err/20",
    ghost: "border-transparent bg-transparent text-muted hover:text-text hover:bg-panel-2",
  }[variant];
  const s = { sm: "px-2.5 py-1.5 text-sm", md: "px-3.5 py-2 text-sm", lg: "px-5 py-3 text-base" }[size];
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg border transition disabled:cursor-not-allowed disabled:opacity-50 ${v} ${s} ${className}`}
    />
  );
}

export function Spinner() {
  return (
    <span
      aria-hidden
      className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
    />
  );
}

export function Panel({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-2xl border border-line bg-panel p-4 ${className}`}>
      {(title || right) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">{title}</h2>}
          {right}
        </div>
      )}
      {children}
    </section>
  );
}
