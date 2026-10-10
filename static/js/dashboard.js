/* Investor Platform desk. Rendering uses DOM nodes only — data and model text
   never enter innerHTML. Companies, years and statuses come from /api/companies. */
(() => {
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined && text !== null) n.textContent = text; if (cls) n.className = cls; return n; };
  const fields = { revenue: 'Revenue', operating_income: 'Operating income', net_income: 'Net income', cash_flow: 'Operating cash flow', total_assets: 'Total assets', total_liabilities: 'Total liabilities' };
  const ratioLabels = { operating_margin: 'Operating margin', net_margin: 'Net margin', liabilities_to_assets: 'Liabilities / assets', cash_conversion: 'Cash conversion' };
  const multipliers = { unit: 1, units: 1, thousand: 1e3, lakh: 1e5, crore: 1e7, million: 1e6, billion: 1e9 };
  let notes = {}, rows = [], catalog = [], selected = new Set(), active = null, year = null, insightToken = 0;

  const num = v => v === null || v === undefined || v === '' ? null : Number.isFinite(Number(v)) ? Number(v) : null;
  const money = (v, row) => num(v) === null ? 'Not reported'
    : `${row?.currency || 'Currency unconfirmed'} ${Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 })} ${row?.unit || 'unit unconfirmed'}`;
  const base = (row, field) => num(row?.[field]) === null || !row?.unit ? null : num(row[field]) * (multipliers[row.unit] || 1);
  const comparable = (a, b) => a && b && a.currency && a.currency === b.currency && a.basis === b.basis && a.unit && b.unit;
  const companyNames = () => catalog.map(c => c.company);
  function toast(text) { $('toast').textContent = text; $('toast').hidden = false; setTimeout(() => $('toast').hidden = true, 6000); }
  function current(company, y = year) { return rows.find(r => r.company === company && Number(r.year) === Number(y)); }
  function catalogEntry(company) { return catalog.find(c => c.company === company); }
  function yearInfo(company, y) { return catalogEntry(company)?.years.find(e => Number(e.year) === Number(y)); }
  function availableYears(company) { return (catalogEntry(company)?.years || []).map(e => Number(e.year)); }
  function source(company) { return current(company)?.source_file || `${year}_${company}.md`; }
  function label(text, cls) { return el('span', text, 'state-label ' + (cls || '')); }

  /* ---------------- status pill ---------------- */
  async function health() {
    try {
      const p = await API.getHealth();
      $('headerStatusText').textContent = p.status;
      $('headerStatus').title = 'Dependency checks: ' + Object.entries(p.checks || {})
        .map(([k, v]) => `${k}: ${v ? 'ok' : 'down'}`).join(', ') + ' — click to refresh';
    } catch (e) {
      $('headerStatusText').textContent = e.code === 'timeout' ? 'Status timed out' : 'Server unavailable';
    }
  }

  /* ---------------- sidebar ---------------- */
  function coverage() {
    const list = $('coverageList'); list.replaceChildren();
    companyNames().forEach(c => {
      const entry = catalogEntry(c);
      const row = el('div', null, 'coverage-row');
      const check = el('input'); check.type = 'checkbox'; check.checked = selected.has(c);
      check.setAttribute('aria-label', 'Compare ' + c);
      check.onchange = () => {
        if (check.checked && selected.size >= 4) { check.checked = false; toast('Select up to four companies.'); return; }
        check.checked ? selected.add(c) : selected.delete(c); renderTable(); chart();
      };
      const button = el('button'); button.type = 'button';
      button.append(el('div', c));
      const years = availableYears(c);
      button.append(el('span', years.length ? years.map(y => 'FY' + y).join(' · ') : 'no reports', 'ticker'));
      const status = yearInfo(c, year)?.extraction_status;
      button.append(el('small', status ? status.replaceAll('_', ' ') : 'No report for selected year'));
      button.setAttribute('aria-current', String(c === active));
      button.onclick = () => choose(c);
      row.append(check, button);
      list.append(row);
    });
    if (!companyNames().length) list.append(el('p', 'No reports yet. Add an annual-report PDF to start.', 'muted'));
  }

  /* ---------------- comparison table ---------------- */
  function spark(prior, value, labelText) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 50 14'); svg.setAttribute('width', '50'); svg.setAttribute('height', '14');
    svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', labelText);
    const line = document.createElementNS(svg.namespaceURI, 'path');
    line.setAttribute('d', `M2 ${prior > value ? 2 : 12} L48 ${prior > value ? 12 : 2}`);
    line.setAttribute('stroke', 'var(--muted)'); line.setAttribute('fill', 'none');
    svg.append(line); return svg;
  }
  function renderTable() {
    const box = $('comparison'); box.replaceChildren();
    const names = [...selected];
    if (!names.length) { box.append(el('p', 'Select companies in the coverage list to build a comparison.', 'skeleton')); return; }
    const table = el('table');
    table.append(el('caption', `Reported financials for FY${year}; currency and unit shown per company`, 'sr-only'));
    const head = el('thead'), hr = el('tr');
    const corner = el('th', 'Reported metric'); corner.scope = 'col'; hr.append(corner);
    names.forEach(c => {
      const r = current(c), th = el('th', c); th.scope = 'col';
      th.append(el('small', `${r?.currency || '?'} · ${r?.unit || '?'} · ${r?.basis || 'basis unconfirmed'}`));
      const review = el('button', r?.extraction_status?.replaceAll('_', ' ') || 'No report');
      review.onclick = () => reviewMetrics(c); th.append(review); hr.append(th);
    });
    head.append(hr); table.append(head);
    const body = el('tbody');
    Object.entries(fields).forEach(([field, labelText]) => {
      const tr = el('tr'), th = el('th', labelText); th.scope = 'row'; tr.append(th);
      names.forEach(c => {
        const row = current(c), value = num(row?.[field]), td = el('td');
        const b = el('button', money(value, row)); b.title = `${money(value, row)} — open the evidence drawer`;
        b.onclick = () => reviewMetrics(c, field); td.append(b);
        if (value === null) {
          const status = row?.extraction_status;
          td.append(label(status === 'failed' ? 'Extraction failed — review available' : 'Not reported in this document'));
        }
        if (row?.checks?.[field]) { const badge = el('span', 'check', 'check'); badge.title = row.checks[field]; badge.setAttribute('aria-label', row.checks[field]); td.append(badge); }
        const prior = current(c, year - 1), old = base(prior, field), now = base(row, field);
        if (value !== null && old !== null && old !== 0 && comparable(row, prior)) {
          const delta = (now - old) / Math.abs(old) * 100;
          td.append(el('small', `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}% YoY`, delta >= 0 ? 'positive' : 'negative'));
          td.append(spark(old, now, `${c} ${labelText}: ${money(prior[field], prior)} to ${money(value, row)}`));
        }
        const provenance = row?.metric_sources?.[field] || {};
        if (provenance.status === 'manual_override' || provenance.source === 'manual') td.append(label('Manual override'));
        else if (provenance.source === 'web') td.append(label('From the web'));
        if (provenance.derived) td.append(label('Derived'));
        if (prior && yearInfo(c, year - 1)) td.append(label(`Comparative FY${year - 1}`, 'comparative'));
        tr.append(td);
      });
      body.append(tr);
    });
    table.append(body); box.append(table);
    $('dataFootnote').textContent = `FY${year}. Values use each report's own currency, unit and basis. Ratios are derived. Select any figure for its page evidence.`;
  }

  /* ---------------- chart ---------------- */
  function svgNode(tag, attrs, text) { const n = document.createElementNS('http://www.w3.org/2000/svg', tag); Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v)); if (text) n.textContent = text; return n; }
  function chart() {
    const field = $('metricSelect').value || 'revenue', box = $('chart'); box.replaceChildren();
    $('chartTitle').textContent = fields[field] + ' comparison';
    const groups = new Map();
    [...selected].forEach(c => {
      const r = current(c), v = base(r, field), key = (r?.currency || 'Unconfirmed currency') + ' · ' + (r?.basis || 'Unconfirmed basis');
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push({ c, r, v, prior: comparable(r, current(c, year - 1)) ? base(current(c, year - 1), field) : null });
    });
    for (const [currency, points] of groups) {
      box.append(el('h3', currency));
      const values = points.flatMap(p => [p.v, p.prior]).filter(v => v !== null);
      const min = Math.min(0, ...values), max = Math.max(0, ...values) || 1, scale = v => 160 + (v - min) / (max - min) * 390, zero = scale(0), height = points.length * 65 + 30;
      const svg = svgNode('svg', { viewBox: `0 0 800 ${height}`, role: 'img', 'aria-label': `${fields[field]}, ${currency}, FY${year}, separate scale` });
      points.forEach((p, i) => {
        const y = 18 + i * 65;
        svg.append(svgNode('text', { x: 0, y: y + 15, fill: 'var(--text)', 'font-size': 12 }, p.c));
        if (p.v !== null) {
          const bar = svgNode('rect', { x: Math.min(zero, scale(p.v)), y, width: Math.max(1, Math.abs(scale(p.v) - zero)), height: 20, fill: 'var(--accent)', rx: 2 });
          bar.append(svgNode('title', {}, money(p.r[field], p.r))); svg.append(bar);
          svg.append(svgNode('text', { x: 565, y: y + 15, fill: 'var(--text)', 'font-size': 11 }, money(p.r[field], p.r)));
        } else svg.append(svgNode('text', { x: 165, y: y + 15, fill: 'var(--muted)', 'font-size': 12 }, 'Not reported in this document'));
        if (p.prior !== null) svg.append(svgNode('rect', { x: Math.min(zero, scale(p.prior)), y: y + 27, width: Math.max(1, Math.abs(scale(p.prior) - zero)), height: 10, fill: 'var(--muted)', opacity: .5 }));
      });
      box.append(svg);
    }
    $('chartNote').textContent = `FY${year}. Each currency and reporting basis has its own scale. Grey bars show comparable prior-year values when available.`;
  }

  /* ---------------- company notebook ---------------- */
  function renderInsights(target, items, filename) {
    const box = $(target); box.replaceChildren();
    if (!Array.isArray(items) || !items.length) { box.append(el('p', 'Not extracted. Use Review / Re-extract to retry.', 'muted')); return; }
    items.forEach(item => {
      const div = el('article', null, 'insight');
      if (typeof item === 'string') { div.append(el('p', item)); }
      else {
        div.append(el('h4', item.title), el('p', item.detail));
        if (item.evidence_page || item.page) {
          const a = el('a', `Annual report · page ${item.evidence_page || item.page}`);
          a.href = '/api/source/' + encodeURIComponent(filename) + `#page=${item.evidence_page || item.page}`;
          a.target = '_blank'; a.rel = 'noopener'; div.append(a);
        }
      }
      box.append(div);
    });
  }
  async function detail() {
    const row = current(active);
    $('detailTitle').textContent = active ? `${active} · FY${year}` : 'Company notebook';
    const grid = $('ratioGrid'); grid.replaceChildren();
    Object.entries(ratioLabels).forEach(([key, labelText]) => {
      const d = el('div', null, 'ratio'), v = row?.derived?.[key];
      d.append(el('small', labelText), el('strong', v === null || v === undefined ? '—' : key === 'cash_conversion' ? v.toFixed(2) + '×' : (v * 100).toFixed(1) + '%'), el('small', 'Derived'));
      grid.append(d);
    });
    const token = ++insightToken;
    $('growthList').textContent = 'Loading evidence…'; $('riskList').textContent = 'Loading evidence…'; $('insightStatus').textContent = '';
    if (!active) return;
    try {
      const result = await API.request(`/api/insights/${encodeURIComponent(active)}/${year}`);
      if (token !== insightToken) return;
      renderInsights('growthList', result.payload?.growth_drivers, result.source);
      renderInsights('riskList', result.payload?.risk_factors, result.source);
      $('insightStatus').textContent = result.version ? 'Evidence checked against PDF pages.' : 'No refreshed insights for this report.';
    } catch (e) {
      if (token !== insightToken) return;
      renderInsights('growthList', [], null); renderInsights('riskList', [], null); $('insightStatus').textContent = e.message;
    }
  }
  function choose(c) {
    active = c;
    if (!current(c)) {
      const available = availableYears(c);
      if (available.length) { year = Math.max(...available); $('yearSelect').value = year; }
    }
    if (!selected.has(c)) { if (selected.size >= 4) selected.delete([...selected][selected.size - 1]); selected.add(c); }
    renderTable(); chart();
    $('companyNote').value = notes[c]?.note || ''; $('pinCompany').checked = notes[c]?.pinned || false; $('noteStatus').textContent = '';
    $('companySelect').value = c; coverage(); detail(); ResearchAssistant.setScope(c, year);
    $('sidebar').classList.remove('open'); $('menuToggle').setAttribute('aria-expanded', 'false');
  }

  /* ---------------- data load ---------------- */
  async function load() {
    try {
      const [metrics, companies] = await Promise.all([API.getMetrics(), API.getCompanies()]);
      rows = metrics.rows; catalog = companies.companies;
      $('dataError').hidden = true;
      const years = [...new Set(catalog.flatMap(c => c.years.map(y => Number(y.year))))].sort((a, b) => b - a);
      if (years.length && (year === null || !years.includes(year))) year = years[0];
      $('yearSelect').replaceChildren();
      years.forEach(y => { const o = el('option', 'FY' + y); o.value = y; $('yearSelect').append(o); });
      $('yearSelect').value = year;
      $('companySelect').replaceChildren();
      companyNames().forEach(c => { const o = el('option', c); o.value = c; $('companySelect').append(o); });
      if (!companyNames().length) { $('comparison').replaceChildren(el('p', 'No reports yet. Add an annual-report PDF to start.', 'skeleton')); coverage(); return; }
      if (!active || !catalogEntry(active)) active = companyNames()[0];
      if (!selected.size) selected = new Set(companyNames().slice(0, 4));
      coverage(); renderTable(); chart(); choose(active); renderJobs();
    } catch (e) {
      const box = $('dataError'); box.hidden = false; box.replaceChildren(el('p', e.message));
      const retry = el('button', 'Retry'); retry.onclick = load; box.append(retry);
      $('comparison').replaceChildren(); $('coverageList').textContent = 'Coverage unavailable.';
    }
  }

  /* ---------------- exports ---------------- */
  function download(blob, name) { const a = el('a'); a.href = URL.createObjectURL(blob); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); }
  function exportCsv() {
    const names = [...selected], quote = v => '"' + String(v ?? '').replaceAll('"', '""') + '"';
    const data = [['Metric', ...names.map(c => `${c} (${current(c)?.currency || '?'} ${current(c)?.unit || '?'})`)],
      ...Object.entries(fields).map(([k, v]) => [v, ...names.map(c => current(c)?.[k] ?? '')])];
    download(new Blob(['\ufeff' + data.map(r => r.map(quote).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' }), `comparison-FY${year}.csv`);
  }
  async function exportPng() {
    const svg = $('chart').querySelector('svg'); if (!svg) { toast('Choose a metric with data first.'); return; }
    const clone = svg.cloneNode(true), style = getComputedStyle(document.documentElement);
    clone.querySelectorAll('*').forEach(n => ['fill', 'stroke'].forEach(a => { const v = n.getAttribute(a); if (v?.startsWith('var(')) n.setAttribute(a, style.getPropertyValue(v.slice(4, -1)).trim()); }));
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    const view = svg.viewBox.baseVal; clone.setAttribute('width', view.width); clone.setAttribute('height', view.height);
    const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml' })), img = new Image();
    img.onload = () => {
      const canvas = document.createElement('canvas'); canvas.width = view.width * 2; canvas.height = view.height * 2;
      const ctx = canvas.getContext('2d'); ctx.fillStyle = style.getPropertyValue('--surface'); ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(blob => { if (blob) download(blob, `comparison-${year}.png`); }); URL.revokeObjectURL(url);
    };
    img.onerror = () => { URL.revokeObjectURL(url); toast('Could not export the chart.'); };
    img.src = url;
  }

  /* ---------------- ingestion ---------------- */
  async function ingest(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.pdf') || file.size > 25 * 1024 * 1024) { toast('Choose a PDF no larger than 25 MB.'); return; }
    const progress = $('uploadProgress'); progress.hidden = false; progress.value = 0; $('fileInput').disabled = true;
    try {
      const data = new FormData(); data.append('file', file);
      const job = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest(); xhr.open('POST', '/api/ingest'); xhr.timeout = 60000;
        xhr.upload.onprogress = e => { if (e.lengthComputable) progress.value = e.loaded / e.total * 100; };
        xhr.onload = () => { let p; try { p = JSON.parse(xhr.responseText); } catch { return reject(new Error('Invalid upload response.')); } xhr.status < 300 ? resolve(p) : reject(new Error(p.detail || p.message || 'Upload failed.')); };
        xhr.onerror = () => reject(new Error('Upload connection failed.')); xhr.ontimeout = () => reject(new Error('Upload timed out.'));
        xhr.send(data);
      });
      progress.removeAttribute('value');
      sessionStorage.setItem('ip.ingest', job.job_id);
      await monitorJob(job.job_id);
    } catch (e) { toast(e.message); $('ingestLog').textContent = e.message; }
    finally { progress.hidden = true; $('fileInput').disabled = false; $('fileInput').value = ''; }
  }
  function dialog(title) {
    const d = el('dialog', null, 'review-dialog'), heading = el('h2', title), close = el('button', 'Close');
    close.type = 'button'; close.onclick = () => d.close(); d.append(heading, close);
    document.body.append(d); d.addEventListener('close', () => d.remove(), { once: true }); return d;
  }
  function input(form, labelText, value, type = 'text') {
    const wrap = el('label', labelText), i = el('input'); i.type = type; i.value = value ?? '';
    wrap.append(i); form.append(wrap); return i;
  }
  function confidenceRow(form, det) {
    const row = el('p', null, 'muted small');
    row.textContent = `Detected confidence: ${Math.round((det.confidence || 0) * 100)}%.`
      + (det.fiscal_year_label ? ` Report label: ${det.fiscal_year_label}.` : '')
      + ' Every field is editable before extraction.';
    form.append(row);
  }
  function confirmIdentity(job) {
    return new Promise(resolve => {
      const d = dialog('Confirm report identity'), form = el('form'), det = job.detection || {}, controls = {};
      d.append(el('p', 'Check these details against the report before extraction starts.'));
      ['company', 'year', 'period_start', 'period_end', 'currency', 'unit', 'basis'].forEach(k => {
        controls[k] = input(form, k.replaceAll('_', ' '), det[k], k.includes('period') ? 'date' : k === 'year' ? 'number' : 'text');
        controls[k].required = k !== 'period_start';
      });
      const aliasInput = input(form, 'Also known as (comma-separated aliases)', (det.company_hint || ''));
      d.append(el('p', 'Units: unit, thousand, lakh, crore, million, billion. Basis: consolidated or standalone.'));
      confidenceRow(form, det);
      const submit = el('button', 'Confirm and extract'), error = el('p'); submit.type = 'submit';
      form.append(submit, error); d.append(form); d.showModal();
      d.addEventListener('close', () => resolve(false), { once: true });
      const send = async body => {
        submit.disabled = true; error.textContent = '';
        try {
          await API.request(`/api/ingest/${job.job_id}/confirm`, { method: 'POST', body: JSON.stringify(body), acceptError: true });
          resolve(true); d.close();
        } catch (err) {
          if (err.code === 'duplicate_report') { submit.disabled = false; askDuplicateChoice(d, form, job, send, err); }
          else { error.textContent = err.message; submit.disabled = false; }
        }
      };
      form.onsubmit = e => {
        e.preventDefault();
        const body = Object.fromEntries(Object.entries(controls).map(([k, i]) => [k, k === 'year' ? Number(i.value) : i.value || null]));
        body.aliases = aliasInput.value.split(',').map(s => s.trim()).filter(Boolean);
        send(body);
      };
    });
  }
  function askDuplicateChoice(d, form, job, send, err) {
    form.querySelector('.duplicate-choice')?.remove();
    const box = el('fieldset', null, 'duplicate-choice');
    box.append(el('legend', err.message));
    const options = [['replace', 'Replace — archive the stored report and use this one'], ['keep_both', 'Keep both — store this as a second version'], ['cancel', 'Cancel this upload']];
    const chosen = {};
    options.forEach(([value, text], i) => {
      const l = el('label'); const r = el('input'); r.type = 'radio'; r.name = 'dup'; r.value = value; r.checked = i === 0;
      r.onchange = () => chosen.value = value; l.append(r, el('span', ' ' + text)); box.append(l);
    });
    chosen.value = 'replace';
    const proceed = el('button', 'Continue'); proceed.type = 'button';
    proceed.onclick = () => {
      if (chosen.value === 'cancel') { API.request(`/api/ingest/${job.job_id}`, { method: 'DELETE' }).catch(() => {}); d.close(); return; }
      box.remove(); send({ duplicate_action: chosen.value });
    };
    box.append(proceed); form.append(box);
  }
  function jobControls(job) {
    const row = el('div', null, 'job-controls');
    if (job.status === 'failed') {
      const retry = el('button', 'Retry'); retry.onclick = async () => {
        try { await API.request(`/api/ingest/${job.job_id}/retry`, { method: 'POST' }); renderJobs(); } catch (e) { toast(e.message); }
      };
      const diagnostics = el('a', 'Download diagnostics');
      diagnostics.href = `/api/ingest/${job.job_id}/diagnostics`; diagnostics.download = `ingest-${job.job_id}.txt`;
      row.append(retry, diagnostics);
    }
    if (['queued', 'awaiting_review'].includes(job.status)) {
      const cancel = el('button', 'Cancel'); cancel.onclick = async () => {
        try { await API.request(`/api/ingest/${job.job_id}`, { method: 'DELETE' }); renderJobs(); } catch (e) { toast(e.message); }
      };
      row.append(cancel);
    }
    return row;
  }
  async function renderJobs() {
    const box = $('jobList'); if (!box) return;
    try {
      const p = await API.request('/api/ingest');
      box.replaceChildren(el('h3', 'Recent ingestion jobs'));
      (p.jobs || []).slice(0, 6).forEach(job => {
        const row = el('div', null, 'job-row');
        row.append(el('strong', job.filename || job.job_id), el('small', `${job.status.replaceAll('_', ' ')}${job.company ? ' · ' + job.company : ''}${job.error ? ' · ' + job.error : ''}`));
        row.append(jobControls(job));
        box.append(row);
      });
    } catch (e) { box.replaceChildren(); }
  }
  async function monitorJob(id) {
    for (let attempt = 0; attempt < 900; attempt++) {
      const job = await API.request('/api/ingest/' + id);
      $('ingestLog').replaceChildren(el('strong', (job.stage || job.status).replaceAll('_', ' ')),
        ...(job.log || []).slice(-4).map(line => el('p', typeof line === 'string' ? line : line.message)));
      if (job.status === 'awaiting_review') {
        if (!await confirmIdentity(job)) {
          const retry = el('button', 'Review pending upload'); retry.onclick = () => monitorJob(id).catch(e => toast(e.message));
          $('ingestLog').append(retry); return;
        }
      }
      if (['completed', 'completed_with_warnings', 'needs_review', 'failed', 'cancelled'].includes(job.status)) {
        sessionStorage.removeItem('ip.ingest');
        toast(job.result?.summary || job.error || job.status);
        if (job.result?.company) { active = job.result.company; year = Number(job.result.year); }
        await load(); return;
      }
      await new Promise(r => setTimeout(r, 2000));
    }
    toast('Processing continues. Refresh to resume progress tracking.');
  }

  /* ---------------- review drawer ---------------- */
  function openSource(company, field) {
    const evidence = current(company)?.metric_sources?.[field];
    const url = evidence?.url;
    if (url && /^https?:\/\//i.test(url)) { window.open(url, '_blank', 'noopener'); return; }
    window.open('/api/source/' + encodeURIComponent(evidence?.document || source(company)) + (evidence?.page ? '#page=' + evidence.page : ''), '_blank', 'noopener');
  }
  function reviewMetrics(company, field = 'revenue') {
    const row = current(company);
    if (!row) { toast('No report for this fiscal year. Select an available year or upload a report.'); return; }
    const d = dialog(`${company} · FY${year} — review`), form = el('form'), select = el('select'), message = el('p'), evidence = el('p', null, 'evidence-text');
    select.setAttribute('aria-label', 'Metric');
    Object.entries(fields).forEach(([k, l]) => { const o = el('option', l); o.value = k; select.append(o); });
    select.value = field; form.append(select);
    const value = input(form, 'Reported value (blank = unavailable)', row[field], 'number'); value.step = 'any';
    const currency = input(form, 'Currency', row.currency), unit = input(form, 'Unit', row.unit), quote = input(form, 'Evidence quote or correction note', '');
    const show = () => {
      const e = row.metric_sources?.[select.value] || {};
      value.value = row[select.value] ?? ''; quote.value = e.quote || '';
      evidence.textContent = `${row.basis || 'Basis unconfirmed'} · period ended ${row.period_end || '?'} · status ${row.extraction_status}.`
        + (e.page ? ` Page ${e.page}, confidence ${e.confidence ?? 'unavailable'}.` : ' Page evidence unavailable.')
        + (e.derived ? ` Derived: ${e.formula}.` : '')
        + (e.status === 'manual_override' ? ` Manual override saved ${e.updated_at || ''} (previous value: ${e.previous_value ?? 'none'}).` : '')
        + (e.status === 'not_found' ? ` Not found: ${e.reason}.` : '')
        + ` ${e.quote || 'No extracted evidence.'}`;
    };
    show(); select.onchange = show;
    const save = el('button', 'Save correction'), markReviewed = el('button', 'Mark as reviewed'),
      open = el('button', 'Open evidence page'), copyCite = el('button', 'Copy figure with citation'), retry = el('button', 'Re-extract');
    save.type = 'submit'; [markReviewed, open, copyCite, retry].forEach(b => b.type = 'button');
    open.onclick = () => openSource(company, select.value);
    copyCite.onclick = async () => {
      const e = row.metric_sources?.[select.value] || {};
      const text = `${company} FY${year} ${fields[select.value]}: ${money(row[select.value], row)} — ${row.source_file || 'annual report'}${e.page ? ', page ' + e.page : ''}`;
      try { await navigator.clipboard.writeText(text); copyCite.textContent = 'Copied'; } catch { toast('Copy unavailable.'); }
    };
    markReviewed.onclick = async () => {
      markReviewed.disabled = true;
      try {
        await API.request(`/api/metrics/${encodeURIComponent(company)}/${year}`, { method: 'PATCH', body: JSON.stringify({ metric: select.value, reviewed: true }) });
        message.textContent = 'Marked as reviewed.'; await load(); d.close();
      } catch (e) { message.textContent = e.message; markReviewed.disabled = false; }
    };
    retry.onclick = async () => {
      try {
        const job = await API.request(`/api/metrics/${encodeURIComponent(company)}/${year}/reextract`, { method: 'POST' });
        sessionStorage.setItem('ip.ingest', job.job_id); d.close(); await monitorJob(job.job_id);
      } catch (e) { message.textContent = e.message; }
    };
    form.append(evidence, el('p', 'Audit trail: corrections are stored with their previous value, source and timestamp.', 'muted small'),
      save, markReviewed, open, copyCite, retry, message);
    d.append(form); d.showModal();
    form.onsubmit = async e => {
      e.preventDefault(); save.disabled = true;
      try {
        await API.request(`/api/metrics/${encodeURIComponent(company)}/${year}`, {
          method: 'PATCH', body: JSON.stringify({
            metric: select.value, value: value.value === '' ? null : Number(value.value),
            currency: currency.value, unit: unit.value, quote: quote.value, source: 'manual',
          })
        });
        await load(); d.close(); toast('Correction saved.');
      } catch (err) { message.textContent = err.message; save.disabled = false; }
    };
  }
  function editAliases(company) {
    const d = dialog(`${company} — known aliases`), form = el('form'), message = el('p');
    const input_ = input(form, 'Aliases (comma-separated)', '');
    const save = el('button', 'Save aliases'); save.type = 'submit';
    form.append(el('p', 'Aliases map alternative spellings and short names to this company.'), save, message);
    d.append(form); d.showModal();
    form.onsubmit = async e => {
      e.preventDefault(); save.disabled = true;
      try {
        const aliases = input_.value.split(',').map(s => s.trim()).filter(Boolean);
        const body = { company, year: availableYears(company)[0] || year, period_end: current(company)?.period_end || `${year}-12-31`, currency: current(company)?.currency || 'USD', unit: current(company)?.unit || 'unit', basis: current(company)?.basis || 'consolidated', aliases };
        await API.request('/api/aliases', { method: 'POST', body: JSON.stringify(body) });
        d.close(); toast('Aliases saved.');
      } catch (err) { message.textContent = err.message; save.disabled = false; }
    };
  }
  async function removeReport(company) {
    const d = dialog(`Remove ${company} FY${year}?`), message = el('p');
    d.append(el('p', 'The report is removed from the dashboard. The previous row is archived, never silently lost.'));
    const confirm = el('button', 'Delete report'); confirm.type = 'button';
    confirm.onclick = async () => {
      confirm.disabled = true;
      try {
        await API.request(`/api/metrics/${encodeURIComponent(company)}/${year}?confirm=true`, { method: 'DELETE' });
        d.close(); selected.delete(company); if (active === company) active = null;
        await load(); toast('Report removed.');
      } catch (e) { message.textContent = e.message; confirm.disabled = false; }
    };
    const cancel = el('button', 'Keep'); cancel.type = 'button'; cancel.onclick = () => d.close();
    d.append(confirm, cancel, message); d.showModal();
  }

  /* ---------------- command palette ---------------- */
  function palette() { const d = $('commandDialog'); d.showModal(); $('commandInput').value = ''; commands(''); $('commandInput').focus(); }
  function commands(query) {
    const box = $('commandResults'); box.replaceChildren();
    companyNames().filter(c => c.toLowerCase().includes(query.toLowerCase())).forEach(c => {
      const b = el('button', 'Open ' + c); b.onclick = () => { $('commandDialog').close(); choose(c); $('companyDetail').scrollIntoView(); }; box.append(b);
    });
    if (query) { const b = el('button', 'Ask: ' + query); b.onclick = () => { $('commandDialog').close(); ResearchAssistant.submit(query); }; box.append(b); }
  }

  /* ---------------- init ---------------- */
  function init() {
    ResearchAssistant.init();
    Object.entries(fields).forEach(([k, v]) => { const o = el('option', v); o.value = k; $('metricSelect').append(o); });
    $('metricSelect').onchange = chart;
    $('yearSelect').onchange = e => { year = Number(e.target.value); renderTable(); chart(); detail(); ResearchAssistant.setScope(active, year); };
    $('companySelect').onchange = e => choose(e.target.value);
    const actions = el('div', null, 'company-actions');
    const review = el('button', 'Review / Re-extract'); review.onclick = () => reviewMetrics(active);
    const aliases = el('button', 'Edit aliases'); aliases.onclick = () => active && editAliases(active);
    const remove = el('button', 'Remove report'); remove.onclick = () => active && removeReport(active);
    actions.append(review, aliases, remove);
    $('companySelect').parentElement.append(actions);
    $('headerStatus').onclick = health;
    $('themeToggle').onclick = () => {
      const dark = document.documentElement.dataset.theme !== 'dark';
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
      $('themeToggle').textContent = dark ? 'Switch to light' : 'Switch to dark';
      try { localStorage.setItem('ip.theme', dark ? 'dark' : 'light'); } catch {}
    };
    try { if (localStorage.getItem('ip.theme') === 'dark') $('themeToggle').click(); } catch {}
    $('exportCsv').onclick = exportCsv; $('exportPng').onclick = exportPng; $('printSummary').onclick = () => window.print();
    $('compareTakeaway').onclick = () => {
      if (selected.size < 2) { toast('Choose at least two companies.'); return; }
      ResearchAssistant.submit(`Compare ${[...selected].join(' vs ')} ${fields[$('metricSelect').value].toLowerCase()} in ${year}`);
    };
    $('fileInput').onchange = e => ingest(e.target.files[0]);
    ['dragover', 'drop'].forEach(name => document.addEventListener(name, e => e.preventDefault()));
    document.addEventListener('drop', e => { const file = [...(e.dataTransfer?.files || [])].find(f => f.name.toLowerCase().endsWith('.pdf')); if (file) ingest(file); });
    $('menuToggle').onclick = () => { const open = $('sidebar').classList.toggle('open'); $('menuToggle').setAttribute('aria-expanded', String(open)); };
    $('commandOpen').onclick = palette; $('commandInput').oninput = e => commands(e.target.value);
    document.addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k' || e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) { e.preventDefault(); palette(); }
      if (e.key === 'Escape') { $('sidebar').classList.remove('open'); $('menuToggle').setAttribute('aria-expanded', 'false'); }
    });
    $('noteForm').onsubmit = async e => {
      e.preventDefault(); if (!active) return;
      const target = active; const note = { company: target, note: $('companyNote').value, pinned: $('pinCompany').checked };
      $('saveNote').disabled = true;
      try { await API.request('/api/notes', { method: 'POST', body: JSON.stringify(note) }); notes[target] = note; $('noteStatus').textContent = ' Saved'; coverage(); }
      catch (err) { $('noteStatus').textContent = err.message; }
      finally { $('saveNote').disabled = false; }
    };
    API.request('/api/notes').then(p => { notes = Object.fromEntries(p.data.map(n => [n.company, n])); if (active) choose(active); }).catch(e => { $('noteStatus').textContent = e.message; });
    window.addEventListener('metrics-updated', load);
    load(); health(); setInterval(health, 30000);
    const pending = sessionStorage.getItem('ip.ingest');
    if (pending) monitorJob(pending).catch(e => toast(e.message));
  }
  init();
})();
