// FencedBus — wrapper sobre el bus existente que aplica stale-event fence.
//
// Cada evento lleva un `runId` opcional. Los subscribers pueden fencear
// por runId pasándolo a `subscribe(type, handler, { runId })`. El
// wrapper descarta los eventos cuyo `runId` no coincida con el
// `activeRunId` actual.
//
// Invariantes:
//   - Si el evento NO tiene runId, se entrega normalmente (compatibilidad).
//   - Si el subscriber NO pide fence, recibe todos los eventos.
//   - Si el subscriber pide fence y runId no coincide, el evento se
//     descarta silenciosamente (con un counter de métrica si está
//     disponible).
//
// Guards:
//   - El fence es per-subscriber; no afecta a otros subscribers.
//   - Si runId es undefined o null en el handler, el evento pasa
//     (compatibilidad con el bus anterior sin run_id).

export class FencedBus {
  /**
   * @param {object} bus          the underlying MessageBus (must support
   *                               subscribe(type, handler) and emit(type, payload, opts))
   * @param {object} turnController  source of activeRunId
   * @param {object} [metrics]      optional metrics recorder
   */
  constructor({ bus, turnController, metrics } = {}) {
    if (!bus) throw new Error('FencedBus requires a bus');
    if (!turnController) throw new Error('FencedBus requires a turnController');
    this._bus = bus;
    this._tc = turnController;
    this._metrics = metrics || null;
    this._unsubscribers = [];
  }

  /** Subscribe to a type. Options:
   *    runId: 'active' (default) — fence against the active run
   *          'all' — do not fence, receive everything (legacy)
   *          specific-string — fence against a specific run_id
   */
  subscribe(type, handler, opts = {}) {
    const fenceMode = opts.runId ?? 'active';
    const wrapper = (event) => {
      if (fenceMode === 'all') {
        return safeInvoke(handler, event);
      }
      // Fence mode
      const eventRunId = event.runId ?? event.payload?.runId ?? event.meta?.runId;
      if (eventRunId == null) {
        // No runId on the event — pass through (back-compat).
        return safeInvoke(handler, event);
      }
      const target = fenceMode === 'active'
        ? this._tc.activeRunId
        : fenceMode;
      if (eventRunId !== target) {
        // Stale event: drop with metric counter.
        if (this._metrics?.stale_events_dropped_total) {
          try {
            this._metrics.stale_events_dropped_total.inc({ type: event.type || event.event });
          } catch (_) { /* metric error: ignore */ }
        }
        return;
      }
      return safeInvoke(handler, event);
    };
    const unsub = this._bus.subscribe(type, wrapper);
    const wrapped = () => { try { unsub(); } catch (_) {} };
    this._unsubscribers.push(wrapped);
    return wrapped;
  }

  /** Subscribe to all events. opts.fence controls behavior. */
  subscribeAll(handler, opts = {}) {
    return this.subscribe('*', handler, opts);
  }

  /** Emit a new event. The FencedBus transparently adds runId, sessionId
   *  and turnId from the active run if not already present.
   *
   *  Order: prefer values from the active RunContext (it is the
   *  authoritative source), then fall back to activeRunId. This
   *  way the caller can attach the RunContext to the turnController
   *  (via newRun's defaults) and the FencedBus will use it.
   */
  emit(type, payload = {}, opts = {}) {
    const enriched = { ...payload };
    const activeRun = this._tc.activeRun;
    if (!enriched.runId) {
      enriched.runId = (activeRun && activeRun.runId) || this._tc.activeRunId;
    }
    if (!enriched.sessionId && activeRun) {
      enriched.sessionId = activeRun.sessionId;
    }
    if (!enriched.turnId && activeRun) {
      enriched.turnId = activeRun.turnId;
    }
    return this._bus.emit(type, enriched, opts);
  }

  /** Cleanup: unsubscribe everything. */
  dispose() {
    for (const u of this._unsubscribers) {
      try { u(); } catch (_) { /* ignore */ }
    }
    this._unsubscribers = [];
  }
}

function safeInvoke(handler, event) {
  try {
    return handler(event);
  } catch (e) {
    console.warn('[FencedBus] handler threw:', e);
  }
}
