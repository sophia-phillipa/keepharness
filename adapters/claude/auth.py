"""Claude Code signs in once, into the harness-owned home (decision D02)."""

from adapters.shared.provider_setup import login_environment


def cli_login_environment(state=None):
    """The terminal's own login token never reaches the sign-in or the status check."""
    environment = login_environment(state, "claude")
    environment.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    return environment
