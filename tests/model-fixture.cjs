// Positive catalog fixtures use the provider modes supported by D-044.
// Tests for absent or malformed capability data must declare their own models.
function executionModes(backend) {
  if (backend === "local") return ["scoped"];
  if (["codex", "claude", "gemini", "deepseek"].includes(backend)) return ["native"];
  throw new Error(`Unknown fixture provider: ${backend}`);
}
module.exports = { executionModes };
