"""Minimal owner-provisioned publication contracts and separate credential storage."""

import json
import os
import re
import stat
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .errors import APIError


class CredentialStore:
    """Secrets never enter runtime configuration, provider options or effect records."""

    def __init__(self, path):
        self.path = Path(path)

    def _read(self):
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return {}
        except OSError:
            raise APIError("effect_credentials_unavailable") from None
        with os.fdopen(descriptor) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise APIError("effect_credentials_not_private")
            try:
                value = json.load(stream)
            except (ValueError, UnicodeError):
                raise APIError("effect_credentials_unavailable") from None
        if not isinstance(value, dict):
            raise APIError("effect_credentials_unavailable")
        return value

    def get(self, binding):
        value = self._read().get(binding)
        if (
            not isinstance(value, dict)
            or set(value) != {"email", "token"}
            or not all(
                isinstance(item, str) and item and "\n" not in item and "\r" not in item
                for item in value.values()
            )
        ):
            raise APIError("effect_credentials_unavailable")
        return value

    def set(self, binding, value):
        """Owner-side provisioning only; no HTTP or MCP credential writer exists."""
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        values = self._read()
        values[binding] = value
        descriptor, temporary = tempfile.mkstemp(
            prefix=".harness-credentials-", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(values, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def integration_contract(config, integration):
    matches = [
        item
        for item in config.get("effect_integrations", [])
        if isinstance(item, dict) and item.get("integration") == integration
    ]
    if len(matches) != 1:
        raise APIError("effect_integration_unavailable")
    contract = matches[0]
    if (
        contract.get("mediated") is not True
        or contract.get("operation") != "jira.create_issue"
        or not isinstance(contract.get("credential_binding"), str)
        or not contract["credential_binding"]
        or not isinstance(contract.get("destination_allowlist"), list)
        or not contract["destination_allowlist"]
        or not all(
            isinstance(item, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", item)
            for item in contract["destination_allowlist"]
        )
    ):
        raise APIError("effect_contract_invalid")
    endpoint = contract.get("endpoint")
    if not isinstance(endpoint, str):
        raise APIError("effect_contract_invalid")
    parsed = urlsplit(endpoint)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or not (
            parsed.scheme == "https"
            or parsed.scheme == "http"
            and parsed.hostname in ("127.0.0.1", "::1")
        )
    ):
        raise APIError("effect_contract_invalid")
    # Reject hidden credentials/extra routing knobs instead of forwarding arbitrary config.
    if set(contract) != {
        "integration",
        "operation",
        "destination_allowlist",
        "mediated",
        "credential_binding",
        "endpoint",
    }:
        raise APIError("effect_contract_invalid")
    return json.loads(json.dumps(contract))


def validate_request(contract, request):
    if not isinstance(request, dict) or set(request) != {
        "integration",
        "operation",
        "destination",
        "arguments",
        "artifact",
    }:
        raise APIError("effect_request_invalid")
    if (
        request["operation"] != contract["operation"]
        or request["integration"] != contract["integration"]
    ):
        raise APIError("effect_operation_unsupported")
    if request["destination"] not in contract["destination_allowlist"]:
        raise APIError("effect_destination_denied", 403)
    if request["arguments"] != {}:
        raise APIError("effect_arguments_invalid")
    artifact = request["artifact"]
    if (
        not isinstance(artifact, dict)
        or set(artifact) != {"fields"}
        or not isinstance(artifact["fields"], dict)
    ):
        raise APIError("effect_artifact_invalid")
    fields = artifact["fields"]
    if (
        fields.get("project") != {"key": request["destination"]}
        or not isinstance(fields.get("summary"), str)
        or not fields["summary"].strip()
    ):
        raise APIError("effect_artifact_invalid")
    if not isinstance(fields.get("issuetype"), dict) or not fields["issuetype"]:
        raise APIError("effect_artifact_invalid")
    labels = fields.get("labels", [])
    if not isinstance(labels, list) or not all(
        isinstance(label, str) and not label.startswith("harness-effect-") for label in labels
    ):
        raise APIError("effect_artifact_invalid")
    try:
        if len(json.dumps(request, allow_nan=False).encode()) > 100000:
            raise APIError("effect_request_too_large")
    except (ValueError, TypeError):
        raise APIError("effect_request_invalid") from None


def main():
    """Provision one binding from the owner terminal without secrets in argv/env."""
    import argparse
    import getpass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Harness runtime JSON path")
    parser.add_argument(
        "--binding", required=True, help="Credential binding from effect_integrations"
    )
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    contracts = [
        integration_contract(config, item["integration"])
        for item in config.get("effect_integrations", [])
    ]
    if not any(item["credential_binding"] == args.binding for item in contracts):
        parser.error("binding is not referenced by an effect integration")
    email = input("Jira account email: ").strip()
    token = getpass.getpass("Jira API token: ")
    if not email or not token or any(char in email + token for char in "\r\n"):
        parser.error("email and token must be nonempty single-line values")
    path = config.get(
        "effect_credentials_path", Path(config["state_dir"]) / "harness.effect_credentials.json"
    )
    CredentialStore(path).set(args.binding, {"email": email, "token": token})
    print("Credential binding saved in the private harness store.")


if __name__ == "__main__":
    main()
