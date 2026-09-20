"""Non-inference Gemini CLI ACP capability probe and explicit OAuth launcher."""

import asyncio
import json
import os
import signal
import tempfile
from pathlib import Path

from agent_service.tools import ToolError
from .policy import prepare


async def _stop(proc):
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), 3)
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()


async def _request(proc, request_id, method, params):
    proc.stdin.write(
        (json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}) + "\n").encode()
    )
    await proc.stdin.drain()
    while True:
        line = await proc.stdout.readline()
        if not line:
            raise ToolError("gemini_acp_incomplete")
        try:
            item = json.loads(line)
            if not isinstance(item, dict):raise ValueError()
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
        selected_oauth = settings.get("security", {}).get("auth", {}).get("selectedType") == "oauth-personal"
    except (OSError, ValueError, AttributeError):
        pass
    # Deliberately check only existence. OAuth credentials must never be read.
    credential_present = (Path.home() / ".gemini" / "oauth_creds.json").is_file()
    if not selected_oauth or not credential_present:
        return {"authenticated": False, "credential_present": False, "models": {}, "error": "gemini_login_required"}
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
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            env=environment,
        )
        try:
            result = await asyncio.wait_for(_request(
            proc,
            1,
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": "tail-harness", "version": "0.5.0"},
                "clientCapabilities": {"auth": {"terminal": False}, "fs": {}, "terminal": False},
            },
            ), timeout=8)
            methods = result.get("authMethods", [])
            await asyncio.wait_for(
                _request(proc, 2, "authenticate", {"methodId": "oauth-personal"}),
                timeout=8,
            )
            return {
                "available": True,
                "oauth_personal": any(isinstance(item, dict) and item.get("id") == "oauth-personal" for item in methods),
                "version": result.get("agentInfo", {}).get("version"),
                "authenticated": True,
                "credential_present": True,
                "models": {"auto-gemini-3": ["configured"]},
                "model_source": "Gemini CLI 0.60.0 local ACP capability probe; alias availability remains account-dependent",
            }
        finally:
            if proc.returncode is None:
                await _stop(proc)


def _main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    args = parser.parse_args()
    async def run():
        task = asyncio.current_task()
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
        return await asyncio.wait_for(login(args.binary), timeout=240)
    print("Preparando o login Google…", flush=True)
    try:
        asyncio.run(run())
    except asyncio.CancelledError:
        print("Login cancelado.", flush=True)
        raise SystemExit(1) from None
    except Exception as error:
        if str(error) == "gemini_client_retired":
            print("[gemini_client_retired] O Google encerrou o acesso do Gemini CLI para contas individuais, incluindo Google AI Pro e Ultra. Repetir o login ou executar gemini no terminal não resolve.", flush=True)
            print("1. Abra o guia oficial de migração em uma nova aba: https://antigravity.google/docs/cli/gcli-migration/", flush=True)
            print("2. Siga o guia para instalar o Antigravity CLI e entrar com sua conta Google.", flush=True)
            print("3. Para utilizá-lo neste painel, é necessário integrar o motor Antigravity. Ele ainda não está disponível aqui; instalar o CLI não habilita este cartão Gemini.", flush=True)
            raise SystemExit(1) from None
        print("Não foi possível concluir o login Google. Tente novamente. Para diagnóstico: 1. Abra um terminal neste computador. 2. Execute: gemini 3. Se aparecer login Google, conclua-o e volte a Verificar conta. Se aparecer migração para Antigravity, o cliente foi descontinuado e repetir o login não resolve.", flush=True)
        raise SystemExit(1) from None
    print("Login Google concluído. Clique em Verificar conta no painel.", flush=True)


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
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            env=environment,
        )
        try:
            initial = await _request(
            proc,
            1,
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": "tail-harness", "version": "0.5.0"},
                "clientCapabilities": {"auth": {"terminal": False}, "fs": {}, "terminal": False},
            },
        )
            if not any(item.get("id") == "oauth-personal" for item in initial.get("authMethods", []) if isinstance(item, dict)):
                raise ToolError("gemini_oauth_unavailable")
            print("Aguardando autorização Google no navegador…", flush=True)
            await _request(proc, 2, "authenticate", {"methodId": "oauth-personal"})
            return {"authenticated": True}
        finally:
            if proc.returncode is None:
                await _stop(proc)


if __name__ == "__main__":
    _main()
