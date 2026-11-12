import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../services/api";

import { Bytes, MicStreamer, TtsPlayer } from "../services/audio";

import { ConnectionStatus, NoorSocket } from "../services/socket";

import type { Health, Preferences, ServerEvent, Task, TurnMetrics, VoiceState, VoiceStatus } from "../types";



export interface AudioLevels {

  mic: () => number;

  out: () => number;

  micWave: (into: Bytes) => void;

  outWave: (into: Bytes) => void;

  micSpectrum: (into: Bytes) => void;

  outSpectrum: (into: Bytes) => void;

}



export interface Toast {

  id: number;

  kind: "ok" | "bad" | "info";

  text: string;

}



/** Stable-ish application state. Pages other than Voice subscribe only to this, so a transcript update never re-renders them. */

interface NoorContextValue {

  connection: ConnectionStatus;

  health: Health | null;

  task: Task | null;

  /** True while a task is running (not in a terminal state). */

  running: boolean;

  voice: VoiceStatus | null;

  voiceState: VoiceState;

  prefs: Preferences | null;

  micOn: boolean;

  micError: string | null;

  speakerOn: boolean;

  levels: AudioLevels;

  toasts: Toast[];

  dismissToast: (id: number) => void;

  reloadPrefs: () => Promise<void>;

  setMic: (on: boolean) => Promise<void>;

  restartMic: () => Promise<void>;

  setSpeaker: (on: boolean) => void;

  activate: () => void;

  sendText: (text: string) => Promise<void>;

  stop: () => Promise<void>;

  approve: (id: string, approve: boolean) => Promise<void>;

  respond: (text: string) => Promise<void>;

  refreshHealth: () => void;

}



/** Fast-changing voice text and measurements (used by the Voice screen only). */

interface NoorLiveValue {

  userSpeaking: boolean;

  /** Live caption of what the user is saying (partial) or last said (final). */

  heard: string;

  /** What NOOR is saying / last said. */

  caption: string;

  voiceError: string | null;

  metrics: TurnMetrics[];

}



const NoorContext = createContext<NoorContextValue | null>(null);

const LiveContext = createContext<NoorLiveValue | null>(null);

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED", "IDLE"]);

const store = {

  get: (k: string) => {

    try {

      return localStorage.getItem(k);

    } catch {

      return null;

    }

  },

  set: (k: string, v: string) => {

    try {

      localStorage.setItem(k, v);

    } catch { /* storage unavailable */ }

  },

};



export function NoorProvider({ children }: { children: ReactNode }) {

  const [connection, setConnection] = useState<ConnectionStatus>("connecting");

  const [health, setHealth] = useState<Health | null>(null);

  const [task, setTask] = useState<Task | null>(null);

  const [voice, setVoice] = useState<VoiceStatus | null>(null);

  const [prefs, setPrefs] = useState<Preferences | null>(null);

  const [micOn, setMicOn] = useState<boolean>(() => store.get("noor.mic") !== "off");

  const [micError, setMicError] = useState<string | null>(null);

  const [speakerOn, setSpeakerOn] = useState(store.get("noor.speaker") !== "off");

  const [toasts, setToasts] = useState<Toast[]>([]);



  const [userSpeaking, setUserSpeaking] = useState(false);

  const [heard, setHeard] = useState("");

  const [caption, setCaption] = useState("");

  const [voiceError, setVoiceError] = useState<string | null>(null);

  const [metrics, setMetrics] = useState<TurnMetrics[]>([]);



  const socket = useRef<NoorSocket | null>(null);

  const mic = useRef<MicStreamer | null>(null);

  const player = useRef<TtsPlayer | null>(null);

  const streaming = useRef("");

  const speakerRef = useRef(speakerOn);

  const prefsRef = useRef<Preferences | null>(null);

  const toastId = useRef(0);

  speakerRef.current = speakerOn;

  prefsRef.current = prefs;



  const refreshHealth = useCallback(() => {

    api.health().then(setHealth).catch(() => setHealth(null));

  }, []);



  const reloadPrefs = useCallback(async () => {

    try {

      setPrefs((await api.settings()).preferences);

    } catch { /* backend offline: defaults apply */ }

  }, []);



  const notify = useCallback((kind: Toast["kind"], text: string) => {

    const id = ++toastId.current;

    setToasts((t) => [...t.slice(-3), { id, kind, text }]);

    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5500);

  }, []);



  const getPlayer = useCallback((): TtsPlayer => {

    if (!player.current) {

      player.current = new TtsPlayer((epoch) => {

        socket.current?.send({ type: "tts_idle", epoch });

        // Audio playback has fully finished! Automatically resume listening hands-free

        // ONLY if user has not manually muted the microphone.

        if (mic.current && store.get("noor.mic") !== "off") {

          mic.current.setPaused(false);

        }

      });

    }

    return player.current;

  }, []);



  const startMic = useCallback(async () => {
    if (mic.current) {
      await mic.current.resume();
      mic.current.setPaused(false);
      store.set("noor.mic", "on");
      setMicOn(true);
      setMicError(null);
      socket.current?.send({ type: "voice_enable", enabled: true });
      return;
    }
    try {
      const p = prefsRef.current;
      const m = new MicStreamer();
      await m.start((pcm) => socket.current?.sendBinary(pcm), store.get("noor.micDevice") || undefined, {
        echo: p?.echo_cancellation ?? true, noise: p?.noise_suppression ?? true, gain: p?.auto_gain ?? true,
      });
      await m.resume();
      mic.current = m;
      store.set("noor.mic", "on");
      setMicOn(true);
      setMicError(null);
      socket.current?.send({ type: "voice_enable", enabled: true });
    } catch (err) {
      console.error("[NOOR Audio] startMic failed:", err);
      const name = err instanceof DOMException ? err.name : "";
      setMicOn(false);
      setMicError(name === "NotAllowedError" ? "Microphone access was denied. Allow it for NOOR in your system privacy settings." :
        name === "NotFoundError" ? "No microphone found. Connect one and try again." : "Could not start the microphone.");
    }
  }, []);



  const stopMic = useCallback(() => {

    mic.current?.stop();

    mic.current = null;

    setMicOn(false);

    socket.current?.send({ type: "voice_enable", enabled: false });

  }, []);



  const onEvent = useCallback((e: ServerEvent) => {

    switch (e.type) {

      case "hello":

        setTask(e.task);

        setVoice(e.voice);

        socket.current?.send({ type: "tts_enable", enabled: speakerRef.current });

        break;

      case "task":

        setTask(e.task);

        break;

      case "finished":

        refreshHealth();

        break;

      case "voice.state":

        setVoice(e);

        if (e.state === "IDLE") setUserSpeaking(false);

        if (e.state === "SPEAKING") {

          mic.current?.setPaused(true);

        } else if (e.state === "LISTENING" || e.state === "IDLE") {

          if (mic.current && store.get("noor.mic") !== "off") {

            mic.current.setPaused(false);

          }

        }

        // The backend is the authority on the microphone permission: revoking it stops capture immediately.

        if (!e.micAllowed && mic.current) {

          mic.current.stop();

          mic.current = null;

          setMicOn(false);

        }

        break;

      case "voice.started":

        if (e.reason === "wake" || e.reason === "activate") {

          if (speakerRef.current) getPlayer().chime();

          setHeard("");

        } else if (e.reason === "speech") {

          setUserSpeaking(true);

          setVoiceError(null);

        }

        break;

      case "voice.stopped":

        setUserSpeaking(false);

        break;

      case "voice.partial_transcript":

        setHeard(e.text);

        break;

      case "voice.final_transcript":

        setHeard(e.text);

        streaming.current = "";

        setCaption("");

        break;

      case "voice.error":

        setVoiceError(e.message);

        notify("bad", e.message);

        break;

      case "metrics.turn":

        setMetrics((m) => (m.length && m[m.length - 1].turn === e.turn ? [...m.slice(0, -1), e] : [...m.slice(-9), e]));

        break;

      case "assistant.delta":

        streaming.current = (streaming.current ? streaming.current + " " : "") + e.text;

        setCaption(streaming.current);

        break;

      case "assistant.message":

        streaming.current = "";

        setCaption(e.text);

        break;

      case "tts.started":

        // Pause microphone before speaking starts

        mic.current?.setPaused(true);

        break;

      case "tts.chunk":

        mic.current?.setPaused(true);

        if (speakerRef.current) getPlayer().enqueue(e.data, e.epoch);

        break;

      case "tts.finished":

        // Speech playback finished: resume mic hands-free if not manually muted

        if (mic.current && store.get("noor.mic") !== "off") {

          mic.current.setPaused(false);

        }

        break;

      case "tts.stop":

        getPlayer().stop(e.epoch); // barge-in: silence immediately

        if (mic.current && store.get("noor.mic") !== "off") {

          mic.current.setPaused(false);

        }

        break;

      case "tts.duck":

        getPlayer().duck(e.on);

        break;

      case "task.completed":

        notify("ok", "Task completed");

        break;

      case "task.failed":

        notify("bad", "Task failed");

        break;

      case "task.cancelled":

        notify("info", "Task stopped");

        break;

      case "permission.denied":

        notify("info", "Action denied");

        break;

      default:

        break;

    }

  }, [getPlayer, notify, refreshHealth]);



  useEffect(() => {

    const s = new NoorSocket(onEvent, (st) => {

      setConnection(st);

      if (st === "open") {

        refreshHealth();

        void reloadPrefs();

        s.send({ type: "voice_enable", enabled: store.get("noor.mic") !== "off" });

      }

    });

    socket.current = s;

    s.connect();

    const poll = window.setInterval(refreshHealth, 15000);

    return () => {

      s.close();

      window.clearInterval(poll);

      mic.current?.stop();

      mic.current = null;

    };

  }, [onEvent, refreshHealth, reloadPrefs]);



  // Voice-first: start listening for the wake word on launch (unless the user turned the mic off or revoked the permission).

  const autoStarted = useRef(false);

  useEffect(() => {

    if (autoStarted.current || connection !== "open" || !voice) return;

    if (store.get("noor.mic") !== "off" && voice.micAllowed) {

      autoStarted.current = true;

      void startMic();

    }

  }, [connection, voice, startMic]);



  const levels = useMemo<AudioLevels>(() => ({

    mic: () => mic.current?.micLevel() ?? 0,

    out: () => player.current?.outLevel() ?? 0,

    micWave: (into) => mic.current?.waveform(into),

    outWave: (into) => player.current?.waveform(into),

    micSpectrum: (into) => mic.current?.spectrum(into),

    outSpectrum: (into) => player.current?.spectrum(into),

  }), []);



  const core = useMemo<NoorContextValue>(() => ({

    connection, health, task,

    running: !!task && !TERMINAL.has(task.status),

    voice,

    voiceState: voice?.state ?? "IDLE",

    prefs, micOn, micError, speakerOn, levels, toasts,

    dismissToast: (id) => setToasts((t) => t.filter((x) => x.id !== id)),

    reloadPrefs,

    setMic: async (on) => {

      store.set("noor.mic", on ? "on" : "off");

      if (on) await startMic();

      else stopMic();

    },

    restartMic: async () => {

      if (!mic.current) return;

      stopMic();

      await new Promise((r) => setTimeout(r, 150));

      await startMic(); // picks up new noise-suppression / echo-cancellation settings

    },

    setSpeaker: (on) => {

      store.set("noor.speaker", on ? "on" : "off");

      setSpeakerOn(on);

      if (!on) player.current?.stop(0);

      socket.current?.send({ type: "tts_enable", enabled: on });

    },

    activate: async () => {
      store.set("noor.mic", "on");
      if (!mic.current) {
        await startMic();
      } else {
        await mic.current.resume();
        mic.current.setPaused(false);
        setMicOn(true);
        socket.current?.send({ type: "voice_enable", enabled: true });
      }
      socket.current?.send({ type: "activate" });
    },

    // Typed commands share the exact pipeline of spoken ones (conversation manager -> agent).

    sendText: async (text) => {

      if (!socket.current?.send({ type: "user_text", text })) await api.startTask(text);

    },

    // STOP: also silence local playback instantly, without waiting for the round trip.

    stop: async () => {

      player.current?.stop(0);

      if (!socket.current?.send({ type: "stop" })) await api.stopTask();

    },

    approve: async (id, approve) => {

      if (!socket.current?.send({ type: "permission_response", id, approve })) await api.answerPermission(id, approve);

    },

    respond: async (text) => {

      if (!socket.current?.send({ type: "respond", text })) await api.respond(text);

    },

    refreshHealth,

  }), [connection, health, task, voice, prefs, micOn, micError, speakerOn, levels, toasts, startMic, stopMic, reloadPrefs, refreshHealth]);



  const live = useMemo<NoorLiveValue>(() => ({ userSpeaking, heard, caption, voiceError, metrics }), [userSpeaking, heard, caption, voiceError, metrics]);



  return (

    <NoorContext.Provider value={core}>

      <LiveContext.Provider value={live}>{children}</LiveContext.Provider>

    </NoorContext.Provider>

  );

}



export function useNoor(): NoorContextValue {

  const ctx = useContext(NoorContext);

  if (!ctx) throw new Error("useNoor must be used inside <NoorProvider>");

  return ctx;

}



export function useNoorLive(): NoorLiveValue {

  const ctx = useContext(LiveContext);

  if (!ctx) throw new Error("useNoorLive must be used inside <NoorProvider>");

  return ctx;

}



