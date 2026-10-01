"""Minimal owner-provisioned publication contracts and separate credential storage."""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from control.product import PRODUCT

from .errors import APIError
from .secret_vault import SecretVault


class CredentialStore(SecretVault):
    """Backward-compatible Jira projection over the generalized private store."""

    def set(self, binding, value):
        self._read()
        super().set(binding, value)

    def get(self, binding):
        value = super().get(binding)
        if set(value) != {"email", "token"}:
            raise APIError("effect_credentials_unavailable")
        return value


def endpoint_identity(endpoint):
    """Canonical destination for duplicate detection, matching HTTP URL semantics."""
    parsed = urlsplit(endpoint)
    host = parsed.hostname.lower()
    if ":" not in host:
        import httpx

        host = httpx.URL(endpoint).raw_host.decode("ascii")
    if ":" in host:
        host = "[" + host + "]"
    port = parsed.port
    if port and port != {"http": 80, "https": 443}.get(parsed.scheme):
        host += ":" + str(port)
    return parsed.scheme.lower() + "://" + host + parsed.path.rstrip("/")


def validate_integration(contract):
    """Generic, explicit provider consumers and environment precedence."""
    required = {"integration", "consumers", "environment", "precedence", "mediated"}
    if (
        not isinstance(contract, dict)
        or not required <= set(contract)
        or set(contract) - required - {"endpoint"}
        or not isinstance(contract["integration"], str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", contract["integration"])
        or not isinstance(contract["consumers"], list)
        or not contract["consumers"]
        or any(
            not isinstance(item, str)
            or item not in {"codex", "claude", "gemini", "deepseek", "local"}
            for item in contract["consumers"]
        )
        or not isinstance(contract["precedence"], str)
        or contract["precedence"] not in {"vault", "environment"}
        or type(contract["mediated"]) is not bool
        or not isinstance(contract["environment"], dict)
    ):
        raise APIError("integration_contract_invalid")
    for name, field in contract["environment"].items():
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name)
            or name.startswith(
                (
                    "TAIL_HARNESS_",
                    "LOCAL_AGENT_",
                    "HARNESS_",
                    PRODUCT.env_prefix + "_",
                    "LD_",
                    "PYTHON",
                )
            )
            or name
            in {
                "PATH",
                "HOME",
                "SHELL",
                "BASH_ENV",
                "ENV",
                "NODE_OPTIONS",
                "ADMIN_TOKEN",
                "ADMIN_SECRET",
                "ADMIN_PASSWORD",
            }
            or "COOKIE" in name
            or not isinstance(field, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", field)
        ):
            raise APIError("integration_contract_invalid")
    if "endpoint" in contract:
        if not isinstance(contract["endpoint"], str):
            raise APIError("integration_contract_invalid")
        try:
            parsed = urlsplit(contract["endpoint"])
        except ValueError:
            raise APIError("integration_contract_invalid") from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
        ):
            raise APIError("integration_contract_invalid")
    return contract


def integration_environment(config, project_id, catalog_ids, backend, contracts=()):
    """Only explicitly bound advisory credentials enter an eligible provider."""
    result = {}
    available = {}
    for contract in [*config.get("integrations", []), *contracts]:
        contract = validate_integration(contract)
        name = contract["integration"]
        if name in available and available[name] != contract:
            raise APIError("integration_contract_ambiguous")
        available[name] = contract
    vault = SecretVault(
        config.get("secret_vault_path", Path(config["state_dir"]) / "harness.secrets.json")
    )
    for binding in config.get("integration_bindings", []):
        if binding.get("project_id") != project_id or (
            binding.get("catalog_id") and binding["catalog_id"] not in catalog_ids
        ):
            continue
        contract = available.get(binding.get("integration"))
        if contract is None:
            if any(
                item.get("integration") == binding.get("integration")
                for item in config.get("effect_integrations", [])
            ):
                integration_contract(config, binding["integration"], project_id)
                continue
            raise APIError("integration_contract_unavailable")
        if contract["mediated"] or backend not in contract["consumers"]:
            continue
        values = vault.get(binding["credential_binding"])
        for name, field in contract["environment"].items():
            if field not in values:
                raise APIError("integration_credential_field_missing")
            value = (
                os.environ.get(name, values[field])
                if contract["precedence"] == "environment"
                else values[field]
            )
            if name in result and result[name] != value:
                raise APIError("integration_environment_ambiguous")
            result[name] = value
    return result


def integration_contract(config, integration, project_id=None):
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
    contract = json.loads(json.dumps(contract))
    if project_id is not None and "integration_bindings" in config:
        from .catalog_pin import effective_catalogs

        catalog_ids = {
            item["id"] for item in effective_catalogs(config, config["projects"][project_id])
        }
        bindings = [
            item
            for item in config["integration_bindings"]
            if item.get("integration") == integration
            and item.get("project_id") == project_id
            and (not item.get("catalog_id") or item["catalog_id"] in catalog_ids)
        ]
        if len(bindings) != 1:
            raise APIError("effect_integration_scope_denied", 403)
        contract["credential_binding"] = bindings[0]["credential_binding"]
    return contract


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


def integration_preflight(config, project_id, catalog_id, backend, execution_mode, contracts=()):
    """Palette prerequisites derived only from contracts and write-only vault metadata."""
    bindings = [
        item
        for item in config.get("integration_bindings", [])
        if item.get("project_id") == project_id
        and (not item.get("catalog_id") or item["catalog_id"] == catalog_id)
    ]
    selected = {item.get("integration") for item in bindings}
    available = {
        item.get("integration"): item
        for item in config.get("integrations", [])
        if item.get("integration") in selected
    }
    available.update({item.get("integration"): item for item in contracts})
    if not available:
        return []
    try:
        status = SecretVault(
            config.get("secret_vault_path", Path(config["state_dir"]) / "harness.secrets.json")
        ).status()
    except APIError:
        return ["The private integration vault is unavailable."]
    fields = {item["binding"]: set(item["fields"]) for item in status}
    problems = []
    for name, contract in available.items():
        try:
            validate_integration(contract)
        except APIError:
            problems.append("Invalid integration contract: " + str(name))
            continue
        if backend not in contract["consumers"]:
            problems.append("Integration is unavailable for this provider: " + name)
            continue
        matches = [item for item in bindings if item.get("integration") == name]
        if len(matches) != 1:
            problems.append("Configure one project/catalog credential binding in Admin: " + name)
            continue
        binding = matches[0].get("credential_binding")
        if binding not in fields:
            problems.append("Provision the credential binding in Admin: " + name)
        elif set(contract["environment"].values()) - fields[binding]:
            problems.append("Provision the required credential fields in Admin: " + name)
        elif not contract["mediated"] and (execution_mode != "native" or backend == "local"):
            problems.append(
                "Integration credential injection requires a supported native provider: " + name
            )
    return problems


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
