/**
 * NOOR orb renderer: a real-time 3D particle sphere drawn on a 2D canvas (no sprites, no GIFs).
 *
 * Structure: ~1,500 particles on three depth layers (outer shell, mid shell, dense core) rotating at different speeds, technical
 * line fragments, three tilted rings, a HUD ring with ticks, a scanning sweep, orbiting points and a waveform ring. Every
 * visual parameter eases towards a per-state target, so state changes are smooth transitions rather than switches.
 * Audio drives the visuals directly: particles are displaced by the live frequency spectrum of the microphone (LISTENING)
 * or of NOOR's own voice (SPEAKING), and the waveform ring is the actual time-domain signal.
 */

export type OrbMode = "IDLE" | "LISTENING" | "THINKING" | "EXECUTING" | "VERIFYING" | "WAITING" | "SPEAKING" | "ERROR" | "STOPPED";

export interface AudioFrame {
  /** Which source drives the orb right now. */
  source: "mic" | "out" | "none";
  /** 0-1 overall loudness. */
  level: number;
  spectrum: Uint8Array;
  wave: Uint8Array;
  /** True while the VAD hears the user (adds excitement on top of the raw audio). */
  userSpeaking: boolean;
}

interface Look {
  hue: number; sat: number; light: number;
  bright: number; radius: number; spin: number; ring: number; scan: number; orbit: number;
  pull: number; wave: number; breathe: number; shake: number; fragments: number;
}

const BASE: Look = { hue: 33, sat: 92, light: 56, bright: 0.5, radius: 0.94, spin: 0.06, ring: 0.25, scan: 0, orbit: 0.2, pull: 0, wave: 0, breathe: 0.025, shake: 0, fragments: 0.5 };

const LOOKS: Record<OrbMode, Partial<Look>> = {
  IDLE: {},
  LISTENING: { hue: 41, light: 64, bright: 0.9, radius: 1.06, spin: 0.12, ring: 0.85, pull: 0.85, wave: 1, breathe: 0.01, fragments: 0.8 },
  THINKING: { hue: 34, bright: 0.8, radius: 0.88, spin: 0.7, ring: 1, orbit: 1, pull: 0.25, breathe: 0.05, fragments: 1 },
  EXECUTING: { hue: 27, bright: 0.85, radius: 1.0, spin: 0.25, ring: 0.7, scan: 1, orbit: 0.5, fragments: 0.9 },
  VERIFYING: { hue: 46, light: 62, bright: 0.85, radius: 0.95, spin: 0.18, ring: 0.9, scan: -0.6, orbit: 0.4, breathe: 0.03, fragments: 0.7 },
  WAITING: { hue: 38, sat: 70, light: 70, bright: 0.7, radius: 0.98, spin: 0.03, ring: 0.6, breathe: 0.045, fragments: 0.4 },
  SPEAKING: { hue: 37, light: 62, bright: 1, radius: 1.0, spin: 0.16, ring: 0.9, wave: 1, breathe: 0.008, fragments: 0.9 },
  ERROR: { hue: 4, sat: 88, light: 58, bright: 0.9, radius: 0.97, spin: 0.08, ring: 1, shake: 1, fragments: 1 },
  STOPPED: { bright: 0.22, radius: 0.86, spin: 0, ring: 0.08, breathe: 0.012, fragments: 0.15 },
};

const N_PARTICLES = 2600;
const N_FRAGMENTS = 110;
const SPECTRUM_BINS = 96;

interface Fragment { x: number; y: number; z: number; tx: number; ty: number; tz: number; len: number; layer: number }

function rand(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

export class OrbRenderer {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private w = 0;
  private h = 0;
  private dpr = 1;
  private bx = new Float32Array(N_PARTICLES);
  private by = new Float32Array(N_PARTICLES);
  private bz = new Float32Array(N_PARTICLES);
  private br = new Float32Array(N_PARTICLES);
  private size = new Float32Array(N_PARTICLES);
  private phase = new Float32Array(N_PARTICLES);
  private bin = new Uint8Array(N_PARTICLES);
  private layer = new Uint8Array(N_PARTICLES);
  private fragments: Fragment[] = [];
  private look: Look = { ...BASE };
  private mode: OrbMode = "IDLE";
  private modeSince = 0;
  private yaw = 0;
  private ringAngle = 0;
  private scanAngle = 0;
  private energy = 0;
  private time = 0;
  private lastT = 0;
  private tiltX = 0;
  private tiltY = 0;
  private targetTiltX = 0;
  private targetTiltY = 0;
  private reduceMotion = false;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Could not get 2D context");
    this.ctx = ctx;
    this.reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.seed();
  }

  private seed(): void {
    const t = rand(20260404);
    const n = Math.PI * (3 - Math.sqrt(5));
    for (let r = 0; r < N_PARTICLES; r++) {
      const l = 1 - (r / (N_PARTICLES - 1)) * 2;
      const i = Math.sqrt(1 - l * l);
      const o = n * r;
      this.bx[r] = Math.cos(o) * i;
      this.by[r] = l;
      this.bz[r] = Math.sin(o) * i;
      const s = t();
      const a = s < 0.7 ? 0 : s < 0.9 ? 1 : 2;
      this.layer[r] = a;
      this.br[r] = a === 0 ? 0.95 + t() * 0.07 : a === 1 ? 0.55 + t() * 0.25 : 0.05 + t() * 0.3;
      this.size[r] = a === 2 ? 0.8 + t() : t() * t() * 1.8 + 0.35;
      this.phase[r] = t() * Math.PI * 2;
      this.bin[r] = Math.min(SPECTRUM_BINS - 1, Math.floor((Math.abs(Math.atan2(this.bz[r], this.bx[r])) / Math.PI) * SPECTRUM_BINS));
    }
    for (let r = 0; r < N_FRAGMENTS; r++) {
      const l = t() * 2 - 1;
      const i = t() * Math.PI * 2;
      const o = Math.sqrt(1 - l * l);
      const s = Math.cos(i) * o;
      const a = l;
      const c = Math.sin(i) * o;
      let d = a * 0.3 - c * 0.9;
      let u = c * 0.2 - s * 0.3;
      let v = s * 0.9 - a * 0.2;
      const x = Math.hypot(d, u, v) || 1;
      d /= x;
      u /= x;
      v /= x;
      this.fragments.push({ x: s, y: a, z: c, tx: d, ty: u, tz: v, len: 0.04 + t() * 0.14, layer: t() < 0.7 ? 0 : 1 });
    }
  }

  resize(w: number, h: number): void {
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.w = w;
    this.h = h;
    this.canvas.width = Math.round(w * this.dpr);
    this.canvas.height = Math.round(h * this.dpr);
  }

  setPointer(x: number, y: number): void {
    this.targetTiltY = x * 0.35;
    this.targetTiltX = y * 0.25;
  }

  setMode(mode: OrbMode): void {
    if (mode !== this.mode) {
      this.mode = mode;
      this.modeSince = this.time;
    }
  }

  frame(t: number, audio: AudioFrame): void {
    const r = t / 1000;
    const l = Math.min(0.05, this.lastT ? r - this.lastT : 0.016);
    this.lastT = r;
    this.time = r;
    const i = this.reduceMotion ? 0.3 : 1;
    const targetLook = { ...BASE, ...LOOKS[this.mode] };
    const s = 1 - Math.exp(-l * 3.2);
    const a = this.look;
    (Object.keys(targetLook) as (keyof Look)[]).forEach((k) => {
      a[k] += ((targetLook[k] ?? a[k]) - a[k]) * s;
    });
    const c = audio.source !== "none";
    const d = c ? Math.min(1, audio.level * 3.2 + (audio.userSpeaking ? 0.12 : 0)) : 0;
    this.energy += (d - this.energy) * (1 - Math.exp(-l * (d > this.energy ? 18 : 7)));
    this.tiltX += (this.targetTiltX - this.tiltX) * (1 - Math.exp(-l * 4));
    this.tiltY += (this.targetTiltY - this.tiltY) * (1 - Math.exp(-l * 4));
    this.yaw += l * a.spin * i * (1 + this.energy * 1.5);
    this.ringAngle += l * (0.25 + a.ring * 0.7 + a.spin) * i;
    this.scanAngle += l * (a.scan >= 0 ? 2.2 : -1.4) * Math.abs(a.scan) * i;

    const u = this.ctx;
    const v = this.w * this.dpr;
    const x = this.h * this.dpr;
    u.setTransform(1, 0, 0, 1, 0, 0);
    u.clearRect(0, 0, v, x);
    const w = v / 2;
    const k = x / 2;
    const E = Math.min(v, x) * 0.275;
    const p = a.hue;
    const f = a.sat;
    const h = a.light;
    const y = (S: number, T = 0, O = 0) =>
      `hsla(${p + O},${f}%,${Math.min(95, h + T)}%,${Math.max(0, Math.min(1, S))})`;
    const C = 1 + Math.sin(r * (this.mode === "WAITING" ? 2.6 : 0.9) * i) * a.breathe;
    const P = a.radius * C * (1 + this.energy * 0.12);
    const M = r - this.modeSince;
    const R = a.shake * Math.exp(-M / 0.9);
    u.globalCompositeOperation = "lighter";

    const U = u.createRadialGradient(w, k, E * 0.2, w, k, E * 2.3);
    U.addColorStop(0, y(0.16 * a.bright + this.energy * 0.12, 0));
    U.addColorStop(0.5, y(0.05 * a.bright, -10));
    U.addColorStop(1, y(0));
    u.fillStyle = U;
    u.fillRect(0, 0, v, x);

    const z = 0.32 + this.tiltX + Math.sin(r * 0.3 * i) * 0.05;
    const b = Math.cos(z);
    const Pe = Math.sin(z);
    const ie = this.yaw + this.tiltY;
    const se = a.wave > 0.5 ? 0.55 : 0.12;

    for (let S = 0; S < N_PARTICLES; S++) {
      const T = this.layer[S];
      const O = ie * (T === 0 ? 1 : T === 1 ? 1.7 : 2.6) + (this.mode === "THINKING" && T > 0 ? r * 0.8 * i : 0);
      const D = Math.cos(O);
      const B = Math.sin(O);
      const W = audio.spectrum[this.bin[S]] ? audio.spectrum[this.bin[S]] / 255 : 0;
      let G = this.br[S] * P;
      G *= 1 + W * se * (0.4 + this.energy);
      G *= 1 - a.pull * 0.22 * (0.5 + 0.5 * Math.sin(r * 1.9 * i + this.phase[S]));
      if (R > 0.01) G *= 1 + Math.sin(this.phase[S] * 9 + r * 38) * 0.07 * R;

      const J = this.bx[S] * G;
      const Z = this.by[S] * G;
      const nt = this.bz[S] * G;
      const Et = J * D + nt * B;
      const ct = -J * B + nt * D;
      const _ = Z * b - ct * Pe;
      const I = Z * Pe + ct * b;
      const F = 1 / (1 - I * 0.32);
      const _e = w + Et * E * F;
      const ft = k + _ * E * F;
      const Vn = (I + 1.2) / 2.4;
      const Dr = 0.65 + 0.35 * Math.sin(r * 2.1 * i + this.phase[S] * 3);
      const Rs = T === 2;
      const Ls = (0.18 + 0.82 * Vn) * a.bright * Dr * (Rs ? 1.25 : 1) * (0.85 + W * 0.6);
      const Wn = (0.7 + this.size[S]) * F * this.dpr * (1 + this.energy * 0.5);
      u.fillStyle = y(Ls, Rs ? 14 : Vn * 10);
      u.fillRect(_e - Wn / 2, ft - Wn / 2, Wn, Wn);
      if (this.size[S] > 1.4) {
        u.fillStyle = y(Ls * 0.22, 18);
        u.beginPath();
        u.arc(_e, ft, Wn * 1.7, 0, Math.PI * 2);
        u.fill();
      }
    }

    u.lineWidth = this.dpr;
    for (const S of this.fragments) {
      const T = ie * (S.layer === 0 ? 1.05 : 1.7);
      const O = Math.cos(T);
      const D = Math.sin(T);
      const B = (S.layer === 0 ? 1 : 0.7) * P;
      const W = (ct: number, _: number, I: number) => {
        const F = ct * O + I * D;
        const _e = -ct * D + I * O;
        const ft = _ * b - _e * Pe;
        const Vn = _ * Pe + _e * b;
        const Dr = 1 / (1 - Vn * 0.32);
        return [w + F * E * Dr, k + ft * E * Dr, Vn];
      };
      const [G, J, Z] = W(S.x * B, S.y * B, S.z * B);
      const [nt, Et] = W((S.x + S.tx * S.len) * B, (S.y + S.ty * S.len) * B, (S.z + S.tz * S.len) * B);
      u.strokeStyle = y((0.12 + 0.5 * ((Z + 1) / 2)) * a.fragments * a.bright, 12);
      u.beginPath();
      u.moveTo(G, J);
      u.lineTo(nt, Et);
      u.stroke();
    }

    const $e = a.ring * (0.18 + this.energy * 0.25);
    for (let S = 0; S < 3; S++) {
      const T = 0.5 + S * 0.55;
      const O = this.ringAngle * (S % 2 === 0 ? 1 : -0.7) + S * 2.1;
      const D = (1.12 + S * 0.1) * P;
      u.strokeStyle = y($e * (1 - S * 0.2), 8);
      u.lineWidth = this.dpr * (S === 0 ? 1.4 : 1);
      u.beginPath();
      for (let B = 0; B <= 72; B++) {
        const W = (B / 72) * Math.PI * 2;
        let G = Math.cos(W) * D;
        let J = Math.sin(W) * D * Math.cos(T);
        let Z = Math.sin(W) * D * Math.sin(T);
        const nt = Math.cos(O);
        const Et = Math.sin(O);
        [G, Z] = [G * nt + Z * Et, -G * Et + Z * nt];
        const ct = J * b - Z * Pe;
        const I = 1 / (1 - (J * Pe + Z * b) * 0.32);
        const F = w + G * E * I;
        const _e = k + ct * E * I;
        if (B === 0) u.moveTo(F, _e);
        else u.lineTo(F, _e);
      }
      if (this.mode === "ERROR") u.setLineDash([this.dpr * 7, this.dpr * 11]);
      u.stroke();
      u.setLineDash([]);
    }

    const ve = E * 1.62 * (0.97 + 0.03 * C);
    u.lineWidth = this.dpr;
    u.strokeStyle = y(0.1 + a.ring * 0.14, -10);
    u.beginPath();
    u.arc(w, k, ve, 0, Math.PI * 2);
    u.stroke();

    for (let S = 0; S < 120; S++) {
      const T = (S / 120) * Math.PI * 2 + this.ringAngle * 0.15;
      const O = S % 10 === 0;
      const D = O ? E * 0.07 : E * 0.03;
      u.strokeStyle = y((O ? 0.4 : 0.16) * (0.4 + a.ring * 0.6), -4);
      u.beginPath();
      u.moveTo(w + Math.cos(T) * ve, k + Math.sin(T) * ve);
      u.lineTo(w + Math.cos(T) * (ve + D), k + Math.sin(T) * (ve + D));
      u.stroke();
    }

    if (Math.abs(a.scan) > 0.05) {
      const S = E * 1.5;
      const T = a.scan >= 0 ? 1 : -1;
      for (let O = 0; O < 28; O++) {
        const D = this.scanAngle - T * O * 0.045;
        u.strokeStyle = y(0.5 * Math.abs(a.scan) * (1 - O / 28) * a.bright, 10);
        u.lineWidth = this.dpr * 2;
        u.beginPath();
        u.arc(w, k, S, D, D + T * 0.05, T < 0);
        u.stroke();
      }
      u.strokeStyle = y(0.22 * Math.abs(a.scan), 16);
      u.lineWidth = this.dpr;
      u.beginPath();
      u.moveTo(w + Math.cos(this.scanAngle) * E * 0.3, k + Math.sin(this.scanAngle) * E * 0.3);
      u.lineTo(w + Math.cos(this.scanAngle) * S, k + Math.sin(this.scanAngle) * S);
      u.stroke();
    }

    if (a.orbit > 0.3) {
      for (let S = 0; S < 5; S++) {
        const T = r * (0.9 + S * 0.35) * i + S * 1.3;
        const O = E * (1.25 + S * 0.07);
        const D = O * (0.35 + S * 0.07);
        const B = 0.6 + S * 0.4;
        const W = Math.cos(T) * O;
        const G = Math.sin(T) * D;
        const J = w + W * Math.cos(B) - G * Math.sin(B);
        const Z = k + W * Math.sin(B) + G * Math.cos(B);
        u.fillStyle = y(0.9 * Math.min(1, a.orbit), 22);
        u.beginPath();
        u.arc(J, Z, this.dpr * 2.2, 0, Math.PI * 2);
        u.fill();
        u.fillStyle = y(0.18 * a.orbit, 20);
        u.beginPath();
        u.arc(J, Z, this.dpr * 7, 0, Math.PI * 2);
        u.fill();
      }
    }

    if (a.wave > 0.05 && c) {
      const S = E * 1.32 * P;
      const T = 128;
      u.strokeStyle = y(0.85 * a.wave, 18);
      u.lineWidth = this.dpr * 1.6;
      u.beginPath();
      for (let O = 0; O <= T; O++) {
        const D = (O / T) * Math.PI * 2 - Math.PI / 2;
        const B = Math.floor((O % T) * (audio.wave.length / T));
        const W = (((audio.wave[B] ?? 128) + (audio.wave[B + 1] ?? 128) + (audio.wave[B + 2] ?? 128)) / 3 - 128) / 128;
        const G = S + W * E * 0.2 * (0.4 + this.energy);
        const J = w + Math.cos(D) * G;
        const Z = k + Math.sin(D) * G;
        if (O === 0) u.moveTo(J, Z);
        else u.lineTo(J, Z);
      }
      u.closePath();
      u.stroke();
    }

    const Qe = E * (0.34 + this.energy * 0.28 + (this.mode === "THINKING" ? 0.05 * Math.sin(r * 6) : 0)) * P;
    const j = u.createRadialGradient(w, k, 0, w, k, Qe * 2.4);
    j.addColorStop(0, y(0.95 * a.bright, 30));
    j.addColorStop(0.25, y(0.5 * a.bright, 12));
    j.addColorStop(1, y(0));
    u.fillStyle = j;
    u.beginPath();
    u.arc(w, k, Qe * 2.4, 0, Math.PI * 2);
    u.fill();
    u.globalCompositeOperation = "source-over";
  }
}
