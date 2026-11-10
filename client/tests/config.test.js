// Vitest tests for ConfigStore.
import { describe, it, expect, beforeEach, vi } from 'vitest';

describe('ConfigStore', () => {
  beforeEach(() => {
    localStorage.clear();
    // Reset module state by re-importing.
    vi.resetModules();
  });

  it('returns defaults when localStorage is empty', async () => {
    const { ConfigStore } = await import('../src/config/store.js');
    const cfg = ConfigStore.get();
    expect(cfg.mode).toBe('local-chain');
    expect(cfg.voice).toBe('es_ES/davefx-medium');
  });

  it('persists changes to localStorage', async () => {
    const { ConfigStore } = await import('../src/config/store.js');
    ConfigStore.set({ voice: 'em_alex' });
    const stored = JSON.parse(localStorage.getItem('kokoro-tts-config'));
    expect(stored.voice).toBe('em_alex');
  });

  it('loads persisted values on next get', async () => {
    const { ConfigStore: A } = await import('../src/config/store.js');
    A.set({ voice: 'claude-high' });
    vi.resetModules();
    const { ConfigStore: B } = await import('../src/config/store.js');
    expect(B.get().voice).toBe('claude-high');
  });

  it('notifies listeners on set', async () => {
    const { ConfigStore } = await import('../src/config/store.js');
    const cb = vi.fn();
    ConfigStore.onChange(cb);
    ConfigStore.set({ voice: 'test-voice' });
    expect(cb).toHaveBeenCalledOnce();
    const cfg = cb.mock.calls[0][0];
    expect(cfg.voice).toBe('test-voice');
  });

  it('does not persist API key when remember is false', async () => {
    const { ConfigStore } = await import('../src/config/store.js');
    ConfigStore.setApiKey('sk-secret');
    const stored = JSON.parse(localStorage.getItem('kokoro-tts-config'));
    expect(stored.openaiApiKey).toBeUndefined();
  });

  it('persists API key when remember is true', async () => {
    const { ConfigStore } = await import('../src/config/store.js');
    ConfigStore.set({ rememberApiKey: true });
    ConfigStore.setApiKey('sk-remember-me');
    const stored = JSON.parse(localStorage.getItem('kokoro-tts-config'));
    expect(stored.openaiApiKey).toBe('sk-remember-me');
  });

  it('reset restores defaults', async () => {
    const { ConfigStore } = await import('../src/config/store.js');
    ConfigStore.set({ voice: 'override' });
    ConfigStore.reset();
    expect(ConfigStore.get().voice).toBe('es_ES/davefx-medium');
  });

  it('set is partial — only changes provided keys', async () => {
    const { ConfigStore } = await import('../src/config/store.js');
    const before = ConfigStore.get();
    ConfigStore.set({ voice: 'new-voice' });
    const after = ConfigStore.get();
    expect(after.voice).toBe('new-voice');
    expect(after.mode).toBe(before.mode);
    expect(after.llmModel).toBe(before.llmModel);
  });
});
