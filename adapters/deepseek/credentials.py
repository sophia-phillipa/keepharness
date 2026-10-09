"""Fail-closed credential isolation for the version-tested Codex engine."""

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

from adapters.shared.private_files import scoped_home_directory, scoped_home_read
from agent_service.secret_vault import redact_secrets
from agent_service.tools import ToolError
from control.product import PRODUCT


def keyring_entry_exists(service, account):
    """Search metadata only; an unavailable or inconclusive probe is not absence."""
    if sys.platform == "linux":
        result = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.secrets",
                "--object-path",
                "/org/freedesktop/secrets",
                "--method",
                "org.freedesktop.Secret.Service.SearchItems",
                "{'service': '" + service + "', 'username': '" + account + "'}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        if re.fullmatch(r"\(\s*(?:@ao\s*)?\[\s*\],\s*(?:@ao\s*)?\[\s*\]\s*\)\s*", result.stdout):
            return False
        if re.search(r"/org/freedesktop/secrets/", result.stdout):
            return True
        raise ValueError("inconclusive keyring probe")
    if sys.platform == "darwin":
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if result.returncode in (0, 44):
            return result.returncode == 0
    raise ValueError("keyring probe unavailable")


def private_environment(config):
    """Anchor the engine at the configured key's state, never an inherited home."""
    try:
        key = Path(config["api_provider"]["key_file"])
        if not key.is_absolute() or ".." in key.parts:
            raise ValueError("invalid key location")
        state = key.parent
        home = state / "providers" / "deepseek"
        if not home.is_dir():
            raise ValueError("missing home")
        with scoped_home_directory(home) as fd:
            if os.fstat(fd).st_uid != os.getuid():
                raise ValueError("foreign home")
            if os.fstat(fd).st_mode & 0o077:
                raise ValueError("home is not private")
        # lstat sees dangling links too; credentials are never read or removed.
        for path in (home / "auth.json", home / "secrets" / "codex_auth.age"):
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            raise ValueError("foreign authentication")
        digest = hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:16]
        for service, account in (("Codex Auth", "cli|" + digest), ("codex", "secrets|" + digest)):
            if keyring_entry_exists(service, account) is not False:
                raise ValueError("foreign keyring credential")
        token = scoped_home_read(state, key.name)
        if not token or not token.strip():
            raise ValueError("missing key")
        return {**os.environ, "HOME": str(home), "CODEX_HOME": str(home)}, token.strip()
    except (OSError, ValueError, TypeError, ToolError, subprocess.SubprocessError):
        raise ToolError("deepseek_credential_isolation") from None


async def check_shell_configuration(rpc, cwd):
    """Codex merges `set` after its exclusion filters; refuse credential reinsertion."""
    try:
        result = await rpc.call("config/read", {"cwd": str(cwd), "includeLayers": False})
        config = result["config"]
        policy = config["shell_environment_policy"]
        variable = PRODUCT.env_prefix + "_API_KEY"
        assigned = policy.get("set") or {}
        filters = {name.upper(): value for name, value in policy.get("filters", {}).items()}
        if (
            config.get("cli_auth_credentials_store") != "file"
            or config.get("allow_login_shell") is not False
            or policy.get("ignore_default_excludes") is not False
            or filters.get(variable) != "exclude"
            or not isinstance(assigned, dict)
            or any(name.upper() == variable for name in assigned)
            or redact_secrets(assigned) != assigned
            or policy.get("experimental_use_profile") is True
        ):
            raise ValueError("unsafe effective policy")
    except Exception:
        raise ToolError("deepseek_credential_isolation") from None
