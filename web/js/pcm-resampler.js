// Continuous box-filter resampling; state crosses AudioWorklet render boundaries.
export class PcmResampler {
  constructor(inputRate, outputRate = 16000) {
    if (!Number.isFinite(inputRate) || inputRate < outputRate || outputRate <= 0) throw new Error('Invalid audio sample rate');
    this.ratio = inputRate / outputRate;
    this.remaining = this.ratio;
    this.sum = 0;
  }
  process(input) {
    const output = [];
    for (const sample of input) {
      let available = 1;
      while (available > 1e-8) {
        const take = Math.min(available, this.remaining);
        this.sum += Math.max(-1, Math.min(1, sample)) * take;
        this.remaining -= take;
        available -= take;
        if (this.remaining < 1e-8) {
          const value = Math.max(-1, Math.min(1, this.sum / this.ratio));
          output.push(Math.round(value * (value < 0 ? 32768 : 32767)));
          this.remaining = this.ratio;
          this.sum = 0;
        }
      }
    }
    return Int16Array.from(output);
  }
}
