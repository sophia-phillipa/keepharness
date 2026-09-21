"""Use the browser login only after an explicit account renewal."""
import os


def cli_login_environment():
    environment = dict(os.environ)
    environment.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    return environment
