// Vitest tests for the client-side ConversationLog.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ConversationLog, Turn, CANCEL_MODES, applyCancelMode } from '../src/conversation/log.js';

describe('ConversationLog (client)', () => {
  let log;

  beforeEach(() => {
    log = new ConversationLog();
  });

  it('starts empty', () => {
    expect(log.count()).toBe(0);
    expect(log.toMessages()).toEqual([]);
  });

  it('addUserTurn adds a user turn', () => {
    const t = log.addUserTurn('hola', 'text');
    expect(t.role).toBe('user');
    expect(t.source).toBe('text');
    expect(t.status).toBe('finalized');
    expect(log.count()).toBe(1);
  });

  it('addAssistantTurn adds an assistant turn', () => {
    const t = log.addAssistantTurn('buenos días', 'pensé que era un saludo');
    expect(t.role).toBe('assistant');
    expect(t.reasoning).toBe('pensé que era un saludo');
  });

  it('toMessages prepends system prompt', () => {
    log.addUserTurn('hola');
    const msgs = log.toMessages('you are helpful');
    expect(msgs[0]).toEqual({ role: 'system', content: 'you are helpful' });
    expect(msgs[1]).toEqual({ role: 'user', content: 'hola' });
  });

  it('reasoning is folded into the assistant content when present', () => {
    log.addAssistantTurn('hola', 'saludo');
    const msgs = log.toMessages();
    expect(msgs[0].content).toContain('[reasoning]');
    expect(msgs[0].content).toContain('saludo');
    expect(msgs[0].content).toContain('hola');
  });

  it('reasoning is not duplicated if already in content', () => {
    log.addAssistantTurn('hola saludo', 'saludo');
    const msgs = log.toMessages();
    // No [reasoning] tag because the content already includes the reasoning text.
    expect(msgs[0].content).not.toContain('[reasoning]');
  });

  describe('streaming', () => {
    it('streamUserPartial creates then updates a streaming turn', () => {
      const t1 = log.streamUserPartial('h');
      const t2 = log.streamUserPartial('ho');
      const t3 = log.streamUserPartial('hola');
      expect(t1.id).toBe(t2.id);
      expect(t2.id).toBe(t3.id);
      expect(t3.content).toBe('hola');
      expect(t3.status).toBe('streaming');
    });

    it('commitUserTurn finalizes the streaming turn', () => {
      log.streamUserPartial('hola');
      const t = log.commitUserTurn();
      expect(t.status).toBe('finalized');
      // Now it appears in toMessages.
      const msgs = log.toMessages();
      expect(msgs[0].role).toBe('user');
    });

    it('streaming turns are excluded from toMessages', () => {
      log.streamUserPartial('hola');
      const msgs = log.toMessages();
      expect(msgs).toEqual([]);
    });
  });

  describe('cancel modes', () => {
    it('silent removes the turn from toMessages', () => {
      log = new ConversationLog({ cancelMode: 'silent' });
      log.addAssistantTurn('hola mundo');
      log.cancelFinalized('assistant', 'barge-in');
      const msgs = log.toMessages();
      expect(msgs.find(m => m.role === 'assistant')).toBeUndefined();
    });

    it('note injects a synthetic system turn', () => {
      log = new ConversationLog({ cancelMode: 'note' });
      log.addAssistantTurn('hola mundo');
      log.cancelFinalized('assistant', 'barge-in');
      const msgs = log.toMessages();
      const sys = msgs.find(m => m.role === 'system');
      expect(sys).toBeTruthy();
      expect(sys.content).toContain('Previous assistant');
      expect(sys.content).toContain('barge-in');
    });

    it('summary uses first sentence only', () => {
      log = new ConversationLog({ cancelMode: 'summary' });
      log.addAssistantTurn('Hola. Esto es una prueba larga. Más texto.');
      log.cancelFinalized('assistant', 'barge-in');
      const msgs = log.toMessages();
      const sys = msgs.find(m => m.role === 'system');
      expect(sys.content).not.toContain('prueba larga');
      expect(sys.content).toContain('Hola.');
    });

    it('partial longer than 80 chars is truncated', () => {
      log = new ConversationLog({ cancelMode: 'note' });
      const long = 'palabra '.repeat(50);
      log.addAssistantTurn(long);
      log.cancelFinalized('assistant', 'barge-in');
      const msgs = log.toMessages();
      const sys = msgs.find(m => m.role === 'system');
      expect(sys.content).toContain('...');
    });

    it('cancelFinalized emits turnCanceled event', () => {
      log = new ConversationLog({ cancelMode: 'note' });
      const events = [];
      log.on('turnCanceled', e => events.push(e));
      log.addAssistantTurn('hola');
      log.cancelFinalized('assistant', 'barge-in');
      expect(events.length).toBe(1);
      expect(events[0].event).toBe('turnCanceled');
      expect(events[0].reason).toBe('barge-in');
    });
  });

  describe('sliding window', () => {
    it('truncates when window is exceeded', () => {
      log = new ConversationLog({ windowSize: 3 });
      for (let i = 0; i < 5; i++) log.addUserTurn(`u${i}`);
      const msgs = log.toMessages();
      const users = msgs.filter(m => m.role === 'user');
      expect(users.length).toBe(3);
      expect(users[0].content).toBe('u2');
      expect(users[2].content).toBe('u4');
    });

    it('setWindowSize re-truncates', () => {
      log = new ConversationLog({ windowSize: 10 });
      for (let i = 0; i < 8; i++) log.addUserTurn(`u${i}`);
      log.setWindowSize(3);
      const msgs = log.toMessages();
      expect(msgs.filter(m => m.role === 'user').length).toBe(3);
    });

    it('clear empties the log', () => {
      log.addUserTurn('x');
      log.addAssistantTurn('y');
      log.clear();
      expect(log.count()).toBe(0);
      expect(log.toMessages()).toEqual([]);
    });
  });

  describe('events', () => {
    it('turnAdded fires for each add', () => {
      const events = [];
      log.on('turnAdded', e => events.push(e));
      log.addUserTurn('a');
      log.addAssistantTurn('b');
      expect(events.length).toBe(2);
    });

    it('cleared event fires on clear', () => {
      const events = [];
      log.on('cleared', e => events.push(e));
      log.addUserTurn('a');
      log.clear();
      expect(events.length).toBe(1);
      expect(events[0].event).toBe('cleared');
    });

    it('unsubscribe stops receiving events', () => {
      const events = [];
      const unsub = log.on('turnAdded', e => events.push(e));
      log.addUserTurn('a');
      unsub();
      log.addUserTurn('b');
      expect(events.length).toBe(1);
    });
  });

  describe('toRealtimeEvents', () => {
    it('emits create events for finalized turns', () => {
      log.addUserTurn('hola');
      log.addAssistantTurn('buenos días');
      const events = log.toRealtimeEvents();
      expect(events.length).toBe(2);
      expect(events.every(e => e.type === 'conversation.item.create')).toBe(true);
    });

    it('excludes canceled turns', () => {
      log = new ConversationLog({ cancelMode: 'note' });
      log.addAssistantTurn('cancel me');
      log.cancelFinalized('assistant', 'barge-in');
      const events = log.toRealtimeEvents();
      // The synthetic system turn IS included.
      const assistantItems = events.filter(e => e.item?.role === 'assistant');
      expect(assistantItems.length).toBe(0);
      const systemItems = events.filter(e => e.item?.role === 'system');
      expect(systemItems.length).toBe(1);
    });
  });

  describe('cancel mode dispatch', () => {
    it('CANCEL_MODES registry covers silent/note/summary', () => {
      expect(CANCEL_MODES).toEqual(['silent', 'note', 'summary']);
    });

    it('applyCancelMode returns null for unknown mode', () => {
      expect(applyCancelMode('unknown', 'assistant', 'x', 'y')).toBeNull();
    });
  });
});
