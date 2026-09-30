"""Every error code the server can emit has readable copy in the harness UI (F-70, F-85).

The codes are collected from the source (``APIError``/``ToolError``/``ResourceError``
literals, attachment skip reasons, the worker's settle codes and the provider
conditions), so a new code fails here until ``agent_service/ui.js`` gives it a sentence
in ``userErrors``, ``attachmentError``, ``attachmentNotice`` or ``conditionCopy``, or it
is listed in ``INTERNAL_ONLY`` with the reason the web UI can never receive it.
"""

import re
from pathlib import Path

from agent_service.services import queue_worker

ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "agent_service" / "ui.js").read_text(encoding="utf-8")
SOURCES = [
    *sorted((ROOT / "agent_service").rglob("*.py")),
    *sorted((ROOT / "adapters").rglob("*.py")),
]
RAISED = re.compile(r"\b(?:APIError|ToolError|ResourceError)\(\s*[\"']([a-z0-9_]+)[\"']")
SKIP_REASON = re.compile(r"[\"']reason[\"']:\s*[\"']([a-z0-9_]+)[\"']")

# Codes built at runtime or written without an exception class.
DYNAMIC = {
    "claude_execution_failed",  # adapters/claude/stream.py fallback
    "codex_login_or_binary_unavailable",  # adapters/shared/scoped.py (provider + suffix)
    "claude_login_or_binary_unavailable",
    "context_limit_exceeded",  # queue_worker settle
    "service_restarted",  # conversation_service recovery
    "model_removed",  # conversation_service cancels a run on a config reload
    "configuration_changed",
    "session_rate_limit",  # conversation_service passes this code to its limiter
    "restart_schedule_failed",  # queue_worker deployment
    "internal_error",  # routes/__init__.py 500 body
    "capability_unavailable",  # execution decision reasons
    "unsupported",
}
# Raised only for non-inference jobs (``kind`` repository_*, test, propose_patch,
# web_fetch, web_search) that MCP clients submit; the web UI submits inference jobs only.
INTERNAL_ONLY = {
    "command_not_authorized",
    "fetch_failed",
    "invalid_arguments",
    "invalid_host",
    "invalid_line_range",
    "ipv4_required",
    "patch_limit",
    "private_address_denied",
    "public_https_required",
    "redirect_limit",
    "repository_unavailable",
    "snapshot_limit",
    "unknown_repository_action",
    "upstream_status_",
    "use_propose_patch",
    "web_content_type_denied",
}


def server_codes():
    codes = set(DYNAMIC)
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        codes.update(RAISED.findall(text))
    # Attachment selections report each skipped file as ``{"path", "reason"}``.
    codes.update(SKIP_REASON.findall((ROOT / "agent_service" / "workspaces.py").read_text()))
    codes.update(queue_worker.PROVIDER_CONDITIONS)
    codes.update(queue_worker.CONDITION_ALIASES)
    return codes


def block(start):
    """The source from ``start`` to the first line that closes it at column 0."""
    begin = UI.index(start)
    return UI[begin : UI.index("\n}", begin)]


def ui_codes():
    keys = re.compile(r"^\s+\"?([a-z0-9_]+)\"?:", re.M)
    codes = set()
    for start in (
        "const userErrors = {",
        "const conditionCopy = {",
        "function attachmentError(",
        "function attachmentNotice(",
    ):
        codes.update(keys.findall(block(start)))
    return codes


def test_every_server_code_has_readable_ui_copy():
    missing = sorted(server_codes() - INTERNAL_ONLY - ui_codes())
    assert not missing, "codes without UI copy: " + ", ".join(missing)


def test_internal_only_codes_are_still_raised():
    assert INTERNAL_ONLY <= server_codes()


def test_every_provider_condition_and_alias_has_condition_copy():
    copy = set(re.findall(r"^\s+([a-z_]+):", block("const conditionCopy = {"), re.M))
    assert queue_worker.PROVIDER_CONDITIONS | set(queue_worker.CONDITION_ALIASES) <= copy


def test_fallbacks_never_print_the_raw_code():
    assert "The server did not complete the request (" not in UI
    assert '"The run did not finish: " + error' not in UI
    assert "}[code] || code" not in UI
