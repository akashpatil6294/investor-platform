import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setupDOM, loadScripts, makeApiMock, flush, CATALOG, ROWS } from './helpers.js';

let api;

beforeEach(async () => {
  vi.resetModules();
  setupDOM();
  api = makeApiMock();
  loadScripts();
  await flush();
});

const click = el => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
const submit = form => form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
const change = input => input.dispatchEvent(new window.Event('change', { bubbles: true }));

describe('dashboard load and render', () => {
  it('builds the sidebar, year selector and comparison table from /api/companies and /api/metrics', async () => {
    expect(api.getCompanies).toHaveBeenCalled();
    expect(api.getMetrics).toHaveBeenCalled();
    const coverage = document.getElementById('coverageList').textContent;
    expect(coverage).toContain('Alpha Ltd');
    expect(coverage).toContain('Beta Industries');
    expect(coverage).not.toContain('AAPL'); // no hard-coded ticker map
    const years = [...document.getElementById('yearSelect').options].map(o => o.value);
    expect(years).toEqual(['2024', '2023']);
    const table = document.getElementById('comparison');
    expect(table.querySelectorAll('thead th').length).toBe(5); // metric + 4 selected companies
    expect(table.textContent).toContain('3,910');
  });

  it('marks not-reported values and the needs-review status honestly', () => {
    const table = document.getElementById('comparison');
    expect(table.textContent).toContain('Not reported in this document');
    expect(table.textContent).toContain('needs review');
    expect(table.textContent).toContain('Manual override');
  });

  it('changing the fiscal year re-renders the table for that year', async () => {
    const select = document.getElementById('yearSelect');
    select.value = '2023';
    change(select);
    await flush();
    expect(document.getElementById('comparison').textContent).toContain('3,832');
  });

  it('shows a retry-able error when the API is unreachable', async () => {
    api.getMetrics.mockRejectedValueOnce(new Error('Cannot connect to the server. Check your connection and retry.'));
    window.dispatchEvent(new window.Event('metrics-updated'));
    await flush();
    const box = document.getElementById('dataError');
    expect(box.hidden).toBe(false);
    expect(box.textContent).toContain('Retry');
  });
});

describe('selection and comparison controls', () => {
  it('toggling a coverage checkbox rebuilds the comparison columns', async () => {
    const checkbox = document.querySelector('#coverageList input[type=checkbox]');
    checkbox.checked = false;
    change(checkbox);
    await flush();
    const columns = document.getElementById('comparison').querySelectorAll('thead th').length;
    expect(columns).toBe(4); // 3 remaining + metric corner
  });

  it('exporting CSV downloads a file for the selected companies and year', () => {
    click(document.getElementById('exportCsv'));
    const anchor = [...document.querySelectorAll('a')].find(a => a.download?.includes('.csv'));
    expect(anchor.download).toBe('comparison-FY2024.csv');
  });

  it('the metric select re-renders the chart title', async () => {
    const select = document.getElementById('metricSelect');
    select.value = 'net_income';
    change(select);
    expect(document.getElementById('chartTitle').textContent).toContain('Net income');
  });

  it('the theme toggle switches and persists the theme', () => {
    click(document.getElementById('themeToggle'));
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(window.localStorage.getItem('ip.theme')).toBe('dark');
  });
});

describe('review drawer and audit actions', () => {
  it('opens the evidence drawer with page evidence and audit info, and saves a correction', async () => {
    const valueButton = [...document.querySelectorAll('#comparison td button')].find(b => b.textContent.includes('3,910'));
    click(valueButton);
    const dialog = document.querySelector('dialog.review-dialog');
    expect(dialog).toBeTruthy();
    expect(dialog.textContent).toContain('Page 2');
    const form = dialog.querySelector('form');
    form.querySelector('input[type=number]').value = '3911';
    submit(form);
    await flush();
    const patch = api.calls.find(c => c.method === 'PATCH' && c.path.startsWith('/api/metrics/Alpha'));
    expect(patch).toBeTruthy();
    expect(patch.body).toMatchObject({ metric: 'revenue', value: 3911, source: 'manual' });
  });

  it('Mark as reviewed sends a reviewed patch without changing values', async () => {
    const valueButton = [...document.querySelectorAll('#comparison td button')].find(b => b.textContent.includes('3,910'));
    click(valueButton);
    const dialog = document.querySelector('dialog.review-dialog');
    [...dialog.querySelectorAll('button')].find(b => b.textContent === 'Mark as reviewed').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    await flush();
    const patch = api.calls.find(c => c.method === 'PATCH' && c.path.startsWith('/api/metrics/Alpha'));
    expect(patch.body).toMatchObject({ metric: 'revenue', reviewed: true });
  });

  it('Re-extract queues a re-extraction job', async () => {
    const valueButton = [...document.querySelectorAll('#comparison td button')].find(b => b.textContent.includes('3,910'));
    click(valueButton);
    const dialog = document.querySelector('dialog.review-dialog');
    [...dialog.querySelectorAll('button')].find(b => b.textContent === 'Re-extract').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
    await flush();
    expect(api.calls.some(c => c.method === 'POST' && c.path.endsWith('/reextract'))).toBe(true);
  });
});

describe('company management', () => {
  it('Edit aliases posts the alias list to /api/aliases', async () => {
    const buttons = [...document.querySelectorAll('.company-actions button')];
    click(buttons.find(b => b.textContent === 'Edit aliases'));
    const dialog = document.querySelector('dialog.review-dialog');
    const input = dialog.querySelector('input');
    input.value = 'Alpha, ALPH';
    submit(dialog.querySelector('form'));
    await flush();
    const post = api.calls.find(c => c.path === '/api/aliases');
    expect(post.body).toMatchObject({ company: 'Alpha Ltd', aliases: ['Alpha', 'ALPH'] });
  });

  it('Remove report asks for confirmation and deletes with confirm=true', async () => {
    const buttons = [...document.querySelectorAll('.company-actions button')];
    click(buttons.find(b => b.textContent === 'Remove report'));
    const dialog = document.querySelector('dialog.review-dialog');
    click([...dialog.querySelectorAll('button')].find(b => b.textContent === 'Delete report'));
    await flush();
    const del = api.calls.find(c => c.method === 'DELETE' && c.path.startsWith('/api/metrics/Alpha'));
    expect(del.path).toContain('confirm=true');
  });
});

describe('ingestion job list', () => {
  it('renders recent jobs with retry and diagnostics for failed ones', async () => {
    await flush();
    const jobList = document.getElementById('jobList').textContent;
    expect(jobList).toContain('alpha.pdf');
    expect(jobList).toContain('beta.pdf');
    const link = [...document.querySelectorAll('#jobList a')].find(a => a.href.includes('jobbad') && a.href.includes('diagnostics'));
    expect(link).toBeTruthy();
    const retry = [...document.querySelectorAll('#jobList button')].find(b => b.textContent === 'Retry');
    click(retry);
    await flush();
    expect(api.calls.some(c => c.method === 'POST' && c.path.endsWith('/retry'))).toBe(true);
  });
});

describe('command palette', () => {
  it('opens with Ctrl+K, lists companies and routes an Ask to the assistant', async () => {
    document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true, cancelable: true }));
    const dialog = document.getElementById('commandDialog');
    expect(window.HTMLDialogElement.prototype.showModal).toHaveBeenCalled();
    const input = document.getElementById('commandInput');
    input.value = 'alp';
    input.dispatchEvent(new window.Event('input', { bubbles: true }));
    const results = [...document.getElementById('commandResults').querySelectorAll('button')];
    expect(results.some(b => b.textContent === 'Open Alpha Ltd')).toBe(true);
    input.value = 'operating margin trend';
    input.dispatchEvent(new window.Event('input', { bubbles: true }));
    const ask = [...document.getElementById('commandResults').querySelectorAll('button')].find(b => b.textContent.startsWith('Ask:'));
    click(ask);
    await flush();
    expect(api.stream).toHaveBeenCalled();
  });
});
