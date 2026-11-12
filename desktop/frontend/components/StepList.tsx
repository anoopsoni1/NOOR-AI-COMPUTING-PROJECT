import type { PlanStep, StepStatus } from "../types";

const GLYPH: Record<StepStatus, { ch: string; cls: string }> = {
  done: { ch: "✓", cls: "text-[var(--ok)]" },
  running: { ch: "→", cls: "text-[var(--amber)] hud-pulse" },
  pending: { ch: "○", cls: "text-[var(--dim)]" },
  failed: { ch: "✕", cls: "text-[var(--danger)]" },
  skipped: { ch: "–", cls: "text-[var(--dim)]" },
};

const TEXT: Record<StepStatus, string> = {
  running: "text-[var(--text)]",
  pending: "text-[var(--muted)]",
  skipped: "text-[var(--dim)] line-through",
  done: "text-[#cfc7b4]",
  failed: "text-[#ffb4a9]",
};

/** The execution plan as a HUD checklist:  ✓ done · → running · ○ pending · ✕ failed */
export default function StepList({ steps, showDetails = false }: { steps: PlanStep[]; showDetails?: boolean; dark?: boolean }) {
  if (steps.length === 0) return <p className="text-xs text-[var(--muted)]">No plan yet.</p>;
  return (
    <ol className="space-y-0.5">
      {steps.map((s) => (
        <li key={s.id} className="flex items-start gap-3 py-1">
          <span className={`hud-mono mt-px w-4 shrink-0 text-center text-sm ${GLYPH[s.status].cls}`}>{GLYPH[s.status].ch}</span>
          <div className="min-w-0 flex-1">
            <p className={`text-[13px] leading-snug ${TEXT[s.status]}`}>
              {s.description}
              {s.attempts > 1 && s.status !== "done" && <span className="hud-label ml-2 !text-[var(--amber)]">attempt {s.attempts}</span>}
            </p>
            {s.error && s.status === "failed" && <p className="mt-0.5 text-[11px] text-[var(--danger)]">{s.error}</p>}
            {showDetails && (
              <p className="mt-0.5 break-all font-mono text-[10px] text-[var(--dim)]">
                {s.tool}({Object.entries(s.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})
              </p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
