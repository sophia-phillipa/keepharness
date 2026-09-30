/* Event-derived run inspection. Chat remains the primary surface. */
(() => {
  'use strict';
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const button = (text, action) => {
    const node = el('button', text, 'btn');
    node.type = 'button';
    node.addEventListener('click', action);
    return node;
  };
  const field = (text, input) => {
    const label = el('label', text, 'run-field');
    label.append(input);
    return label;
  };
  const input = (id, type = 'text') => {
    const node = el('input');
    node.id = id;
    node.type = type;
    return node;
  };
  const select = (id, choices) => {
    const node = el('select');
    node.id = id;
    for (const [value, label] of choices) node.add(new Option(label, value));
    return node;
  };
  const state = { tab: 'Pipeline', run: '', spans: [], selectedSpan: '', content: false,
    activity: { jobs: [], providers: [], needs_you: [], counts: {} }, logs: [], after: 0,
    more: true, logLoading: false, sequence: 0, activitySequence: 0, zoom: 1, detailTab: 'Metrics' };
  const main = document.querySelector('main');
  const drawer = el('section', null, 'run-console');
  drawer.id = 'run-console';
  drawer.hidden = true;
  drawer.setAttribute('aria-label', 'Run console');
  const resizer = el('div', null, 'run-console-resizer');
  resizer.tabIndex = 0;
  resizer.setAttribute('role', 'separator');
  resizer.setAttribute('aria-label', 'Resize run console');
  resizer.setAttribute('aria-orientation', 'horizontal');
  resizer.setAttribute('aria-controls', drawer.id);
  const header = el('div', null, 'run-console-header');
  const tabs = el('div', null, 'run-console-tabs');
  tabs.setAttribute('role', 'tablist');
  tabs.setAttribute('aria-label', 'Run console views');
  const tabButtons = [];
  const body = el('div', null, 'run-console-body');
  body.id = 'run-console-panel';
  body.setAttribute('role', 'tabpanel');
  body.tabIndex = -1;
  for (const name of ['Pipeline', 'Timeline', 'Logs', 'Runs', 'Agents']) {
    const tab = button(name, () => setTab(name));
    tab.id = 'run-tab-' + name.toLowerCase();
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-controls', body.id);
    tabButtons.push(tab);
    tabs.append(tab);
  }
  tabs.addEventListener('keydown', event => {
    const index = tabButtons.indexOf(document.activeElement);
    if (index < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? 4 : (index + (event.key === 'ArrowRight' ? 1 : 4)) % 5;
    tabButtons[next].click();
    tabButtons[next].focus();
  });
  const close = button('Collapse run console', () => toggle(false));
  close.classList.add('run-console-close');
  header.append(el('h2', 'Run console'), tabs, close);
  const controls = el('div', null, 'run-console-controls');
  const runSelect = select('console-run', [['', 'Select a run']]);
  runSelect.addEventListener('change', () => chooseRun(runSelect.value));
  const error = el('p', '', 'run-console-error');
  error.setAttribute('role', 'status');
  controls.append(field('Run', runSelect));
  drawer.append(resizer, header, controls, error, body);
  const strip = el('div', null, 'run-status-strip');
  strip.setAttribute('role', 'region');
  strip.setAttribute('aria-label', 'Run status');
  const toggleButton = button('Run console · checking activity…', () => toggle(drawer.hidden));
  toggleButton.id = 'run-status-toggle';
  toggleButton.setAttribute('aria-controls', drawer.id);
  toggleButton.setAttribute('aria-expanded', 'false');
  toggleButton.title = 'Open run console (Ctrl/⌘+J)';
  const inboxButton = button('Needs you (0)', () => openInbox());
  inboxButton.id = 'needs-you-toggle';
  inboxButton.setAttribute('aria-haspopup', 'dialog');
  strip.append(toggleButton, inboxButton);
  main.append(drawer, strip);
  const inbox = el('dialog', null, 'needs-you-dialog');
  inbox.id = 'needs-you-inbox';
  inbox.setAttribute('aria-labelledby', 'needs-you-title');
  const inboxHeader = el('div', null, 'run-console-header');
  const inboxTitle = el('h2', 'Needs you');
  inboxTitle.id = 'needs-you-title';
  inboxHeader.append(inboxTitle, button('Close inbox', () => inbox.close()));
  const inboxList = el('div');
  inbox.append(inboxHeader, inboxList);
  document.body.append(inbox);
  inbox.addEventListener('close', () => inboxButton.focus());
  let previousFocus, refreshTimer, lastContext = '', inboxSignature = '';
  const projectFilter = select('console-project', []);
  const workFilter = input('console-work-item');
  const stateFilter = select('console-state', [['', 'All states'], ...['running', 'queued', 'completed', 'failed', 'cancelled', 'interrupted'].map(v => [v, v])]);
  const logSearch = input('console-log-search', 'search');
  const logType = select('console-log-type', [['', 'All event types']]);
  logSearch.addEventListener('input', renderLogs);
  logType.addEventListener('change', renderLogs);
  stateFilter.addEventListener('change', renderRuns);
  const currentProject = () => projectFilter.value || document.getElementById('project')?.value || 'sem-projeto';
  const showError = message => { error.textContent = message || ''; };
  const duration = span => {
    if (span.start_ts == null) return 'Unknown duration';
    const seconds = Math.max(0, (span.end_ts ?? Date.now() / 1000) - span.start_ts);
    return seconds.toFixed(1) + ' s' + (span.end_ts == null ? ' · pending' : '');
  };
  const outcome = span => span.attrs?.effect_status || (span.end_ts == null ? 'pending' : span.attrs?.outcome || span.status);
  const tokenCount = span => {
    const attrs = span.attrs || {};
    const values = ['input', 'output'].map(key => attrs['gen_ai.usage.' + key + '_tokens']);
    return values.some(v => v != null) ? values.map(v => v ?? '?').join(' in / ') + ' out tokens' : 'Tokens not reported';
  };
  function contentValue(span, section) {
    const records = span.content || [];
    if (!Array.isArray(records)) return records[section] ?? 'Not recorded';
    const values = [];
    for (const record of records) {
      const data = record.data || {};
      if (section === 'prompt' && record.request?.prompt) values.push(record.request.prompt);
      if (section === 'prompt' && ['maestro_step', 'maestro_planning'].includes(record.name) && (data.task || data.prompt)) values.push(data.task || data.prompt);
      if (section === 'input' && record.name === 'tool_start') values.push(data.input ?? data.arguments ?? data);
      if (section === 'input' && record.request && !records.some(item => item.name === 'tool_start')) values.push(record.request);
      if (section === 'output' && ['answer_delta', 'reasoning_delta'].includes(record.name)) values.push(data.text || '');
      if (section === 'output' && record.name === 'tool_end') values.push(data.result ?? data.output ?? data);
      if (section === 'output' && ['completed', 'terminal'].includes(record.name)) values.push(data.result?.answer ?? data.answer ?? data);
    }
    if (!values.length) return 'Not recorded';
    return values.every(value => typeof value === 'string') ? values.join('') : values;
  }
  function toggle(open) {
    if (open && drawer.hidden) previousFocus = document.activeElement;
    drawer.hidden = !open;
    toggleButton.setAttribute('aria-expanded', String(open));
    if (open) {
      setTab(state.tab);
      tabButtons.find(node => node.textContent === state.tab).focus();
      void refresh();
    } else {
      (previousFocus?.isConnected ? previousFocus : toggleButton).focus();
    }
  }
  function setTab(name) {
    state.tab = name;
    for (const tab of tabButtons) {
      const selected = tab.textContent === name;
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
    }
    body.setAttribute('aria-labelledby', 'run-tab-' + name.toLowerCase());
    controls.hidden = name === 'Agents' || name === 'Runs';
    render();
    if (name === 'Logs' && !state.logs.length) void loadLogs();
  }
  function resize(height) {
    const next = Math.round(Math.max(190, Math.min(innerHeight * .75, height)));
    drawer.style.setProperty('--th-console-height', next + 'px');
    resizer.setAttribute('aria-valuenow', String(next));
    resizer.setAttribute('aria-valuemin', '190');
    resizer.setAttribute('aria-valuemax', String(Math.round(innerHeight * .75)));
  }
  resizer.addEventListener('pointerdown', event => {
    event.preventDefault();
    resizer.setPointerCapture(event.pointerId);
  });
  resizer.addEventListener('pointermove', event => {
    if (resizer.hasPointerCapture(event.pointerId)) resize(strip.getBoundingClientRect().top - event.clientY);
  });
  resizer.addEventListener('pointerup', event => resizer.releasePointerCapture(event.pointerId));
  resizer.addEventListener('keydown', event => {
    if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    resize(drawer.getBoundingClientRect().height + (event.key === 'ArrowUp' ? 30 : -30));
  });
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'j' && !inbox.open) {
      event.preventDefault();
      toggle(drawer.hidden);
    } else if (event.key === 'Escape' && !drawer.hidden && !document.querySelector('dialog[open]')) {
      event.preventDefault();
      toggle(false);
    }
  });
  function syncContext() {
    const context = [conversation, document.getElementById('project')?.value].join(':');
    if (context === lastContext) return;
    lastContext = context;
    const project = document.getElementById('project');
    projectFilter.replaceChildren(...[...project.options].map(option => new Option(option.text, option.value)));
    projectFilter.value = project.value;
    workFilter.value = '';
    state.run = job || '';
    state.content = false;
    state.detailTab = 'Metrics';
    state.selectedSpan = '';
    state.spans = [];
    state.logs = [];
    state.after = 0;
    state.sequence++;
  }
  async function refresh() {
    if (document.body.dataset.connectionReady !== 'true') return;
    syncContext();
    const sequence = ++state.activitySequence;
    const params = new URLSearchParams({ project_id: currentProject() });
    if (workFilter.value.trim()) params.set('work_item', workFilter.value.trim());
    try {
      const data = await json('/v1/activity?' + params);
      if (sequence !== state.activitySequence) return;
      const changed = JSON.stringify(state.activity) !== JSON.stringify(data);
      state.activity = { jobs: [], providers: [], needs_you: [], counts: {}, ...data };
      const counts = state.activity.counts;
      toggleButton.textContent = `${counts.running || 0} running · ${counts.queued || 0} queued · ${counts.needs_you || 0} needs you · Run console`;
      inboxButton.textContent = `Needs you (${counts.needs_you || 0})`;
      refreshRunOptions();
      if (state.tab === 'Logs' && !state.more && data.jobs?.some(item => item.job_id === state.run && item.state === 'running')) {
        state.more = true;
        renderLogs();
      }
      if (!drawer.hidden) {
        if (['Runs', 'Agents'].includes(state.tab)) {
          if (changed && !body.querySelector('[data-tag-form]')) render();
        }
        else if (state.run) await fetchSpans();
      }
      if (inbox.open) renderInbox();
    } catch (failure) {
      if (sequence !== state.activitySequence) return;
      toggleButton.textContent = 'Activity unavailable · Open run console to retry';
      showError(failure.message);
    }
  }
  function refreshRunOptions() {
    const jobs = state.activity.jobs.filter(item => !conversation || item.conversation_id === conversation || item.job_id === state.run);
    const options = jobs.map(item => [item.job_id, [item.work_item, item.model, item.state, item.job_id.slice(0, 8)].filter(Boolean).join(' · ')]);
    if (state.run && !options.some(([id]) => id === state.run)) options.push([state.run, state.run]);
    runSelect.replaceChildren(new Option('Select a run', ''), ...options.map(([id, label]) => new Option(label, id)));
    runSelect.value = state.run;
  }
  async function chooseRun(id) {
    state.run = id;
    state.content = false;
    state.detailTab = 'Metrics';
    state.selectedSpan = '';
    state.spans = [];
    state.logs = [];
    state.after = 0;
    state.more = true;
    state.logLoading = false;
    state.sequence++;
    refreshRunOptions();
    render();
    await fetchSpans();
    if (state.tab === 'Logs') await loadLogs();
  }
  async function fetchSpans() {
    if (!state.run) return;
    const id = state.run, sequence = state.sequence, content = state.content;
    try {
      const data = await json('/v1/jobs/' + encodeURIComponent(id) + '/spans' + (content ? '?include_content=true' : ''));
      if (id !== state.run || sequence !== state.sequence || content !== state.content) return;
      const changed = JSON.stringify(state.spans) !== JSON.stringify(data.spans || []);
      state.spans = data.spans || [];
      showError('');
      if (changed && ['Pipeline', 'Timeline'].includes(state.tab)) renderSpans();
    } catch (failure) { if (sequence === state.sequence) showError(failure.message); }
  }
  function render() {
    if (state.tab === 'Runs') renderRuns();
    else if (state.tab === 'Agents') renderAgents();
    else if (state.tab === 'Logs') renderLogs();
    else renderSpans();
  }
  function renderSpans() {
    const focusedId = body.contains(document.activeElement) ? document.activeElement.id : '';
    const scrollTop = body.scrollTop;
    body.replaceChildren();
    if (!state.run) { body.append(el('p', 'Select a run to inspect its recorded steps.')); return; }
    const split = el('div', null, 'run-span-split');
    const list = el('div', null, 'run-span-list');
    if (state.tab === 'Timeline') {
      const zoom = input('console-zoom', 'range');
      zoom.min = '1'; zoom.max = '8'; zoom.step = '.5'; zoom.value = state.zoom;
      zoom.addEventListener('input', () => { state.zoom = Number(zoom.value); renderSpans(); document.getElementById('console-zoom').focus(); });
      body.append(field('Timeline zoom', zoom));
    }
    if (!state.spans.length) list.append(el('p', 'No spans recorded yet.'));
    const starts = state.spans.map(span => span.start_ts).filter(value => value != null);
    const start = Math.min(...starts);
    const end = Math.max(...state.spans.map(span => span.end_ts ?? Date.now() / 1000));
    for (const span of state.spans) {
      const row = button('', () => { state.selectedSpan = span.span_id; renderSpans(); });
      row.classList.add('run-span-row');
      row.id = 'run-span-' + span.span_id;
      row.dataset.state = outcome(span);
      row.setAttribute('aria-pressed', String(state.selectedSpan === span.span_id));
      row.append(el('strong', span.name), el('span', `${outcome(span)} · ${span.attrs?.['gen_ai.request.model'] || span.kind} · ${duration(span)} · ${tokenCount(span)}`));
      if (span.attrs?.enforcement) row.append(el('span', 'Publication: ' + span.attrs.enforcement));
      if (state.tab === 'Timeline') {
        const track = el('span', null, 'run-waterfall-track');
        track.style.width = state.zoom * 100 + '%';
        const bar = el('span', null, 'run-waterfall-bar');
        bar.style.marginLeft = Math.max(0, ((span.start_ts ?? start) - start) / Math.max(1, end - start) * 100) + '%';
        bar.style.width = Math.max(.5, ((span.end_ts ?? end) - (span.start_ts ?? start)) / Math.max(1, end - start) * 100) + '%';
        bar.title = duration(span);
        track.append(bar); row.append(track);
      }
      list.append(row);
    }
    split.append(list);
    const selected = state.spans.find(span => span.span_id === state.selectedSpan);
    if (selected) split.append(spanDetail(selected));
    body.append(split);
    if (focusedId) document.getElementById(focusedId)?.focus({ preventScroll: true });
    body.scrollTop = scrollTop;
  }
  function spanDetail(span) {
    const detail = el('section', null, 'run-span-detail');
    detail.setAttribute('aria-label', 'Span detail');
    detail.append(el('h3', span.name));
    if (span.kind === 'harness.effect') {
      const attrs = span.attrs || {};
      detail.append(el('p', 'Effect status: ' + outcome(span)), el('p', 'Publication: ' + (attrs.enforcement || 'unenforced')));
      for (const [label, value] of [['Operation', attrs.operation], ['Destination', attrs.destination],
        ['Integration', attrs.integration], ['Jira site', attrs.endpoint],
        ['Artifact digest', attrs.artifact_digest], ['Arguments digest', attrs.arguments_digest],
        ['Approval', attrs.approved_by], ['Gate', attrs.gate_id], ['Receipt', attrs.receipt_issue_key]]) {
        if (value) detail.append(el('p', label + ': ' + value));
      }
      const history = el('ol', null, 'effect-history');
      const names = { effect_prepared: 'Prepared', effect_intent: 'Intent recorded', effect_approved: 'Approval recorded',
        effect_execution: 'Execution started', effect_done: 'Receipt recorded', effect_failed: 'Failed',
        effect_unknown: 'Outcome unknown', effect_reconciled: 'Human reconciliation decision' };
      for (const event of span.events || []) history.append(el('li', names[event.name] || event.name));
      detail.append(history);
      if (attrs.effect_status === 'unknown') {
        detail.append(el('p', 'The outcome is unknown. An empty search is inconclusive. Publication will not be retried automatically.'));
        detail.append(button('Reconcile', () => showReconciliation(detail, attrs.effect_id)));
      }
    }
    const reveal = button(state.content ? 'Hide content' : 'Show content', async () => {
      state.content = !state.content;
      state.sequence++;
      if (!state.content) {
        state.spans = state.spans.map(({ content, ...metadata }) => metadata);
        state.detailTab = 'Metrics';
        renderSpans();
      } else await fetchSpans();
    });
    reveal.id = 'run-content-toggle';
    detail.append(reveal);
    if (!state.content) detail.append(el('p', 'Input, output and prompt are hidden.'));
    const detailTabs = el('div', null, 'run-detail-tabs');
    detailTabs.setAttribute('role', 'tablist');
    detailTabs.setAttribute('aria-label', 'Span details');
    const detailBody = el('pre', null, 'run-detail-value');
    detailBody.id = 'run-detail-value';
    detailBody.setAttribute('role', 'tabpanel');
    for (const name of ['Metrics', 'Attributes', ...(state.content ? ['Input', 'Output', 'Prompt'] : [])]) {
      const tab = button(name, () => { state.detailTab = name; renderSpans(); document.getElementById('run-detail-' + name.toLowerCase())?.focus(); });
      tab.id = 'run-detail-' + name.toLowerCase();
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-controls', detailBody.id);
      tab.setAttribute('aria-selected', String(state.detailTab === name));
      tab.tabIndex = state.detailTab === name ? 0 : -1;
      detailTabs.append(tab);
    }
    detailTabs.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const options = [...detailTabs.children];
      const index = options.indexOf(document.activeElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : options.length - 1)) % options.length;
      options[next].click();
    });
    const attrs = span.attrs || {};
    const value = state.detailTab === 'Attributes' ? attrs : state.detailTab === 'Metrics'
      ? { duration_seconds: span.end_ts == null ? null : span.end_ts - span.start_ts, ...Object.fromEntries(Object.entries(attrs).filter(([key]) => key.includes('usage') || key.includes('duration') || key.includes('tokens'))) }
      : contentValue(span, state.detailTab.toLowerCase());
    detailBody.setAttribute('aria-labelledby', 'run-detail-' + state.detailTab.toLowerCase());
    detailBody.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    detail.append(detailTabs, detailBody);
    return detail;
  }
  function showReconciliation(detail, effectId) {
    if (detail.querySelector('.effect-reconciliation')) return;
    const form = el('section', null, 'effect-reconciliation');
    form.append(el('p', 'Record your decision: check external evidence or keep this outcome unknown. This never republishes the artifact. An enrolled human session is required.'));
    const feedback = el('p'); feedback.setAttribute('role', 'status');
    let deciding = false;
    const decide = async decision => {
      if (deciding) return;
      deciding = true;
      form.querySelectorAll('button').forEach(node => { node.disabled = true; });
      try {
        const result = await post('/v1/effects/' + encodeURIComponent(effectId) + '/reconcile', { decision });
        feedback.textContent = 'Decision recorded. Effect status: ' + (result.status || result.effect?.status || 'unknown') + '.';
        await fetchSpans();
      } catch (failure) { feedback.textContent = failure.message; }
      finally { deciding = false; form.querySelectorAll('button').forEach(node => { node.disabled = false; }); }
    };
    form.append(button('Check evidence', () => decide('check')), button('Keep unknown', () => decide('keep_unknown')), feedback);
    detail.append(form);
    form.querySelector('button').focus();
  }
  async function loadLogs() {
    if (!state.run || state.logLoading || !state.more) return;
    const sequence = state.sequence, id = state.run;
    state.logLoading = true;
    renderLogs();
    try {
      const data = await json('/v1/jobs/' + encodeURIComponent(id) + '/events?' + new URLSearchParams({ format: 'json', after: state.after, limit: '200' }));
      if (sequence !== state.sequence || id !== state.run) return;
      const known = new Set(state.logs.map(item => item.id));
      state.logs.push(...(data.events || []).filter(item => !known.has(item.id)));
      state.after = data.next_after ?? state.logs.at(-1)?.id ?? state.after;
      state.more = Boolean(data.has_more);
      const type = logType.value;
      logType.replaceChildren(new Option('All event types', ''), ...[...new Set(state.logs.map(item => item.type))].sort().map(type => new Option(type, type)));
      logType.value = type;
      showError('');
    } catch (failure) { if (sequence === state.sequence) showError(failure.message); }
    finally { if (sequence === state.sequence) { state.logLoading = false; if (state.tab === 'Logs') renderLogs(); } }
  }
  function renderLogs() {
    if (state.tab !== 'Logs') return;
    const toolbar = el('div', null, 'run-console-controls');
    toolbar.append(field('Search logs', logSearch), field('Event type', logType));
    const list = el('ol', null, 'run-log-list');
    const search = logSearch.value.toLowerCase();
    for (const event of state.logs) {
      const text = `${event.id} · ${event.type} · ${JSON.stringify(event.data)}`;
      if ((logType.value && event.type !== logType.value) || !text.toLowerCase().includes(search)) continue;
      list.append(el('li', text, 'run-log-row'));
    }
    const more = button(state.logLoading ? 'Loading events…' : 'Load more events', loadLogs);
    more.disabled = !state.run || state.logLoading || !state.more;
    const searchFocus = document.activeElement === logSearch;
    body.replaceChildren(toolbar, el('p', `${state.logs.length} events loaded · search applies to loaded events`), list, more);
    if (searchFocus) logSearch.focus();
  }
  function renderRuns() {
    if (state.tab !== 'Runs') return;
    const filters = el('form', null, 'run-console-controls');
    filters.addEventListener('submit', event => { event.preventDefault(); void refresh(); });
    const apply = el('button', 'Apply filters', 'btn');
    apply.type = 'submit';
    filters.append(field('Project filter', projectFilter), field('Work item filter', workFilter), field('Run state', stateFilter), apply);
    const table = el('table', null, 'run-table');
    const head = el('tr');
    for (const title of ['Run / conversation', 'State', 'Provider / model', 'Work item']) head.append(el('th', title));
    const thead = el('thead'); thead.append(head); table.append(thead);
    const tbody = el('tbody');
    for (const item of state.activity.jobs.filter(item => !stateFilter.value || item.state === stateFilter.value)) {
      const row = el('tr');
      const name = el('td');
      name.append(button(item.job_id, async () => {
        try { await load(item.conversation_id || item.job_id, !item.conversation_id); syncContext(); await chooseRun(item.job_id); setTab('Pipeline'); }
        catch (failure) { showError(failure.message); }
      }));
      const work = el('td', item.work_item || '—');
      work.append(button('Tag work item', () => tagWorkItem(item, work)));
      row.append(name, el('td', item.state + (item.wait_reason ? ' · ' + item.wait_reason : '')), el('td', [item.backend, item.model].filter(Boolean).join(' / ')), work);
      tbody.append(row);
    }
    table.append(tbody);
    body.replaceChildren(filters, table);
    if (!tbody.children.length) body.append(el('p', 'No runs match these filters.'));
  }
  function tagWorkItem(item, holder) {
    const form = el('form');
    form.dataset.tagForm = 'true';
    const key = input('console-tag-' + item.job_id);
    key.value = item.work_item || '';
    key.maxLength = 128;
    const save = el('button', 'Save work item', 'btn'); save.type = 'submit';
    form.append(field('Work item key', key), save, button('Cancel tagging', renderRuns));
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (save.disabled) return; save.disabled = true;
      try {
        await api('/v1/jobs/' + encodeURIComponent(item.job_id) + '/work-item', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ work_item: key.value.trim() || null }) });
        form.remove();
        await refresh();
        renderRuns();
      } catch (failure) { showError(failure.message); save.disabled = false; }
    });
    holder.replaceChildren(form); key.focus();
  }
  function renderAgents() {
    body.replaceChildren();
    for (const provider of state.activity.providers) {
      const row = el('section', null, 'run-agent-row');
      row.append(el('h3', [provider.backend, provider.model].filter(Boolean).join(' / ')), el('p', `${provider.state} · ${provider.running} running · ${provider.queued} queued`));
      row.append(el('p', provider.quota ? 'Quota: ' + JSON.stringify(provider.quota) : 'Quota not reported'));
      body.append(row);
    }
    if (!body.children.length) body.append(el('p', 'No configured agents are available.'));
  }
  async function openInbox() {
    inbox.showModal();
    renderInbox();
    await refresh();
  }
  function renderInbox() {
    const requests = state.activity.needs_you.filter(item => {
      const deadline = item.timeout_at ?? item.expires_at;
      return !deadline || deadline > Date.now() / 1000;
    });
    const signature = JSON.stringify(requests);
    if (signature === inboxSignature) return;
    inboxSignature = signature;
    const focused = inboxList.contains(document.activeElement) ? document.activeElement : null;
    const retained = new Map([...inboxList.querySelectorAll('.needs-you-card')].map(card => [card.dataset.requestId, card]));
    const cards = requests.map(item => retained.get(item.gate_id || item.approval_id) || requestCard(item));
    for (const child of [...inboxList.children]) if (!cards.includes(child)) child.remove();
    for (const [index, card] of cards.entries()) {
      if (inboxList.children[index] !== card) inboxList.insertBefore(card, inboxList.children[index] || null);
    }
    if (!requests.length) inboxList.append(el('p', 'No live requests need your attention.'));
    if (focused && !focused.isConnected) inbox.querySelector('button').focus();
  }
  function requestCard(item) {
    const card = el('section', null, 'needs-you-card');
    const id = item.gate_id || item.approval_id;
    card.dataset.requestId = id;
    card.append(el('h3', item.question || item.tool || 'Approval required'), el('p', [item.work_item, item.project_id, item.job_id].filter(Boolean).join(' · ')));
    const choices = [];
    const questions = [];
    const publish = item.publish && item.effect_id;
    const isGate = item.kind === 'gate' || item.kind === 'publish';
    if (publish) appendPublishEvidence(card, item);
    else if (isGate) {
      const group = el('fieldset'); group.append(el('legend', item.multi_select ? 'Choose options' : 'Choose one option'));
      for (const option of item.options || []) {
        const choice = input('needs-' + id + '-' + option.id, item.multi_select ? 'checkbox' : 'radio');
        choice.name = 'needs-' + id; choice.value = option.id; choices.push(choice);
        group.append(field(option.label || option.id, choice));
      }
      card.append(group);
      if (item.risk || item.publish) card.append(el('p', [item.risk && 'Risk: ' + item.risk, item.publish && 'Publication approval'].filter(Boolean).join(' · ')));
      if (item.evidence?.length) card.append(el('pre', JSON.stringify(item.evidence, null, 2)));
    } else {
      card.append(el('p', item.approval_kind || 'Action approval'), el('pre', JSON.stringify(item.request || {}, null, 2)));
      for (const question of item.request?.questions || []) {
        const answer = input('needs-answer-' + id + '-' + question.id);
        questions.push([question.id, answer]);
        card.append(field(question.question || question.header || question.id, answer));
      }
    }
    const feedback = el('p'); feedback.setAttribute('role', 'status');
    let deciding = false;
    const resolve = async payload => {
      if (deciding) return;
      deciding = true;
      const fields = [...card.querySelectorAll('button,input')];
      fields.forEach(node => { node.disabled = true; });
      feedback.textContent = 'Sending your decision…';
      try {
        await post('/v1/approvals/' + encodeURIComponent(id), payload);
        state.activity.needs_you = state.activity.needs_you.filter(candidate => (candidate.gate_id || candidate.approval_id) !== id);
        renderInbox();
        await refresh();
      } catch (failure) {
        feedback.textContent = failure.message;
        if (['approval_expired', 'gate_expired', 'gate_invalidated', 'gate_already_resolved'].includes(failure.code)) await refresh();
        else { fields.forEach(node => { node.disabled = false; }); fields.find(node => node.tagName === 'BUTTON')?.focus(); }
      } finally { deciding = false; }
    };
    if (publish) {
      card.append(button('Approve', () => resolve({ choice: 'approve' })), button('Deny', () => resolve({ choice: 'deny' })));
    } else if (isGate) {
      const submit = button('Submit answer', () => {
        const selected = choices.filter(choice => choice.checked).map(choice => choice.value);
        if (selected.length) void resolve({ choice: item.multi_select ? selected : selected[0] });
      });
      submit.disabled = true;
      choices.forEach(choice => choice.addEventListener('change', () => { submit.disabled = !choices.some(choice => choice.checked); }));
      card.append(submit);
    } else {
      card.append(button('Allow once', () => resolve({ approved: true, scope: 'once', answers: Object.fromEntries(questions.map(([id, answer]) => [id, { answers: [answer.value] }])) })), button('Deny', () => resolve({ approved: false, scope: 'once' })));
    }
    card.append(feedback);
    return card;
  }
  window.runConsole = {
    attachAnswer(target, id) {
      if (!id) return;
      target.append(button('View run', async () => { syncContext(); toggle(true); setTab('Pipeline'); await chooseRun(id); }));
    },
    observe(event) {
      if (event.job_id === state.run || job === state.run) {
        state.more = true;
        if (state.tab === 'Logs') renderLogs();
      }
      if (!['answer_delta', 'reasoning_delta'].includes(event.type)) {
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(refresh, 120);
      }
    },
  };
  resize(330);
  void refresh();
  setInterval(() => { if (!document.hidden) void refresh(); }, 4000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void refresh(); });
})();
