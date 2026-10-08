"""The pinned app-server's effective safe configuration response."""

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
