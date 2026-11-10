// Cola de reproduccion pura (testeable sin AudioContext).
// Lleva la cuenta de muestras reproducidas por response_id.

export class PlaybackQueue {
  constructor() {
    this.chunks = [];          // [{id, data: Float32Array, off}]
    this.played = new Map();   // id -> muestras reproducidas
  }

  push(id, samples) {
    if (samples.length) this.chunks.push({ id, data: samples, off: 0 });
  }

  get bufferedSamples() {
    return this.chunks.reduce((n, c) => n + c.data.length - c.off, 0);
  }

  /** Rellena `out` (silencio si no hay datos). Devuelve la respuesta en curso. */
  render(out) {
    let w = 0;
    let id = null;
    let ended = false;
    while (w < out.length && this.chunks.length) {
      const c = this.chunks[0];
      const n = Math.min(out.length - w, c.data.length - c.off);
      out.set(c.data.subarray(c.off, c.off + n), w);
      c.off += n;
      w += n;
      id = this.current = c.id;
      this.played.set(id, (this.played.get(id) ?? 0) + n);
      if (c.off >= c.data.length) {
        this.chunks.shift();
        if (!this.chunks.length || this.chunks[0].id !== id) ended = true;
      }
    }
    if (w < out.length) out.fill(0, w);
    return { id, played: id === null ? 0 : this.played.get(id), ended };
  }

  /** Corta ya. Con `id`, solo esa respuesta; sin id, todo. */
  flush(id = null) {
    const target = id ?? this.chunks[0]?.id ?? this.current ?? null;
    this.chunks = id === null ? [] : this.chunks.filter((c) => c.id !== id);
    return { id: target, played: target === null ? 0 : this.played.get(target) ?? 0 };
  }

  playedSamples(id) {
    return this.played.get(id) ?? 0;
  }
}
