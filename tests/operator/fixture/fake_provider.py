"""Deterministic stand-in for the Claude Code CLI (stream-json) and the Gemini CLI (ACP).

The operator suite's fixture harness points its provider binaries here, so every
walkthrough streams, reads images, asks for approval and fails on cue without any
cloud inference. The last marker in the newest message (after any replayed history)
picks the behavior:

    OP-SLOW      long stream (about 14 s) for Stop and reload-resume
    OP-TABLE     Markdown table, long code line and a long URL
    OP-IMAGE     describes the attached images (count, media type, size)
    OP-TOOLS     reasoning plus a Read tool call, for the run console
    OP-APPROVAL  asks to run a shell command and reports the decision
    OP-QUESTION  asks a multiple-choice question and reports the answer
    OP-ERROR     ends the run with a provider error
    (none)       a short streamed reply that contains OPERATOR_OK
"""

import base64
import json
import re
import sys
import time
import uuid

MARKER = re.compile(r"OP-(SLOW|TABLE|IMAGE|TOOLS|APPROVAL|QUESTION|ERROR)")
TABLE = (
    "Here is the fixture matrix.\n\n"
    "| Area | Configuration | Availability | Infrastructure | Observability | Ownership | Notes |\n"
    "|---|---|---|---|---|---|---|\n"
    "| Composer | ready | 99.9% | local | traced | operator | wide table row |\n"
    "| Console | ready | 99.5% | local | traced | operator | second row |\n\n"
    "```python\n"
    "def fixture_line():\n"
    "    return '" + "x" * 200 + "'\n"
    "```\n\n"
    "Reference: https://example.invalid/" + "long-path-segment/" * 8 + "end\n"
)


def emit(value):
    print(json.dumps(value), flush=True)


def marker(text):
    # A replayed history precedes "CURRENT REQUEST:" and sources follow "SOURCES:";
    # only the newest message picks the behavior.
    newest = text.rsplit("CURRENT REQUEST:\n", 1)[-1].split("\nSOURCES:\n", 1)[0]
    found = MARKER.findall(newest)
    return found[-1] if found else ""


def chunks(text, size=12):
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def image_summary(images):
    if not images:
        return "No image reached the fixture."
    parts = []
    for item in images:
        size = len(base64.b64decode(item.get("data", ""), validate=False))
        parts.append(f"{item.get('media_type') or item.get('mimeType', '?')} ({size} bytes)")
    return f"The fixture received {len(images)} image(s): " + ", ".join(parts) + ". OPERATOR_OK"


# ---------------------------------------------------------------- Claude Code (stream-json)


def claude_metadata(request):
    kind = request.get("request", {}).get("subtype")
    if kind == "get_usage":
        reset = int(time.time()) + 3600
        payload = {
            "rate_limits": {
                "five_hour": {"utilization": 42, "resets_at": reset},
                "seven_day": {"utilization": 17, "resets_at": reset + 86400},
            }
        }
    else:
        effort = {"supportsEffort": True, "supportedEffortLevels": ["low", "medium", "high"]}
        payload = {"models": [{"value": "claude-sonnet-5-5", **effort}, {"value": "claude-opus-5-5", **effort}]}
    emit(
        {
            "type": "control_response",
            "response": {"subtype": "success", "request_id": request["request_id"], "response": payload},
        }
    )


def claude_stream(text, model, session, delay=0.05):
    message = "msg-" + uuid.uuid4().hex[:8]
    emit({"type": "stream_event", "session_id": session, "event": {"type": "message_start", "message": {"id": message, "usage": {"output_tokens": 1}}}})
    for piece in chunks(text):
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": piece}}})
        time.sleep(delay)
    emit({"type": "stream_event", "event": {"type": "message_delta", "usage": {"output_tokens": max(1, len(text) // 4)}}})


def claude_tool(tool_id, name, tool_input, failed=False):
    emit({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}}})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "fixture output", "is_error": failed}]}})


def claude_result(text, session, error=False):
    if error:
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "session_id": session})
        return
    usage = {"input_tokens": 120, "output_tokens": max(1, len(text) // 4), "cache_read_input_tokens": 0}
    emit({"type": "result", "subtype": "success", "result": text, "session_id": session, "usage": usage})


def claude(first, arguments):
    model = arguments[arguments.index("--model") + 1] if "--model" in arguments else "fixture"
    session = "fixture-" + uuid.uuid4().hex[:12]
    content = first.get("message", {}).get("content", [])
    text = " ".join(part.get("text", "") for part in content if part.get("type") == "text")
    images = [part.get("source", {}) for part in content if part.get("type") == "image"]
    emit({"type": "system", "subtype": "init", "session_id": session, "model": model})
    kind = marker(text)
    if kind == "ERROR":
        claude_stream("The fixture is about to fail on purpose. ", model, session)
        claude_result("", session, error=True)
        return
    if kind == "SLOW":
        answer = "Slow fixture stream. " + " ".join(f"chunk-{i:02d}" for i in range(40)) + " OPERATOR_OK"
        claude_stream(answer, model, session, delay=0.35)
    elif kind == "TABLE":
        answer = TABLE + "OPERATOR_OK"
        claude_stream(answer, model, session, delay=0.01)
    elif kind == "IMAGE":
        answer = image_summary(images)
        claude_stream(answer, model, session)
    elif kind == "TOOLS":
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "Planning a fixture read."}}})
        claude_tool("tool-read-1", "Read", {"file_path": "README.md"})
        claude_tool("tool-grep-1", "Grep", {"pattern": "fixture"})
        answer = "The fixture read README.md and searched for fixture. OPERATOR_OK"
        claude_stream(answer, model, session)
    elif kind in ("APPROVAL", "QUESTION"):
        if kind == "APPROVAL":
            emit({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "tool-bash-1", "name": "Bash", "input": {"command": "echo operator-fixture"}}}})
            request = {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "echo operator-fixture", "description": "Print a fixture line"}}
        else:
            question = {"question": "Which fixture option should run?", "options": [{"label": "Alpha", "description": "First option"}, {"label": "Beta", "description": "Second option"}], "multiSelect": False}
            request = {"subtype": "can_use_tool", "tool_name": "AskUserQuestion", "input": {"questions": [question]}}
        emit({"type": "control_request", "request_id": "fixture-request-1", "request": request})
        reply = json.loads(sys.stdin.readline() or "{}").get("response", {}).get("response", {})
        allowed = reply.get("behavior") == "allow"
        if kind == "APPROVAL":
            emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tool-bash-1", "content": "operator-fixture" if allowed else "denied", "is_error": not allowed}]}})
            answer = ("Approval granted: the fixture command ran." if allowed else "Approval denied: nothing ran.") + " OPERATOR_OK"
        else:
            answers = reply.get("updatedInput", {}).get("answers", {})
            answer = ("You chose " + ", ".join(answers.values()) + "." if allowed else "The question was declined.") + " OPERATOR_OK"
        claude_stream(answer, model, session)
    else:
        answer = f"OPERATOR_OK from the {model} fixture. It received a {len(text)}-character prompt."
        claude_stream(answer, model, session)
    claude_result(answer, session)


# ---------------------------------------------------------------- Gemini CLI (ACP JSON-RPC)


def acp_reply(ident, result):
    emit({"jsonrpc": "2.0", "id": ident, "result": result})


def acp_chunks(session, text, delay=0.05):
    for piece in chunks(text):
        emit({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": piece}}}})
        time.sleep(delay)


def gemini(first):
    item, session, model = first, "gemini-fixture-" + uuid.uuid4().hex[:8], "fixture"
    while item is not None:
        method, ident, params = item.get("method"), item.get("id"), item.get("params", {})
        if method == "initialize":
            acp_reply(ident, {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}})
        elif method in ("session/new", "session/load"):
            session = params.get("sessionId", session)
            acp_reply(ident, {"sessionId": session})
        elif method == "session/set_model":
            model = params.get("modelId", model)
            acp_reply(ident, {})
        elif method == "session/prompt":
            parts = params.get("prompt", [])
            text = " ".join(part.get("text", "") for part in parts if part.get("type") == "text")
            images = [part for part in parts if part.get("type") == "image"]
            kind = marker(text)
            if kind == "ERROR":
                emit({"jsonrpc": "2.0", "id": ident, "error": {"code": -32603, "message": "fixture failure"}})
                return
            if kind == "IMAGE":
                answer = image_summary(images)
            elif kind == "SLOW":
                answer = "Slow Gemini fixture. " + " ".join(f"chunk-{i:02d}" for i in range(40)) + " OPERATOR_OK"
            else:
                answer = f"OPERATOR_OK from the {model} Gemini fixture. It received a {len(text)}-character prompt."
            acp_chunks(session, answer, 0.35 if kind == "SLOW" else 0.05)
            acp_reply(ident, {"stopReason": "end_turn"})
            return
        else:
            emit({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "unsupported"}})
        line = sys.stdin.readline()
        item = json.loads(line) if line.strip() else None


def main():
    line = sys.stdin.readline()
    if not line.strip():
        return
    first = json.loads(line)
    if first.get("jsonrpc"):
        gemini(first)
        return
    while first.get("type") == "control_request":
        claude_metadata(first)
        line = sys.stdin.readline()
        if not line.strip():
            return
        first = json.loads(line)
    claude(first, sys.argv[1:])


if __name__ == "__main__":
    main()
