"""The pinned app-server's effective safe configuration response."""


def write_deepseek_key(path, token="fixture-key"):
    """Provision the private homes that production key setup creates before a run."""
    for name in ("deepseek", "home"):
        (path.parent / "providers" / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(token)


SAFE_CONFIG = {
    "config": {
        "cli_auth_credentials_store": "file",
        "allow_login_shell": False,
        "shell_environment_policy": {
            "ignore_default_excludes": False,
            "filters": {"KEEPHARNESS_API_KEY": "exclude"},
            "set": {},
        },
    }
}
