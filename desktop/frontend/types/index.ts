export type AgentState =

  | "IDLE" | "PLANNING" | "WAITING_FOR_PERMISSION" | "EXECUTING" | "OBSERVING" | "VERIFYING"

  | "RECOVERING" | "COMPLETED" | "FAILED" | "CANCELLED" | "WAITING_FOR_USER";



export type StepStatus = "pending" | "running" | "done" | "failed" | "skipped";



export interface PlanStep {

  id: number;

  tool: string;

  description: string;

  args: Record<string, unknown>;

  expect: string;

  status: StepStatus;

  attempts: number;

  error: string | null;

}



export interface ActionRecord {

  step_id: number;

  tool: string;

  args: Record<string, unknown>;

  success: boolean;

  message: string;

  error_code: string | null;

  duration_ms: number;

  agent_state: string;

  ts: string;

  screenshot: string | null;

}



export interface PermissionRequest {

  id: string;

  tool: string;

  risk: string;

  title: string;

  details: { label: string; value: string }[];

  reason: string;

  voice_approvable: boolean;

  speech: string;

  level: "low" | "medium" | "high";

}



export interface LogLine {

  ts: string;

  level: "info" | "warn" | "error";

  text: string;

}



export interface Task {

  id: string;

  goal: string;

  status: AgentState;

  understanding: string;

  plan: PlanStep[];

  actions: ActionRecord[];

  errors: string[];

  log: LogLine[];

  pending_question: string | null;

  pending_permission: PermissionRequest | null;

  result: string;

  replans: number;

  paused: boolean;

  created_at: string;

  completed_at: string | null;

}



export interface TaskDetail extends Task {

  executions: Record<string, unknown>[];

}



export type TaskSummary = Pick<Task, "id" | "goal" | "status" | "created_at" | "completed_at" | "result" | "replans" | "errors">;



export interface Memory {

  id: string;

  content: string;

  category: "preference" | "app" | "folder" | "workflow" | "outcome" | "other";

  source: "user" | "auto";

  created_at: string;

  updated_at: string;

}



export type Policy = "default" | "ask" | "allow";



export interface Rule {

  tool: string;

  category: string;

  description: string;

  policy: Policy;

}



export interface AuditEntry {

  id: string;

  ts: string;

  tool: string;

  title: string;

  risk: string;

  outcome: "approved" | "denied" | "blocked";

  reason: string;

}



export interface Health {

  status: string;

  llmConfigured: boolean;

  provider: string;

  model: string;

  visionEnabled: boolean;

  dbInMemory: boolean;

  busy: boolean;

  desktopAutomation: boolean;

  voice?: VoiceStatus;

}



export interface Preferences {

  max_steps: number;

  max_step_retries: number;

  max_replans: number;

  automation_timeout_s: number;

  browser_headless: boolean;

  policy_medium: "ask" | "auto" | "block";

  policy_high: "confirm" | "block";

  screenshot_retention_hours: number;

  task_retention_days: number;

  auto_memory: boolean;

  memory_enabled: boolean;

  voice_replies: boolean;

  tts_provider?: "kokoro" | "edge" | "piper" | "openai" | "elevenlabs" | "system" | "off";

  tts_voice: string;

  speaking_rate: number;

  tts_speed?: number;

  response_style: "concise" | "normal";

  response_language: "english" | "hinglish" | "hindi";

  wake_word_enabled: boolean;

  wake_sensitivity: number;

  conversation_window_s: number;

  speech_start_threshold: number;

  speech_end_threshold: number;

  silence_timeout_ms: number;

  minimum_speech_ms: number;

  maximum_utterance_s: number;

  noise_suppression: boolean;

  echo_cancellation: boolean;

  auto_gain: boolean;

  dev_metrics: boolean;

  llm_provider: "ollama" | "groq" | "gemini" | null;

  llm_model: string | null;

  llm_fallback_provider: "ollama" | "groq" | "gemini" | "none" | null;

  llm_fallback_enabled: boolean;

}



export type CapabilityMode = "ENABLED" | "ASK" | "DISABLED";



export interface Capability {

  capability: string;

  label: string;

  description: string;

  mode: CapabilityMode;

  default: CapabilityMode;

  tools: string[];

}



export interface TurnMetrics {

  turn: number;

  marks_ms: Record<string, number>;

  latency_ms: { stt: number | null; to_first_token: number | null; to_first_audio: number | null; task_duration: number | null };

}



export interface SettingsResponse {

  preferences: Preferences;

  defaults: Preferences;

  environment: Record<string, string | boolean>;

}



export interface Stats {

  total: number;

  byStatus: Record<string, number>;

  today: { total: number; completed: number; failed: number; cancelled: number; waiting: number };

  recent: TaskSummary[];

}



export type VoiceState = "IDLE" | "WAKE_DETECTED" | "LISTENING" | "PROCESSING" | "EXECUTING" | "SPEAKING";



export interface VoiceStatus {

  state: VoiceState;

  engaged: boolean;

  mic: boolean;

  micAllowed: boolean;

  tts: boolean;

  wakeWord: { enabled: boolean; phrase: string; available: boolean; model: string | null; error: string };

  stt: { provider: string | null; local: boolean };

  tts_provider: string | null;

  vad: { engine: string; endSilenceMs: number };

}



export type ServerEvent =

  | ({ type: "voice.state" } & VoiceStatus)

  | { type: "voice.started"; reason: string; kind?: string }

  | { type: "voice.stopped" }

  | { type: "voice.partial_transcript"; text: string }

  | { type: "voice.final_transcript"; text: string; source: string }

  | ({ type: "metrics.turn" } & TurnMetrics)

  | { type: `task.${"completed" | "failed" | "cancelled"}`; taskId?: string }

  | { type: `permission.${"requested" | "granted" | "denied" | "capability"}` }

  | { type: "voice.error"; message: string }

  | { type: "assistant.delta"; text: string }

  | { type: "assistant.message"; text: string }

  | { type: "tts.started" }

  | { type: "tts.chunk"; epoch: number; mime: string; data: string }

  | { type: "tts.stop"; epoch: number }

  | { type: "tts.duck"; on: boolean }

  | { type: "tts.finished"; interrupted?: boolean }

  | { type: `agent.${string}`; taskId?: string; state?: string }

  | { type: "hello"; task: Task | null; voice: VoiceStatus }

  | { type: "task"; task: Task }

  | { type: "finished"; taskId: string; status: AgentState }

  | { type: "pong" };



export type LlmProviderId = "ollama" | "groq" | "gemini";

export type LlmFallbackProvider = LlmProviderId | "none";



export interface LlmProviderInfo {

  id: LlmProviderId;

  name: string;

  needs_api_key: boolean;

  configured: boolean;

  default_model: string;

}



export interface LlmModel {

  id: string;

  label: string;

}



export interface LlmTestResult {

  ok: boolean;

  provider: string;

  model: string;

  message: string;

}





export interface TTSProviderInfo {

  id: string;

  name: string;

  is_local: boolean;

  needs_key: boolean;

  configured: boolean;

  device: string;

  sample_rate: number;

  description: string;

  status: string;

}



export interface VoiceProfile {

  id: string;

  name: string;

  provider: string;

  language: string;

  gender: "female" | "male" | "neutral";

  accent: string;

  sample_rate: number;

  description: string;

  is_default: boolean;

  is_local: boolean;

}



export interface VoiceTestResult {

  success: boolean;

  provider: string;

  voice: string;

  language: string;

  duration_seconds: number;

  inference_ms: number;

  rtf: number;

  sample_rate: number;

  mime_type: string;
  device?: string;

  audio_base64?: string;

  error?: string;

}

export interface SystemMetrics {
  system: {
    cpu_percent: number;
    ram_used_mb: number;
    ram_total_mb: number;
    ram_percent: number;
    gpu_total_mb: number;
    gpu_used_mb: number;
    gpu_util_percent: number;
  };
  llm: {
    provider: string;
    model: string;
    configured: boolean;
  };
  voice: {
    tts_provider: string;
    tts_voice: string;
    language: string;
    stt_device: string;
  };
  task: {
    taskId?: string;
    goal?: string;
    status?: string;
    executions?: number;
    actions?: Array<{
      tool: string;
      duration_ms: number;
      success: boolean;
    }>;
  };
  latency?: {
    turn: number;
    marks_ms: Record<string, number>;
    latency_ms: {
      stt: number | null;
      to_first_token: number | null;
      to_first_audio: number | null;
      task_duration: number | null;
    };
  } | null;
}

