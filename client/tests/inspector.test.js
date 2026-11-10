// Vitest tests for InspectorPanel.
import { describe, it, expect, beforeEach } from 'vitest';
import { EventBusInstance, EventType } from '../src/events/index.js';
import { SessionState } from '../src/session/state.js';

describe('InspectorPanel', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    SessionState.reset();
    // Clear all subscribers
    EventBusInstance._subscribers.clear();
    EventBusInstance._allSubscribers.clear();
  });

  it('mounts and renders the four FSMs + session', async () => {
    const { InspectorPanel } = await import('../src/inspector/panel.js');
    const root = document.querySelector('#root');
    new InspectorPanel(root);
    expect(root.querySelector('#fsm-connection')).toBeTruthy();
    expect(root.querySelector('#fsm-turn')).toBeTruthy();
    expect(root.querySelector('#fsm-pipeline')).toBeTruthy();
    expect(root.querySelector('#fsm-message')).toBeTruthy();
    expect(root.querySelector('#session-snapshot')).toBeTruthy();
  });

  it('reacts to CONN_OPEN event', async () => {
    const { InspectorPanel } = await import('../src/inspector/panel.js');
    const root = document.querySelector('#root');
    new InspectorPanel(root);
    EventBusInstance.emit(EventType.CONN_OPENING);
    EventBusInstance.emit(EventType.CONN_OPEN);
    expect(root.querySelector('#fsm-connection').textContent).toBe('OPEN');
  });

  it('reacts to VAD events (turn flow)', async () => {
    const { InspectorPanel } = await import('../src/inspector/panel.js');
    const root = document.querySelector('#root');
    new InspectorPanel(root);
    EventBusInstance.emit(EventType.VAD_SPEECH_START);
    expect(root.querySelector('#fsm-turn').textContent).toBe('USER_SPEAKING');
    EventBusInstance.emit(EventType.VAD_SPEECH_END);
    expect(root.querySelector('#fsm-turn').textContent).toBe('USER_DONE');
  });

  it('updates session snapshot when SessionState changes', async () => {
    const { InspectorPanel } = await import('../src/inspector/panel.js');
    const root = document.querySelector('#root');
    new InspectorPanel(root);
    SessionState.update({ connection: 'open', voice_id: 'ef_dora' });
    const text = root.querySelector('#session-snapshot').textContent;
    expect(text).toContain('"connection": "open"');
    expect(text).toContain('"voice_id": "ef_dora"');
  });
});
