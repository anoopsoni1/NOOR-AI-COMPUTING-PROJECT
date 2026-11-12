import { Link } from "react-router-dom";
import { Dot, Empty, Loading, Notice, PageTitle, Panel, Section, Stat } from "../components/hud";
import { ago, glyphFor, STATE_LABEL } from "../components/format";
import TaskInput from "../components/TaskInput";
import { useAsync } from "../hooks/useAsync";
import { useNoor } from "../hooks/useNoor";
import { api } from "../services/api";

function Tile({ label, value, dot, sub }: { label: string; value: string; dot: "on" | "off" | "ok" | "bad" | "ask"; sub?: string }) {
  return (
    <Panel className="p-4">
      <p className="hud-label">{label}</p>
      <p className="hud-value mt-2 flex items-center gap-2 text-sm"><Dot kind={dot} />{value}</p>
      {sub && <p className="mt-1 truncate text-[11px] text-[var(--muted)]">{sub}</p>}
    </Panel>
  );
}

export default function DashboardPage() {
  const { task, running, connection, health, voice, micOn, voiceState } = useNoor();
  const { data, error, loading } = useAsync(() => api.stats(), [task?.status]);
  const today = data?.today;
  const online = connection === "open";
  const voiceText = !online ? "offline" : voice && !voice.micAllowed ? "mic permission off" : !micOn ? "mic off" : voiceState === "IDLE" ? (voice?.wakeWord.available ? "ready" : "tap the orb") : voiceState.replace("_", " ").toLowerCase();
  const seg = (n: number) => (today && today.total ? `${(n / today.total) * 100}%` : "0%");

  return (
    <>
      <PageTitle title="Dashboard" subtitle="System telemetry and what NOOR has been doing." />
      {error && <Notice>{error}</Notice>}

      <div className="mb-7 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="NOOR status" value={online ? "online" : "offline"} dot={online ? "ok" : "bad"} sub={health ? `${health.provider} · ${health.model.split("/").pop()}` : undefined} />
        <Tile label="Voice" value={voiceText} dot={online && micOn ? "on" : "off"} sub={voice?.stt.provider ? `stt ${voice.stt.provider} · tts ${voice.tts_provider ?? "off"}` : undefined} />
        <Tile label="Active task" value={task && running ? STATE_LABEL[task.status] : "none"} dot={running ? "on" : "off"} sub={task && running ? task.goal : undefined} />
        <Tile label="Database" value={health?.dbInMemory ? "in-memory" : online ? "connected" : "—"} dot={health?.dbInMemory ? "ask" : online ? "ok" : "off"} />
      </div>

      <Section label="Today">
        {loading && !data ? <Loading /> : (
          <>
            <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Tasks" value={today?.total ?? 0} />
              <Stat label="Completed" value={today?.completed ?? 0} tone="ok" />
              <Stat label="Waiting" value={today?.waiting ?? 0} tone="amber" />
              <Stat label="Failed" value={today?.failed ?? 0} tone="bad" />
            </div>
            <div className="flex h-1 overflow-hidden bg-[var(--line)]" aria-hidden>
              <div className="bg-[var(--ok)]" style={{ width: seg(today?.completed ?? 0) }} />
              <div className="bg-[var(--amber)]" style={{ width: seg(today?.waiting ?? 0) }} />
              <div className="bg-[var(--danger)]" style={{ width: seg(today?.failed ?? 0) }} />
            </div>
          </>
        )}
      </Section>

      <Section label="Command"><TaskInput /></Section>

      <Section label="Recent" right={<Link className="hud-label hover:!text-[var(--amber)]" to="/history">All history →</Link>}>
        {data && data.recent.length === 0 ? (
          <Empty>No tasks yet. Say “Hey NOOR” and tell it what to do.</Empty>
        ) : (
          <div className="hud-panel hud-divide">
            {data?.recent.map((t) => {
              const g = glyphFor(t.status);
              return (
                <Link key={t.id} to={`/history/${t.id}`} className="hud-row flex items-center gap-3 px-4 py-3">
                  <span className={`hud-mono w-4 text-center ${g.cls}`}>{g.ch}</span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13px] text-[var(--text)]">{t.goal}</p>
                    <p className="text-[11px] text-[var(--muted)]">{ago(t.created_at)}</p>
                  </div>
                  <span className="hud-label">{STATE_LABEL[t.status]}</span>
                </Link>
              );
            })}
          </div>
        )}
      </Section>
    </>
  );
}
