const fs = require('node:fs/promises');
const path = require('node:path');
async function mount(page, handler) {
  await page.addInitScript(() => localStorage.setItem("tail-harness-tour-seen", "0.13.6"));
  await page.route('http://console.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/v1/')) {
      const reply = await handler(url, route.request());
      if (reply) return route.fulfill(reply);
      const data = url.pathname === '/v1/projects' ? { projects: ['sem-projeto'] }
        : url.pathname === '/v1/models' ? { models: [{ id: 'fixture', backend: 'local', efforts: ['configured'] }], providers: { local: true } }
        : url.pathname === '/v1/conversations' ? { conversations: [] }
        : url.pathname === '/v1/version' ? { version: 'fixture', build: 'fixture' } : {};
      return route.fulfill({ json: data });
    }
    const file = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
    return route.fulfill({ body: await fs.readFile(path.join(__dirname, file.startsWith('assets/') ? '../tail_ui' : '../agent_service', file)), contentType: file.endsWith('.svg') ? 'image/svg+xml' : file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html' });
  });
  await page.goto('http://console.test');
  await page.locator('#startup-gate').waitFor({ state: 'hidden' });
}
const run = { job_id: 'run-a', conversation_id: 'conversation-a', project_id: 'sem-projeto', state: 'running', backend: 'local', model: 'fixture', created: 1, work_item: 'CASE-42' };
const span = { span_id: 'span-a', trace_id: 'run-a', parent_id: null, kind: 'invoke_agent', name: 'Fixture agent', start_ts: 1, end_ts: null, status: 'unset', attrs: { 'gen_ai.request.model': 'fixture', 'gen_ai.usage.input_tokens': 23, 'harness.outcome': 'pending' }, events: [] };
module.exports = { mount, run, span };
