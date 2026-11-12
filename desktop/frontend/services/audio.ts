/** Browser-side audio: microphone capture (with echo cancellation / noise suppression) and low-latency TTS playback. */



export type Bytes = Uint8Array<ArrayBuffer>;



function rmsOf(analyser: AnalyserNode, buf: Bytes): number {

  analyser.getByteTimeDomainData(buf);

  let sum = 0;

  for (let i = 0; i < buf.length; i++) {

    const v = (buf[i] - 128) / 128;

    sum += v * v;

  }

  return Math.sqrt(sum / buf.length);

}



const WORKLET_CODE = `

class PcmDownsampler extends AudioWorkletProcessor {

  constructor() {

    super();

    this.ratio = sampleRate / 16000;

    this.acc = 0;

    this.cnt = 0;

    this.pos = 0;

    this.out = new Int16Array(320);

    this.n = 0;

  }

  process(inputs) {

    const ch = inputs[0] && inputs[0][0];

    if (!ch) return true;

    for (let i = 0; i < ch.length; i++) {

      this.acc += ch[i];

      this.cnt++;

      this.pos += 1;

      if (this.pos >= this.ratio) {

        const v = this.acc / this.cnt;

        this.acc = 0;

        this.cnt = 0;

        this.pos -= this.ratio;

        this.out[this.n++] = Math.max(-1, Math.min(1, v)) * 32767;

        if (this.n === 320) {

          this.port.postMessage(this.out.buffer.slice(0));

          this.n = 0;

        }

      }

    }

    return true;

  }

}

registerProcessor("pcm-downsampler", PcmDownsampler);

`;



export class MicStreamer {

  private ctx: AudioContext | null = null;

  private stream: MediaStream | null = null;

  private analyser: AnalyserNode | null = null;

  private workletNode: AudioWorkletNode | null = null;

  private scriptNode: ScriptProcessorNode | null = null;

  private srcNode: MediaStreamAudioSourceNode | null = null;

  private level = new Uint8Array(512);

  private isPaused: boolean = false;



  setPaused(paused: boolean): void {

    this.isPaused = paused;

  }



  get paused(): boolean {

    return this.isPaused;

  }

  async resume(): Promise<void> {
    if (this.ctx && this.ctx.state === "suspended") {
      try {
        await this.ctx.resume();
      } catch (err) {
        console.warn("[NOOR Audio] ctx.resume failed:", err);
      }
    }
  }



  /** Starts capture; `onFrame` receives 20 ms of 16 kHz mono PCM16 every 20 ms. */

  async start(onFrame: (pcm: ArrayBuffer) => void, deviceId?: string, opts: { echo: boolean; noise: boolean; gain: boolean } = { echo: true, noise: true, gain: true }): Promise<void> {

    this.stream = await navigator.mediaDevices.getUserMedia({

      audio: {

        deviceId: deviceId ? { ideal: deviceId } : undefined,

        channelCount: 1,

        echoCancellation: opts.echo, // NOOR's own voice is removed from the mic signal

        noiseSuppression: opts.noise,

        autoGainControl: opts.gain,

      },

    });

    const ctx = new AudioContext({ latencyHint: "interactive" });

    this.ctx = ctx;

    if (ctx.state === "suspended") {

      try {

        await ctx.resume();

      } catch { /* ignored */ }

    }

    const src = ctx.createMediaStreamSource(this.stream);

    this.srcNode = src;

    this.analyser = ctx.createAnalyser();

    this.analyser.fftSize = 1024;

    src.connect(this.analyser);



    let workletLoaded = false;

    let blobUrl = "";

    try {

      const blob = new Blob([WORKLET_CODE], { type: "application/javascript" });

      blobUrl = URL.createObjectURL(blob);

      await ctx.audioWorklet.addModule(blobUrl);

      workletLoaded = true;

    } catch {

      try {

        const isFile = typeof window !== "undefined" && window.location.protocol === "file:";

        await ctx.audioWorklet.addModule(isFile ? "./pcm-worklet.js" : "/pcm-worklet.js");

        workletLoaded = true;

      } catch {

        workletLoaded = false;

      }

    } finally {

      if (blobUrl) {

        try { URL.revokeObjectURL(blobUrl); } catch { /* ignored */ }

      }

    }



    if (workletLoaded) {

      try {

        const node = new AudioWorkletNode(ctx, "pcm-downsampler");

        this.workletNode = node;

        node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {

          if (!this.isPaused) onFrame(e.data);

        };

        src.connect(node);
        // Connect to destination via zero-gain node so Chromium rendering pipeline stays active
        const silentGain = ctx.createGain();
        silentGain.gain.value = 0;
        node.connect(silentGain);
        silentGain.connect(ctx.destination);
        

      } catch {
        this.fallbackProcessor(ctx, src, onFrame);
      }
    } else {
      this.fallbackProcessor(ctx, src, onFrame);
    }

  }



  private fallbackProcessor(ctx: AudioContext, src: MediaStreamAudioSourceNode, onFrame: (pcm: ArrayBuffer) => void): void {

    const proc = ctx.createScriptProcessor(2048, 1, 1);

    this.scriptNode = proc;

    const ratio = ctx.sampleRate / 16000;

    let carry: number[] = []; // raw samples not yet consumed

    let out: number[] = []; // 16 kHz samples not yet framed

    proc.onaudioprocess = (e) => {

      if (this.isPaused) return;

      const input = e.inputBuffer.getChannelData(0);

      for (let i = 0; i < input.length; i++) carry.push(input[i]);

      const n = Math.floor(carry.length / ratio);

      for (let i = 0; i < n; i++) {

        const from = Math.floor(i * ratio);

        const to = Math.max(from + 1, Math.floor((i + 1) * ratio));

        let sum = 0;

        for (let j = from; j < to; j++) sum += carry[j];

        out.push(Math.max(-1, Math.min(1, sum / (to - from))) * 32767);

      }

      carry = carry.slice(Math.floor(n * ratio));

      while (out.length >= 320) {

        if (!this.isPaused) onFrame(Int16Array.from(out.slice(0, 320)).buffer);

        out = out.slice(320);

      }

    };

    src.connect(proc);

    proc.connect(ctx.destination); // required for the node to run; its output is silent

  }



  micLevel(): number {

    if (this.isPaused) return 0;

    return this.analyser ? rmsOf(this.analyser, this.level) : 0;

  }



  waveform(into: Bytes): void {

    if (this.isPaused) {

      into.fill(128);

      return;

    }

    this.analyser?.getByteTimeDomainData(into);

  }



  /** Frequency magnitudes (0-255) of the live microphone signal. */

  spectrum(into: Bytes): void {

    if (this.isPaused) {

      into.fill(0);

      return;

    }

    this.analyser?.getByteFrequencyData(into);

  }



  stop(): void {

    this.stream?.getTracks().forEach((t) => t.stop());

    try { this.workletNode?.disconnect(); } catch { /* ignore */ }

    try { this.scriptNode?.disconnect(); } catch { /* ignore */ }

    try { this.srcNode?.disconnect(); } catch { /* ignore */ }

    try { this.analyser?.disconnect(); } catch { /* ignore */ }

    void this.ctx?.close();

    this.stream = null;

    this.ctx = null;

    this.analyser = null;

    this.workletNode = null;

    this.scriptNode = null;

    this.srcNode = null;

  }

}



export class TtsPlayer {

  private ctx = new AudioContext({ latencyHint: "interactive" });

  private gain = this.ctx.createGain();

  private analyser = this.ctx.createAnalyser();

  private level = new Uint8Array(512);

  private sources = new Set<AudioBufferSourceNode>();

  private nextTime = 0;

  private epoch = 0;

  private chain: Promise<void> = Promise.resolve();

  private decoding = 0;



  constructor(private onIdle: (epoch: number) => void) {

    this.analyser.fftSize = 1024;

    this.gain.connect(this.analyser);

    this.analyser.connect(this.ctx.destination);

  }



  /** Queue one synthesized sentence. Chunks are decoded in order and scheduled back-to-back. */

  enqueue(b64: string, epoch: number): void {

    if (epoch < this.epoch) return;

    this.decoding++;

    this.chain = this.chain.then(async () => {

      try {

        if (this.ctx.state === "suspended") await this.ctx.resume();

        const bin = atob(b64);

        const bytes = new Uint8Array(bin.length);

        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);

        const buf = await this.ctx.decodeAudioData(bytes.buffer);

        if (epoch < this.epoch) return; // interrupted while decoding

        const src = this.ctx.createBufferSource();

        src.buffer = buf;

        src.connect(this.gain);

        const at = Math.max(this.ctx.currentTime + 0.02, this.nextTime);

        this.nextTime = at + buf.duration;

        this.sources.add(src);

        src.onended = () => {

          this.sources.delete(src);

          this.maybeIdle(epoch);

        };

        src.start(at);

      } catch {

        /* undecodable chunk: skip */

      } finally {

        this.decoding--;

        this.maybeIdle(epoch);

      }

    });

  }



  private maybeIdle(epoch: number): void {

    if (this.sources.size === 0 && this.decoding === 0 && epoch >= this.epoch) this.onIdle(epoch);

  }



  /** Barge-in: stop everything immediately and ignore any audio from older epochs. */

  stop(epoch: number): void {

    this.epoch = Math.max(this.epoch, epoch);

    this.sources.forEach((s) => {

      s.onended = null;

      try {

        s.stop();

      } catch { /* already stopped */ }

    });

    this.sources.clear();

    this.nextTime = 0;

    this.gain.gain.setTargetAtTime(1, this.ctx.currentTime, 0.01);

  }



  duck(on: boolean): void {

    this.gain.gain.setTargetAtTime(on ? 0.2 : 1, this.ctx.currentTime, 0.02);

  }



  /** Soft two-note chime confirming the wake word was heard. */

  chime(): void {

    const t = this.ctx.currentTime;

    [660, 880].forEach((f, i) => {

      const o = this.ctx.createOscillator();

      const g = this.ctx.createGain();

      o.frequency.value = f;

      g.gain.setValueAtTime(0.0001, t + i * 0.09);

      g.gain.exponentialRampToValueAtTime(0.12, t + i * 0.09 + 0.02);

      g.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.09 + 0.18);

      o.connect(g).connect(this.ctx.destination);

      o.start(t + i * 0.09);

      o.stop(t + i * 0.09 + 0.2);

    });

  }



  outLevel(): number {

    return rmsOf(this.analyser, this.level);

  }



  waveform(into: Bytes): void {

    this.analyser.getByteTimeDomainData(into);

  }



  /** Frequency magnitudes (0-255) of NOOR's own voice as it is played. */

  spectrum(into: Bytes): void {

    this.analyser.getByteFrequencyData(into);

  }

}



