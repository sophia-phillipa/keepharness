"""Explicit control of administratively registered user service units only."""

import re
import shutil

from .tools import ToolError, process


async def operate(config, project, action, unit=""):
    if not shutil.which("systemctl"):
        raise ToolError("service_manager_unavailable_requires_systemd_user")
    units = project.get("service_units", [])
    if action not in ("list", "status", "start", "stop", "restart"):
        raise ToolError("invalid_service_action")
    if action != "list" and unit not in units:
        raise ToolError("service_not_registered")
    selected = units if action == "list" else [unit]
    for name in selected:
        if not re.fullmatch(r"[A-Za-z0-9_@.-]+\.service", name) or name.startswith("-"):
            raise ToolError("invalid_service_unit")
    result = []
    for name in selected:
        operation = None
        if action in ("start", "stop", "restart"):
            code, output = await process(["systemctl", "--user", action, name], 60)
            operation = {"exit_code": code, "output": output[:3000]}
        code, output = await process(
            [
                "systemctl",
                "--user",
                "show",
                name,
                "--property=Id,LoadState,ActiveState,SubState",
                "--no-pager",
            ],
            15,
        )
        fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        result.append(
            {"unit": name, "status": fields, "status_exit_code": code, "operation": operation}
        )
    return {"action": action, "services": result}
