import { ReactNode } from "react";

/** NOOR HUD component kit: every page is assembled from these, so the application reads as one system. */

export function PageTitle({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex items-end justify-between gap-4 border-b border-[var(--line)] pb-4">
      <div>
        <h1 className="hud-title">{title}</h1>
        {subtitle && <p className="mt-1.5 text-xs text-[var(--muted)]">{subtitle}</p>}
      </div>
      {actions}
    </div>
  );
}

export function Section({ label, children, right }: { label: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="mb-7">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="hud-label">{label}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

export function Panel({ children, className = "", hi = false }: { children: ReactNode; className?: string; hi?: boolean }) {
  return <div className={`${hi ? "hud-panel-hi" : "hud-panel"} ${className}`}>{children}</div>;
}

export function Stat({ label, value, tone = "" , hint }: { label: string; value: ReactNode; tone?: "" | "ok" | "bad" | "amber"; hint?: string }) {
  const color = tone === "ok" ? "text-[var(--ok)]" : tone === "bad" ? "text-[var(--danger)]" : tone === "amber" ? "text-[var(--amber)]" : "text-[var(--text)]";
  return (
    <Panel className="p-4">
      <p className="hud-label">{label}</p>
      <p className={`hud-mono mt-2 text-2xl ${color}`}>{value}</p>
      {hint && <p className="mt-1 truncate text-[11px] text-[var(--dim)]">{hint}</p>}
    </Panel>
  );
}

export function KV({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <dt className="hud-label">{label}</dt>
      <dd className="mt-1 truncate text-sm text-[var(--text)]">{value}</dd>
    </div>
  );
}

export function Dot({ kind = "on" }: { kind?: "on" | "off" | "ok" | "bad" | "ask" }) {
  return <span className={`hud-dot ${kind === "off" ? "hud-dot-off" : kind === "ok" ? "hud-dot-ok" : kind === "bad" ? "hud-dot-bad" : kind === "ask" ? "hud-dot-ask" : ""}`} />;
}

export function Notice({ kind = "error", children }: { kind?: "error" | "info" | "warn"; children: ReactNode }) {
  const c = kind === "error" ? "border-[rgba(239,107,91,0.45)] text-[#ffb4a9]" : kind === "warn" ? "border-[var(--line-strong)] text-[#f5d58f]" : "border-[var(--line)] text-[var(--muted)]";
  return <div className={`mb-4 border-l-2 bg-[rgba(255,255,255,0.02)] px-3 py-2 text-xs ${c}`}>{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="hud-panel border-dashed px-6 py-10 text-center text-xs text-[var(--muted)]">{children}</div>;
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 py-10 text-[var(--muted)]">
      <span className="hud-dot hud-pulse" />
      <span className="hud-label !text-[var(--muted)]">{label}…</span>
    </div>
  );
}

export function Toggle({ checked, onChange, disabled = false, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-5 w-10 rounded-sm border transition-colors disabled:opacity-40 ${checked ? "border-[var(--amber)] bg-[rgba(245,184,61,0.2)]" : "border-[var(--line)] bg-transparent"}`}
    >
      <span className={`absolute top-0.5 h-3.5 w-3.5 rounded-sm transition-all ${checked ? "left-[22px] bg-[var(--amber)]" : "left-0.5 bg-[var(--dim)]"}`} />
    </button>
  );
}

export function Segmented<T extends string>({ value, options, onChange, disabled = false }: { value: T; options: { value: T; label: string; tone?: "bad" }[]; onChange: (v: T) => void; disabled?: boolean }) {
  return (
    <div className="inline-flex border border-[var(--line)]" role="radiogroup">
      {options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={value === o.value}
          disabled={disabled}
          onClick={() => onChange(o.value)}
          className={`hud-mono px-3 py-1.5 text-[10px] uppercase tracking-[0.16em] transition-colors ${value === o.value ? (o.tone === "bad" ? "bg-[rgba(239,107,91,0.2)] text-[#ffc2b9]" : "bg-[rgba(245,184,61,0.2)] text-[#ffe7b0]") : "text-[var(--muted)] hover:text-[var(--text)]"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Label + hint on the left, control on the right. */
export function Field({ label, hint, children, wide = false }: { label: string; hint?: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-6 px-4 py-3">
      <div className="min-w-0">
        <p className="text-sm text-[var(--text)]">{label}</p>
        {hint && <p className="mt-0.5 text-[11px] text-[var(--muted)]">{hint}</p>}
      </div>
      <div className={`shrink-0 ${wide ? "w-64" : "w-44"}`}>{children}</div>
    </div>
  );
}

export function Slider({ value, min, max, step = 1, onChange, format, label }: { value: number; min: number; max: number; step?: number; onChange: (v: number) => void; format?: (v: number) => string; label: string }) {
  return (
    <div className="flex items-center gap-3">
      <input type="range" className="hud-range" min={min} max={max} step={step} value={value} aria-label={label} onChange={(e) => onChange(Number(e.target.value))} />
      <span className="hud-mono w-14 shrink-0 text-right text-[11px] text-[var(--amber)]">{format ? format(value) : value}</span>
    </div>
  );
}
