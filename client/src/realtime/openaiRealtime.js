// OpenAIRealtimeTransport — WebRTC to gpt-realtime, single model.
//
// Maintains a ConversationLog that is the single source of truth for
// the conversation. Sends conversation.item.create / item.delete
// events to OpenAI to keep the server-side model in sync with the
// client-side log.
//
// On barge-in: marks the in-flight assistant turn as canceled in the
// log AND sends conversation.item.delete to OpenAI so the partial
// response is removed from the model's memory.

import { RealtimeTransport } from './transport.js';
import { ConversationLog } from '../conversation/log.js';
import { getSessionContext } from '../session/context.js';
import { EventType } from '../events/types.js';
import { VoiceChannel, ChatChannel } from '../events/bus.js';

const REALTIME_URL = 'https://api.openai.com/v1/realtime';

export class OpenAIRealtimeTransport extends RealtimeTransport {
  constructor(config) {
    super({ ...config, mode: 'openai-realtime', transport: 'webrtc' });
    if (!config.apiKey) throw new Error('OpenAIRealtimeTransport requires an API key');
    this._pc = null;
    this._dc = null;
    this._audioEl = null;
    this._mediaStream = null;
    this._currentResponseId = null;     // the assistant response being played
    this._currentItemId = null;         // the conversation.item being built
    this._currentPartial = '';          // for cancel-on-barge-in snapshot
    this.conversationLog = new ConversationLog({
      windowSize: config.conversationWindowSize ?? 20,
      cancelMode: config.cancelMode ?? 'note',
      overflowStrategy: config.overflowStrategy ?? 'truncate',
    });
    this.sessionContext = getSessionContext();
  }

  async connect() {
    this.state = 'connecting';
    const ephemeralKey = this.config.apiKey;
    this._pc = new RTCPeerConnection();
    this._audioEl = document.createElement('audio');
    this._audioEl.autoplay = true;
    this._pc.ontrack = (e) => {
      this._audioEl.srcObject = e.streams[0];
      this._emit('audioChunk', { type: 'start', sample_rate: 24000 });
    };
    this._mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this._pc.addTrack(this._mediaStream.getTracks()[0]);
    this._dc = this._pc.createDataChannel('oai-events');
    this._dc.onmessage = (e) => this._onDcMessage(e);
    this._dc.onopen = () => this._sendSessionUpdate();
    const offer = await this._pc.createOffer();
    await this._pc.setLocalDescription(offer);
    const sdpRes = await fetch(`${REALTIME_URL}?model=${encodeURIComponent(this.config.model || 'gpt-realtime')}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${ephemeralKey}`, 'Content-Type': 'application/sdp' },
      body: offer.sdp,
    });
    if (!sdpRes.ok) {
      this.state = 'error';
      throw new Error(`OpenAI SDP exchange failed: HTTP ${sdpRes.status}`);
    }
    const answerSdp = await sdpRes.text();
    await this._pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
    this.state = 'open';
  }

  _sendSessionUpdate() {
    this._dc.send(JSON.stringify({
      type: 'session.update',
      session: {
        type: 'realtime',
        model: this.config.model || 'gpt-realtime',
        audio: {
          input: { turn_detection: { type: 'semantic_vad' } },
          output: { voice: this.config.voice || 'marin', format: { type: 'audio/pcm', rate: 24000 } },
        },
      },
    }));
  }

  _onDcMessage(e) {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    switch (msg.type) {
      case 'response.created':
        this._currentResponseId = msg.response?.id;
        break;
      case 'response.output_item.added':
        this._currentItemId = msg.item?.id;
        if (msg.item?.role === 'user') {
          // Sync to the log.
          this.conversationLog.addUserTurn(msg.item.content?.[0]?.text || '', 'openai');
        }
        break;
      case 'response.output_audio.delta':
        this._emit('audioChunk', { type: 'pcm', delta: msg.delta, sample_rate: 24000 });
        break;
      case 'response.output_audio_transcript.delta':
        this._currentPartial += msg.delta;
        this._emit('assistantDelta', { text: this._currentPartial, delta: msg.delta });
        this.sessionContext.setSttPartial(this._currentPartial);
        break;
      case 'response.output_text.delta':
        // Same as transcript delta for the text-only path.
        this._currentPartial += msg.delta;
        this._emit('assistantDelta', { text: this._currentPartial, delta: msg.delta });
        break;
      case 'response.done':
        if (this._currentPartial.trim()) {
          this.conversationLog.addAssistantTurn(this._currentPartial.trim());
        }
        this._currentPartial = '';
        this._currentItemId = null;
        this._currentResponseId = null;
        this.sessionContext.setSttPartial('');
        this._emit('turnEnd', { response: msg.response });
        break;
      case 'input_audio_buffer.speech_started':
        this._emit('speechStarted', {});
        this.sessionContext.setVadActive(true);
        break;
      case 'input_audio_buffer.speech_stopped':
        this._emit('speechStopped', {});
        this.sessionContext.setVadActive(false);
        break;
      case 'conversation.item.created':
        // OpenAI confirms an item.create we sent.
        break;
      case 'error':
        this._emit('turnEnd', { reason: 'error', error: msg });
        break;
    }
  }

  async sendAudioChunk(_pcm32) { /* no-op for WebRTC mic */ }

  async sendText(text) {
    if (!this._dc || this._dc.readyState !== 'open') return;
    // Push to the local log + send to OpenAI.
    this.conversationLog.addUserTurn(text, 'text');
    this._dc.send(JSON.stringify({
      type: 'conversation.item.create',
      item: { type: 'message', role: 'user', content: [{ type: 'input_text', text }] },
    }));
    this._dc.send(JSON.stringify({ type: 'response.create' }));
  }

  interrupt() {
    if (this._dc && this._dc.readyState === 'open') {
      // Cancel the current response on OpenAI side.
      this._dc.send(JSON.stringify({ type: 'response.cancel' }));
      // Delete the partial item from OpenAI's memory.
      if (this._currentItemId) {
        this._dc.send(JSON.stringify({ type: 'conversation.item.delete', item_id: this._currentItemId }));
      }
    }
    // Default: keep the partial turn in the conversation context
    // (fluid multi-turn). Set cancelOnInterrupt in config for privacy.
    const partial = this._currentPartial;
    if (this.config.cancelOnInterrupt) {
      this.conversationLog.cancelFinalized('assistant', 'barge-in', partial);
    } else {
      this.conversationLog.markInterrupted('assistant', 'barge-in', partial);
    }
    this._currentPartial = '';
    this._currentItemId = null;
    this._currentResponseId = null;
    this.sessionContext.setInterruption('barge-in');
  }

  updateSession(patch) {
    if (this._dc && this._dc.readyState === 'open') {
      this._dc.send(JSON.stringify({ type: 'session.update', session: patch }));
    }
  }

  close() {
    this.state = 'closing';
    if (this._dc) this._dc.close();
    if (this._pc) this._pc.close();
    if (this._mediaStream) this._mediaStream.getTracks().forEach(t => t.stop());
    if (this._audioEl) this._audioEl.srcObject = null;
    this.state = 'closed';
  }
}
