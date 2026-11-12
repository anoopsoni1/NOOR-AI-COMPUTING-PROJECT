import type {

  AuditEntry, Capability, CapabilityMode, Health, LlmModel, LlmProviderInfo, LlmTestResult, Memory, Policy, Preferences, Rule, SettingsResponse, Stats, Task, TaskDetail, TaskSummary,

  TTSProviderInfo, VoiceProfile, VoiceTestResult, SystemMetrics,
} from "../types";



export const BACKEND_URL: string =

  (window.noor?.backendUrl || import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export const API_TOKEN: string = window.noor?.apiToken || import.meta.env.VITE_API_TOKEN || "";



export class ApiError extends Error {

  constructor(public status: number, message: string) {

    super(message);

  }

}



async function request<T>(method: string, path: string, body?: unknown): Promise<T> {

  const res = await fetch(`${BACKEND_URL}/api${path}`, {

    method,

    headers: { "Content-Type": "application/json", ...(API_TOKEN ? { "X-Noor-Token": API_TOKEN } : {}) },

    body: body === undefined ? undefined : JSON.stringify(body),

  });

  if (!res.ok) {

    let detail = res.statusText;

    try {

      const j = await res.json();

      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);

    } catch { /* non-JSON error body */ }

    throw new ApiError(res.status, detail);

  }

  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);

}





export const api = {

  health: () => request<Health>("GET", "/health"),

  stats: () => request<Stats>("GET", "/stats"),

  startTask: (goal: string) => request<{ id: string }>("POST", "/tasks", { goal }),

  pauseTask: () => request<{ ok: boolean }>("POST", "/tasks/pause"),

  resumeTask: () => request<{ ok: boolean }>("POST", "/tasks/resume"),

  stopTask: () => request<{ stopped: boolean }>("POST", "/tasks/stop"),

  respond: (text: string) => request<{ ok: boolean }>("POST", "/tasks/respond", { text }),

  currentTask: () => request<Task | null>("GET", "/tasks/current"),

  listTasks: (status?: string) => request<TaskSummary[]>("GET", `/tasks?limit=100${status ? `&status=${status}` : ""}`),

  getTask: (id: string) => request<TaskDetail>("GET", `/tasks/${id}`),

  deleteTask: (id: string) => request<void>("DELETE", `/tasks/${id}`),

  answerPermission: (id: string, approve: boolean) => request<{ ok: boolean }>("POST", `/permissions/requests/${id}`, { approve }),

  capabilities: () => request<Capability[]>("GET", "/permissions/capabilities"),

  setCapability: (capability: string, mode: CapabilityMode) => request<{ mode: string }>("PUT", `/permissions/capabilities/${capability}`, { mode }),

  rules: () => request<Rule[]>("GET", "/permissions/rules"),

  setRule: (tool: string, policy: Policy) => request<{ tool: string }>("PUT", `/permissions/rules/${tool}`, { policy }),

  audit: () => request<AuditEntry[]>("GET", "/permissions/log"),

  memories: () => request<Memory[]>("GET", "/memories"),

  addMemory: (content: string, category?: string) => request<Memory>("POST", "/memories", { content, category }),

  editMemory: (id: string, content: string, category?: string) => request<Memory>("PUT", `/memories/${id}`, { content, category }),

  deleteMemory: (id: string) => request<void>("DELETE", `/memories/${id}`),

  settings: () => request<SettingsResponse>("GET", "/settings"),

  saveSettings: (p: Preferences) => request<SettingsResponse>("PUT", "/settings", p),

  llmProviders: () => request<LlmProviderInfo[]>("GET", "/llm/providers"),

  llmModels: (provider: string) => request<LlmModel[]>(`GET`, `/llm/models?provider=${encodeURIComponent(provider)}`),

  llmTest: (provider: string, model?: string) => request<LlmTestResult>("POST", "/llm/test", { provider, model: model ?? null }),

  llmReconfigure: (provider: string, model?: string) => request<{ ok: boolean; provider: string; model: string | null }>("PUT", "/llm/reconfigure", { provider, model: model ?? null }),

  voiceProviders: () => request<TTSProviderInfo[]>("GET", "/voice/providers"),

  voiceVoices: (provider?: string, language?: string) => {

    const params = new URLSearchParams();

    if (provider) params.set("provider", provider);

    if (language) params.set("language", language);

    const qs = params.toString();

    return request<VoiceProfile[]>("GET", `/voice/voices${qs ? `?${qs}` : ""}`);

  },

  voiceTest: (body: { provider?: string; voice?: string; text?: string; speed?: number; language?: string }) =>

    request<VoiceTestResult>("POST", "/voice/test", body),

  voiceStatus: () => request<Record<string, unknown>>("GET", "/voice/status"),

  systemMetrics: () => request<SystemMetrics>("GET", "/system/metrics"),

};



