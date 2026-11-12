import { ArrowLeft, Trash2 } from "lucide-react";
import { useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ago, duration, fmtTime, glyphFor, StateTag } from "../components/format";
import { Empty, KV, Loading, Notice, PageTitle, Panel, Section } from "../components/hud";
import StepList from "../components/StepList";
import VirtualList, { VRow } from "../components/VirtualList";
import { useAsync } from "../hooks/useAsync";
import { api } from "../services/api";
import type { TaskDetail, TaskSummary } from "../types";

export default function HistoryPage() {
  const { id } = useParams();
  return id ? <TaskDetailView id={id} /> : <TaskList />;
}

function dayLabel(iso: string): string {
  const d = new Date(iso);
  if (d.toDateString() === new Date().toDateString()) return "Today";
  if (d.toDateString() === new Date(Date.now() - 86_400_000).toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { dateStyle: "long" });
}

function TaskList() {
  const { data, error, loading, reload } = useAsync(() => api.listTasks(), []);

  // Flatten "day header + tasks" into rows for the virtualized list.
  const rows = useMemo<VRow[]>(() => {
    const out: VRow[] = [];
    let last = "";
    (data ?? []).forEach((t: TaskSummary) => {
      const label = dayLabel(t.created_at);
      if (label !== last) {
        last = label;
        out.push({ key: `h-${label}`, height: 40, render: () => <p className="hud-label px-1 pt-5">{label}</p> });
      }
      const g = glyphFor(t.status);
      out.push({
        key: t.id, height: 58,
        render: () => (
          <div className="hud-row flex h-full items-center gap-3 border-b border-[var(--line)] px-3">
            <span className={`hud-mono w-4 text-center text-sm ${g.cls}`}>{g.ch}</span>
            <Link to={`/history/${t.id}`} className="min-w-0 flex-1">
              <p className="truncate text-[13px] text-[var(--text)]">{t.goal}</p>
              <p className="text-[11px] text-[var(--muted)]">
                {ago(t.created_at)} · {duration(t.created_at, t.completed_at)}
                {t.replans ? ` · ${t.replans} re-plan(s)` : ""}
                {t.status === "WAITING_FOR_PERMISSION" ? " · waiting for approval" : t.status === "FAILED" ? " · failed" : ""}
              </p>
            </Link>
            <StateTag state={t.status} />
            <button
              className="p-1.5 text-[var(--dim)] hover:text-[var(--danger)]"
              aria-label="Delete task"
              onClick={async () => {
                if (confirm("Delete this task from history?")) {
                  await api.deleteTask(t.id).catch((e: Error) => alert(e.message));
                  reload();
                }
              }}
            >
              <Trash2 size={14} />
            </button>
          </div>
        ),
      });
    });
    return out;
  }, [data, reload]);

  return (
    <>
      <PageTitle title="Task History" subtitle="Everything NOOR has done. Open a task for its full execution timeline." />
      {error && <Notice>{error}</Notice>}
      {loading && !data && <Loading />}
      {!loading && data?.length === 0 && <Empty>No tasks yet.</Empty>}
      {rows.length > 0 && <VirtualList rows={rows} className="hud-panel h-[calc(100vh-13rem)] px-2" />}
    </>
  );
}

interface Execution {
  stepId: number;
  tool: string;
  attempt: number;
  durationMs: number;
  args?: Record<string, unknown>;
  result?: { success: boolean; message: string; errorCode: string | null } | null;
  observation?: { url?: string; title?: string; blockers?: string[] } | null;
  verdict?: { ok: boolean; category: string; reason: string } | null;
}

function TaskDetailView({ id }: { id: string }) {
  const nav = useNavigate();
  const { data: t, error, loading } = useAsync(() => api.getTask(id), [id]);
  const execs = (t?.executions ?? []) as unknown as Execution[];
  return (
    <>
      <button className="hud-label mb-4 inline-flex items-center gap-2 hover:!text-[var(--amber)]" onClick={() => nav("/history")}><ArrowLeft size={13} /> History</button>
      {error && <Notice>{error}</Notice>}
      {loading && <Loading />}
      {t && <Detail t={t} execs={execs} />}
    </>
  );
}

function Detail({ t, execs }: { t: TaskDetail; execs: Execution[] }) {
  return (
    <>
      <PageTitle title="Task" subtitle={`${fmtTime(t.created_at)} · ${t.id.slice(0, 8)}`} actions={<StateTag state={t.status} />} />
      <Panel className="mb-7 grid gap-5 p-5 md:grid-cols-[1fr_auto_auto]">
        <KV label="Goal" value={<span className="whitespace-normal text-base">“{t.goal}”</span>} />
        <KV label="Duration" value={duration(t.created_at, t.completed_at)} />
        <KV label="Re-plans" value={t.replans} />
      </Panel>
      {t.result && <Section label="Result"><Notice kind={t.status === "COMPLETED" ? "info" : "error"}><span className="whitespace-pre-wrap">{t.result}</span></Notice></Section>}

      <Section label="Plan"><Panel className="p-4"><StepList steps={t.plan} showDetails /></Panel></Section>

      <Section label="Tool calls & verification">
        {execs.length === 0 ? <p className="text-xs text-[var(--muted)]">No executions recorded.</p> : (
          <div className="hud-panel hud-divide">
            {execs.map((e, i) => (
              <div key={i} className="px-4 py-3">
                <div className="flex items-center gap-3">
                  <span className={`hud-mono w-4 text-center ${e.result?.success ? "text-[var(--ok)]" : "text-[var(--danger)]"}`}>{e.result?.success ? "✓" : "✕"}</span>
                  <span className="hud-mono text-xs text-[var(--amber)]">{e.tool}</span>
                  <span className="text-xs text-[var(--muted)]">step {e.stepId}{e.attempt > 1 ? ` · attempt ${e.attempt}` : ""}</span>
                  <span className="hud-mono ml-auto text-[10px] text-[var(--dim)]">{e.durationMs} ms</span>
                </div>
                <p className="mt-1 pl-7 text-xs text-[#cfc7b4]">{e.result?.message}</p>
                {e.observation?.url && <p className="mt-0.5 truncate pl-7 text-[11px] text-[var(--dim)]">observed: {e.observation.title || "page"} — {e.observation.url}</p>}
                {e.verdict && (
                  <p className={`mt-0.5 pl-7 text-[11px] ${e.verdict.ok ? "text-[var(--ok)]" : "text-[var(--amber)]"}`}>
                    verification: {e.verdict.ok ? "passed" : `failed (${e.verdict.category}) — ${e.verdict.reason}`}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </Section>

      {t.errors.length > 0 && (
        <Section label="Errors">
          <Panel className="p-4"><ul className="space-y-1 text-xs text-[var(--danger)]">{t.errors.map((e, i) => <li key={i}>• {e}</li>)}</ul></Panel>
        </Section>
      )}
    </>
  );
}
