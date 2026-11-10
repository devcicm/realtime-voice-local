// Tests del SDK realtime (client/sdk) con WebSocket y audio falsos.
import { describe, it, expect, vi } from 'vitest';
import { RealtimeVoiceClient } from '../sdk/voice-client.js';
import { PlaybackQueue } from '../sdk/playback-queue.js';
import {
  StreamResampler, FrameAccumulator, floatToPcm16, pcm16ToFloat, unpackAudio,
} from '../sdk/audio-utils.js';

class FakeWS {
  static last = null;
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.sent = [];
    FakeWS.last = this;
    queueMicrotask(() => this.onopen?.());
  }
  send(d) { this.sent.push(d); }
  close() { this.readyState = 3; this.onclose?.(); }
  // helpers
  server(ev) { this.onmessage({ data: JSON.stringify(ev) }); }
  serverAudio(id, seq, samples) {
    const buf = new ArrayBuffer(8 + samples.length * 2);
    const v = new DataView(buf);
    v.setUint32(0, id, true);
    v.setUint32(4, seq, true);
    new Int16Array(buf, 8).set(samples);
    this.onmessage({ data: buf });
  }
  json() { return this.sent.filter((d) => typeof d === 'string').map((d) => JSON.parse(d)); }
}

class FakeAudio {
  constructor() { this.q = new PlaybackQueue(); this.flushes = []; }
  init() {}
  play(id, samples) { this.q.push(id, samples); }
  flush(id) { this.flushes.push(id); return this.q.flush(id); }
  /** simula que el worklet reprodujo `n` muestras */
  tick(n) { const r = this.q.render(new Float32Array(n)); if (r.id !== null) this.onPlayback?.(r); }
}

async function connected(opts = {}) {
  const audio = new FakeAudio();
  const rt = new RealtimeVoiceClient({ url: 'ws://x/ws/voice', WebSocket: FakeWS, audio, positionEveryMs: 0, ...opts });
  const p = rt.connect();
  const ws = FakeWS.last;
  ws.server({ type: 'session.created', session: { barge_in: true }, audio: { output_sample_rate: 1000 } });
  await p;
  await rt._ensureAudio();
  return { rt, ws, audio };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('audio-utils', () => {
  it('pcm16 roundtrip', () => {
    const f = Float32Array.from([0, 0.5, -0.5, 1, -1]);
    const back = pcm16ToFloat(floatToPcm16(f));
    back.forEach((v, i) => expect(v).toBeCloseTo(f[i], 3));
  });

  it('unpackAudio lee la cabecera little-endian', () => {
    const buf = new ArrayBuffer(12);
    const v = new DataView(buf);
    v.setUint32(0, 7, true); v.setUint32(4, 2, true);
    new Int16Array(buf, 8).set([100, -100]);
    const f = unpackAudio(buf);
    expect(f.responseId).toBe(7);
    expect(f.seq).toBe(2);
    expect([...f.pcm]).toEqual([100, -100]);
    expect(() => unpackAudio(new ArrayBuffer(3))).toThrow();
  });

  it('resampler 48k->16k en streaming conserva la duracion', () => {
    const r = new StreamResampler(48000, 16000);
    let total = 0;
    for (let i = 0; i < 100; i++) total += r.process(new Float32Array(128).fill(0.25)).length;
    expect(Math.abs(total - (100 * 128) / 3)).toBeLessThanOrEqual(1);
  });

  it('FrameAccumulator emite frames exactos de 20 ms', () => {
    const acc = new FrameAccumulator(320);
    const frames = [];
    acc.push(new Float32Array(1000), (f) => frames.push(f.length));
    expect(frames).toEqual([320, 320, 320]);
  });
});

describe('PlaybackQueue', () => {
  it('cuenta muestras reproducidas por respuesta y hace flush', () => {
    const q = new PlaybackQueue();
    q.push(1, new Float32Array(300).fill(0.1));
    q.push(2, new Float32Array(300).fill(0.2));
    const out = new Float32Array(128);
    expect(q.render(out).id).toBe(1);
    q.render(out);
    const r = q.render(out); // cruza a la respuesta 2
    expect(r.id).toBe(2);
    expect(q.playedSamples(1)).toBe(300);
    expect(q.playedSamples(2)).toBe(84);
    expect(q.flush(2)).toEqual({ id: 2, played: 84 });
    expect(q.bufferedSamples).toBe(0);
    expect(q.render(out).id).toBe(null);
    expect(out.every((s) => s === 0)).toBe(true);
  });
});

describe('RealtimeVoiceClient', () => {
  it('conecta, aplica session inicial y reproduce audio', async () => {
    const { rt, ws, audio } = await connected({ session: { speed: 1.2 } });
    expect(ws.json()[0]).toEqual({ type: 'session.update', session: { speed: 1.2 } });
    expect(rt.outputRate).toBe(1000);
    const states = [];
    rt.addEventListener('state', (e) => states.push(e.detail.state));
    ws.server({ type: 'response.created', response_id: 1 });
    ws.serverAudio(1, 0, new Int16Array(500));
    expect(audio.q.bufferedSamples).toBe(500);
    expect(states).toEqual(['thinking', 'speaking']);
  });

  it('no vuelve a listening en huecos entre frases, si al terminar', async () => {
    const { rt, ws, audio } = await connected();
    ws.server({ type: 'response.created', response_id: 1 });
    ws.serverAudio(1, 0, new Int16Array(100));
    audio.tick(100); // cola vacia, pero el servidor sigue generando
    expect(rt.state).toBe('speaking');
    ws.serverAudio(1, 1, new Int16Array(100));
    ws.server({ type: 'response.done', response_id: 1, text: 'x', audio_ms: 200 });
    audio.tick(100);
    expect(rt.state).toBe('listening');
  });

  it('reporta playback.position en ms', async () => {
    const { ws, audio } = await connected();
    ws.server({ type: 'response.created', response_id: 1 });
    ws.serverAudio(1, 0, new Int16Array(500));
    audio.tick(250);
    const pos = ws.json().filter((e) => e.type === 'playback.position');
    expect(pos.at(-1)).toEqual({ type: 'playback.position', response_id: 1, played_ms: 250 });
  });

  it('barge-in: corta audio al instante y envia response.truncate con lo escuchado', async () => {
    const { rt, ws, audio } = await connected();
    const interrupted = vi.fn();
    rt.addEventListener('interrupted', interrupted);
    ws.server({ type: 'response.created', response_id: 3 });
    ws.serverAudio(3, 0, new Int16Array(1000));
    audio.tick(400);
    ws.server({ type: 'input_audio.speech_started', response_id: 3 });
    expect(audio.flushes).toEqual([3]);            // corte optimista local
    expect(audio.q.bufferedSamples).toBe(0);
    ws.serverAudio(3, 1, new Int16Array(500));     // audio rezagado: descartado
    expect(audio.q.bufferedSamples).toBe(0);
    ws.server({ type: 'response.cancelled', response_id: 3, reason: 'barge-in', audio_ms: 1000 });
    await flush();
    const tr = ws.json().find((e) => e.type === 'response.truncate');
    expect(tr).toEqual({ type: 'response.truncate', response_id: 3, played_ms: 400 });
    expect(audio.flushes).toEqual([3]);            // idempotente
    expect(interrupted).toHaveBeenCalledOnce();
    expect(rt.state).toBe('listening');
  });

  it('cancelacion sin audio enviado no manda truncate', async () => {
    const { ws } = await connected();
    ws.server({ type: 'response.created', response_id: 4 });
    ws.server({ type: 'response.cancelled', response_id: 4, reason: 'user_continued', audio_ms: 0 });
    await flush();
    expect(ws.json().some((e) => e.type === 'response.truncate')).toBe(false);
  });

  it('cancel() manual corta local y avisa al servidor', async () => {
    const { rt, ws, audio } = await connected();
    ws.server({ type: 'response.created', response_id: 5 });
    ws.serverAudio(5, 0, new Int16Array(100));
    rt.cancel();
    expect(audio.flushes).toEqual([5]);
    expect(ws.json().at(-1)).toEqual({ type: 'response.cancel' });
  });

  it('reenvia eventos de texto, transcript y metricas', async () => {
    const { rt, ws } = await connected();
    const got = [];
    for (const t of ['transcript', 'text', 'metrics', 'response-done']) {
      rt.addEventListener(t, (e) => got.push([t, e.detail.type]));
    }
    ws.server({ type: 'transcript.final', response_id: 1, text: 'hola' });
    ws.server({ type: 'response.text.delta', response_id: 1, delta: 'Hola' });
    ws.server({ type: 'response.done', response_id: 1, text: 'Hola', audio_ms: 10 });
    ws.server({ type: 'metrics.turn', response_id: 1 });
    expect(got.map((g) => g[0])).toEqual(['transcript', 'text', 'response-done', 'metrics']);
  });

  it('sendAudio envia binario solo con el socket abierto', async () => {
    const { rt, ws } = await connected();
    const pcm = new Int16Array(320).buffer;
    rt.sendAudio(pcm);
    expect(ws.sent.at(-1)).toBe(pcm);
    ws.readyState = 3;
    rt.sendAudio(pcm);
    expect(ws.sent.filter((d) => d === pcm)).toHaveLength(1);
  });
});
