import { Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Empty, Notice, PageTitle, Panel, Section, Toggle } from "../components/hud";
import { useAsync } from "../hooks/useAsync";
import { useNoor } from "../hooks/useNoor";
import { api } from "../services/api";
import type { Memory } from "../types";

const CATEGORIES: Memory["category"][] = ["preference", "app", "folder", "workflow", "outcome", "other"];

export default function MemoriesPage() {
  const { prefs, reloadPrefs } = useNoor();
  const { data, error, reload } = useAsync(() => api.memories(), []);
  const [text, setText] = useState("");
  const [category, setCategory] = useState<Memory["category"]>("preference");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const enabled = prefs?.memory_enabled ?? true;

  const guard = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try {
      await fn();
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const setEnabled = async (on: boolean) => {
    try {
      const cur = (await api.settings()).preferences;
      await api.saveSettings({ ...cur, memory_enabled: on });
      await reloadPrefs();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <>
      <PageTitle title="Memories" subtitle="What NOOR remembers to work better for you. Passwords and credentials are never stored." />
      {(err ?? error) && <Notice>{err ?? error}</Notice>}

      <Panel className="mb-7 flex items-center justify-between gap-6 p-4">
        <div>
          <p className="hud-value">Memory {enabled ? "on" : "off"}</p>
          <p className="mt-1 text-xs text-[var(--muted)]">{enabled ? "NOOR recalls these facts and may learn new, non-sensitive habits." : "NOOR neither recalls nor stores anything. Existing memories are kept until you delete them."}</p>
        </div>
        <Toggle checked={enabled} onChange={(v) => void setEnabled(v)} label="Memory" />
      </Panel>

      <Section label="Add a memory">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return;
            void guard(async () => {
              await api.addMemory(text.trim(), category);
              setText("");
            });
          }}
        >
          <input className="hud-input" placeholder="e.g. I usually save project files in D:/Projects" value={text} onChange={(e) => setText(e.target.value)} maxLength={300} />
          <select className="hud-input !w-36" value={category} onChange={(e) => setCategory(e.target.value as Memory["category"])} aria-label="Category">
            {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
          </select>
          <button className="hud-btn hud-btn-primary" type="submit"><Plus size={14} /> Add</button>
        </form>
      </Section>

      <Section label={`Stored (${data?.length ?? 0})`}>
        {data?.length === 0 && <Empty>Nothing remembered yet. NOOR adds useful, non-sensitive habits after successful tasks.</Empty>}
        <div className={`hud-panel hud-divide ${enabled ? "" : "opacity-60"}`}>
          {data?.map((m) => (
            <div key={m.id} className="flex items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                {editing === m.id ? (
                  <input className="hud-input" value={draft} autoFocus onChange={(e) => setDraft(e.target.value)} maxLength={300}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void guard(async () => { await api.editMemory(m.id, draft, m.category); setEditing(null); });
                      if (e.key === "Escape") setEditing(null);
                    }} />
                ) : (
                  <p className="text-[13px] text-[var(--text)]">{m.content}</p>
                )}
                <p className="hud-label mt-1 !text-[var(--dim)]">{m.category} · {m.source === "auto" ? "learned by NOOR" : "added by you"}</p>
              </div>
              <button className="hud-btn !px-2.5 !py-1.5" aria-label="Edit memory" onClick={() => { setEditing(m.id); setDraft(m.content); }}><Pencil size={12} /> Edit</button>
              <button className="hud-btn hud-btn-danger !px-2.5 !py-1.5" aria-label="Delete memory" onClick={() => void guard(() => api.deleteMemory(m.id))}><Trash2 size={12} /> Delete</button>
            </div>
          ))}
        </div>
      </Section>
    </>
  );
}
