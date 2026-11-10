// RealtimeVoiceClient — SDK de navegador para /ws/voice (voz en vivo local).
//
//   import { RealtimeVoiceClient } from './sdk/voice-client.js';
//   const rt = new RealtimeVoiceClient({ url: 'ws://localhost:8990/ws/voice' });
//   rt.addEventListener('transcript', (e) => console.log('tu:', e.detail.text));
//   rt.addEventListener('text', (e) => console.log('ia:', e.detail.delta));
//   await rt.connect();
//   await rt.startMic();     // habla; interrumpe cuando quieras
//
// Eventos (CustomEvent, datos en `detail`):
//   state, session, speech-started, speech-stopped, transcript, response-start,
//   text, segment, response-done, interrupted, truncated, metrics, error,
//   message (cada evento JSON crudo del servidor)
//
// El audio se inyecta via `audio` (adaptador): por defecto WebAudioIO usa
// AudioWorklets; los tests pasan uno falso.
import { unpackAudio, pcm16ToFloat } from './audio-utils.js';

const SDK_BASE = new URL('.', import.meta.url);

export class RealtimeVoiceClient extends EventTarget {
  /**
   * @param {object} opts
   * @param {string} opts.url                 ws://host:8990/ws/voice
   * @param {object} [opts.session]           SessionConfig inicial (instructions, language, speed, vad...)
   * @param {object} [opts.audio]             adaptador de audio (ver WebAudioIO)
   * @param {Function} [opts.WebSocket]       implementacion WS (tests)
   * @param {number} [opts.positionEveryMs]   frecuencia de playback.position
   */
  constructor(opts = {}) {
    super();
    this.url = opts.url ?? defaultUrl();
    this.sessionInit = opts.session ?? null;
    this.audio = opts.audio ?? null;
    this.WS = opts.WebSocket ?? globalThis.WebSocket;
    this.positionEveryMs = opts.positionEveryMs ?? 100;
    this.ws = null;
    this.state = 'idle';
    this.outputRate = 24000;
    this.session = null;
    this.current = null;            // response_id en curso
    this.cancelled = new Set();     // ids cuyo audio se descarta
    this._cuts = new Map();         // id -> Promise<{id, played_ms}>
    this._lastPosSent = 0;
    this._pingT0 = 0;
  }

  // ------------------------------------------------------------- conexion
  connect() {
    if (this.ws) return Promise.resolve(this.session);
    this._setState('connecting');
    return new Promise((resolve, reject) => {
      const ws = new this.WS(this.url);
      ws.binaryType = 'arraybuffer';
      this.ws = ws;
      let opened = false;
      ws.onopen = () => { opened = true; };
      ws.onerror = (e) => {
        if (!opened) reject(new Error('no se pudo conectar a ' + this.url));
        this._emit('error', { message: 'websocket error', cause: e });
      };
      ws.onclose = () => {
        this.ws = null;
        this.audio?.flush?.(null);
        this._setState('idle');
        this._emit('close', {});
      };
      ws.onmessage = (e) => {
        if (typeof e.data === 'string') {
          let ev;
          try { ev = JSON.parse(e.data); } catch { return; }
          if (ev.type === 'session.created') {
            if (this.sessionInit) this.updateSession(this.sessionInit);
            resolve(ev.session);
          }
          this._onEvent(ev);
        } else {
          this._onAudio(e.data);
        }
      };
    });
  }

  disconnect() {
    this.stopMic();
    this.ws?.close();
    this.ws = null;
  }

  // ------------------------------------------------------------ microfono
  async startMic() {
    await this._ensureAudio();
    await this.audio.startMic((pcm16Buffer) => this.sendAudio(pcm16Buffer));
    this._setState('listening');
  }

  stopMic() {
    this.audio?.stopMic?.();
  }

  /** Envia PCM16 LE 16 kHz (ArrayBuffer / TypedArray). */
  sendAudio(pcm16) {
    if (this.ws?.readyState === 1) this.ws.send(pcm16);
  }

  // ------------------------------------------------------------- acciones
  sendText(text) { this._send({ type: 'conversation.item.create', text }); }
  commit() { this._send({ type: 'input_audio.commit' }); }
  clearInput() { this._send({ type: 'input_audio.clear' }); }
  clearConversation() { this._send({ type: 'conversation.clear' }); }
  updateSession(session) { this._send({ type: 'session.update', session }); }
  ping() { this._pingT0 = now(); this._send({ type: 'ping', t: this._pingT0 }); }

  /** Interrumpe manualmente la respuesta (boton "parar"). */
  cancel() {
    if (this.current != null) this._cutLocal(this.current);
    this._send({ type: 'response.cancel' });
  }

  // ------------------------------------------------------------- internos
  _onEvent(ev) {
    this._emit('message', ev);
    switch (ev.type) {
      case 'session.created':
      case 'session.updated':
        this.session = ev.session;
        if (ev.audio?.output_sample_rate) {
          this.outputRate = ev.audio.output_sample_rate;
          this.audio?.setOutputRate?.(this.outputRate);
        }
        this._emit('session', ev);
        break;
      case 'input_audio.speech_started':
        // Barge-in optimista: silenciar ya, sin esperar al servidor.
        if (ev.response_id != null && this.session?.barge_in !== false) this._cutLocal(ev.response_id);
        this._setState('listening');
        this._emit('speech-started', ev);
        break;
      case 'input_audio.speech_stopped':
        this._setState('thinking');
        this._emit('speech-stopped', ev);
        break;
      case 'transcript.final':
        this._emit('transcript', ev);
        break;
      case 'response.created':
        this.current = ev.response_id;
        this._setState('thinking');
        this._emit('response-start', ev);
        break;
      case 'response.text.delta':
        this._emit('text', ev);
        break;
      case 'response.segment':
        this._emit('segment', ev);
        break;
      case 'response.done':
        this._doneId = ev.response_id;
        if (!ev.audio_ms) this._setState('listening');
        this._emit('response-done', ev);
        break;
      case 'response.cancelled':
        this._onCancelled(ev);
        break;
      case 'conversation.truncated':
        this._emit('truncated', ev);
        break;
      case 'metrics.turn':
        this._emit('metrics', ev);
        break;
      case 'pong':
        this._emit('pong', { rtt_ms: this._pingT0 ? now() - this._pingT0 : null });
        break;
      case 'error':
        this._emit('error', ev);
        break;
    }
  }

  async _onCancelled(ev) {
    const id = ev.response_id;
    const res = await this._cutLocal(id);
    if (ev.audio_ms > 0) {
      // El servidor espera esto para guardar en el contexto solo lo escuchado.
      this._send({ type: 'response.truncate', response_id: id, played_ms: res.played_ms });
    }
    if (this.current === id) this.current = null;
    this._emit('interrupted', { ...ev });
    this._setState('listening');
  }

  /** Corta el audio local de `id` (idempotente); resuelve {id, played_ms}. */
  _cutLocal(id) {
    let p = this._cuts.get(id);
    if (p) return p;
    this.cancelled.add(id);
    p = Promise.resolve(this.audio?.flush?.(id)).then((r) => ({
      id, played_ms: r ? Math.round((r.played / this.outputRate) * 1000) : 0,
    }));
    this._cuts.set(id, p);
    if (this._cuts.size > 64) {
      const old = this._cuts.keys().next().value;
      this._cuts.delete(old);
      this.cancelled.delete(old);
    }
    return p;
  }

  _onAudio(buffer) {
    let frame;
    try { frame = unpackAudio(buffer); } catch { return; }
    if (this.cancelled.has(frame.responseId)) return;
    if (this.state !== 'speaking') this._setState('speaking');
    this.audio?.play?.(frame.responseId, pcm16ToFloat(frame.pcm));
    this._emit('audio', { responseId: frame.responseId, seq: frame.seq, samples: frame.pcm.length });
  }

  /** Lo llama el adaptador de audio con la posicion de reproduccion. */
  _onPlayback({ id, played, ended }) {
    if (this.cancelled.has(id)) return;
    const playedMs = (played / this.outputRate) * 1000;
    const t = now();
    if (ended || t - this._lastPosSent >= this.positionEveryMs) {
      this._lastPosSent = t;
      this._send({ type: 'playback.position', response_id: id, played_ms: Math.round(playedMs) });
    }
    // Solo cuando el servidor ya termino: si no, es un hueco entre frases.
    if (ended && id === this._doneId) this._setState('listening');
  }

  async _ensureAudio() {
    if (!this.audio) this.audio = new WebAudioIO();
    this.audio.onPlayback = (p) => this._onPlayback(p);
    await this.audio.init?.(this.outputRate);
  }

  _send(obj) {
    if (this.ws?.readyState === 1) this.ws.send(JSON.stringify(obj));
  }

  _setState(s) {
    if (this.state === s) return;
    this.state = s;
    this._emit('state', { state: s });
  }

  _emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }
}

// --------------------------------------------------------------------------
/** Adaptador WebAudio: microfono (worklet 16 kHz) + reproductor (worklet). */
export class WebAudioIO {
  constructor({ base = SDK_BASE, constraints } = {}) {
    this.base = base;
    this.constraints = constraints ?? {
      echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1,
    };
    this.ctx = null;
    this.player = null;
    this.mic = null;
    this.stream = null;
    this.onPlayback = null;
    this._flushWaiters = [];
  }

  async init(outputRate) {
    if (this.ctx) return;
    // Un contexto a la tasa de salida del TTS: sin remuestreo en reproduccion.
    this.ctx = new AudioContext({ sampleRate: outputRate, latencyHint: 'interactive' });
    await this.ctx.audioWorklet.addModule(new URL('player-worklet.js', this.base));
    await this.ctx.audioWorklet.addModule(new URL('mic-worklet.js', this.base));
    this.player = new AudioWorkletNode(this.ctx, 'rt-player', { outputChannelCount: [1] });
    this.player.connect(this.ctx.destination);
    this.player.port.onmessage = (e) => {
      const m = e.data;
      if (m.type === 'pos') this.onPlayback?.(m);
      else if (m.type === 'flushed') this._flushWaiters.shift()?.(m);
    };
    if (this.ctx.state === 'suspended') await this.ctx.resume();
  }

  setOutputRate() { /* el contexto se crea con la tasa anunciada en session.created */ }

  async startMic(onFrame) {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: this.constraints });
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.mic = new AudioWorkletNode(this.ctx, 'rt-mic-capture', { processorOptions: { frameMs: 20 } });
    this.mic.port.onmessage = (e) => onFrame(e.data);
    src.connect(this.mic);
  }

  stopMic() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.mic?.disconnect();
    this.mic = null;
    this.stream = null;
  }

  play(id, samples) {
    this.player?.port.postMessage({ type: 'push', id, samples }, [samples.buffer]);
  }

  flush(id) {
    if (!this.player) return Promise.resolve(null);
    return new Promise((resolve) => {
      this._flushWaiters.push(resolve);
      this.player.port.postMessage({ type: 'flush', id });
    });
  }

  async close() {
    this.stopMic();
    await this.ctx?.close();
    this.ctx = null;
  }
}

function now() {
  return globalThis.performance?.now?.() ?? Date.now();
}

function defaultUrl() {
  const loc = globalThis.location;
  if (!loc || !loc.host) return 'ws://localhost:8990/ws/voice';
  return `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.hostname}:8990/ws/voice`;
}
