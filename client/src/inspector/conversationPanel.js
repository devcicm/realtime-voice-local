// ConversationPanel — shows the live ConversationLog as a list of turns.
// Has a "Clear context" button that calls log.clear().

export class ConversationPanel {
  constructor(rootEl, conversationLog) {
    this.root = rootEl;
    this.log = conversationLog;
    this._render();
    this._wire();
  }

  _render() {
    this.root.innerHTML = `
      <h2>Conversation</h2>
      <p class="hint" id="convSummary">0 turns, window=20</p>
      <div class="conv-list" id="convList" style="max-height: 280px; overflow-y: auto; font-family: 'Consolas', monospace; font-size: 0.78em;"></div>
      <div class="form-row" style="margin-top:8px; display:flex; gap:6px;">
        <button class="btn btn-secondary" id="convClear">Clear context</button>
        <button class="btn btn-secondary" id="convRefresh">Refresh</button>
      </div>
    `;
  }

  _wire() {
    this.root.querySelector('#convClear').addEventListener('click', () => {
      if (confirm('¿Borrar todo el contexto de la conversación? El LLM olvidará la historia.')) {
        this.log.clear();
        this._refresh();
      }
    });
    this.root.querySelector('#convRefresh').addEventListener('click', () => this._refresh());
    this.log.on('turnAdded', () => this._refresh());
    this.log.on('turnCanceled', () => this._refresh());
    this.log.on('cleared', () => this._refresh());
    this.log.on('slid', () => this._refresh());
    this._refresh();
  }

  _refresh() {
    const list = this.root.querySelector('#convList');
    if (!list) return;
    const snap = this.log.snapshot();
    const summary = `${snap.turnCount} turns (+${snap.syntheticCount} synth), window=${snap.windowSize}, cancel=${snap.cancelMode}`;
    const sumEl = this.root.querySelector('#convSummary');
    if (sumEl) sumEl.textContent = summary;

    const items = [];
    for (const t of [...snap.turns, ...snap.synthetic].slice().sort((a, b) => a.createdAtMs - b.createdAtMs)) {
      const status = t.status;
      const statusClass = status === 'canceled' ? 'canceled' : (status === 'streaming' ? 'streaming' : '');
      const role = t.role === 'user' ? 'U' : t.role === 'assistant' ? 'A' : 'S';
      const preview = (t.content || '').slice(0, 80).replace(/</g, '&lt;');
      const badge = t.cancelReason ? ` <span style="color:#ffa502;">[canceled: ${t.cancelReason}]</span>` : '';
      items.push(
        `<div class="conv-item ${statusClass}" data-id="${t.id}" style="padding:3px 6px; border-bottom:1px solid #222;">
          <span style="color:#00d4ff;">[${role}]</span> ${preview}${preview.length >= 80 ? '...' : ''}${badge}
        </div>`,
      );
    }
    list.innerHTML = items.join('') || '<div style="color:#888;">(no turns yet)</div>';
  }
}
