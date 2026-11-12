import type { AgentState } from "../types";
import { Dot } from "./hud";

export const STATE_LABEL: Record<AgentState, string> = {
  IDLE: "Idle",
  PLANNING: "Planning",
  WAITING_FOR_PERMISSION: "Waiting for permission",
  EXECUTING: "Executing",
  OBSERVING: "Observing",
  VERIFYING: "Verifying",
  RECOVERING: "Recovering",
  COMPLETED: "Completed",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
  WAITING_FOR_USER: "Waiting for you",
};

const KIND: Record<string, "on" | "off" | "ok" | "bad" | "ask"> = {
  COMPLETED: "ok", FAILED: "bad", CANCELLED: "off", IDLE: "off", WAITING_FOR_PERMISSION: "ask", WAITING_FOR_USER: "ask",
};

/** Small uppercase status tag: ● EXECUTING */
export function StateTag({ state }: { state: AgentState }) {
  return (
    <span className="hud-value inline-flex items-center gap-2 text-[11px]">
      <Dot kind={KIND[state] ?? "on"} />
      {STATE_LABEL[state]}
    </span>
  );
}

export function fmtTime(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—";
}

export function duration(a: string, b: string | null): string {
  if (!b) return "—";
  const s = Math.max(0, Math.round((new Date(b).getTime() - new Date(a).getTime()) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function ago(iso: string): string {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} hr ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function glyphFor(status: AgentState): { ch: string; cls: string } {
  if (status === "COMPLETED") return { ch: "✓", cls: "text-[var(--ok)]" };
  if (status === "FAILED") return { ch: "✕", cls: "text-[var(--danger)]" };
  if (status === "WAITING_FOR_PERMISSION" || status === "WAITING_FOR_USER") return { ch: "⚠", cls: "text-[var(--amber)]" };
  if (status === "CANCELLED") return { ch: "–", cls: "text-[var(--dim)]" };
  return { ch: "→", cls: "text-[var(--amber)]" };
}
