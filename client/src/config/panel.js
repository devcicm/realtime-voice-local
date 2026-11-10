// ConfigPanel — UI for the runtime configuration.
//
// Reads from ConfigStore and writes back via ConfigStore.set(...).
// This is the single source of truth for what the user can change at
// runtime without restarting the servers.

import { ConfigStore } from './store.js';

const FIELDS = [
  { key: 'mode',          label: 'Modo realtime',     type: 'select',
    options: [
      { value: 'local-chain',     label: 'Local (STT → LLM → TTS)' },
      { value: 'openai-realtime', label: 'OpenAI Realtime (gpt-realtime)' },
    ] },
  { key: 'transport',     label: 'Transporte',        type: 'select',
    options: [
      { value: 'websocket', label: 'WebSocket' },
      { value: 'webrtc',    label: 'WebRTC' },
    ] },
  { key: 'sttUrl',        label: 'STT URL',           type: 'text' },
  { key: 'llmUrl',        label: 'LLM URL',           type: 'text' },
  { key: 'ttsWsUrl',      label: 'TTS WebSocket',     type: 'text' },
  { key: 'llmProvider',   label: 'LLM provider',      type: 'select',
    options: [
      { value: 'lmstudio', label: 'LM Studio' },
      { value: 'ollama',   label: 'Ollama' },
      { value: 'openai',   label: 'OpenAI' },
    ] },
  { key: 'llmModel',      label: 'LLM model',         type: 'select',
    options: [
      { value: 'qwen3.5-0.8b-mtp',           label: 'Qwen3.5 0.8B (MTP)' },
      { value: 'qwen2.5:0.5b-instruct-q8_0', label: 'Qwen2.5 0.5B (Q8_0)' },
      { value: 'qwen2.5:1.5b-instruct-q8_0', label: 'Qwen2.5 1.5B (Q8_0)' },
      { value: 'google/gemma-3n-e4b',        label: 'Gemma 3N E4B' },
      { value: 'google/gemma-4-e2b',         label: 'Gemma 4 E2B' },
      { value: 'google/gemma-2-2b',          label: 'Gemma 2 2B' },
    ] },
  { key: 'ttsBackend',    label: 'TTS backend',       type: 'select',
    options: [
      { value: 'piper',  label: 'Piper' },
      { value: 'xtts',   label: 'XTTS (voice cloning)' },
    ] },
  { key: 'voice',         label: 'Voz',               type: 'text' },
  { key: 'sttModel',      label: 'STT model',         type: 'select',
    options: ['parakeet', 'tiny', 'base', 'small', 'medium', 'large-v3', 'turbo'].map(v => ({ value: v, label: v })) },
  { key: 'syncMode',      label: 'Sync mode',         type: 'select',
    options: [
      { value: 'free',     label: 'Libre (sin gatekeeper)' },
      { value: 'balanced', label: 'Balanceado (recomendado)' },
      { value: 'strict',   label: 'Estricto' },
      { value: 'burst',    label: 'Ráfaga' },
    ] },
  { key: 'syncThresholdMs', label: 'Sync threshold (ms)', type: 'number' },
  { key: 'maxTokens',     label: 'Max tokens',        type: 'number' },
  { key: 'systemPrompt',  label: 'System prompt',     type: 'textarea', rows: 3 },
  { key: 'cancelOnInterrupt', label: 'Borrar contexto al interrumpir (privacy mode)',
    type: 'checkbox',
    description: 'Por defecto los turnos interrumpidos se preservan (continuidad).' },
];

export class ConfigPanel {
  constructor(rootEl) {
    this.root = rootEl;
    this._render();
    ConfigStore.onChange(() => this._syncFromStore());
  }

  _render() {
    const cfg = ConfigStore.get();
    this.root.innerHTML = `
      <h2>Configuración</h2>
      <p class="hint">Cambios persisten en localStorage. API keys sólo en memoria salvo que actives "recordar".</p>
      <div class="form"></div>
      <div class="form-row api-key-row" style="display:${cfg.mode === 'openai-realtime' ? 'block' : 'none'}">
        <label>OpenAI API key (en memoria)</label>
        <input type="password" id="openaiApiKey" placeholder="sk-..." value="${cfg.openaiApiKey || ''}" autocomplete="off" />
        <label class="check"><input type="checkbox" id="rememberApiKey" ${cfg.rememberApiKey ? 'checked' : ''} /> Recordar en localStorage</label>
        <button id="saveApiKey" class="btn">Guardar key</button>
      </div>
      <div class="form-row">
        <button id="resetConfig" class="btn btn-secondary">Restablecer defaults</button>
      </div>
    `;
    const form = this.root.querySelector('.form');
    for (const f of FIELDS) {
      const row = document.createElement('div');
      row.className = 'form-row';
      const id = `cfg-${f.key}`;
      const label = document.createElement('label');
      label.textContent = f.label;
      label.htmlFor = id;
      row.appendChild(label);
      let input;
      if (f.type === 'select') {
        input = document.createElement('select');
        for (const opt of f.options) {
          const o = document.createElement('option');
          o.value = opt.value;
          o.textContent = opt.label;
          if (cfg[f.key] === opt.value) o.selected = true;
          input.appendChild(o);
        }
        const cur = cfg[f.key];
        if (cur && !f.options.some(o => o.value === cur)) {
          const o = document.createElement('option');
          o.value = cur;
          o.textContent = `${cur} (custom)`;
          o.selected = true;
          input.appendChild(o);
        }
      } else if (f.type === 'textarea') {
        input = document.createElement('textarea');
        input.rows = f.rows || 3;
        input.value = cfg[f.key] || '';
      } else {
        input = document.createElement('input');
        input.type = f.type;
        input.value = cfg[f.key] ?? '';
      }
      input.id = id;
      input.addEventListener('change', () => {
        const val = f.type === 'number' ? Number(input.value) : input.value;
        ConfigStore.set({ [f.key]: val });
      });
      row.appendChild(input);
      form.appendChild(row);
    }

    this.root.querySelector('#saveApiKey').addEventListener('click', () => {
      const apiKey = this.root.querySelector('#openaiApiKey').value.trim();
      const remember = this.root.querySelector('#rememberApiKey').checked;
      ConfigStore.set({ rememberApiKey: remember });
      ConfigStore.setApiKey(apiKey);
    });

    this.root.querySelector('#resetConfig').addEventListener('click', () => {
      if (confirm('¿Restablecer toda la configuración a los defaults?')) {
        ConfigStore.reset();
      }
    });
  }

  _syncFromStore() {
    const cfg = ConfigStore.get();
    for (const f of FIELDS) {
      const el = this.root.querySelector(`#cfg-${f.key}`);
      if (el && document.activeElement !== el) el.value = cfg[f.key] ?? '';
    }
    const apiKeyRow = this.root.querySelector('.api-key-row');
    if (apiKeyRow) {
      apiKeyRow.style.display = cfg.mode === 'openai-realtime' ? 'block' : 'none';
    }
  }
}
