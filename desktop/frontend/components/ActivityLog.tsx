import type { LogLine } from "../types";

const COLOR = { info: "text-[var(--muted)]", warn: "text-[var(--amber)]", error: "text-[var(--danger)]" } as const;

export default function ActivityLog({ lines }: { lines: LogLine[] }) {
  return (
    <div className="hud-panel max-h-72 overflow-auto p-3 font-mono text-[11px] leading-relaxed">
      {lines.length === 0 && <p className="text-[var(--dim)]">No activity yet.</p>}
      {lines.map((l, i) => (
        <p key={i} className={COLOR[l.level]}>
          <span className="text-[var(--dim)]">{new Date(l.ts).toLocaleTimeString()} </span>
          {l.text}
        </p>
      ))}
    </div>
  );
}
