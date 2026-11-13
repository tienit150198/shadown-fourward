export class Pcm16Encoder {
  constructor(sourceRate, targetRate = 16000, frameSize = 1024) {
    if (sourceRate < targetRate) throw new Error("Sample rate không được hỗ trợ.");
    Object.assign(this, { sourceRate, targetRate, frameSize });
    this.phase = this.sum = this.count = this.offset = 0;
    this.frame = new Int16Array(frameSize);
  }

  push(samples) {
    const frames = [];
    for (const sample of samples) {
      this.sum += sample;
      this.count++;
      this.phase += this.targetRate;
      if (this.phase < this.sourceRate) continue;
      this.phase -= this.sourceRate;
      const value = Math.max(-1, Math.min(1, this.sum / this.count));
      this.frame[this.offset++] = Math.round(value * (value < 0 ? 32768 : 32767));
      this.sum = this.count = 0;
      if (this.offset === this.frameSize) {
        frames.push(this.frame.buffer);
        this.frame = new Int16Array(this.frameSize);
        this.offset = 0;
      }
    }
    return frames;
  }
}

export function pcmBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return btoa(binary);
}

export function decodePcm16(base64) {
  const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
  if (bytes.length % 2) throw new Error("Âm thanh Live không hợp lệ.");
  const view = new DataView(bytes.buffer);
  return Float32Array.from({ length: bytes.length / 2 }, (_, i) => view.getInt16(i * 2, true) / 32768);
}
