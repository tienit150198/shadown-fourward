import { Pcm16Encoder } from "./live-pcm.js";

class MicrophonePcm extends AudioWorkletProcessor {
  constructor() {
    super();
    this.encoder = new Pcm16Encoder(sampleRate);
  }
  process(inputs) {
    const samples = inputs[0]?.[0];
    if (samples) for (const frame of this.encoder.push(samples)) this.port.postMessage(frame, [frame]);
    return true;
  }
}
registerProcessor("microphone-pcm", MicrophonePcm);
