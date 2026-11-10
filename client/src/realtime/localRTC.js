// LocalRTCTransport — STT + LLM over HTTP, TTS audio over WebRTC.
//
// Inherits all of LocalWSTransport's behavior (including the
// ConversationLog, SessionContext, and STT debouncer). Overrides only
// the audio transport: TTS PCM frames arrive over an RTCPeerConnection
// instead of a WebSocket. The signaling endpoint must be a small
// aiohttp + aiortc server (out of scope here; this is a stub).

import { LocalWSTransport } from './localWS.js';

export class LocalRTCTransport extends LocalWSTransport {
  constructor(config) {
    super({ ...config, transport: 'webrtc' });
    this._pc = null;
    this._signalingUrl = config.signalingUrl || 'ws://localhost:8991/signaling';
  }

  async connect() {
    this.state = 'connecting';
    this._pc = new RTCPeerConnection();
    this._pc.addTransceiver('audio', { direction: 'recvonly' });
    const offer = await this._pc.createOffer();
    await this._pc.setLocalDescription(offer);
    const sigRes = await fetch(this._signalingUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sdp: offer.sdp }),
    });
    if (!sigRes.ok) throw new Error(`Signaling failed: HTTP ${sigRes.status}`);
    const { sdp: answerSdp } = await sigRes.json();
    await this._pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
    this._pc.ontrack = (e) => {
      // The remote track delivers TTS audio. We hook into it to feed
      // the existing audioChunk emitter.
      const stream = e.streams[0];
      const audioEl = document.createElement('audio');
      audioEl.autoplay = true;
      audioEl.srcObject = stream;
      this._emit('audioChunk', { type: 'start', sample_rate: 24000, source: 'webrtc' });
    };
    this.state = 'open';
  }

  close() {
    this.state = 'closing';
    if (this._pc) this._pc.close();
    super.close();
  }
}
