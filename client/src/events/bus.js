// EventBus — pub/sub with envelope + per-channel namespacing.
// Mirrors server/shared/events/bus.py.

import { Event } from './envelope.js';

export class EventBus {
  constructor() {
    this._subscribers = new Map();   // type -> Set<handler>
    this._allSubscribers = new Set();
  }

  subscribe(type, handler) {
    if (!this._subscribers.has(type)) this._subscribers.set(type, new Set());
    this._subscribers.get(type).add(handler);
    return () => this._subscribers.get(type)?.delete(handler);
  }

  subscribeAll(handler) {
    this._allSubscribers.add(handler);
    return () => this._allSubscribers.delete(handler);
  }

  subscribers(type) {
    return [...(this._subscribers.get(type) || [])];
  }

  publish(event) {
    for (const h of this._allSubscribers) {
      try { h(event); } catch (_) { console.error('subscriber-all error', event.type, _); }
    }
    for (const h of (this._subscribers.get(event.type) || [])) {
      try { h(event); } catch (_) { console.error('subscriber error', event.type, _); }
    }
    return event;
  }

  emit(type, payload = null, opts = {}) {
    const evt = new Event({
      type,
      source: opts.source || 'unknown',
      payload: payload || {},
      correlationId: opts.correlationId ?? null,
      causationId: opts.causationId ?? null,
      trace: opts.trace ?? null,
    });
    return this.publish(evt);
  }
}

export class Channel {
  constructor(name, bus) {
    this.name = name;
    this._bus = bus;
  }
  emit(type, payload, opts = {}) {
    return this._bus.emit(type, payload, { source: this.name, ...opts });
  }
  subscribe(type, handler) { return this._bus.subscribe(type, handler); }
  subscribeAll(handler) { return this._bus.subscribeAll(handler); }
}

const _shared = new EventBus();
export const VoiceChannel = new Channel('voice', _shared);
export const ChatChannel = new Channel('chat', _shared);
export const EventBusInstance = _shared;
