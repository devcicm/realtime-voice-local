// LocalWSTransport — STT (HTTP) + LLM (HTTP) + TTS (WebSocket) over localhost.
//
// Maintains a ConversationLog that is the single source of truth for
// the LLM context. Replaces the previous ad-hoc message array.
//
// STT streaming: uses SttDebouncer to commit transcriptions after 1.5s
// of silence. Partial transcripts are forwarded to SessionContext for
// the UI.

import { RealtimeTransport } from './transport.js';
import { ConversationLog } from '../conversation/log.js';
import { getSessionContext } from '../session/context.js';
import { SttDebouncer } from '../audio/sttDebouncer.js';
import { EventType } from '../events/types.js';
import { VoiceChannel, ChatChannel } from '../events/bus.js';

const SAMPLE_RATE = 16000;

export class LocalWSTransport extends RealtimeTransport {
  constructor(config) {
    super({ ...config, mode: 'local-chain', transport: 'websocket' });
    this._ws = null;
    this._wsReady = null;
    this._currentRequestId = null;     // the TTS request currently playing
    this._currentChunkId = null;
    this._abortController = null;
    this.conversationLog = new ConversationLog({
      windowSize: config.conversationWindowSize ?? 20,
      cancelMode: config.cancelMode ?? 'note',
      overflowStrategy: config.overflowStrategy ?? 'truncate',
    });
    this.sessionContext = getSessionContext();
    this._sttDebouncer = new SttDebouncer({
      debounceMs: config.sttDebounceMs ?? 1500,
      onPartial: (text) => this._onSttPartial(text),
      onCommit: (text) => this._onSttCommit(text),
    });
    // Synth events so the inspector can pick up our state changes.
    this.conversationLog.on('turnAdded', (e) => {
      if (e.turn.role === 'user') VoiceChannel.emit(EventType.STT_TRANSCRIBED, { text: e.turn.content, source: e.turn.source });
      if (e.turn.role === 'assistant' && !e.synthetic) ChatChannel.emit(EventType.LLM_TOKEN_DELTA, { text: e.turn.content, final: true });
    });
    this.conversationLog.on('turnCanceled', (e) => {
      VoiceChannel.emit(EventType.INTERRUPT_ACK, { reason: e.reason, mode: e.mode });
    });
  }

  _onSttPartial(text) {
    this.sessionContext.setSttPartial(text);
  }

  _onSttCommit(text) {
    this.sessionContext.setSttPartial('');
    this.conversationLog.addUserTurn(text, 'stt');
  }

  async connect() {
    this.state = 'connecting';
    this._wsReady = new Promise((resolve, reject) => {
      const ws = new WebSocket(this.config.ttsWsUrl || 'ws://localhost:8990/ws/tts');
      ws.binaryType = 'arraybuffer';
      ws.onopen = () => {
        this._ws = ws;
        this.state = 'open';
        ws.send(JSON.stringify({
          type: 'config',
          voice: this.config.voice || 'es_ES/davefx-medium',
          lang: 'e',
          speed: 1.0,
          format: 'pcm',
          protocol_version: 2,
        }));
        resolve();
      };
      ws.onerror = (err) => {
        this.state = 'error';
        reject(err);
      };
      ws.onclose = () => {
        this.state = 'closed';
        this._emit('turnEnd', { reason: 'ws-closed' });
      };
      ws.onmessage = (ev) => this._onWsMessage(ev);
    });
    await this._wsReady;
  }

  _onWsMessage(ev) {
    if (typeof ev.data !== 'string') return;
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    switch (msg.type) {
      case 'audio_start':
        this._currentRequestId = msg.request_id;
        this._currentChunkId = msg.chunk_id;
        this._emit('audioChunk', {
          type: 'start',
          request_id: msg.request_id,
          chunk_id: msg.chunk_id,
          sample_rate: msg.sample_rate,
        });
        break;
      case 'audio_chunk':
        this._pendingChunkId = msg.chunk_id;
        this._pendingSeq = msg.seq;
        this._pendingDuration = msg.duration_ms;
        break;
      case 'audio_end':
        this._emit('audioChunk', {
          type: 'end',
          request_id: msg.request_id,
          chunk_id: msg.chunk_id,
          word_offsets_ms: msg.word_offsets_ms,
          duration_ms: msg.duration_ms,
          word_count: msg.word_count,
        });
        break;
      case 'error':
        this._emit('turnEnd', { reason: 'error', error: msg });
        break;
    }
  }

  onBinaryChunk(buffer) {
    if (this._pendingChunkId) {
      this._emit('audioChunk', {
        type: 'pcm',
        chunk_id: this._pendingChunkId,
        seq: this._pendingSeq,
        pcm: buffer,
        duration_ms: this._pendingDuration,
        sample_rate: SAMPLE_RATE,
      });
      this._pendingChunkId = null;
    }
  }

  /** Send audio (Float32 PCM @ 16kHz mono) to STT, then LLM, then TTS. */
  async sendAudioChunk(pcm32) {
    const wav = pcm32ToWav(pcm32, SAMPLE_RATE);
    const form = new FormData();
    form.append('audio', new Blob([wav], { type: 'audio/wav' }), 'chunk.wav');
    form.append('language', 'es');
    form.append('model', 'tiny');
    const sttRes = await fetch(`${this.config.sttUrl}/transcribe`, {
      method: 'POST',
      body: form,
    });
    if (!sttRes.ok) throw new Error(`STT HTTP ${sttRes.status}`);
    const sttData = await sttRes.json();
    const text = (sttData.text || '').trim();
    if (!text) return;
    this._emit('transcriptDelta', { text, source: 'stt' });
    // Stream into the debouncer; commits after 1.5s of silence.
    this._sttDebouncer.pushPartial(text);
  }

  /** Send text input to the LLM and forward to TTS. */
  async sendText(text) {
    this.conversationLog.addUserTurn(text, 'text');
    this._emit('transcriptDelta', { text, source: 'text' });
    await this._runLLMStream();
  }

  async _runLLMStream() {
    this.sessionContext.setTurn('model_thinking');
    this._abortController = new AbortController();
    this._emit('assistantDelta', { text: '', reasoning: '' });
    // The single source of truth for the LLM context.
    const sysWithContext = this._buildSystemPrompt();
    const messages = this.conversationLog.toMessages(null);
    // Replace the last user message's content (just added) with the
    // already-known text — but toMessages includes it correctly. No edit needed.
    // We need to keep the *just added* user turn at the end; the
    // current toMessages already does that.
    const payload = {
      model: this.config.model || 'qwen2.5:0.5b-instruct-q8_0',
      messages: [
        { role: 'system', content: sysWithContext },
        ...messages,
      ],
      max_tokens: this.config.maxTokens || 400,
      temperature: 0.7,
      stream: true,
    };
    const response = await fetch(`${this.config.llmUrl}/chat/completions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: this._abortController.signal,
    });
    if (!response.ok) throw new Error(`LLM HTTP ${response.status}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let acc = '';
    let reasoning = '';
    this.sessionContext.setTurn('model_speaking');
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, idx).trim();
          buf = buf.slice(idx + 1);
          if (!line.startsWith('data:')) continue;
          const payload2 = line.slice(5).trim();
          if (payload2 === '[DONE]') continue;
          let d;
          try { d = JSON.parse(payload2); } catch { continue; }
          const delta = d.choices?.[0]?.delta || {};
          const text = delta.content || '';
          const rsn = delta.reasoning_content || '';
          if (text) {
            acc += text;
            this._emit('assistantDelta', { text: acc, delta: text });
          }
          if (rsn) {
            reasoning += rsn;
            this._emit('assistantDelta', { text: acc, reasoning });
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') throw err;
    }
    if (acc.trim()) {
      this.conversationLog.addAssistantTurn(acc.trim(), reasoning.trim() || null);
    }
    this.sessionContext.setTurn('idle');
    this._emit('turnEnd', {});
  }

  _buildSystemPrompt() {
    const sysPrompt = this.config.systemPrompt || 'You are a helpful assistant. Respond in Spanish.';
    const ambient = this.sessionContext.toSystemContext();
    if (!ambient) return sysPrompt;
    return `${ambient}\n\n${sysPrompt}`;
  }

  /**
   * Cancel the current in-flight LLM stream and any pending STT.
   * The partial assistant turn is marked 'interrupted' (kept in
   * conversation context) so the LLM has continuity across barge-in.
   * Set `this.config.cancelOnInterrupt = true` to remove it from
   * context instead (privacy mode).
   */
  interrupt() {
    if (this._abortController) {
      try { this._abortController.abort(); } catch (_) {}
      this._abortController = null;
    }
    if (this._currentRequestId) {
      const partial = this._currentPartialText;
      if (this.config.cancelOnInterrupt) {
        // Privacy mode: remove the partial turn from context.
        this.conversationLog.cancelFinalized('assistant', 'barge-in', partial);
      } else {
        // Default: keep the partial turn in context for continuity.
        this.conversationLog.markInterrupted('assistant', 'barge-in', partial);
      }
    }
    this._sttDebouncer.cancel();
    if (this._ws && this._ws.readyState === 1) {
      this._ws.send(JSON.stringify({ type: 'interrupt' }));
    }
    this.sessionContext.setInterruption('barge-in');
    this.sessionContext.setTurn('interrupted');
  }

  get _currentPartialText() {
    // Used by interrupt() to snapshot the partial assistant text.
    return this._lastAssistantText || '';
  }

  updateSession(patch) {
    if (!this._ws || this._ws.readyState !== 1) return;
    this._ws.send(JSON.stringify({ type: 'config', ...patch }));
  }

  close() {
    this.state = 'closing';
    if (this._ws) this._ws.close();
    this.state = 'closed';
  }
}

// ---- helpers ----

function pcm32ToWav(pcm32, sampleRate) {
  const numSamples = pcm32.length;
  const buffer = new ArrayBuffer(44 + numSamples * 2);
  const view = new DataView(buffer);
  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + numSamples * 2, true);
  writeString(view, 8, 'WAVE');
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, 'data');
  view.setUint32(40, numSamples * 2, true);
  let offset = 44;
  for (let i = 0; i < numSamples; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, pcm32[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

function writeString(view, offset, s) {
  for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
}
