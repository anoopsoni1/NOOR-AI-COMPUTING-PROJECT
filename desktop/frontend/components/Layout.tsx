import { Brain, History, LayoutDashboard, ListChecks, Settings, ShieldCheck, Sparkles } from "lucide-react";
import { Suspense } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useNoor } from "../hooks/useNoor";
import VoicePage from "../pages/VoicePage";
import { Dot, Loading } from "./hud";
import PermissionDialog from "./PermissionDialog";
import StopButton from "./StopButton";
import Toasts from "./Toasts";
import { STATE_LABEL } from "./format";

const NAV = [
  { to: "/", label: "Voice", icon: Sparkles, end: true },
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/task", label: "Current Task", icon: ListChecks },
  { to: "/history", label: "History", icon: History },
  { to: "/permissions", label: "Permissions", icon: ShieldCheck },
  { to: "/memories", label: "Memories", icon: Brain },
  { to: "/settings", label: "Settings", icon: Settings },
];

function SystemBlock() {
  const { connection, health, voice, micOn, voiceState } = useNoor();
  const online = connection === "open";
  const voiceLabel = !online ? "offline" : !micOn ? "mic off" : voiceState === "IDLE" ? (voice?.wakeWord.available ? "ready" : "tap orb") : voiceState.replace("_", " ").toLowerCase();
  const rows: [string, string, "on" | "off" | "ok" | "bad"][] = [
    ["Backend", online ? "online" : connection === "connecting" ? "connecting" : "offline", online ? "ok" : "bad"],
    ["Voice", voiceLabel, !online || !micOn ? "off" : "on"],
    ["Model", health?.llmConfigured ? (health.model.split("/").pop() ?? health.model) : "not set", health?.llmConfigured ? "on" : "bad"],
  ];
  return (
    <div className="border-t border-[var(--line)] px-4 py-4">
      <p className="hud-label mb-3">System</p>
      <ul className="mb-4 space-y-2">
        {rows.map(([k, v, kind]) => (
          <li key={k} className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-2 text-[11px] text-[var(--muted)]"><Dot kind={kind} />{k}</span>
            <span className="hud-value max-w-[6.5rem] truncate text-[10px] text-[var(--text)]">{v}</span>
          </li>
        ))}
      </ul>
      <StopButton block />
    </div>
  );
}

/** Shown on every page except Voice while a task is live: what NOOR is doing right now, with STOP. */
function ExecutionBar() {
  const { task, running } = useNoor();
  const nav = useNavigate();
  if (!task || !running) return null;
  const steps = task.plan.filter((s) => s.status !== "skipped");
  const done = steps.filter((s) => s.status === "done").length;
  const current = task.plan.find((s) => s.status === "running");
  return (
    <div className="flex items-center gap-4 border-b border-[var(--line-strong)] bg-[rgba(245,184,61,0.05)] px-6 py-2">
      <button className="flex min-w-0 flex-1 items-center gap-3 text-left" onClick={() => nav("/task")} title="Open the current task">
        <span className="hud-dot hud-pulse" />
        <span className="hud-label !text-[var(--amber)]">{task.paused ? "Paused" : STATE_LABEL[task.status]}</span>
        <span className="truncate text-xs text-[var(--text)]">{task.goal}</span>
        {current && <span className="hidden truncate text-[11px] text-[var(--muted)] md:inline">→ {current.description}</span>}
        {steps.length > 0 && <span className="hud-mono ml-auto shrink-0 text-[11px] text-[var(--muted)]">{done}/{steps.length}</span>}
      </button>
      <StopButton />
    </div>
  );
}

export default function Layout() {
  const { health } = useNoor();
  const onVoice = useLocation().pathname === "/";
  return (
    <div className="flex h-full bg-[var(--bg)]">
      <aside className="flex w-52 shrink-0 flex-col border-r border-[var(--line)] bg-[#06070c]">
        <div className="flex items-center gap-3 px-5 py-5">
          <span className="flex h-7 w-7 items-center justify-center border border-[var(--amber)] bg-[rgba(245,184,61,0.12)] text-sm font-semibold text-[var(--amber)]">N</span>
          <span className="hud-title !tracking-[0.4em]">NOOR</span>
        </div>
        <nav className="flex-1 space-y-0.5 px-2">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex items-center gap-3 border-l-2 px-3 py-2 text-[13px] transition-colors ${
                  isActive ? "border-[var(--amber)] bg-[rgba(245,184,61,0.08)] text-[var(--amber)]" : "border-transparent text-[var(--muted)] hover:bg-white/[0.03] hover:text-[var(--text)]"
                }`
              }
            >
              <Icon size={15} />
              {label}
            </NavLink>
          ))}
        </nav>
        <SystemBlock />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {!onVoice && <ExecutionBar />}
        {health?.dbInMemory && (
          <div className="border-b border-[var(--line)] bg-black px-6 py-1.5 text-[11px] text-[var(--amber)]/70">
            MongoDB is unreachable — using a temporary in-memory database. History and memories will be lost on restart.
          </div>
        )}
        <main className="relative min-h-0 flex-1">
          {/* The voice screen stays mounted (hidden off-route) so the orb and its render state persist; it pauses drawing when hidden. */}
          <div className={onVoice ? "absolute inset-0" : "hidden"}>
            <VoicePage visible={onVoice} />
          </div>
          {!onVoice && (
            <div className="absolute inset-0 overflow-auto p-8">
              <div className="mx-auto max-w-4xl">
                <Suspense fallback={<Loading />}>
                  <Outlet />
                </Suspense>
              </div>
            </div>
          )}
        </main>
      </div>
      <PermissionDialog />
      <Toasts />
    </div>
  );
}
