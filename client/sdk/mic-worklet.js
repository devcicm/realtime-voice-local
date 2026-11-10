// AudioWorklet de captura: remuestrea a 16 kHz y emite frames PCM16 de 20 ms
// (ArrayBuffer transferido, sin copias extra en el hilo principal).
import { StreamResampler, FrameAccumulator, floatToPcm16, INPUT_SAMPLE_RATE } from './audio-utils.js';

class MicCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const frameMs = options?.processorOptions?.frameMs ?? 20;
    this.resampler = new StreamResampler(sampleRate, INPUT_SAMPLE_RATE);
    this.acc = new FrameAccumulator(Math.round(INPUT_SAMPLE_RATE * frameMs / 1000));
    this.enabled = true;
    this.port.onmessage = (e) => { if (e.data?.type === 'enable') this.enabled = !!e.data.value; };
  }

  process(inputs) {
    const ch = inputs[0]?.[0];
    if (ch && this.enabled) {
      this.acc.push(this.resampler.process(ch), (frame) => {
        const pcm = floatToPcm16(frame);
        this.port.postMessage(pcm.buffer, [pcm.buffer]);
      });
    }
    return true;
  }
}

registerProcessor('rt-mic-capture', MicCapture);
