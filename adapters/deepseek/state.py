"""Read-only facade for DeepSeek's supported, isolated Codex engine (D-043)."""

from dataclasses import replace
from pathlib import Path

from adapters.codex.state import CodexStateAdapter
from adapters.shared.provider_state import ProviderStateUnsupportedError


class DeepSeekStateAdapter(CodexStateAdapter):
    provider = "deepseek"
    engine = "codex"

    def __init__(self, state_dir, *, environment=None):
        state = Path(state_dir)
        # Neither instructions nor an app-server request may inherit the owner's Codex home.
        super().__init__(
            environment={
                **(environment or {}),
                "HOME": str(state / "providers/home"),
                "CODEX_HOME": str(state / "providers/deepseek"),
            }
        )

    def read_state(self, project_root):
        snapshot = super().read_state(project_root)
        return replace(
            snapshot,
            provider="deepseek",
            items=tuple(
                replace(
                    item,
                    writable=False,
                    reason="Read-only DeepSeek facade; managed by its Codex engine.",
                )
                for item in snapshot.items
                if item.kind in ("hook", "instructions")
            ),
            warnings=(
                *snapshot.warnings,
                "DeepSeek uses its isolated Codex engine. dsh is not the active engine; dsh bridge configuration and trust are not verified.",
            ),
        )

    def set_enabled(self, *args, **kwargs):
        raise ProviderStateUnsupportedError("DeepSeek hooks and rules are read-only.")
