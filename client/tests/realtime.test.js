// Vitest tests for the RealtimeTransport interface and implementations.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { RealtimeTransport } from '../src/realtime/transport.js';
import { LocalWSTransport } from '../src/realtime/localWS.js';
import { OpenAIRealtimeTransport } from '../src/realtime/openaiRealtime.js';
import { RealtimeRouter } from '../src/realtime/router.js';

// ---- RealtimeTransport base ----

describe('RealtimeTransport', () => {
  it('starts in idle state', () => {
    const t = new RealtimeTransport({});
    expect(t.state).toBe('idle');
  });

  it('subscribers can be removed', () => {
    const t = new RealtimeTransport({});
    const cb = vi.fn();
    const unsub = t.on('audioChunk', cb);
    t._emit('audioChunk', { foo: 1 });
    expect(cb).toHaveBeenCalledOnce();
    unsub();
    t._emit('audioChunk', { foo: 2 });
    expect(cb).toHaveBeenCalledOnce();
  });

  it('throws on unknown event', () => {
    const t = new RealtimeTransport({});
    expect(() => t.on('bogus', () => {})).toThrow();
  });

  it('abstract methods throw', async () => {
    const t = new RealtimeTransport({});
    await expect(t.connect()).rejects.toThrow();
    await expect(t.sendAudioChunk(new Float32Array(8))).rejects.toThrow();
    expect(() => t.interrupt()).toThrow();
  });
});

// ---- LocalWSTransport ----

describe('LocalWSTransport', () => {
  it('uses config defaults', () => {
    const t = new LocalWSTransport({});
    expect(t.config.mode).toBe('local-chain');
    expect(t.config.transport).toBe('websocket');
  });

  it('emits transcriptDelta on sendAudioChunk → STT success', async () => {
    const t = new LocalWSTransport({ sttUrl: 'http://stub' });
    const transcripts = [];
    t.on('transcriptDelta', (e) => transcripts.push(e));

    // Mock fetch for STT
    const origFetch = globalThis.fetch;
    globalThis.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({ text: 'hola mundo' }),
    });

    await t.sendAudioChunk(new Float32Array(16000));

    expect(transcripts).toHaveLength(1);
    expect(transcripts[0].text).toBe('hola mundo');
    expect(transcripts[0].source).toBe('stt');

    globalThis.fetch = origFetch;
  });

  it('skips empty transcripts', async () => {
    const t = new LocalWSTransport({ sttUrl: 'http://stub' });
    const transcripts = [];
    t.on('transcriptDelta', (e) => transcripts.push(e));

    const origFetch = globalThis.fetch;
    globalThis.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({ text: '   ' }),
    });
    await t.sendAudioChunk(new Float32Array(16000));
    expect(transcripts).toHaveLength(0);
    globalThis.fetch = origFetch;
  });

  it('throws on STT HTTP error', async () => {
    const t = new LocalWSTransport({ sttUrl: 'http://stub' });
    const origFetch = globalThis.fetch;
    globalThis.fetch = vi.fn().mockResolvedValueOnce({ ok: false, status: 500 });
    await expect(t.sendAudioChunk(new Float32Array(16000))).rejects.toThrow(/STT HTTP 500/);
    globalThis.fetch = origFetch;
  });
});

// ---- OpenAIRealtimeTransport ----

describe('OpenAIRealtimeTransport', () => {
  it('requires an API key', () => {
    expect(() => new OpenAIRealtimeTransport({})).toThrow(/API key/);
  });

  it('reports the openai-realtime mode', () => {
    const t = new OpenAIRealtimeTransport({ apiKey: 'sk-test' });
    expect(t.config.mode).toBe('openai-realtime');
    expect(t.config.transport).toBe('webrtc');
  });
});

// ---- RealtimeRouter ----

describe('RealtimeRouter', () => {
  it('builds a LocalWSTransport by default', () => {
    const r = new RealtimeRouter({});
    const t = r.build();
    expect(t).toBeInstanceOf(LocalWSTransport);
  });

  it('builds an OpenAIRealtimeTransport when mode is openai-realtime', () => {
    const r = new RealtimeRouter({ mode: 'openai-realtime', apiKey: 'sk-test' });
    const t = r.build();
    expect(t).toBeInstanceOf(OpenAIRealtimeTransport);
  });

  it('rebuild replaces the previous transport', () => {
    const r = new RealtimeRouter({});
    const t1 = r.build();
    const t2 = r.build();
    expect(t1).not.toBe(t2);
    expect(r.get()).toBe(t2);
  });

  it('setConfig merges', () => {
    const r = new RealtimeRouter({ mode: 'local-chain', voice: 'a' });
    r.setConfig({ voice: 'b' });
    expect(r.config.voice).toBe('b');
    expect(r.config.mode).toBe('local-chain');
  });
});
