// AudioWorklet de reproduccion: cola por respuesta, flush instantaneo y
// reporte de muestras reproducidas (para truncar el contexto a lo escuchado).
import { PlaybackQueue } from './playback-queue.js';

class Player extends AudioWorkletProcessor {
  constructor() {
    super();
    this.q = new PlaybackQueue();
    this.sinceReport = 0;
    this.reportEvery = Math.round(sampleRate * 0.1); // ~100 ms
    this.port.onmessage = (e) => {
      const m = e.data;
      if (m.type === 'push') this.q.push(m.id, m.samples);
      else if (m.type === 'flush') {
        const r = this.q.flush(m.id);
        this.port.postMessage({ type: 'flushed', ...r });
      }
    };
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    const { id, played, ended } = this.q.render(out);
    if (id !== null) {
      this.sinceReport += out.length;
      if (this.sinceReport >= this.reportEvery || ended) {
        this.sinceReport = 0;
        this.port.postMessage({ type: 'pos', id, played, ended });
      }
    }
    for (let c = 1; c < outputs[0].length; c++) outputs[0][c].set(out);
    return true;
  }
}

registerProcessor('rt-player', Player);
