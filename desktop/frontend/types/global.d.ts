interface NoorSystemPrefs {
  startAtLogin: boolean;
  minimizeToTray: boolean;
}

interface Window {
  /** Injected by the Electron preload script. Undefined when running in a plain browser. */
  noor?: {
    backendUrl: string;
    apiToken: string;
    platform: string;
    system?: { get: () => Promise<NoorSystemPrefs>; set: (patch: Partial<NoorSystemPrefs>) => Promise<NoorSystemPrefs> };
  };
}

interface ImportMetaEnv {
  readonly VITE_BACKEND_URL?: string;
  readonly VITE_API_TOKEN?: string;
}
