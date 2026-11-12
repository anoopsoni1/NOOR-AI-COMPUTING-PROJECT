import { useEffect, useRef, useState } from "react";
import type { OrbMode } from "../services/orbRenderer";
import { useNoor, useNoorLive } from "./useNoor";

/** Short human status shown under the orb for each mode. */
export const MODE_LABEL: Record<OrbMode, string> = {
  IDLE: "Ready",
  LISTENING: "Listening…",
  THINKING: "Processing…",
  EXECUTING: "Processing…",
  VERIFYING: "Processing…",
  WAITING: "Waiting for you",
  SPEAKING: "Speaking…",
  ERROR: "Error",
  STOPPED: "Stopped",
};

const MODES: OrbMode[] = ["IDLE", "LISTENING", "THINKING", "EXECUTING", "VERIFYING", "WAITING", "SPEAKING", "ERROR", "STOPPED"];

/** Design preview: open `#/?orb=LISTENING` to force a mode (the orb then runs on synthetic audio). Never set in normal use. */
export function previewMode(): OrbMode | null {
  const q = new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("orb")?.toUpperCase();
  return (MODES as string[]).includes(q ?? "") ? (q as OrbMode) : null;
}

/**
 * Derives the orb's mode from the real system state (voice pipeline + agent task), never from a timer script.
 * ERROR and STOPPED are brief flashes that settle back to the live state.
 */
export const MODE_TONE: Record<OrbMode, string> = {
  IDLE: "text-amber-200/70", LISTENING: "text-amber-100", THINKING: "text-amber-200/85", EXECUTING: "text-amber-300", VERIFYING: "text-amber-200",
  WAITING: "text-orange-200", SPEAKING: "text-amber-100", ERROR: "text-red-300", STOPPED: "text-slate-400",
};

export function useOrbMode(): OrbMode {
  const { voiceState, task } = useNoor();
  const { voiceError } = useNoorLive();
  const [flash, setFlash] = useState<"ERROR" | "STOPPED" | null>(null);
  const seen = useRef("");

  useEffect(() => {
    const key = task ? `${task.id}:${task.status}` : "";
    if (key === seen.current) return;
    seen.current = key;
    let timer: number | undefined;
    if (task?.status === "FAILED") {
      setFlash("ERROR");
      timer = window.setTimeout(() => setFlash(null), 3500);
    } else if (task?.status === "CANCELLED") {
      setFlash("STOPPED");
      timer = window.setTimeout(() => setFlash(null), 2500);
    }
    return () => window.clearTimeout(timer);
  }, [task]);

  useEffect(() => {
    if (!voiceError) return;
    setFlash("ERROR");
    const timer = window.setTimeout(() => setFlash(null), 3000);
    return () => window.clearTimeout(timer);
  }, [voiceError]);

  const forced = previewMode();
  if (forced) return forced;

  const status = task?.status;
  const taskLive = !!status && !["COMPLETED", "FAILED", "CANCELLED", "IDLE"].includes(status);

  if (voiceState === "SPEAKING") return "SPEAKING";
  if (flash) return flash;
  if (status === "WAITING_FOR_PERMISSION" || status === "WAITING_FOR_USER") return "WAITING";
  if (voiceState === "PROCESSING" || status === "PLANNING") return "THINKING";
  if (status === "OBSERVING" || status === "VERIFYING") return "VERIFYING";
  if (voiceState === "EXECUTING" || taskLive) return "EXECUTING";
  if (voiceState === "LISTENING" || voiceState === "WAKE_DETECTED") return "LISTENING";
  return "IDLE";
}

