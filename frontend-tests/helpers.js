/* Shared harness: real dashboard.html + real dashboard.js/chat.js against a
   fully mocked window.API. Captures every API call for assertion. */
import { readFileSync } from 'node:fs';
import { vi } from 'vitest';

export const CATALOG = [
  {
    company: 'Alpha Ltd', years: [
      { year: 2024, version: 1, currency: 'USD', unit: 'million', basis: 'consolidated', period_end: '2024-09-28', extraction_status: 'ok', source_file: '2024_Alpha.md', updated_at: '2026-10-01T00:00:00Z', data_quality: { metrics_present: 6, metrics_total: 6, missing: [], needs_review: false, manual_overrides: 0 } },
      { year: 2023, version: 1, currency: 'USD', unit: 'million', basis: 'consolidated', period_end: '2023-09-30', extraction_status: 'ok', source_file: '2023_Alpha.md', updated_at: '2026-10-01T00:00:00Z', data_quality: { metrics_present: 6, metrics_total: 6, missing: [], needs_review: false, manual_overrides: 0 } },
    ],
  },
  {
    company: 'Beta Industries', years: [
      { year: 2024, version: 1, currency: 'INR', unit: 'crore', basis: 'standalone', period_end: '2024-03-31', extraction_status: 'needs_review', source_file: '2024_Beta.md', updated_at: '2026-10-02T00:00:00Z', data_quality: { metrics_present: 4, metrics_total: 6, missing: ['cash_flow', 'total_liabilities'], needs_review: true, manual_overrides: 0 } },
    ],
  },
];

export const ROWS = [
  { company: 'Alpha Ltd', year: 2024, currency: 'USD', unit: 'million', basis: 'consolidated', period_end: '2024-09-28', extraction_status: 'ok', source_file: '2024_Alpha.md', revenue: 3910, net_income: 937, operating_income: 1232, cash_flow: 1182, total_assets: 3649, total_liabilities: 3080, derived: { operating_margin: 0.315, net_margin: 0.239, liabilities_to_assets: 0.844, cash_conversion: 1.262 }, checks: {}, metric_sources: { revenue: { document: '2024_Alpha.md', page: 2, quote: 'Total net sales 3,910', status: 'extracted' } } },
  { company: 'Alpha Ltd', year: 2023, currency: 'USD', unit: 'million', basis: 'consolidated', period_end: '2023-09-30', extraction_status: 'ok', source_file: '2023_Alpha.md', revenue: 3832, net_income: 969, operating_income: 1143, cash_flow: 1105, total_assets: 3525, total_liabilities: 2904, derived: {}, checks: {}, metric_sources: {} },
  { company: 'Beta Industries', year: 2024, currency: 'INR', unit: 'crore', basis: 'standalone', period_end: '2024-03-31', extraction_status: 'needs_review', source_file: '2024_Beta.md', revenue: 1790, net_income: 530, operating_income: null, cash_flow: null, total_assets: 1830, total_liabilities: null, derived: { operating_margin: null, net_margin: 0.296, liabilities_to_assets: null, cash_conversion: null }, checks: { cash_flow: 'Missing value' }, metric_sources: { total_assets: { document: '2024_Beta.md', page: 4, quote: 'Total assets 1,830', status: 'manual_override', updated_at: '2026-10-03T00:00:00Z', previous_value: 1831 } } },
];

export function readRepoFile(rel) {
  return readFileSync(new URL('../' + rel, import.meta.url), 'utf8');
}

export function setupDOM() {
  document.documentElement.innerHTML = readRepoFile('template/dashboard.html');
  HTMLDialogElement.prototype.showModal = vi.fn(function () { this.open = true; });
  HTMLDialogElement.prototype.close = vi.fn(function () { this.open = false; });
  window.matchMedia = window.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {} }));
  window.URL.createObjectURL = vi.fn(() => 'blob:mock');
  window.URL.revokeObjectURL = vi.fn();
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: vi.fn(async () => {}) }, configurable: true });
}

export function loadScripts() {
  (0, eval)(readRepoFile('static/js/api.js'));
  (0, eval)(readRepoFile('static/js/chat.js'));
  (0, eval)(readRepoFile('static/js/dashboard.js'));
}

export function makeApiMock() {
  const calls = [];
  const request = vi.fn(async (path, options = {}) => {
    calls.push({ method: (options.method || 'GET').toUpperCase(), path, body: options.body ? JSON.parse(options.body) : null });
    if (path === '/api/notes' && (options.method || 'GET') === 'GET') return { data: [] };
    if (path.startsWith('/api/notes') && options.method === 'POST') return { success: true };
    if (path.startsWith('/api/insights/')) {
      return { payload: { growth_drivers: [{ title: 'Services growth', detail: 'Services expanded.', page: 3 }], risk_factors: [] }, source: '2024_Alpha.md', version: 'v1' };
    }
    if (path === '/api/ingest' && (options.method || 'GET') === 'GET') {
      return { jobs: [
        { job_id: 'jobok1', kind: 'ingest', status: 'completed', filename: 'alpha.pdf', company: 'Alpha Ltd' },
        { job_id: 'jobbad', kind: 'ingest', status: 'failed', filename: 'beta.pdf', error: 'Extraction failed: no financial KPIs could be located.' },
      ] };
    }
    if (/\/api\/ingest\/jobbad$/.test(path)) {
      return { job_id: 'jobbad', kind: 'ingest', status: 'failed', stage: 'extracting', filename: 'beta.pdf', log: [], error: 'failed' };
    }
    return { success: true };
  });
  const api = {
    request,
    calls,
    getHealth: vi.fn(async () => ({ status: 'Ready', ready: true, checks: { postgres: true } })),
    getCompanies: vi.fn(async () => ({ companies: CATALOG })),
    getMetrics: vi.fn(async () => ({ rows: ROWS })),
    sendFeedback: vi.fn(async payload => { calls.push({ method: 'POST', path: '/api/chat/feedback', body: payload }); return { success: true }; }),
    stream: vi.fn(async (body, onEvent, signal) => {
      calls.push({ method: 'POST', path: '/api/chat/stream', body, signal });
      onEvent({ type: 'done', answer: 'Alpha revenue was **$3,910M**.', sources: [{ document: '2024_Alpha.md', page: 2, snippet: 'Total net sales' }], scope: 'Alpha Ltd · FY2024', warning: null, answer_source: 'Stored metrics', company: 'Alpha Ltd', year: 2024, suggestions: ['What drove revenue?'] });
    }),
    describeError: e => e.message || 'error',
  };
  window.API = api;
  return api;
}

export function flush(times = 6) {
  return new Promise(resolve => {
    let n = 0;
    const step = () => (n++ < times ? setTimeout(step, 0) : resolve());
    setTimeout(step, 0);
  });
}

export function jobWithId(id) {
  return { job_id: id, kind: 'ingest', status: 'queued', stage: 'converting', filename: 'x.pdf', log: [] };
}
