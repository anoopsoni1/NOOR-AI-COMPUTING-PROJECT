import { OctagonX } from "lucide-react";
import { useState } from "react";
import { useNoor } from "../hooks/useNoor";

/**
 * Global emergency stop. One press: silences NOOR, stops listening for this turn, cancels the agent loop and any pending approval,
 * halts desktop input, closes the automation browser and marks the task cancelled. Stopping always wins over finishing.
 */
export default function StopButton({ size = "md", block = false }: { size?: "md" | "lg"; block?: boolean }) {
  const { stop, running, voiceState } = useNoor();
  const [busy, setBusy] = useState(false);
  const live = running || voiceState === "SPEAKING" || voiceState === "PROCESSING" || voiceState === "EXECUTING";
  return (
    <button
      className={`hud-btn hud-btn-danger ${size === "lg" ? "!px-6 !py-2.5 !text-xs" : ""} ${block ? "w-full" : ""} ${live ? "!bg-[rgba(239,107,91,0.34)]" : "!opacity-80"}`}
      disabled={busy}
      title="Emergency stop — silence NOOR, cancel the task and return control to you"
      onClick={async () => {
        setBusy(true);
        try {
          await stop();
        } finally {
          setBusy(false);
        }
      }}
    >
      <OctagonX size={size === "lg" ? 16 : 14} />
      Stop
    </button>
  );
}
