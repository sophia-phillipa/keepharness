"""DeepSeek's Codex state facade, bound to the same private homes as its runtime."""

from pathlib import Path

from adapters.codex.state import CodexStateAdapter
from adapters.shared.private_files import validate_private_file
from adapters.shared.provider_state import ProviderStateSchemaError, SecretStr
from agent_service.tools import ToolError
from control.product import PRODUCT

from .account import store_key


class DeepSeekStateAdapter(CodexStateAdapter):
    provider = "deepseek"
    extra_args = ("-c", 'cli_auth_credentials_store="file"')

    def __init__(self, state):
        self.state = Path(state)
        super().__init__(
            environment={
                "HOME": str(self.state / "providers" / "home"),
                "CODEX_HOME": str(self.state / "providers" / "deepseek"),
            }
        )

    def _environment(self):
        # Listing state never needs the API key or any credential contents. Reject
        # directory aliases before the app-server can follow them to the owner's state.
        home = Path(self.environment["CODEX_HOME"])
        try:
            for value in self.environment.values():
                path = Path(value)
                if any(part.is_symlink() for part in (path, *path.parents)):
                    raise ProviderStateSchemaError(
                        "DeepSeek's private home must not contain symlinks."
                    )
            try:
                metadata = (home / "config.toml").lstat()
            except FileNotFoundError:
                pass
            else:
                validate_private_file(metadata)
        except (OSError, ToolError):
            raise ProviderStateSchemaError(
                "DeepSeek's private config is unsafe or unreadable."
            ) from None
        for name in self.credential_isolation()["forbidden_files"]:
            try:
                (home / name).lstat()
            except FileNotFoundError:
                continue
            except OSError:
                raise ProviderStateSchemaError(
                    "DeepSeek's credential isolation is unreadable."
                ) from None
            raise ProviderStateSchemaError(
                "DeepSeek's private home contains foreign authentication."
            )
        return super()._environment()

    def credential_isolation(self):
        return {
            "home": self.environment["CODEX_HOME"],
            "forbidden_files": ("auth.json", "secrets/codex_auth.age"),
            "keyring": "Codex Auth / codex",
            "env_only": (PRODUCT.env_prefix + "_API_KEY",),
        }

    def set_api_key(self, secret: SecretStr) -> None:
        store_key(self.state, secret.get_secret_value())
