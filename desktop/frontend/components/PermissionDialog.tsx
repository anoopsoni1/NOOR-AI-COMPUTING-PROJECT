import { ShieldAlert } from "lucide-react";
import { useState } from "react";
import { useNoor } from "../hooks/useNoor";

const LEVEL: Record<string, string> = {
  low: "border-[var(--line-strong)] text-[var(--amber)]",
  medium: "border-[var(--amber)] text-[var(--amber)]",
  high: "border-[rgba(239,107,91,0.7)] text-[#ffb4a9]",
};

/** Modal shown whenever the agent needs approval for a sensitive action. */
export default function PermissionDialog() {
  const { task, approve } = useNoor();
  const [busy, setBusy] = useState(false);
  const req = task?.pending_permission;
  if (!req) return null;

  const answer = async (ok: boolean) => {
    setBusy(true);
    try {
      await approve(req.id, ok);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-[2px]" role="dialog" aria-modal="true" aria-labelledby="perm-title">
      <div className="hud-panel-hi w-full max-w-lg p-6">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <ShieldAlert size={20} className="mt-0.5 text-[var(--amber)]" />
            <div>
              <p className="hud-label">NOOR wants to:</p>
              <h2 id="perm-title" className="mt-1 text-lg text-[var(--text)]">{req.title}</h2>
            </div>
          </div>
          <span className={`hud-mono shrink-0 border px-2 py-0.5 text-[10px] uppercase tracking-[0.2em] ${LEVEL[req.level] ?? LEVEL.medium}`}>{req.level} risk</span>
        </div>
        <dl className="mb-4 max-h-64 space-y-3 overflow-auto border border-[var(--line)] bg-black/30 p-3">
          {req.details.map((d, i) => (
            <div key={i}>
              <dt className="hud-label">{d.label}</dt>
              <dd className="mt-0.5 whitespace-pre-wrap break-words text-sm text-[var(--text)]">{d.value || "—"}</dd>
            </div>
          ))}
        </dl>
        {req.reason && <p className="mb-2 text-xs text-[var(--muted)]">Why you are asked: {req.reason}</p>}
        <p className="mb-5 text-xs text-[var(--muted)]">
          {req.voice_approvable ? "You can also answer by voice: say “yes” or “no”." : "This needs your approval here on screen — a spoken “yes” will not authorise it."}
        </p>
        <div className="flex justify-end gap-3">
          <button className="hud-btn" disabled={busy} autoFocus={req.level === "high"} onClick={() => answer(false)}>Cancel</button>
          <button className="hud-btn hud-btn-primary" disabled={busy} autoFocus={req.level !== "high"} onClick={() => answer(true)}>Approve</button>
        </div>
      </div>
    </div>
  );
}
