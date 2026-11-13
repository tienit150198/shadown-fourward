import { PcmResampler } from './pcm-resampler.js';

class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.resampler = new PcmResampler(sampleRate, 16000);
    this.frame = new Int16Array(960); // 60ms of mono input per message.
    this.offset = 0;
  }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const value of this.resampler.process(input)) {
      this.frame[this.offset++] = value;
      if (this.offset === this.frame.length) {
        // Explicit little-endian serialization, independent of native platform order.
        const bytes = new Uint8Array(this.frame.length * 2);
        const view = new DataView(bytes.buffer);
        for (let i = 0; i < this.frame.length; i++) view.setInt16(i * 2, this.frame[i], true);
        this.port.postMessage(bytes.buffer, [bytes.buffer]);
        this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor('shadow-pcm-capture', PcmCapture);
