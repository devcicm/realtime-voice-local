// Utilidades de audio puras (sin DOM): usadas por el cliente y los worklets.

export const INPUT_SAMPLE_RATE = 16000;
export const AUDIO_HEADER_BYTES = 8; // <II  response_id, seq (little-endian)

/** Remuestreo lineal en streaming (estado entre llamadas, sin clicks). */
export class StreamResampler {
  constructor(fromRate, toRate) {
    this.ratio = fromRate / toRate;
    this.pos = 0;        // posicion fraccional dentro del buffer actual
    this.last = 0;       // ultima muestra del bloque anterior
  }

  /** @param {Float32Array} input @returns {Float32Array} */
  process(input) {
    if (this.ratio === 1) return input.slice();
    const n = input.length;
    if (n === 0) return new Float32Array(0);
    const out = [];
    let pos = this.pos; // en [-1, ...): el indice -1 es this.last (continuidad)
    while (pos <= n - 1) {
      const i = Math.floor(pos);
      const frac = pos - i;
      const a = i < 0 ? this.last : input[i];
      const b = frac > 0 ? input[i + 1] : a;
      out.push(a + (b - a) * frac);
      pos += this.ratio;
    }
    this.pos = pos - n;
    this.last = input[input.length - 1] ?? this.last;
    return Float32Array.from(out);
  }
}

export function floatToPcm16(f32) {
  const out = new Int16Array(f32.length);
  for (let i = 0; i < f32.length; i++) {
    const s = Math.max(-1, Math.min(1, f32[i]));
    out[i] = s < 0 ? s * 32768 : s * 32767;
  }
  return out;
}

export function pcm16ToFloat(i16) {
  const out = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) out[i] = i16[i] / 32768;
  return out;
}

/** Frame binario servidor->cliente: [u32 response_id][u32 seq][PCM16 LE]. */
export function unpackAudio(buffer) {
  if (buffer.byteLength < AUDIO_HEADER_BYTES) throw new Error('frame de audio corto');
  const view = new DataView(buffer);
  const responseId = view.getUint32(0, true);
  const seq = view.getUint32(4, true);
  const pcm = new Int16Array(buffer.slice(AUDIO_HEADER_BYTES));
  return { responseId, seq, pcm };
}

/** Acumula muestras y emite frames de tamano fijo (p. ej. 320 = 20 ms a 16 kHz). */
export class FrameAccumulator {
  constructor(frameSamples) {
    this.frameSamples = frameSamples;
    this.buf = new Float32Array(frameSamples);
    this.fill = 0;
  }

  push(samples, onFrame) {
    let off = 0;
    while (off < samples.length) {
      const n = Math.min(this.frameSamples - this.fill, samples.length - off);
      this.buf.set(samples.subarray(off, off + n), this.fill);
      this.fill += n;
      off += n;
      if (this.fill === this.frameSamples) {
        onFrame(this.buf.slice());
        this.fill = 0;
      }
    }
  }
}
