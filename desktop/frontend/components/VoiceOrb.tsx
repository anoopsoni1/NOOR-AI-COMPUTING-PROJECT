import { useEffect, useRef } from "react";
import { useNoor, useNoorLive } from "../hooks/useNoor";
import { previewMode, useOrbMode } from "../hooks/useOrbMode";
import { AudioFrame, OrbMode, OrbRenderer } from "../services/orbRenderer";

/**
 * Interactive NOOR orb. The renderer is created once and persists; the draw loop runs only while the screen is visible and the window is
 * not hidden. Visuals are driven by the live microphone while LISTENING and by NOOR's own audio output while SPEAKING.
 * Click = talk without the wake word (also interrupts NOOR). The orb leans towards the pointer.
 */
export default function VoiceOrb({ visible, onModeChange }: { visible: boolean; onModeChange?: (m: OrbMode) => void }) {
  const { levels, activate } = useNoor();
  const { userSpeaking } = useNoorLive();
  const mode = useOrbMode();
  const host = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const orbRef = useRef<OrbRenderer | null>(null);
  const live = useRef({ mode, userSpeaking });
  live.current = { mode, userSpeaking };

  useEffect(() => onModeChange?.(mode), [mode, onModeChange]);

  // one renderer for the lifetime of the app
  useEffect(() => {
    const el = canvas.current;
    const box = host.current;
    if (!el || !box) return;
    const orb = new OrbRenderer(el);
    orbRef.current = orb;
    const ro = new ResizeObserver(() => {
      if (box.clientWidth > 0 && box.clientHeight > 0) orb.resize(box.clientWidth, box.clientHeight);
    });
    ro.observe(box);
    return () => {
      ro.disconnect();
      orbRef.current = null;
    };
  }, []);

  // the draw loop: only while visible
  useEffect(() => {
    const orb = orbRef.current;
    if (!visible || !orb) return;
    const spec = new Uint8Array(256);
    const wave = new Uint8Array(512).fill(128);
    const audio: AudioFrame = { source: "none", level: 0, spectrum: spec, wave, userSpeaking: false };
    let raf = 0;
    const loop = (t: number) => {
      raf = requestAnimationFrame(loop);
      if (document.hidden) return; // window minimised / tab hidden: skip the frame
      const { mode: m, userSpeaking: speaking } = live.current;
      orb.setMode(m);
      const source = m === "SPEAKING" ? "out" : m === "LISTENING" ? "mic" : "none";
      audio.source = source;
      audio.userSpeaking = speaking && m === "LISTENING";
      if (previewMode() && source !== "none") { // design preview only: synthetic voice-like audio
        const s = t / 1000;
        const env = 0.5 + 0.5 * Math.sin(s * 3.1) * Math.sin(s * 1.3 + 1);
        for (let i = 0; i < spec.length; i++) spec[i] = Math.max(0, Math.min(255, env * 230 * Math.exp(-i / 55) * (0.6 + 0.4 * Math.sin(i * 0.9 + s * 9))));
        for (let i = 0; i < wave.length; i++) wave[i] = 128 + env * 70 * Math.sin(i * 0.22 + s * 14) * Math.sin(i * 0.031 + s);
        audio.level = env * 0.28;
      } else if (source === "out") {
        levels.outSpectrum(spec);
        levels.outWave(wave);
        audio.level = levels.out();
      } else if (source === "mic") {
        levels.micSpectrum(spec);
        levels.micWave(wave);
        audio.level = levels.mic();
      } else {
        spec.fill(0);
        audio.level = 0;
      }
      orb.frame(t, audio);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [visible, levels]);

  return (
    <div
      ref={host}
      className="absolute inset-0 cursor-pointer"
      onClick={activate}
      onPointerMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        orbRef.current?.setPointer(((e.clientX - r.left) / r.width) * 2 - 1, ((e.clientY - r.top) / r.height) * 2 - 1);
      }}
      onPointerLeave={() => orbRef.current?.setPointer(0, 0)}
      role="button"
      aria-label="NOOR voice orb. Click to talk."
      title="Click to talk without the wake word"
    >
      <canvas ref={canvas} className="h-full w-full" />
    </div>
  );
}
