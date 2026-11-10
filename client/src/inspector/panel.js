// InspectorPanel — visualizes the 4 FSMs (Connection, Turn, Pipeline,
// Message) and the SessionState in real time.
//
// It subscribes to the EventBus and updates when the FSMs transition.
// State changes are also reflected via the SessionState module.

import { EventBusInstance, EventType } from '../events/index.js';
import { SessionState } from '../session/state.js';
import {
  ConnectionStateMachine, ConnectionStates,
  TurnStateMachine, TurnStates,
  PipelineStateMachine, PipelineStates,
  MessageStateMachine, MessageStates,
} from '../state/machine.js';

export class InspectorPanel {
  constructor(rootEl) {
    this.root = rootEl;
    this._conn = new ConnectionStateMachine();
    this._turn = new TurnStateMachine();
    this._pipe = new PipelineStateMachine();
    this._msg = new MessageStateMachine();
    this._render();
    this._wire();
  }

  _render() {
    this.root.innerHTML = `
      <h2>Session Inspector</h2>
      <div class="inspector-grid">
        <div class="fsm">
          <h3>Connection</h3>
          <div class="state" id="fsm-connection">${this._conn.current}</div>
          <div class="history" id="hist-connection"></div>
        </div>
        <div class="fsm">
          <h3>Turn</h3>
          <div class="state" id="fsm-turn">${this._turn.current}</div>
          <div class="history" id="hist-turn"></div>
        </div>
        <div class="fsm">
          <h3>Pipeline</h3>
          <div class="state" id="fsm-pipeline">${this._pipe.current}</div>
          <div class="history" id="hist-pipeline"></div>
        </div>
        <div class="fsm">
          <h3>Message</h3>
          <div class="state" id="fsm-message">${this._msg.current}</div>
          <div class="history" id="hist-message"></div>
        </div>
      </div>
      <h3>Session</h3>
      <pre class="session" id="session-snapshot">${this._formatSession()}</pre>
    `;
  }

  _wire() {
    // Wire FSMs to listen to event bus.
    EventBusInstance.subscribe(EventType.CONN_OPENING, () => this._conn.transition('CONNECTING'));
    EventBusInstance.subscribe(EventType.CONN_OPEN,    () => { try { this._conn.transition('OPEN'); } catch (_) {} });
    EventBusInstance.subscribe(EventType.CONN_CLOSING, () => this._conn.transition('CLOSING'));
    EventBusInstance.subscribe(EventType.CONN_CLOSED,  () => this._conn.transition('CLOSED'));
    EventBusInstance.subscribe(EventType.CONN_FAILED,  () => this._conn.transition('ERROR'));

    EventBusInstance.subscribe(EventType.VAD_SPEECH_START,  () => this._turn.transition('USER_SPEAKING'));
    EventBusInstance.subscribe(EventType.VAD_SPEECH_END,    () => this._turn.transition('USER_DONE'));
    EventBusInstance.subscribe(EventType.LLM_TOKEN_DELTA,  () => this._turn.transition('MODEL_THINKING'));
    EventBusInstance.subscribe(EventType.TTS_AUDIO_DELTA,  () => this._turn.transition('MODEL_SPEAKING'));
    EventBusInstance.subscribe(EventType.INTERRUPT_REQUESTED, () => this._turn.transition('INTERRUPTED'));
    EventBusInstance.subscribe(EventType.TTS_AUDIO_END,    () => this._turn.transition('IDLE'));

    // Observe FSMs for UI updates.
    for (const fsm of [this._conn, this._turn, this._pipe, this._msg]) {
      fsm.on(() => this._renderFsm(fsm));
    }

    // Re-render the session snapshot on any state change.
    SessionState.onChange(() => this._renderSession());
  }

  _renderFsm(fsm) {
    const el = this.root.querySelector(`#fsm-${fsm.name.toLowerCase().replace('statemachine', '')}`);
    if (el) el.textContent = fsm.current;
  }

  _formatSession() {
    return JSON.stringify(SessionState.snapshot(), null, 2);
  }

  _renderSession() {
    const el = this.root.querySelector('#session-snapshot');
    if (el) el.textContent = this._formatSession();
  }
}
