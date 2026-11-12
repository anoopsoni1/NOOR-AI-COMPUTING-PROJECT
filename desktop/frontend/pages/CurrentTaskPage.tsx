import { MessageCircleQuestion, Pause, Play } from "lucide-react";
import { useState } from "react";
import ActivityLog from "../components/ActivityLog";
import { STATE_LABEL, StateTag } from "../components/format";
import { Empty, KV, Notice, PageTitle, Panel, Section } from "../components/hud";
import StepList from "../components/StepList";
import StopButton from "../components/StopButton";
import TaskInput from "../components/TaskInput";
import { useNoor } from "../hooks/useNoor";
import { api } from "../services/api";
import type { ActionRecord } from "../types";

export function ActionTable({ actions }: { actions: ActionRecord[] }) {
  if (actions.length === 0) return <p className="text-xs text-[var(--muted)]">No actions executed yet.</p>;
  return (
    <div className="hud-panel overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="hud-label border-b border-[var(--line)]">
            <th className="px-3 py-2 font-normal">Step</th><th className="px-3 font-normal">Tool</th><th className="px-3 font-normal">Result</th><th className="px-3 text-right font-normal">Time</th>
          </tr>
        </thead>
        <tbody className="hud-divide">
          {actions.map((a, i) => (
            <tr key={i} className="align-top">
              <td className="px-3 py-2 text-[var(--dim)]">#{a.step_id}</td>
              <td className="hud-mono px-3 py-2 text-[var(--amber)]">{a.tool}</td>
              <td className={`px-3 py-2 ${a.success ? "text-[#cfc7b4]" : "text-[var(--danger)]"}`}>{a.message}{a.error_code ? ` (${a.error_code})` : ""}</td>
              <td className="px-3 py-2 text-right text-[var(--dim)]">{a.duration_ms} ms</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function CurrentTaskPage() {
  const { task, running, respond } = useNoor();
  const [answer, setAnswer] = useState("");

  if (!task) {
    return (
      <>
        <PageTitle title="Current Task" />
        <Empty>No task has run in this session.</Empty>
        <div className="mt-6"><TaskInput /></div>
      </>
    );
  }

  const steps = task.plan.filter((s) => s.status !== "skipped");
  const done = steps.filter((s) => s.status === "done").length;
  const pct = steps.length ? Math.round((done / steps.length) * 100) : 0;

  return (
    <>
      <PageTitle
        title="Current Task"
        subtitle={`Task ${task.id.slice(0, 8)} · ${task.replans} re-plan(s) · ${task.actions.length} action(s)`}
        actions={running ? (
          <div className="flex items-center gap-3">
            <button className="hud-btn" onClick={() => void (task.paused ? api.resumeTask() : api.pauseTask())}>
              {task.paused ? <><Play size={13} /> Continue</> : <><Pause size={13} /> Pause</>}
            </button>
            <StopButton size="lg" />
          </div>
        ) : undefined}
      />

      <Panel className="mb-7 p-5">
        <div className="grid gap-5 md:grid-cols-[1fr_auto]">
          <KV label="Goal" value={<span className="whitespace-normal text-base leading-snug">“{task.goal}”</span>} />
          <div>
            <p className="hud-label">Status</p>
            <p className="mt-1.5"><StateTag state={task.status} />{task.paused && <span className="hud-label ml-3 !text-[var(--amber)]">paused</span>}</p>
          </div>
        </div>
        {task.understanding && <p className="mt-3 text-xs text-[var(--muted)]">{task.understanding}</p>}
        <div className="mt-5">
          <div className="mb-1.5 flex items-center justify-between"><span className="hud-label">Progress</span><span className="hud-mono text-[11px] text-[var(--muted)]">{done}/{steps.length} · {pct}%</span></div>
          <div className="h-1 bg-[var(--line)]"><div className="h-full bg-[var(--amber)] transition-all" style={{ width: `${pct}%` }} /></div>
        </div>
      </Panel>

      {task.pending_question && (
        <form
          className="hud-panel-hi mb-7 p-4"
          onSubmit={(e) => {
            e.preventDefault();
            void respond(answer.trim() || "done");
            setAnswer("");
          }}
        >
          <p className="mb-3 flex items-start gap-2 text-sm text-[var(--amber)]"><MessageCircleQuestion size={16} className="mt-0.5 shrink-0" />{task.pending_question}</p>
          <div className="flex gap-2">
            <input className="hud-input" placeholder="Type your answer, or just continue once you are done" value={answer} onChange={(e) => setAnswer(e.target.value)} />
            <button className="hud-btn hud-btn-primary whitespace-nowrap" type="submit">{answer.trim() ? "Send" : "Continue"}</button>
            <button className="hud-btn" type="button" onClick={() => void respond("cancel")}>Give up</button>
          </div>
        </form>
      )}

      <Section label="Plan"><Panel className="p-4"><StepList steps={task.plan} showDetails /></Panel></Section>

      {!running && task.result && <Section label={task.status === "COMPLETED" ? "Result" : STATE_LABEL[task.status]}>
        <Notice kind={task.status === "COMPLETED" ? "info" : "error"}><span className="whitespace-pre-wrap">{task.result}</span></Notice>
      </Section>}

      <Section label={`Actions (${task.actions.length})`}><ActionTable actions={task.actions} /></Section>
      <Section label="Activity"><ActivityLog lines={task.log} /></Section>
    </>
  );
}
