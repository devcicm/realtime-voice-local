// ChatPanel — chat UI bound to a RealtimeTransport.
//
// Wires the transport's audioChunk/transcriptDelta/assistantDelta
// events to the DOM. The SyncRenderer takes care of per-word
// highlighting as audio plays.

import { ChatChannel, EventType } from '../events/index.js';
import { SyncRenderer } from '../sync/renderer.js';
import { PlaybackSmoother } from '../sync/smoothing.js';
import { ConfigStore } from '../config/index.js';

export class ChatPanel {
  constructor(rootEl, router) {
    this.root = rootEl;
    this.router = router;
    this.messages = [];          // { role, content, renderer }
    this.smoother = new PlaybackSmoother({ mode: 'HYBRID' });
    this._render();
    this._wire();
  }

  _render() {
    this.root.innerHTML = `
      <h2>Chat</h2>
      <div class="chat" id="chatContainer" style="height:300px;overflow-y:auto;border:1px solid #333;padding:8px;margin-bottom:8px;"></div>
      <div class="chat-input">
        <input type="text" id="chatInput" placeholder="Escribe algo..." style="flex:1;padding:8px;" />
        <button id="sendBtn" class="btn btn-primary">Enviar</button>
        <button id="stopBtn" class="btn btn-secondary" disabled>Stop</button>
      </div>
    `;
    this.container = this.root.querySelector('#chatContainer');
    this.input = this.root.querySelector('#chatInput');
    this.root.querySelector('#sendBtn').addEventListener('click', () => this._send());
    this.input.addEventListener('keydown', (e) => { if (e.key === 'Enter') this._send(); });
    this.root.querySelector('#stopBtn').addEventListener('click', () => this._stop());
  }

  _wire() {
    const t = this.router.transport;
    t.on('assistantDelta', ({ text, delta }) => this._onAssistantDelta(delta || text));
    t.on('audioChunk', (p) => this._onAudioChunk(p));
    t.on('transcriptDelta', ({ text }) => this._onUserTranscript(text));
    t.on('turnEnd', () => this._onTurnEnd());

    ChatChannel.subscribe(EventType.LLM_TOKEN_DELTA, (e) => this._onAssistantDelta(e.payload.delta || e.payload.text));
  }

  _send() {
    const text = this.input.value.trim();
    if (!text) return;
    this.input.value = '';
    this._onUserTranscript(text);
    this.router.transport.sendText(text);
  }

  _stop() {
    this.router.transport.interrupt();
    if (this._currentRenderer) this._currentRenderer.cancel(0);
  }

  _onUserTranscript(text) {
    this._appendMessage('user', text);
  }

  _onAssistantDelta(text) {
    if (!text) return;
    if (!this._currentMsgEl) {
      this._currentMsgEl = this._appendMessage('assistant', '');
      this._currentText = '';
    }
    this._currentText += text;
    if (!this._currentRenderer) {
      this._currentRenderer = new SyncRenderer(this._currentMsgEl.querySelector('.content'), this._currentText);
    } else {
      this._currentRenderer.setText(this._currentText);
    }
  }

  _onAudioChunk(p) {
    if (p.type === 'start' && this._currentRenderer) {
      this._currentRenderer.onAudioStart();
    } else if (p.type === 'end' && this._currentRenderer) {
      this._currentRenderer.onAudioEnd(p);
    } else if (p.type === 'pcm' && this._currentRenderer) {
      this._currentRenderer.onAudioChunk(p.seq, p.duration_ms || 0);
    }
  }

  _onTurnEnd() {
    this._currentMsgEl = null;
    this._currentText = '';
    this._currentRenderer = null;
  }

  _appendMessage(role, content) {
    const el = document.createElement('div');
    el.className = `msg ${role}`;
    el.innerHTML = `<div class="role">${role === 'user' ? 'Tú' : 'Asistente'}</div><div class="content">${escapeHtml(content)}</div>`;
    this.container.appendChild(el);
    this.container.scrollTop = this.container.scrollHeight;
    this.messages.push({ role, content, el });
    return el;
  }
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
