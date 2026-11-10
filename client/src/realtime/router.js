// RealtimeRouter — picks the right transport based on config.
//
// When the user changes mode/transport in the UI, the router closes the
// current transport and opens a new one. The chat history is preserved.

import { LocalWSTransport } from './localWS.js';
import { LocalRTCTransport } from './localRTC.js';
import { OpenAIRealtimeTransport } from './openaiRealtime.js';

export class RealtimeRouter {
  constructor(config) {
    this.config = config;
    this.transport = null;
  }

  setConfig(newConfig) {
    this.config = { ...this.config, ...newConfig };
  }

  build() {
    if (this.transport) {
      try { this.transport.close(); } catch (_) { /* ignore */ }
    }
    if (this.config.mode === 'openai-realtime') {
      this.transport = new OpenAIRealtimeTransport(this.config);
    } else if (this.config.transport === 'webrtc') {
      this.transport = new LocalRTCTransport(this.config);
    } else {
      // Default: local chain via WebSocket.
      this.transport = new LocalWSTransport(this.config);
    }
    return this.transport;
  }

  get() { return this.transport; }

  async connect() {
    if (!this.transport) this.build();
    return this.transport.connect();
  }
}
