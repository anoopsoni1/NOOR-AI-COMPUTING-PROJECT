import { useEffect, useMemo, useState } from "react";
import { Field, KV, Loading, Notice, PageTitle, Panel, Section, Segmented, Slider, Toggle, Dot } from "../components/hud";
import { useAsync } from "../hooks/useAsync";
import { useNoor } from "../hooks/useNoor";
import { api, BACKEND_URL } from "../services/api";
import type { LlmModel, LlmProviderInfo, Preferences, TTSProviderInfo, VoiceProfile, VoiceTestResult } from "../types";

const LANGUAGES: { value: "english" | "hinglish" | "hindi"; label: string }[] = [
  { value: "english", label: "English" },
  { value: "hinglish", label: "Hinglish (Hindi + English)" },
  { value: "hindi", label: "Hindi (हिंदी)" },
];

function useSystemPrefs() {
  const sys = window.noor?.system;
  const [value, setValue] = useState<NoorSystemPrefs | null>(null);
  useEffect(() => {
    void sys?.get().then(setValue);
  }, [sys]);
  return { supported: !!sys, value, set: async (patch: Partial<NoorSystemPrefs>) => setValue(await sys!.set(patch)) };
}

function MicDevices() {
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [value, setValue] = useState(() => {
    try {
      return localStorage.getItem("noor.micDevice") ?? "";
    } catch {
      return "";
    }
  });
  useEffect(() => {
    navigator.mediaDevices?.enumerateDevices().then((d) => setDevices(d.filter((x) => x.kind === "audioinput"))).catch(() => undefined);
  }, []);
  return (
    <select
      className="hud-input"
      value={value}
      aria-label="Microphone"
      onChange={(e) => {
        setValue(e.target.value);
        try {
          localStorage.setItem("noor.micDevice", e.target.value);
        } catch { /* storage unavailable */ }
      }}
    >
      <option value="">System default</option>
      {devices.map((d) => <option key={d.deviceId} value={d.deviceId}>{d.label || "Microphone"}</option>)}
    </select>
  );
}

export default function SettingsPage() {
  const { refreshHealth, reloadPrefs, restartMic, micOn, setMic, voice } = useNoor();
  const { data, error } = useAsync(() => api.settings(), []);
  const [draft, setDraft] = useState<Preferences | null>(null);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [startMicOnLaunch, setStartMicOnLaunch] = useState(() => {
    try {
      return localStorage.getItem("noor.mic") !== "off";
    } catch {
      return true;
    }
  });
  const system = useSystemPrefs();

  // ---- LLM provider / model state ----
  const [providers, setProviders] = useState<LlmProviderInfo[]>([]);
  const [models, setModels] = useState<LlmModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [test, setTest] = useState<{ kind: "idle" | "ok" | "error"; message: string } | null>(null);
  const [applying, setApplying] = useState(false);

  // ---- Voice / TTS state ----
  const [ttsProviders, setTtsProviders] = useState<TTSProviderInfo[]>([]);
  const [ttsVoices, setTtsVoices] = useState<VoiceProfile[]>([]);
  const [ttsVoicesLoading, setTtsVoicesLoading] = useState(false);
  const [voiceTesting, setVoiceTesting] = useState(false);
  const [voiceTestResult, setVoiceTestResult] = useState<VoiceTestResult | null>(null);
  const [voiceAudio, setVoiceAudio] = useState<HTMLAudioElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.llmProviders().then((p) => { if (!cancelled) setProviders(p); }).catch(() => undefined);
    api.voiceProviders().then((p) => { if (!cancelled) setTtsProviders(p); }).catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!data || !draft) return;
    let cancelled = false;
    const prov = (draft.llm_provider ?? data.environment.llmProvider ?? "groq") as string;
    setModelsLoading(true);
    api.llmModels(prov).then((m) => { if (!cancelled) setModels(m); }).catch(() => { if (!cancelled) setModels([]); }).finally(() => { if (!cancelled) setModelsLoading(false); });
    return () => { cancelled = true; };
  }, [data, draft?.llm_provider]);

  useEffect(() => {
    if (!data || !draft) return;
    if (draft.llm_model && !models.some((m) => m.id === draft.llm_model)) {
      setDraft({ ...draft, llm_model: models[0]?.id ?? null });
    }
  }, [draft?.llm_provider, models, data, draft]);

  // Load dynamic voice list whenever TTS provider or language changes
  useEffect(() => {
    if (!draft) return;
    let cancelled = false;
    setTtsVoicesLoading(true);
    const prov = draft.tts_provider ?? "kokoro";
    const lang = draft.response_language ?? "english";
    api.voiceVoices(prov, lang)
      .then((v) => { if (!cancelled) setTtsVoices(v); })
      .catch(() => { if (!cancelled) setTtsVoices([]); })
      .finally(() => { if (!cancelled) setTtsVoicesLoading(false); });
    return () => { cancelled = true; };
  }, [draft?.tts_provider, draft?.response_language]);

  // Keep selected voice valid for current provider
  useEffect(() => {
    if (!draft || !ttsVoices.length) return;
    if (draft.tts_voice && !ttsVoices.some((v) => v.id === draft.tts_voice)) {
      setDraft({ ...draft, tts_voice: ttsVoices[0]?.id ?? "af_heart" });
    }
  }, [draft?.tts_provider, ttsVoices]);

  useEffect(() => {
    if (data) setDraft(data.preferences);
  }, [data]);

  const dirty = useMemo(() => !!data && !!draft && JSON.stringify(data.preferences) !== JSON.stringify(draft), [data, draft]);

  if (!draft || !data) return <><PageTitle title="Settings" />{error ? <Notice>{error}</Notice> : <Loading />}</>;

  const set = <K extends keyof Preferences>(k: K, v: Preferences[K]) => setDraft({ ...draft, [k]: v });
  const num = (k: keyof Preferences, min?: number, max?: number) => (
    <input className="hud-input" type="number" min={min} max={max} value={draft[k] as number} aria-label={String(k)} onChange={(e) => set(k, Number(e.target.value) as never)} />
  );
  const env = data.environment;
  const audioChanged = draft.noise_suppression !== data.preferences.noise_suppression || draft.echo_cancellation !== data.preferences.echo_cancellation || draft.auto_gain !== data.preferences.auto_gain;

  const save = async () => {
    setErr(null);
    try {
      const r = await api.saveSettings(draft);
      setDraft(r.preferences);
      await reloadPrefs();
      refreshHealth();
      if (audioChanged && micOn) await restartMic();
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const applyLive = async () => {
    setErr(null);
    setTest(null);
    setApplying(true);
    try {
      const r = await api.llmReconfigure(draft.llm_provider ?? "groq", draft.llm_model ?? undefined);
      setTest({ kind: "ok", message: `Applied live — NOOR now uses ${r.provider} / ${r.model ?? "default"}` });
    } catch (e) {
      setTest({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    } finally {
      setApplying(false);
    }
  };

  const runTest = async () => {
    setErr(null);
    setTest({ kind: "idle", message: "Testing..." });
    try {
      const r = await api.llmTest(draft.llm_provider ?? "groq", draft.llm_model ?? undefined);
      setTest({ kind: "ok", message: r.message });
    } catch (e) {
      setTest({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  };

  const runVoiceTest = async () => {
    setErr(null);
    setVoiceTesting(true);
    setVoiceTestResult(null);
    try {
      const sampleText = draft.response_language === "hindi"
        ? "नमस्ते! मैं नूर हूँ, आपकी स्थानीय वॉइस असिस्टेंट।"
        : draft.response_language === "hinglish"
        ? "Hello! Main Noor hoon, how can I help you today?"
        : "Hello, I am Noor. I can run tasks and answer your questions.";
      const res = await api.voiceTest({
        provider: draft.tts_provider || "kokoro",
        voice: draft.tts_voice || "af_heart",
        text: sampleText,
        speed: (draft.speaking_rate ?? 0) / 100,
        language: draft.response_language || "english",
      });
      setVoiceTestResult(res);
      if (res.audio_base64) {
        if (voiceAudio) {
          voiceAudio.pause();
        }
        const a = new Audio(`data:audio/wav;base64,${res.audio_base64}`);
        setVoiceAudio(a);
        await a.play();
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setVoiceTesting(false);
    }
  };

  const currentProvider = providers.find((p) => p.id === (draft.llm_provider ?? data.environment.llmProvider));
  const keyStatus = currentProvider ? (
    currentProvider.needs_api_key
      ? currentProvider.configured
        ? <span className="text-[var(--ok)]"><Dot kind="ok" /> API key configured (never shown)</span>
        : <span className="text-[var(--danger)]"><Dot kind="bad" /> API key missing — set it in .env (never stored in the UI)</span>
      : <span className="text-[var(--muted)]"><Dot kind="ok" /> No API key needed (runs locally)</span>
  ) : null;

  const currentTtsProvider = ttsProviders.find((p) => p.id === (draft.tts_provider || "kokoro"));

  return (
    <>
      <PageTitle title="Settings" subtitle="Voice, listening, language, AI provider and system. Changes apply live." />
      {err && <Notice>{err}</Notice>}

      <Section label="AI provider & model">
        <div className="hud-panel hud-divide">
          <Field label="Provider" hint="Ollama runs local models with no cloud key. Groq and Gemini use keys you set in .env." wide>
            <select className="hud-input" value={draft.llm_provider ?? ""} aria-label="LLM provider" disabled={modelsLoading} onChange={(e) => set("llm_provider", (e.target.value || null) as never)}>
              <option value="groq">Groq (cloud - fast)</option>
              <option value="ollama">Ollama (local)</option>
              <option value="gemini">Google Gemini (cloud)</option>
            </select>
          </Field>
          <Field label="Model" hint={modelsLoading ? "Loading models..." : `Default: ${currentProvider?.default_model ?? "-"}`} wide>
            <select className="hud-input" value={draft.llm_model ?? ""} aria-label="LLM model" disabled={modelsLoading || !models.length} onChange={(e) => set("llm_model", (e.target.value || null) as never)}>
              {models.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              {!models.length && <option value="" disabled>No models available</option>}
              {draft.llm_model && !models.some((m) => m.id === draft.llm_model) && <option value={draft.llm_model}>{draft.llm_model}</option>}
            </select>
            {modelsLoading ? <span className="ml-2 text-[10px] text-[var(--muted)]">loading...</span> : null}
          </Field>
          <Field label="Fallback provider" hint="Only used if your main provider fails. Never switches silently; opt-in below." wide>
            <div className="flex items-center gap-3">
              <Toggle checked={!!draft.llm_fallback_enabled} onChange={(v) => set("llm_fallback_enabled", v)} label="Enable fallback" />
              <select className="hud-input" disabled={!draft.llm_fallback_enabled || modelsLoading} aria-label="Fallback provider" onChange={(e) => set("llm_fallback_provider", (e.target.value || "none") as never)}>
                <option value="none">none</option>
                <option value="groq">Groq (cloud)</option>
                <option value="ollama">Ollama (local)</option>
                <option value="gemini">Google Gemini (cloud)</option>
              </select>
            </div>
          </Field>
          <div className="flex items-center gap-3">
            <button className="hud-btn hud-btn-outline" onClick={() => void runTest()} disabled={modelsLoading || test?.kind === "idle"}>
              {test?.kind === "idle" ? "Testing..." : "Test connection"}
            </button>
            <button className="hud-btn hud-btn-primary" onClick={() => void applyLive()} disabled={applying || modelsLoading}>
              {applying ? "Applying..." : "Apply live"}
            </button>
            {test && (
              <span className={`ml-3 text-xs font-mono ${test.kind === "ok" ? "text-[var(--ok)]" : "text-[var(--danger)]"}`}>{test.message}</span>
            )}
          </div>
          <p className="px-4 pt-2 text-[10px] text-[var(--muted)]">{keyStatus}</p>
        </div>
      </Section>

      <Section label="Voice Engine & Speech">
        <div className="hud-panel hud-divide">
          <Field label="Voice engine (TTS)" hint="Kokoro runs completely locally (free). Edge TTS provides neural cloud voices." wide>
            <select
              className="hud-input"
              value={draft.tts_provider ?? "kokoro"}
              aria-label="TTS provider"
              onChange={(e) => set("tts_provider", e.target.value as Preferences["tts_provider"])}
            >
              <option value="kokoro">Kokoro (Free, Local Neural — 24kHz ONNX)</option>
              <option value="edge">Edge TTS (Cloud Neural — Microsoft)</option>
              <option value="piper">Piper (Local Fast Neural)</option>
              <option value="system">Windows System (SAPI Offline)</option>
              <option value="elevenlabs">ElevenLabs (Cloud, Optional)</option>
            </select>
            {currentTtsProvider && (
              <p className="mt-1 text-[10px] text-[var(--muted)]">
                {currentTtsProvider.is_local ? "Runs 100% on your machine (no API keys, zero cloud latency)" : "Requires internet connection"} · {currentTtsProvider.sample_rate / 1000}kHz audio · Device: {currentTtsProvider.device}
              </p>
            )}
          </Field>

          <Field label="Response language" hint="NOOR understands Hindi/Hinglish/English input and replies in the chosen language." wide>
            <Segmented
              value={draft.response_language}
              options={LANGUAGES.map((l) => ({ value: l.value, label: l.label }))}
              onChange={(v) => {
                let nextVoice = draft.tts_voice;
                if ((draft.tts_provider ?? "kokoro") === "kokoro") {
                  if (v === "hindi") nextVoice = "hf_alpha";
                  else if (v === "english") nextVoice = "af_heart";
                } else if (draft.tts_provider === "edge") {
                  if (v === "hindi" || v === "hinglish") nextVoice = "hi-IN-MadhurNeural";
                  else if (v === "english") nextVoice = "en-US-AriaNeural";
                }
                setDraft({ ...draft, response_language: v, tts_voice: nextVoice });
              }}
            />
          </Field>

          <Field label="Voice" hint={ttsVoicesLoading ? "Loading voices..." : `Select an audio profile (${ttsVoices.length} available)`} wide>
            <select
              className="hud-input"
              value={draft.tts_voice ?? "af_heart"}
              disabled={ttsVoicesLoading || !ttsVoices.length}
              aria-label="Voice"
              onChange={(e) => set("tts_voice", e.target.value)}
            >
              {ttsVoices.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name} ({v.gender}, {v.accent || v.language}) {v.description ? `— ${v.description}` : ""}
                </option>
              ))}
              {!ttsVoices.some((v) => v.id === draft.tts_voice) && draft.tts_voice && (
                <option value={draft.tts_voice}>{draft.tts_voice}</option>
              )}
            </select>
            {ttsVoicesLoading && <span className="ml-2 text-[10px] text-[var(--muted)]">loading voices...</span>}
            {(draft.tts_provider ?? "kokoro") === "kokoro" && draft.response_language === "hinglish" && (
              <p className="mt-1 text-[11px] text-amber-300/80">
                Note: Kokoro synthesizes English and experimental Hindi. For conversational Hinglish (mixed scripts), Edge TTS is recommended.
              </p>
            )}
            {(draft.tts_provider ?? "kokoro") === "kokoro" && draft.response_language === "hindi" && (
              <p className="mt-1 text-[11px] text-[var(--muted)]">
                Kokoro Hindi voices: hf_alpha, hf_beta, hm_omega, hm_psi (uses eSpeak-NG phonemization).
              </p>
            )}
          </Field>

          <div className="flex items-center gap-3">
            <button
              className="hud-btn hud-btn-outline"
              onClick={() => void runVoiceTest()}
              disabled={voiceTesting || ttsVoicesLoading}
            >
              {voiceTesting ? "Generating audio..." : "Test voice preview"}
            </button>
            {voiceTestResult && voiceTestResult.success && (
              <span className="text-xs font-mono text-[var(--ok)]">
                {voiceTestResult.sample_rate / 1000}kHz · RTF {voiceTestResult.rtf?.toFixed(2) ?? "-"} · {voiceTestResult.inference_ms}ms ({voiceTestResult.device})
              </span>
            )}
            {voiceTestResult && !voiceTestResult.success && (
              <span className="text-xs font-mono text-[var(--danger)]">
                {voiceTestResult.error}
              </span>
            )}
          </div>

          <Field label="Speaking speed" hint="Slower <-> faster" wide>
            <Slider
              value={draft.speaking_rate}
              min={-50}
              max={50}
              step={5}
              onChange={(v) => set("speaking_rate", v)}
              format={(v) => `${v > 0 ? "+" : ""}${v}%`}
              label="Speaking speed"
            />
          </Field>

          <Field label="Wake word" hint="Say it to start; off = tap the orb instead">
            <div className="flex items-center gap-3">
              <span className="hud-value text-[11px]">Hey NOOR</span>
              <Toggle checked={draft.wake_word_enabled} onChange={(v) => set("wake_word_enabled", v)} label="Wake word" />
            </div>
          </Field>

          <Field label="Response style" hint="How much NOOR says">
            <Segmented
              value={draft.response_style}
              options={[{ value: "concise", label: "Concise" }, { value: "normal", label: "Normal" }]}
              onChange={(v) => set("response_style", v)}
            />
          </Field>

          <Field label="NOOR speaks replies" hint="Captions are always shown">
            <Toggle checked={draft.voice_replies} onChange={(v) => set("voice_replies", v)} label="Speak replies" />
          </Field>

          <Field label="Follow-up window" hint="Keep listening without the wake word after NOOR finishes" wide>
            <Slider
              value={draft.conversation_window_s}
              min={0}
              max={60}
              step={5}
              onChange={(v) => set("conversation_window_s", v)}
              format={(v) => `${v}s`}
              label="Follow-up window"
            />
          </Field>
        </div>
      </Section>

      <Section label="Listening">
        <div className="hud-panel hud-divide">
          <Field label="Microphone" hint="Takes effect when the microphone restarts" wide><MicDevices /></Field>
          <Field label="Silence timeout" hint="A pause this long ends your sentence" wide><Slider value={draft.silence_timeout_ms} min={300} max={2500} step={100} onChange={(v) => set("silence_timeout_ms", v)} format={(v) => `${v}ms`} label="Silence timeout" /></Field>
          <Field label="Wake sensitivity" hint="Forgiving <-> strict (fewer accidental wakes)" wide><Slider value={draft.wake_sensitivity} min={0.55} max={0.95} step={0.05} onChange={(v) => set("wake_sensitivity", v)} format={(v) => v.toFixed(2)} label="Wake sensitivity" /></Field>
          <Field label="Noise suppression" hint="Removes steady background noise"><Toggle checked={draft.noise_suppression} onChange={(v) => set("noise_suppression", v)} label="Noise suppression" /></Field>
          <Field label="Echo cancellation" hint="Stops NOOR hearing its own voice"><Toggle checked={draft.echo_cancellation} onChange={(v) => set("echo_cancellation", v)} label="Echo cancellation" /></Field>
          <Field label="Automatic gain" hint="Evens out quiet and loud speech"><Toggle checked={draft.auto_gain} onChange={(v) => set("auto_gain", v)} label="Automatic gain" /></Field>
        </div>
        <details className="mt-3">
          <summary className="hud-label cursor-pointer hover:!text-[var(--amber)]">Advanced voice detection</summary>
          <div className="hud-panel hud-divide mt-2">
            <Field label="Speech start threshold" hint="Loudness (0–1) needed to start; higher ignores more noise" wide><Slider value={draft.speech_start_threshold} min={0.004} max={0.08} step={0.002} onChange={(v) => set("speech_start_threshold", v)} format={(v) => v.toFixed(3)} label="Speech start threshold" /></Field>
            <Field label="Speech end threshold" hint="Lower bar to keep speech going" wide><Slider value={draft.speech_end_threshold} min={0.002} max={0.06} step={0.002} onChange={(v) => set("speech_end_threshold", v)} format={(v) => v.toFixed(3)} label="Speech end threshold" /></Field>
            <Field label="Minimum speech" hint="Shorter sounds are ignored (ms)">{num("minimum_speech_ms", 60, 1000)}</Field>
            <Field label="Maximum utterance" hint="Hard stop for one command (s)">{num("maximum_utterance_s", 3, 60)}</Field>
          </div>
        </details>
      </Section>

      <Section label="Agent">
        <div className="hud-panel hud-divide">
          <Field label="Max step retries" hint="Tries per action on transient failures such as timeouts">{num("max_step_retries", 0, 5)}</Field>
          <Field label="Max re-plans" hint="Recovery attempts before giving up">{num("max_replans", 0, 8)}</Field>
          <Field label="Max actions per task" hint="Hard budget that prevents runaway loops">{num("max_steps", 1, 200)}</Field>
          <Field label="Automation timeout" hint="Whole-task time limit (seconds)">{num("automation_timeout_s", 30, 7200)}</Field>
          <Field label="Medium-risk actions" hint="Move/rename files, scripts, installs"><Segmented value={draft.policy_medium} options={[{ value: "ask", label: "Ask" }, { value: "auto", label: "Auto" }, { value: "block", label: "Block", tone: "bad" }]} onChange={(v) => set("policy_medium", v)} /></Field>
          <Field label="High-risk actions" hint="Email, uploads, financial, destructive"><Segmented value={draft.policy_high} options={[{ value: "confirm", label: "Confirm" }, { value: "block", label: "Block", tone: "bad" }]} onChange={(v) => set("policy_high", v)} /></Field>
          <Field label="Headless browser" hint="Hide the automation browser window"><Toggle checked={draft.browser_headless} onChange={(v) => set("browser_headless", v)} label="Headless browser" /></Field>
        </div>
      </Section>

      <Section label="Memory & data">
        <div className="hud-panel hud-divide">
          <Field label="Use memory" hint="Off: NOOR neither recalls nor stores anything"><Toggle checked={draft.memory_enabled} onChange={(v) => set("memory_enabled", v)} label="Use memory" /></Field>
          <Field label="Learn from tasks" hint="Save non-sensitive habits automatically"><Toggle checked={draft.auto_memory} onChange={(v) => set("auto_memory", v)} label="Learn from tasks" /></Field>
          <Field label="Keep screenshots (hours)" hint="Older screenshots are deleted automatically">{num("screenshot_retention_hours", 0, 8760)}</Field>
          <Field label="Keep task history (days)" hint="Older tasks are removed from MongoDB">{num("task_retention_days", 1, 3650)}</Field>
        </div>
      </Section>

      <Section label="System">
        <div className="hud-panel hud-divide">
          <Field label="Start with Windows" hint={system.supported ? "Launch NOOR when you sign in" : "Available in the desktop app"}><Toggle checked={system.value?.startAtLogin ?? false} disabled={!system.supported} onChange={(v) => void system.set({ startAtLogin: v })} label="Start with Windows" /></Field>
          <Field label="Minimize to tray" hint={system.supported ? "Closing the window keeps NOOR listening" : "Available in the desktop app"}><Toggle checked={system.value?.minimizeToTray ?? false} disabled={!system.supported} onChange={(v) => void system.set({ minimizeToTray: v })} label="Minimize to tray" /></Field>
          <Field label="Start microphone on launch" hint="Listen for the wake word as soon as NOOR opens"><Toggle checked={startMicOnLaunch} onChange={(v) => { setStartMicOnLaunch(v); void setMic(v && micOn); try { localStorage.setItem("noor.mic", v ? "on" : "off"); } catch { /* unavailable */ } }} label="Start microphone on launch" /></Field>
          <Field label="Developer metrics" hint="Show per-turn voice latency on the Voice screen"><Toggle checked={draft.dev_metrics} onChange={(v) => set("dev_metrics", v)} label="Developer metrics" /></Field>
        </div>
      </Section>

      <div className="sticky bottom-0 -mx-8 mb-6 flex items-center gap-3 border-t border-[var(--line)] bg-[var(--bg)]/95 px-8 py-3 backdrop-blur">
        <button className="hud-btn hud-btn-primary" disabled={!dirty} onClick={() => void save()}>Save changes</button>
        <button className="hud-btn" disabled={!dirty} onClick={() => setDraft(data.preferences)}>Discard</button>
        <button className="hud-btn" onClick={() => setDraft(data.defaults)}>Reset to defaults</button>
        {saved && <span className="hud-label !text-[var(--ok)]">Saved</span>}
        {dirty && !saved && <span className="hud-label">Unsaved changes</span>}
      </div>

      <Section label="Environment (read-only — edit .env)">
        <Panel className="grid grid-cols-2 gap-x-6 gap-y-4 p-4 md:grid-cols-4">
          <KV label="Backend" value={BACKEND_URL} />
          <KV label="LLM provider" value={String(env.llmProvider)} />
          <KV label="Model" value={String(env.llmModel)} />
          <KV label="TTS provider" value={String(draft.tts_provider || "kokoro")} />
          <KV label="TTS voice" value={String(draft.tts_voice || "af_heart")} />
          <KV label="Database" value={`${env.database}${env.dbInMemory ? " (in-memory)" : ""}`} />
          <KV label="Browser" value={String(env.browserChannel)} />
          <KV label="Wake / speech" value={voice ? `${voice.wakeWord.available ? "wake ready" : "wake loading"} · stt ${voice.stt.provider ?? "none"}` : "-"} />
        </Panel>
      </Section>
    </>
  );
}
