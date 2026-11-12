// Downsamples microphone audio to 16 kHz mono PCM16 in 20 ms frames (320 samples) and posts them to the main thread.
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
