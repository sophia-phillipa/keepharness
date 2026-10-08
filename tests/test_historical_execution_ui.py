"""Unknown historical transport is visibly unavailable and cannot become native."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_unknown_historical_execution_mode_is_unavailable_in_ui():
    if not shutil.which("node"):
        pytest.skip("Node.js unavailable")
    source = Path("agent_service/ui.js").read_text()

    def function(name):
        start = source.index("function " + name + "(")
        return source[start : source.index("\n}", start) + 2]

    script = "\n".join([function("executionModeLabel"), function("syncExecutionMode")])
    script += """
let executionMode = null, executionModeChosen = false;
const conversation = 'legacy', parent = null, busy = false, loading = false, submitting = false, uploads = false;
const elements = new Map();
const $ = id => { if (!elements.has(id)) elements.set(id, {classList: {toggle() {}}, dataset: {}, setAttribute(k, v) {this[k] = v;}}); return elements.get(id); };
const selected = () => ({backend: 'codex', execution_modes: ['native']});
const supportedExecutionModes = () => ['native'];
syncExecutionMode();
const assert = require('node:assert/strict');
assert.equal(executionMode, null);
assert.equal($('header-execution-mode').textContent, 'Execution mode unavailable');
assert.equal($('execution-mode-label').textContent, 'Execution mode unavailable');
assert.equal($('execution-mode-indicator')['aria-label'], 'Execution mode unavailable');
assert.match($('execution-mode-unavailable').textContent, /Start a new native conversation/);
assert.equal(supportedExecutionModes().includes(executionMode), false);
for (const [mode, label] of [['native', 'Native conversation'], ['scoped', 'Isolated conversation']]) {
  executionMode = mode;
  syncExecutionMode();
  assert.equal($('execution-mode-label').textContent, label);
  assert.equal($('header-execution-mode').textContent, label);
}
"""
    result = subprocess.run(["node", "-e", script], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "executionMode = data.execution_mode ?? null;" in source
