import { Keyboard, Mic, MicOff, Volume2, VolumeX } from "lucide-react";
import { ReactNode, useState } from "react";
import MetricsPanel from "../components/MetricsPanel";
import StepList from "../components/StepList";
import StopButton from "../components/StopButton";
import TaskInput from "../components/TaskInput";
import VoiceOrb from "../components/VoiceOrb";
import { MODE_LABEL, MODE_TONE, useOrbMode } from "../hooks/useOrbMode";
import { useNoor, useNoorLive } from "../hooks/useNoor";

function Hud({ label, value, className = "", onClick }: { label: string; value: ReactNode; className?: string; onClick?: () => void }) {
  const inner = (
    <>
      <span className="block text-[10px] tracking-[0.28em] text-amber-200/35">{label}</span>
      <span className="block max-w-[15rem] truncate text-[11px] tracking-[0.18em] text-amber-100/70">{value}</span>
    </>
  );
  const cls = `pointer-events-auto absolute border-amber-300/20 p-3 font-mono uppercase ${className}`;
  return onClick ? <button onClick={onClick} className={`${cls} text-left hover:border-amber-300/40`}>{inner}</button> : <div className={cls}>{inner}</div>;
}

/** Main screen. The orb is NOOR's visual state; the label under it always reflects the real agent/voice state from the backend. */
export default function VoicePage({ visible }: { visible: boolean }) {
  const { voice, micOn, micError, speakerOn, setMic, setSpeaker, task, running, connection, health, prefs } = useNoor();
  const { heard, caption, voiceError, userSpeaking } = useNoorLive();
  const mode = useOrbMode();
  const [typing, setTyping] = useState(false);
  const [showSteps, setShowSteps] = useState(false);

  const wake = voice?.wakeWord;
  const done = task?.plan.filter((s) => s.status === "done").length ?? 0;
  const total = task?.plan.filter((s) => s.status !== "skipped").length ?? 0;
  const micBlocked = voice ? !voice.micAllowed : false;
  const problem = connection !== "open" ? "Cannot reach the NOOR backend — run: python -m backend.main"
    : health && !health.llmConfigured ? "Add your LLM API key to .env and restart the backend"
    : micBlocked ? "The microphone permission is turned off — enable it in Permissions"
    : !micOn ? "Microphone is muted — click the mic button below or click the Orb to speak"
    : micError ?? voiceError;

  return (
    <div className="relative flex h-full flex-col bg-[#04050a] text-amber-50">
      <div className="pointer-events-none flex flex-col items-center pt-5">
        <h1 className="text-xl font-light tracking-[0.6em] text-amber-100/85">NOOR</h1>
        <p className="mt-1 text-[10px] uppercase tracking-[0.35em] text-amber-200/35">Speak a goal. NOOR gets it done.</p>
      </div>

      {/* orb stage */}
      <div className="relative min-h-0 flex-1">
        <VoiceOrb visible={visible} />

        <Hud label="System status" value={connection === "open" ? `online · ${health?.provider ?? "—"}/${health?.model ?? "—"}` : "offline"} className="left-4 top-2 border-l border-t" />
        <Hud label="Voice" value={`${micOn ? "mic on" : "mic off"} · ${wake?.available ? "wake ready" : wake?.enabled ? "wake loading" : "tap orb"} · lang ${prefs?.response_language ?? "—"}`} className="right-4 top-2 border-r border-t text-right" />
        <Hud
          label={`Task${total ? ` ${done}/${total}` : ""}`}
          value={task && running ? task.goal : task ? "completed" : "none"}
          className="bottom-2 left-4 border-b border-l"
          onClick={task ? () => setShowSteps((v) => !v) : undefined}
        />
        <Hud label="Processing" value={`${mode.toLowerCase()}${userSpeaking ? " · hearing you" : ""}`} className="bottom-2 right-4 border-b border-r text-right" />

        {showSteps && task && (
          <div className="absolute bottom-16 left-4 max-h-[55%] w-80 overflow-auto border border-amber-300/15 bg-black/80 p-3 backdrop-blur">
            <p className="mb-2 text-[10px] uppercase tracking-[0.25em] text-amber-200/45">Execution plan</p>
            <StepList steps={task.plan} />
          </div>
        )}
        {prefs?.dev_metrics && <MetricsPanel />}
      </div>

      {/* status, transcript, controls */}
      <div className="flex flex-col items-center px-6 pb-6 pt-2">
        <p className={`text-sm uppercase tracking-[0.4em] ${!micOn || micBlocked ? "text-slate-400" : MODE_TONE[mode]}`} aria-live="polite">
          {task?.paused ? "Paused" : (!micOn || micBlocked ? "Muted" : MODE_LABEL[mode])}
        </p>
        <div className="mt-3 min-h-[4.5rem] max-w-2xl space-y-1.5 text-center">
          {heard && <p className={`text-sm ${userSpeaking ? "italic text-amber-200/90" : "text-amber-100/55"}`}>“{heard}”</p>}
          {caption && <p className="text-base leading-snug text-amber-50">{caption}</p>}
          {task?.pending_question && !caption && <p className="text-sm text-amber-200">{task.pending_question}</p>}
          {problem && <p className="text-xs text-red-300/90">{problem}</p>}
        </div>

        {typing && <div className="mt-2 w-full max-w-xl"><TaskInput autoFocus /></div>}

        <div className="mt-4 flex items-center gap-3">
          <button disabled={micBlocked} onClick={() => void setMic(!micOn)} className={`hud-btn !p-2.5 ${micOn ? "!border-amber-300/50 !text-amber-200" : "!text-slate-500"}`} aria-label={micOn ? "Mute microphone" : "Turn microphone on"} title={micBlocked ? "Microphone permission is off" : micOn ? "Microphone on" : "Microphone off"}>
            {micOn ? <Mic size={16} /> : <MicOff size={16} />}
          </button>
          <button onClick={() => setSpeaker(!speakerOn)} className={`hud-btn !p-2.5 ${speakerOn ? "!border-amber-300/50 !text-amber-200" : "!text-slate-500"}`} aria-label={speakerOn ? "Mute NOOR's voice" : "Unmute NOOR's voice"} title={speakerOn ? "NOOR speaks" : "NOOR is muted"}>
            {speakerOn ? <Volume2 size={16} /> : <VolumeX size={16} />}
          </button>
          <StopButton size="lg" />
          <button onClick={() => setTyping((v) => !v)} className={`hud-btn !p-2.5 ${typing ? "!border-amber-300/50 !text-amber-200" : ""}`} aria-label="Type a command" title="Type a command">
            <Keyboard size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

