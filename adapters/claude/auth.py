"""Claude Code signs in on the CLI's own login (decision D-038)."""

from adapters.shared.provider_setup import login_environment


def cli_login_environment():
    """The terminal's own login token never reaches the sign-in or the status check."""
    environment = login_environment()
    environment.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    return environment
