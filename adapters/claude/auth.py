"""Use the browser login only after an explicit account renewal."""

from adapters.shared.process import child_environment


def cli_login_environment():
    environment = child_environment()
    environment.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    return environment
