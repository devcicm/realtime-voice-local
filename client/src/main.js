// main.js — bootstrap for the modular Vite client.
//
// 1. Mount the config panel + inspector + chat shell.
// 2. Build the RealtimeRouter from ConfigStore.
// 3. Connect to services; on config change, rebuild + reconnect.
// 4. Wire event bus ↔ renderer for visual feedback.

import { ConfigStore, ConfigPanel } from './config/index.js';
import { RealtimeRouter } from './realtime/index.js';
import { InspectorPanel } from './inspector/index.js';
import { ChatPanel } from './chat/panel.js';
import { MicCapture } from './audio/micCapture.js';
import { getTracer, getRegistry, tracedFetch } from './observability/index.js';

const root = {
  config:     document.querySelector('#config-card'),
  mic:        document.querySelector('#mic-card'),
  voice:      document.querySelector('#voice-card'),
  chat:       document.querySelector('#chat-card'),
  inspector:  document.querySelector('#inspector-card'),
  traces:     document.querySelector('#traces-card'),
};

// 1. Inspector (always on, observes the EventBus).
new InspectorPanel(root.inspector);

// 2. Config panel.
new ConfigPanel(root.config);

// 3. Router: rebuilds the transport when the user changes config.
const router = new RealtimeRouter(ConfigStore.get());
ConfigStore.onChange((cfg) => {
  router.setConfig(cfg);
  // Eagerly rebuild so the next connect() uses the new transport.
  router.build();
});

// 4. Chat panel (lazy — needs the transport).
let chat = null;
async function bootstrap() {
  try {
    await router.connect();
    console.info('[main] transport connected:', router.transport.config.mode);
  } catch (err) {
    console.error('[main] transport connect failed:', err);
  }
  if (!chat) {
    chat = new ChatPanel(root.chat, router);
  }
}
bootstrap();

// 5. Mic capture (UI in #mic-card).
const mic = new MicCapture(root.mic, router);
mic.bind();

// 6. Tracer hook: every fetch the client makes gets a trace_id.
const origFetch = window.fetch;
window.fetch = (url, opts) => tracedFetch(url, opts);

// 7. Periodic registry export: hit /metrics on the STT server so the
// client-side Tracer is in sync with the server. (No-op if not available.)
setInterval(async () => {
  try {
    const cfg = ConfigStore.get();
    await origFetch(`${cfg.sttUrl}/metrics`).catch(() => {});
  } catch (_) { /* ignore */ }
}, 10000);

// 8. Expose for debugging.
if (import.meta.env?.DEV) {
  window.__kokoro = { ConfigStore, router, getTracer, getRegistry };
}
