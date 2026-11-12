import { useState } from "react";
import { ago } from "../components/format";
import { Dot, Notice, PageTitle, Panel, Section, Segmented } from "../components/hud";
import { useAsync } from "../hooks/useAsync";
import { useNoor } from "../hooks/useNoor";
import { api } from "../services/api";
import type { CapabilityMode, Policy, Preferences } from "../types";

const STATUS: Record<CapabilityMode, { text: string; dot: "on" | "ask" | "off" }> = {
  ENABLED: { text: "Enabled", dot: "on" },
  ASK: { text: "Requires confirmation", dot: "ask" },
  DISABLED: { text: "Disabled", dot: "off" },
};

const LEVELS: { key: "low" | "medium" | "high"; title: string; examples: string }[] = [
  { key: "low", title: "Low risk", examples: "Open applications · search · read pages and normal files · screenshots · scroll · navigate" },
  { key: "medium", title: "Medium risk", examples: "Move, rename or modify files · run scripts · install software · control the mouse/keyboard outside the browser" },
  { key: "high", title: "High risk", examples: "Delete important files · send messages or email · upload private files · financial actions · password and security changes" },
];

export default function PermissionsPage() {
  const { reloadPrefs, prefs } = useNoor();
  const caps = useAsync(() => api.capabilities(), []);
  const rules = useAsync(() => api.rules(), []);
  const audit = useAsync(() => api.audit(), []);
  const [err, setErr] = useState<string | null>(null);
  const [showTools, setShowTools] = useState(false);

  const guard = async (fn: () => Promise<unknown>, after: () => void) => {
    try {
      await fn();
      after();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const setPolicy = (patch: Partial<Preferences>) =>
    guard(async () => {
      const cur = (await api.settings()).preferences;
      await api.saveSettings({ ...cur, ...patch });
    }, () => void reloadPrefs());

  return (
    <>
      <PageTitle title="Permissions" subtitle="Exactly what NOOR may access. Nothing is escalated silently; OS security is never bypassed." />
      {err && <Notice>{err}</Notice>}

      <Section label="Capabilities">
        <div className="hud-panel hud-divide">
          {caps.data?.map((c) => (
            <div key={c.capability} className="flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-4">
              <div className="min-w-0 flex-1 basis-72">
                <p className="hud-value flex items-center gap-3"><Dot kind={STATUS[c.mode].dot} />{c.label}</p>
                <p className="mt-1 text-xs text-[var(--muted)]">{c.description}</p>
                {c.tools.length > 0 && <p className="mt-1.5 break-words font-mono text-[10px] leading-relaxed text-[var(--dim)]">{c.tools.join(" · ")}</p>}
              </div>
              <div className="flex flex-col items-end gap-2">
                <span className="hud-label !text-[var(--text)]">{STATUS[c.mode].text}{c.mode === "ENABLED" && c.default === "ASK" ? "" : ""}</span>
                <Segmented<CapabilityMode>
                  value={c.mode}
                  options={[{ value: "ENABLED", label: "Enabled" }, { value: "ASK", label: "Confirm" }, { value: "DISABLED", label: "Off", tone: "bad" }]}
                  onChange={(mode) => guard(() => api.setCapability(c.capability, mode), caps.reload)}
                />
              </div>
            </div>
          ))}
          {caps.loading && !caps.data && <p className="p-4 text-xs text-[var(--muted)]">Loading…</p>}
        </div>
        <p className="mt-2 text-[11px] text-[var(--dim)]">Turning the microphone off stops listening immediately. “Confirm” asks before every action in that area. LLM requests (the AI thinking) are not gated here; they are required for NOOR to work.</p>
      </Section>

      <Section label="Action risk levels">
        <div className="grid gap-3 md:grid-cols-3">
          {LEVELS.map((l) => (
            <Panel key={l.key} className="flex flex-col p-4">
              <p className="hud-value flex items-center gap-2"><Dot kind={l.key === "high" ? "bad" : l.key === "medium" ? "ask" : "ok"} />{l.title}</p>
              <p className="mt-2 flex-1 text-xs leading-relaxed text-[var(--muted)]">{l.examples}</p>
              <div className="mt-4">
                {l.key === "low" && <span className="hud-label !text-[var(--ok)]">Runs automatically</span>}
                {l.key === "medium" && prefs && (
                  <Segmented<Preferences["policy_medium"]> value={prefs.policy_medium}
                    options={[{ value: "ask", label: "Ask" }, { value: "auto", label: "Auto" }, { value: "block", label: "Block", tone: "bad" }]}
                    onChange={(v) => setPolicy({ policy_medium: v })} />
                )}
                {l.key === "high" && prefs && (
                  <>
                    <Segmented<Preferences["policy_high"]> value={prefs.policy_high}
                      options={[{ value: "confirm", label: "Confirm" }, { value: "block", label: "Block", tone: "bad" }]}
                      onChange={(v) => setPolicy({ policy_high: v })} />
                    <p className="mt-2 text-[10px] text-[var(--dim)]">Never automatic. Never approvable by voice alone.</p>
                  </>
                )}
              </div>
            </Panel>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-[var(--dim)]">Always blocked, regardless of settings: typing passwords or card numbers, system and credential folders, disk-level commands.</p>
      </Section>

      <Section label="Per-tool overrides" right={<button className="hud-label hover:!text-[var(--amber)]" onClick={() => setShowTools((v) => !v)}>{showTools ? "Hide" : "Show"}</button>}>
        {showTools && (
          <div className="hud-panel hud-divide">
            {rules.data?.map((r) => (
              <div key={r.tool} className="flex items-center justify-between gap-4 px-4 py-2.5">
                <div className="min-w-0">
                  <p className="hud-mono text-xs text-[var(--amber)]">{r.tool}</p>
                  <p className="truncate text-[11px] text-[var(--muted)]">{r.description}</p>
                </div>
                <select className="hud-input !w-40" value={r.policy} aria-label={`Policy for ${r.tool}`}
                  onChange={(e) => guard(() => api.setRule(r.tool, e.target.value as Policy), rules.reload)}>
                  <option value="default">Default</option>
                  <option value="ask">Ask every time</option>
                  <option value="allow">Always allow</option>
                </select>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section label="Decision log">
        <div className="hud-panel hud-divide">
          {audit.data?.length === 0 && <p className="p-4 text-xs text-[var(--muted)]">No approvals or blocks recorded yet.</p>}
          {audit.data?.map((a) => (
            <div key={a.id} className="flex items-center justify-between gap-4 px-4 py-2.5">
              <div className="min-w-0">
                <p className="truncate text-[13px] text-[var(--text)]">{a.title || a.tool}</p>
                <p className="text-[11px] text-[var(--dim)]">{ago(a.ts)}{a.reason ? ` · ${a.reason}` : ""}</p>
              </div>
              <span className={`hud-label ${a.outcome === "approved" ? "!text-[var(--ok)]" : a.outcome === "blocked" ? "!text-[var(--danger)]" : "!text-[var(--muted)]"}`}>{a.outcome}</span>
            </div>
          ))}
        </div>
      </Section>
    </>
  );
}
