"""Non-inference Gemini CLI ACP capability probe and explicit OAuth launcher."""

import asyncio
import json
import signal
import tempfile
from pathlib import Path

from adapters.shared.process import process_diagnostics
from agent_service.tools import ToolError
from control.product import PRODUCT

from .policy import prepare


async def _request(proc, request_id, method, params):
    proc.stdin.write(
        (
            json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            + "\n"
        ).encode()
    )
    await proc.stdin.drain()
    while True:
        line = await proc.stdout.readline()
        if not line:
            raise ToolError("gemini_acp_incomplete")
        try:
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError()
        except ValueError:
            raise ToolError("gemini_acp_invalid") from None
        if item.get("id") == request_id:
            if "error" in item:
                message = str(item["error"].get("message", ""))
                if "no longer supported" in message and "Antigravity" in message:
                    raise ToolError("gemini_client_retired")
                raise ToolError("gemini_acp_unavailable")
            return item.get("result", {})


async def check(binary):
    """Confirm the locally selected OAuth state without an inference request."""
    settings_path = Path.home() / ".gemini" / "settings.json"
    selected_oauth = False
    try:
        settings = json.loads(settings_path.read_text())
        selected_oauth = (
            settings.get("security", {}).get("auth", {}).get("selectedType") == "oauth-personal"
        )
    except (OSError, ValueError, AttributeError):
        pass
    # Deliberately check only existence. OAuth credentials must never be read.
    credential_present = (Path.home() / ".gemini" / "oauth_creds.json").is_file()
    if not selected_oauth or not credential_present:
        return {
            "authenticated": False,
            "credential_present": False,
            "models": {},
            "error": "gemini_login_required",
        }
    try:
        return await _check_authenticated_cli(binary)
    except (ToolError, OSError, TimeoutError, ValueError) as exc:
        error = str(exc) if isinstance(exc, ToolError) else "gemini_check_unavailable"
        return {"authenticated": False, "credential_present": True, "models": {}, "error": error}


async def _check_authenticated_cli(binary):
    with tempfile.TemporaryDirectory(prefix="tail-harness-gemini-check-") as directory:
        command, environment = prepare({"binary": binary}, directory, {}, "ask")
        environment.pop("GEMINI_CLI_SYSTEM_SETTINGS_PATH", None)
        environment["NO_BROWSER"] = "true"
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=directory,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=environment,
        )
        async with process_diagnostics(proc, "gemini", environment=environment):
            result = await asyncio.wait_for(
                _request(
                    proc,
                    1,
                    "initialize",
                    {
                        "protocolVersion": 1,
                        "clientInfo": {"name": PRODUCT.mcp_name, "version": "0.13.6"},
                        "clientCapabilities": {
                            "auth": {"terminal": False},
                            "fs": {},
                            "terminal": False,
                        },
                    },
                ),
                timeout=8,
            )
            methods = result.get("authMethods", [])
            await asyncio.wait_for(
                _request(proc, 2, "authenticate", {"methodId": "oauth-personal"}),
                timeout=8,
            )
            return {
                "available": True,
                "oauth_personal": any(
                    isinstance(item, dict) and item.get("id") == "oauth-personal"
                    for item in methods
                ),
                "version": result.get("agentInfo", {}).get("version"),
                "authenticated": True,
                "credential_present": True,
                "models": {"auto-gemini-3": ["configured"]},
                "model_source": "Gemini CLI 0.60.0 local ACP capability probe; alias availability remains account-dependent",
            }


def _main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    args = parser.parse_args()

    async def run():
        task = asyncio.current_task()
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
        return await asyncio.wait_for(login(args.binary), timeout=240)

    print("Preparing Google login…", flush=True)
    try:
        asyncio.run(run())
    except asyncio.CancelledError:
        print("Login canceled.", flush=True)
        raise SystemExit(1) from None
    except Exception as error:
        if str(error) == "gemini_client_retired":
            print(
                "[gemini_client_retired] Google has ended Gemini CLI access for individual accounts, including Google AI Pro and Ultra. Repeating the login or running gemini in the terminal does not fix this.",
                flush=True,
            )
            print(
                "1. Open the official migration guide in a new tab: https://antigravity.google/docs/cli/gcli-migration/",
                flush=True,
            )
            print(
                "2. Follow the guide to install the Antigravity CLI and sign in with your Google account.",
                flush=True,
            )
            print(
                "3. Using it in this panel requires integrating the Antigravity engine. It is not yet available here; installing the CLI does not enable this Gemini card.",
                flush=True,
            )
            raise SystemExit(1) from None
        print(
            "Could not complete the Google login. Try again. To diagnose: 1. Open a terminal on this computer. 2. Run: gemini 3. If a Google login appears, complete it and go back to Verify account. If a migration to Antigravity appears, the client has been discontinued and repeating the login does not fix this.",
            flush=True,
        )
        raise SystemExit(1) from None
    print("Google login complete. Click Verify account in the panel.", flush=True)


async def login(binary):
    """Explicit OAuth action for the panel; never replace another auth type silently.

    The caller must run this only after the user asked to log in. Gemini CLI owns
    the browser flow and credential storage; this function never reads either.
    """
    with tempfile.TemporaryDirectory(prefix="tail-harness-gemini-login-") as directory:
        command, environment = prepare({"binary": binary}, directory, {}, "ask")
        environment.pop("GEMINI_CLI_SYSTEM_SETTINGS_PATH", None)
        environment.pop("NO_BROWSER", None)
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=directory,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=environment,
        )
        async with process_diagnostics(proc, "gemini", environment=environment):
            initial = await _request(
                proc,
                1,
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientInfo": {"name": PRODUCT.mcp_name, "version": "0.13.6"},
                    "clientCapabilities": {
                        "auth": {"terminal": False},
                        "fs": {},
                        "terminal": False,
                    },
                },
            )
            if not any(
                item.get("id") == "oauth-personal"
                for item in initial.get("authMethods", [])
                if isinstance(item, dict)
            ):
                raise ToolError("gemini_oauth_unavailable")
            print("Waiting for Google authorization in the browser…", flush=True)
            await _request(proc, 2, "authenticate", {"methodId": "oauth-personal"})
            return {"authenticated": True}


if __name__ == "__main__":
    _main()
