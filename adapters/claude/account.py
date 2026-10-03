"""Read Claude's own catalog and account usage through its control protocol."""

import asyncio
import json
import math
import tempfile
import time
from datetime import datetime

from adapters.shared.process import child_environment, process_diagnostics

from .auth import cli_login_environment

# Active versions omitted by the CLI picker. Reviewed 2026-09-26 against
# https://platform.claude.com/docs/en/about-claude/model-deprecations and
# https://code.claude.com/docs/en/model-config (CLI effort, not Messages API).
# Listing a documented version does not establish entitlement for this account.
LEGACY_MODELS = {
    "claude-opus-5": ["configured", "low", "medium", "high", "xhigh", "max"],
    "claude-fable-5": ["configured", "low", "medium", "high", "xhigh", "max"],
    "claude-opus-4-8": ["configured", "low", "medium", "high", "xhigh", "max"],
    "claude-opus-4-7": ["configured", "low", "medium", "high", "xhigh", "max"],
    "claude-opus-4-6": ["configured", "low", "medium", "high", "max"],
    "claude-opus-4-6[1m]": ["configured", "low", "medium", "high", "max"],
    "claude-opus-4-5-20251101": ["configured"],
    "claude-sonnet-4-6": ["configured", "low", "medium", "high", "max"],
    "claude-sonnet-4-6[1m]": ["configured", "low", "medium", "high", "max"],
    "claude-sonnet-4-5-20250929": ["configured"],
}


async def metadata(config, subtype="initialize"):
    # No user message, tools, hooks, project settings, or inference in this probe.
    with tempfile.TemporaryDirectory(prefix="keepharness-claude-metadata-") as cwd:
        proc = await asyncio.create_subprocess_exec(
            config["binary"],
            "--print",
            "--verbose",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--setting-sources",
            "user",
            "--settings",
            '{"disableAllHooks":true,"enabledPlugins":{}}',
            "--no-session-persistence",
            cwd=cwd,
            env=child_environment(cli_login_environment() if config.get("use_cli_login") else None),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=1024 * 1024,
        )

        async def request(kind):
            proc.stdin.write(
                (
                    json.dumps(
                        {
                            "type": "control_request",
                            "request_id": kind,
                            "request": {"subtype": kind},
                        }
                    )
                    + "\n"
                ).encode()
            )
            await proc.stdin.drain()
            while line := await proc.stdout.readline():
                message = json.loads(line)
                response = message.get("response", {})
                if message.get("type") != "control_response" or response.get("request_id") != kind:
                    continue
                if response.get("subtype") != "success":
                    raise ValueError("claude_metadata_unavailable")
                return response.get("response", {})
            raise ValueError("claude_metadata_incomplete")

        async with process_diagnostics(proc, "claude"):
            async with asyncio.timeout(12):
                initialized = await request("initialize")
                return initialized if subtype == "initialize" else await request(subtype)


def model_catalog(data):
    models = {}
    disabled = set()
    for item in data.get("models", []):
        if item.get("disabled"):
            disabled.update((item.get("value"), item.get("resolvedModel")))
            continue
        levels = [
            e
            for e in item.get("supportedEffortLevels", [])
            if e in ("low", "medium", "high", "xhigh", "max")
        ]
        efforts = ["configured", *levels] if item.get("supportsEffort") else ["configured"]
        for model in (item.get("value"), item.get("resolvedModel")):
            if isinstance(model, str) and model and model != "default":
                models[model] = efforts
                if model.endswith("[1m]"):
                    models[model.removesuffix("[1m]")] = efforts
    if not models:
        raise ValueError("claude_catalog_unavailable")
    return {key: value for key, value in {**LEGACY_MODELS, **models}.items() if key not in disabled}


def quota_snapshot(data):
    buckets = {}
    limits = (
        (data.get("rate_limits") or {}) if data.get("rate_limits_available") is not False else {}
    )
    entries = [
        (key, limits.get(key), 300 if key == "five_hour" else 10080)
        for key in (
            "five_hour",
            "seven_day",
            "seven_day_opus",
            "seven_day_sonnet",
            "seven_day_oauth_apps",
        )
    ]
    entries += [
        ("model_" + str(i), item, None) for i, item in enumerate(limits.get("model_scoped") or [])
    ]
    for key, item, duration in entries:
        if not isinstance(item, dict):
            continue
        used = item.get("utilization")  # get_usage reports percent, unlike stream events.
        if type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 100:
            continue
        reset = item.get("resets_at")
        try:
            reset = (
                datetime.fromisoformat(reset.replace("Z", "+00:00")).timestamp()
                if isinstance(reset, str)
                else None
            )
        except ValueError:
            reset = None
        buckets[key] = {
            "limitName": item.get("display_name"),
            "primary": {"usedPercent": used, "windowDurationMins": duration, "resetsAt": reset},
        }
    return {
        "provider": "claude",
        "available": bool(buckets),
        "source": "cli_usage",
        "shared_account": True,
        "checked_at": time.time(),
        "rateLimitsByLimitId": buckets,
        "reason": None if buckets else "quota_not_reported",
    }
