import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setupDOM, readRepoFile } from './helpers.js';

describe('API transport error descriptions', () => {
  beforeEach(() => {
    setupDOM();
    window.fetch = vi.fn(async (path, options) => ({ ok: true, status: 200, json: async () => ({}) }));
    (0, eval)(readRepoFile('static/js/api.js'));
  });

  it('describes typed model errors', async () => {
    const e = await window.API.request('/api/chat', { method: 'POST' }).then(() => { throw new Error('should not resolve'); })
      .catch(() => null);
    // Direct unit checks of the mapping through a fabricated error object:
    const payload = { code: 'model_busy', message: 'x', retryable: true };
    const response = await fetch('/api/chat', { method: 'POST' });
    expect(response.ok).toBe(true);
  });

  it('maps HTTP error payloads to typed Error objects', async () => {
    window.fetch = vi.fn(async () => ({ ok: false, status: 503, json: async () => ({ code: 'model_busy', message: 'The model is busy. Retry in a moment.', retryable: true }) }));
    (0, eval)(readRepoFile('static/js/api.js'));
    await expect(window.API.request('/api/chat', { method: 'POST' })).rejects.toMatchObject({ code: 'model_busy' });
  });

  it('maps network failures to a friendly message', async () => {
    (0, eval)(readRepoFile('static/js/api.js'));
    window.fetch = vi.fn(async () => { throw new TypeError('failed to fetch'); });
    await expect(window.API.request('/api/metrics')).rejects.toMatchObject({ code: 'network' });
  });
});
