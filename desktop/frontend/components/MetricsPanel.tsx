import { useEffect, useState } from "react";
import { useNoorLive } from "../hooks/useNoor";
import { api } from "../services/api";
import type { SystemMetrics } from "../types";

const ROWS: [string, (m: { marks_ms: Record<string, number>; latency_ms: Record<string, number | null> }) => number | null | undefined][] = [
  ["speech_end → stt_complete", (m) => m.latency_ms.stt],
  ["speech_end → llm_first_token", (m) => m.latency_ms.to_first_token],
  ["speech_end → tts_first_audio", (m) => m.latency_ms.to_first_audio],
  ["task_start → task_complete", (m) => m.latency_ms.task_duration],
];

const fmt = (v: number | null | undefined) => (v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${v} ms`);

export default function MetricsPanel() {
  const { metrics } = useNoorLive();
  const [sys, setSys] = useState<SystemMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const fetchMetrics = async () => {
      try {
        const data = await api.systemMetrics();
        if (mounted) {
          setSys(data);
          setError(null);
        }
      } catch (err) {
        if (mounted) setError(err instanceof Error ? err.message : "Metrics error");
      }
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 2000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  const lastVoice = metrics[metrics.length - 1] || sys?.latency;

  return (
    <div className="hud-panel-hi pointer-events-auto absolute right-4 top-16 w-80 p-3.5 font-mono text-[10.5px] max-h-[85vh] overflow-y-auto shadow-2xl backdrop-blur-md bg-black/80 border border-[var(--hud-border)] rounded-sm">
      <div className="flex items-center justify-between border-b border-[var(--hud-border)] pb-1.5 mb-2.5">
        <span className="hud-label text-[11px] font-semibold tracking-wider text-[var(--accent)]">
          SYSTEM & PERFORMANCE DIAGNOSTICS
        </span>
        <span className="flex h-2 w-2 relative">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
          <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
        </span>
      </div>

      {/* 1. System Resource Gauges */}
      <div className="space-y-2 mb-3">
        <p className="text-[10px] uppercase font-bold text-[var(--dim)] tracking-widest">Hardware Resources</p>

        {/* CPU */}
        <div>
          <div className="flex justify-between text-[10px] text-[var(--muted)] mb-0.5">
            <span>CPU Load</span>
            <span className="text-white font-medium">{sys ? `${sys.system.cpu_percent.toFixed(1)}%` : "..."}</span>
          </div>
          <div className="w-full bg-neutral-800 h-1.5 rounded-full overflow-hidden">
            <div
              className={`h-full transition-all duration-500 rounded-full ${
                (sys?.system.cpu_percent || 0) > 80
                  ? "bg-rose-500"
                  : (sys?.system.cpu_percent || 0) > 50
                  ? "bg-amber-400"
                  : "bg-emerald-400"
              }`}
              style={{ width: `${Math.min(100, Math.max(0, sys?.system.cpu_percent || 0))}%` }}
            />
          </div>
        </div>

        {/* RAM */}
        <div>
          <div className="flex justify-between text-[10px] text-[var(--muted)] mb-0.5">
            <span>System RAM</span>
            <span className="text-white font-medium">
              {sys ? `${(sys.system.ram_used_mb / 1024).toFixed(1)} / ${(sys.system.ram_total_mb / 1024).toFixed(1)} GB (${sys.system.ram_percent}%)` : "..."}
            </span>
          </div>
          <div className="w-full bg-neutral-800 h-1.5 rounded-full overflow-hidden">
            <div
              className={`h-full transition-all duration-500 rounded-full ${
                (sys?.system.ram_percent || 0) > 85 ? "bg-rose-500" : "bg-cyan-400"
              }`}
              style={{ width: `${Math.min(100, Math.max(0, sys?.system.ram_percent || 0))}%` }}
            />
          </div>
        </div>

        {/* GPU / VRAM */}
        <div>
          <div className="flex justify-between text-[10px] text-[var(--muted)] mb-0.5">
            <span>NVIDIA GPU VRAM</span>
            <span className="text-white font-medium">
              {sys && sys.system.gpu_total_mb > 0
                ? `${sys.system.gpu_used_mb} / ${sys.system.gpu_total_mb} MB (${Math.round(
                    (sys.system.gpu_used_mb / sys.system.gpu_total_mb) * 100
                  )}%)`
                : "Active (Shared / Host)"}
            </span>
          </div>
          <div className="w-full bg-neutral-800 h-1.5 rounded-full overflow-hidden">
            <div
              className="h-full bg-purple-400 transition-all duration-500 rounded-full"
              style={{
                width: sys && sys.system.gpu_total_mb > 0
                  ? `${Math.min(100, (sys.system.gpu_used_mb / sys.system.gpu_total_mb) * 100)}%`
                  : "15%",
              }}
            />
          </div>
        </div>
      </div>

      {/* 2. Engines & Hardware Routing */}
      <div className="border-t border-neutral-800 pt-2 mb-3">
        <p className="text-[10px] uppercase font-bold text-[var(--dim)] tracking-widest mb-1.5">Active Engines</p>
        <div className="grid grid-cols-2 gap-1.5 text-[10px]">
          <div className="bg-neutral-900/90 p-1.5 rounded border border-neutral-800">
            <span className="text-[var(--dim)] block text-[9px]">LLM ENGINE</span>
            <span className="text-[var(--amber)] font-medium truncate block" title={sys?.llm.model}>
              {sys ? `${sys.llm.provider.toUpperCase()}` : "..."}
            </span>
            <span className="text-[9px] text-[var(--muted)] truncate block">{sys?.llm.model || "—"}</span>
          </div>
          <div className="bg-neutral-900/90 p-1.5 rounded border border-neutral-800">
            <span className="text-[var(--dim)] block text-[9px]">LOCAL TTS</span>
            <span className="text-emerald-400 font-medium truncate block">
              {sys?.voice.tts_provider.toUpperCase() || "KOKORO"}
            </span>
            <span className="text-[9px] text-[var(--muted)] truncate block">{sys?.voice.tts_voice || "af_heart"}</span>
          </div>
          <div className="bg-neutral-900/90 p-1.5 rounded border border-neutral-800">
            <span className="text-[var(--dim)] block text-[9px]">FASTER-WHISPER</span>
            <span className="text-cyan-400 font-medium truncate block">
              {sys?.voice.stt_device ? `DEVICE: ${sys.voice.stt_device.toUpperCase()}` : "CUDA"}
            </span>
            <span className="text-[9px] text-[var(--muted)]">FP16 Accelerated</span>
          </div>
          <div className="bg-neutral-900/90 p-1.5 rounded border border-neutral-800">
            <span className="text-[var(--dim)] block text-[9px]">LANGUAGE</span>
            <span className="text-amber-300 font-medium truncate block">
              {sys?.voice.language.toUpperCase() || "ENGLISH"}
            </span>
            <span className="text-[9px] text-[var(--muted)]">Active Response</span>
          </div>
        </div>
      </div>

      {/* 3. Voice Latency Breakdown */}
      <div className="border-t border-neutral-800 pt-2 mb-3">
        <div className="flex justify-between items-center mb-1.5">
          <p className="text-[10px] uppercase font-bold text-[var(--dim)] tracking-widest">
            Voice Latency {lastVoice ? `· Turn #${lastVoice.turn}` : ""}
          </p>
        </div>
        {lastVoice ? (
          <>
            <table className="w-full text-[10px]">
              <tbody>
                {ROWS.map(([name, get]) => (
                  <tr key={name} className="border-b border-neutral-900/50">
                    <td className="py-1 pr-2 text-[var(--muted)]">{name}</td>
                    <td className="text-right font-medium text-[var(--amber)]">{fmt(get(lastVoice))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {lastVoice.marks_ms && (
              <p className="mt-1.5 text-[9px] text-[var(--dim)] leading-tight truncate" title={JSON.stringify(lastVoice.marks_ms)}>
                marks: {Object.entries(lastVoice.marks_ms).slice(0, 4).map(([k, v]) => `${k}=${v}ms`).join(" · ")}
              </p>
            )}
          </>
        ) : (
          <p className="text-[10px] text-[var(--dim)] italic">Awaiting speech or query input...</p>
        )}
      </div>

      {/* 4. Recent Automation Actions */}
      {sys?.task?.actions && sys.task.actions.length > 0 && (
        <div className="border-t border-neutral-800 pt-2">
          <p className="text-[10px] uppercase font-bold text-[var(--dim)] tracking-widest mb-1">
            Recent Task Actions ({sys.task.status})
          </p>
          <div className="space-y-1">
            {sys.task.actions.map((act, idx) => (
              <div key={idx} className="flex justify-between items-center text-[9.5px] bg-neutral-900/60 px-1.5 py-0.5 rounded">
                <span className="text-[var(--muted)] truncate max-w-[170px]">{act.tool}</span>
                <span className={act.success ? "text-emerald-400 font-mono" : "text-rose-400 font-mono"}>
                  {act.duration_ms} ms
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {error && <p className="mt-2 text-rose-400 text-[9px]">{error}</p>}
    </div>
  );
}
