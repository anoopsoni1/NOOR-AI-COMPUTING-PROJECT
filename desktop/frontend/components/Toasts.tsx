import { X } from "lucide-react";
import { useNoor } from "../hooks/useNoor";
import { Dot } from "./hud";

/** Notifications driven by real backend events (task finished/failed/stopped, voice errors, denied actions). */
export default function Toasts() {
  const { toasts, dismissToast } = useNoor();
  if (toasts.length === 0) return null;
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-40 flex w-72 flex-col gap-2" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className="hud-panel-hi pointer-events-auto flex items-start gap-3 px-3 py-2.5">
          <span className="mt-1.5"><Dot kind={t.kind === "ok" ? "ok" : t.kind === "bad" ? "bad" : "on"} /></span>
          <p className="flex-1 text-xs text-[var(--text)]">{t.text}</p>
          <button className="text-[var(--dim)] hover:text-[var(--text)]" onClick={() => dismissToast(t.id)} aria-label="Dismiss"><X size={13} /></button>
        </div>
      ))}
    </div>
  );
}
