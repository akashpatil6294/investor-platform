import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setupDOM, loadScripts, makeApiMock, flush } from './helpers.js';

let api;

beforeEach(async () => {
  vi.resetModules();
  setupDOM();
  api = makeApiMock();
  loadScripts();
  await flush();
});

const submit = form => form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
const click = el => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));

describe('research assistant chat', () => {
  it('sends the question with scope and renders the tier badge, sources and suggestions', async () => {
    const input = document.getElementById('chatInput');
    input.value = 'What was Alpha revenue in 2024?';
    submit(document.getElementById('chatForm'));
    await flush();
    expect(api.stream).toHaveBeenCalledTimes(1);
    const call = api.calls.find(c => c.path === '/api/chat/stream');
    expect(call.body).toMatchObject({ question: 'What was Alpha revenue in 2024?', scope: 'company' });
    const log = document.getElementById('chatLog').textContent;
    expect(log).toContain('Stored metrics');            // tier badge
    expect(log).toContain('$3,910M');                    // answer
    expect(log).toContain('2024_Alpha.md · page 2');     // source citation
    const suggestions = [...document.querySelectorAll('#chatSuggestions button')].map(b => b.textContent);
    expect(suggestions).toContain('What drove revenue?');
  });

  it('renders typed errors with a retry control for retryable failures', async () => {
    api.stream.mockImplementationOnce(async (body, onEvent) => {
      onEvent({ type: 'error', code: 'model_busy', message: 'The model is busy. Retry in a moment.', retryable: true });
    });
    const input = document.getElementById('chatInput');
    input.value = 'What was Alpha revenue?';
    submit(document.getElementById('chatForm'));
    await flush();
    const log = document.getElementById('chatLog');
    expect(log.textContent).toContain('The model is busy');
    const retry = [...log.querySelectorAll('button')].find(b => b.textContent === 'Retry');
    expect(retry).toBeTruthy();
  });

  it('non-retryable failures show no retry button', async () => {
    api.stream.mockImplementationOnce(async (body, onEvent) => {
      onEvent({ type: 'error', code: 'not_configured', message: 'The assistant has no working model configured. Check the server settings.', retryable: false });
    });
    const input = document.getElementById('chatInput');
    input.value = 'What was Alpha revenue?';
    submit(document.getElementById('chatForm'));
    await flush();
    const log = document.getElementById('chatLog');
    expect(log.textContent).toContain('no working model');
    expect([...log.querySelectorAll('button')].find(b => b.textContent === 'Retry')).toBeUndefined();
  });

  it('Stop aborts the stream while busy', async () => {
    const input = document.getElementById('chatInput');
    input.value = 'Long question?';
    submit(document.getElementById('chatForm'));
    const stop = document.getElementById('stopBtn');
    expect(stop.hidden).toBe(false);
    click(stop);
    await flush();
    const call = api.calls.find(c => c.path === '/api/chat/stream');
    expect(call.signal.aborted).toBe(true);
  });

  it('feedback buttons store helpful / not-helpful with the question', async () => {
    const input = document.getElementById('chatInput');
    input.value = 'What was Alpha revenue?';
    submit(document.getElementById('chatForm'));
    await flush();
    const helpful = [...document.querySelectorAll('#chatLog button')].find(b => b.textContent === 'Helpful');
    click(helpful);
    await flush();
    expect(api.calls.some(c => c.path === '/api/chat/feedback' && c.body.helpful === true)).toBe(true);
    expect(document.getElementById('chatLog').textContent).toContain('Marked helpful');
  });

  it('copy answer copies the model text', async () => {
    const input = document.getElementById('chatInput');
    input.value = 'What was Alpha revenue?';
    submit(document.getElementById('chatForm'));
    await flush();
    const copy = [...document.querySelectorAll('#chatLog button')].find(b => b.textContent === 'Copy answer');
    click(copy);
    await flush();
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('Alpha revenue was **$3,910M**.');
  });

  it('clear conversation empties the log', async () => {
    const input = document.getElementById('chatInput');
    input.value = 'Hello';
    submit(document.getElementById('chatForm'));
    await flush();
    click(document.getElementById('clearChatBtn'));
    await flush();
    expect(document.getElementById('chatLog').textContent).not.toContain('Alpha revenue');
    expect(sessionStorage.getItem('ip.conversation')).toBe('[]');
  });

  it('setScope updates the chip with the company and fiscal year', () => {
    window.ResearchAssistant.setScope('Alpha Ltd', 2024);
    expect(document.getElementById('chatScopeChip').textContent).toContain('Alpha Ltd · FY2024');
  });
});
