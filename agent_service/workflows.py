"""Bounded sequential workflow documents and project-owned saved chains."""

import hashlib
import json
import os
import re
from pathlib import Path

from .invocations import Invocation, InvocationError, normalize_legacy_step, validate_chain
from .tools import ToolError

MAX_DOCUMENT_BYTES = 262144
MAX_RESULT_BYTES = 16384
IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")


class WorkflowError(ToolError):
    pass


def _json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def parse_document(text, suffix=".json"):
    if len(text.encode()) > MAX_DOCUMENT_BYTES:
        raise WorkflowError("workflow_too_large")
    try:
        if suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError:
                raise WorkflowError("workflow_yaml_unavailable_use_json") from None
            value = yaml.safe_load(text)
        else:
            value = json.loads(text)
        # Also rejects YAML-only values, cycles and nonfinite numbers.
        _json(value)
    except WorkflowError:
        raise
    except (ValueError, TypeError, RecursionError):
        raise WorkflowError("workflow_invalid_document") from None
    except Exception as error:
        if type(error).__module__.startswith("yaml"):
            raise WorkflowError("workflow_invalid_document") from None
        raise
    return value


def _schema(value, depth=0):
    if (
        not isinstance(value, dict)
        or depth > 8
        or set(value) - {"type", "properties", "required", "items", "enum", "description"}
    ):
        raise WorkflowError("workflow_invalid_schema")
    if value.get("type") not in (
        None,
        "object",
        "array",
        "string",
        "number",
        "integer",
        "boolean",
        "null",
    ):
        raise WorkflowError("workflow_invalid_schema")
    if "properties" in value:
        if not isinstance(value["properties"], dict):
            raise WorkflowError("workflow_invalid_schema")
        for item in value["properties"].values():
            _schema(item, depth + 1)
    if "items" in value:
        _schema(value["items"], depth + 1)
    if "required" in value and (
        not isinstance(value["required"], list)
        or any(not isinstance(key, str) for key in value["required"])
    ):
        raise WorkflowError("workflow_invalid_schema")
    if "enum" in value and (not isinstance(value["enum"], list) or not value["enum"]):
        raise WorkflowError("workflow_invalid_schema")


def _requirements(value, candidate):
    if not isinstance(value, dict) or set(value) - {
        "permissions",
        "integrations",
        "operations",
        "mode",
    }:
        raise WorkflowError("workflow_invalid_requirements")
    for name in ("permissions", "integrations", "operations"):
        required = value.get(name, [])
        if not isinstance(required, list) or any(
            not isinstance(item, str) or not item for item in required
        ):
            raise WorkflowError("workflow_invalid_requirements")
        if candidate is not None:
            allowed = candidate.get(name, {}) if name == "permissions" else candidate.get(name, [])
            if any(
                (
                    allowed.get(item) is not True
                    if isinstance(allowed, dict)
                    else item not in allowed
                )
                for item in required
            ):
                raise WorkflowError("workflow_requirement_denied:" + name)
    if "mode" in value:
        if not isinstance(value["mode"], str):
            raise WorkflowError("workflow_invalid_requirements")
        if candidate is not None and value["mode"] != candidate.get("mode"):
            raise WorkflowError("workflow_requirement_denied:mode")


def validate_result(value, schema):
    """Check the supported JSON-schema subset without coercing model output."""
    if value is None:
        return schema.get("type") == "null"
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
    }
    kind = schema.get("type")
    if kind == "null" or (kind in types and not isinstance(value, types[kind])):
        return False
    if kind in ("number", "integer") and isinstance(value, bool):
        return False
    if "enum" in schema and not any(
        type(value) is type(option) and value == option for option in schema["enum"]
    ):
        return False
    if isinstance(value, dict):
        if any(key not in value for key in schema.get("required", [])):
            return False
        return all(
            validate_result(value[key], item)
            for key, item in schema.get("properties", {}).items()
            if key in value
        )
    if isinstance(value, list) and "items" in schema:
        return all(validate_result(item, schema["items"]) for item in value)
    return True


def validate_workflow(value, available=None, resources=None):
    """Normalize declarations; available=None performs document validation only."""
    try:
        document = _json(value)
        if len(document.encode()) > MAX_DOCUMENT_BYTES:
            raise WorkflowError("workflow_too_large")
        value = json.loads(document)
    except (ValueError, TypeError, RecursionError):
        raise WorkflowError("workflow_invalid_document") from None
    if (
        not isinstance(value, dict)
        or value.get("version", 1) != 1
        or type(value.get("version", 1)) is not int
    ):
        raise WorkflowError("workflow_invalid_version")
    if any(key in value for key in ("parallel", "repeat")):
        raise WorkflowError("workflow_sequential_only")
    steps = value.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
        raise WorkflowError("workflow_invalid_steps")
    workflow_id = value.get("id", "workflow")
    if not isinstance(workflow_id, str) or not IDENTIFIER.fullmatch(workflow_id):
        raise WorkflowError("workflow_invalid_id")
    found = {}
    for item in resources or []:
        found.setdefault(item["resource_id"], []).append(item)
    normalized, seen, invocations = [], set(), []
    for index, raw in enumerate(steps):
        if not isinstance(raw, dict) or any(key in raw for key in ("parallel", "repeat")):
            raise WorkflowError("workflow_invalid_step")
        step = dict(raw)
        step_id = step.get("id", "step-" + str(index + 1))
        if not isinstance(step_id, str) or not IDENTIFIER.fullmatch(step_id) or step_id in seen:
            raise WorkflowError("workflow_invalid_step_id")
        try:
            if "kind" in step and "invocation" not in step:
                invocation = Invocation(
                    **{
                        key: step[key]
                        for key in ("kind", "resource_id", "args", "mode", "requested_backend")
                        if key in step
                    },
                    order=index,
                    id=step_id,
                )
            else:
                invocation = normalize_legacy_step(step, index)
            if (
                invocation.order != index
                or invocation.kind == "workflow"
                or invocation.mode == "conversational"
            ):
                raise InvocationError("workflow_invalid_invocation")
        except (InvocationError, KeyError, TypeError) as error:
            raise WorkflowError(str(error)) from None
        backend = invocation.requested_backend or step.get("backend")
        item = next(
            (
                item
                for item in found.get(invocation.resource_id, [])
                if item.get("backend") in (None, backend)
            ),
            None,
        )
        builtin = (
            invocation.kind == "builtin" and invocation.resource_id.startswith("builtin/")
        ) or (invocation.kind == "agent" and invocation.resource_id.startswith("builtin/roles/"))
        if invocation.kind == "builtin" and not builtin:
            raise WorkflowError("workflow_resource_unavailable")
        if (
            not builtin
            and resources is not None
            and (not item or not item.get("selectable") or item.get("kind") != invocation.kind)
        ):
            raise WorkflowError("workflow_resource_unavailable")
        if not builtin and available is not None and resources is None:
            raise WorkflowError("workflow_resource_unavailable")
        backend = invocation.requested_backend or step.get("backend") or (item or {}).get("backend")
        if invocation.requested_backend and step.get("backend") not in (None, backend):
            raise WorkflowError("workflow_backend_mismatch")
        model = step.get("model") or (item or {}).get("model")
        effort = step.get("effort") or (item or {}).get("effort")
        candidate = None
        if available is not None:
            candidate = next(
                (
                    choice
                    for choice in available
                    if choice["backend"] == backend
                    and choice["model"] == model
                    and effort in choice["efforts"]
                ),
                None,
            )
            if candidate is None:
                raise WorkflowError("workflow_model_or_effort_denied")
        requires = step.get("requires", {})
        _requirements(requires, candidate)
        gate = step.get("gate", False)
        if not isinstance(gate, (bool, dict)):
            raise WorkflowError("workflow_invalid_gate")
        if isinstance(gate, dict):
            if not isinstance(gate.get("question"), str) or not gate["question"].strip():
                raise WorkflowError("workflow_invalid_gate")
            options = gate.get("options", ["continue", "skip"])
            if not isinstance(options, list) or not 2 <= len(options) <= 12:
                raise WorkflowError("workflow_invalid_gate")
            options = [
                {"id": option, "label": option} if isinstance(option, str) else option
                for option in options
            ]
            if any(
                not isinstance(option, dict)
                or any(
                    not isinstance(option.get(key), str) or not option[key]
                    for key in ("id", "label")
                )
                for option in options
            ) or len({option["id"] for option in options}) != len(options):
                raise WorkflowError("workflow_invalid_gate")
            gate = {**gate, "options": options}
        if type(step.get("publish", False)) is not bool:
            raise WorkflowError("workflow_invalid_publish")
        if "effect" in step and (not isinstance(step["effect"], dict) or not step.get("publish")):
            raise WorkflowError("workflow_invalid_effect")
        for key in ("inputs", "outputs"):
            if key in step:
                _schema(step[key])
        condition = step.get("condition")
        if condition is not None:
            if (
                not isinstance(condition, dict)
                or set(condition) not in ({"from", "is"}, {"from", "equals"})
                or not isinstance(condition["from"], str)
                or condition["from"].split(".")[0] not in seen
            ):
                raise WorkflowError("workflow_invalid_condition")
        step.update(
            id=step_id,
            invocation=invocation.to_dict(),
            backend=backend,
            model=model,
            effort=effort,
            role=step.get("role") or (item or {}).get("name") or step_id,
            task=("/" + item.get("name", step_id) + " " + invocation.args)
            if item
            else step.get("task", invocation.args)
            if not builtin
            else invocation.args,
            reason=step.get("reason") or "Declared workflow step",
            gate=gate,
            publish=step.get("publish", False),
            requires=requires,
        )
        if step["publish"]:
            # Only the execution-scoped P3 executor can upgrade this label.
            step["enforcement"] = "unenforced"
        if item:
            step["resource_revision"] = item.get("revision", "")
            step["deps_revisions"] = item.get("deps_revisions", {})
            step["resource_selections"] = [
                {
                    "id": item["resource_id"],
                    "revision": item.get("revision", ""),
                    "token": "/" + item.get("name", step_id),
                }
            ]
        normalized.append(step)
        invocations.append(invocation)
        seen.add(step_id)
    try:
        validate_chain(invocations)
    except InvocationError as error:
        raise WorkflowError(str(error)) from None
    result = {**value, "id": workflow_id, "version": 1, "steps": normalized}
    result.pop("digest", None)
    result.pop("revision", None)
    result["digest"] = hashlib.sha256(_json(result).encode()).hexdigest()
    result["revision"] = result["digest"]
    return result


def load_workflow(path, available=None, resources=None):
    path = Path(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise WorkflowError("workflow_too_large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise WorkflowError("workflow_invalid_document") from None
    return validate_workflow(parse_document(text, path.suffix), available, resources)


def parse_result(text):
    if not isinstance(text, str):
        return None
    matches = list(re.finditer(r"(?m)^```harness-result\s*\n(.*?)\n```\s*$", text, re.S))
    if len(matches) != 1 or len(matches[0][1].encode()) > MAX_RESULT_BYTES:
        return None
    try:
        value = json.loads(matches[0][1])
        _json(value)
        return value if isinstance(value, dict) else None
    except (ValueError, TypeError, RecursionError):
        return None


def evaluate_condition(condition, outputs):
    parts = condition["from"].split(".")
    value = parse_result(outputs.get(parts[0]))
    for key in parts[1:]:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    if value is None:
        return None
    expected = condition.get("is", condition.get("equals"))
    return type(value) is type(expected) and value == expected


def discover_workflows(config, project_id, backend, *, private=False):
    from . import resources

    result = {"items": [], "warnings": []}
    project = config["projects"][project_id]
    locations = []
    if project.get("root"):
        locations.append(
            (Path(project["root"]).resolve(), "project", "project/" + project_id, "", "project")
        )
    for catalog in config.get("catalogs", []):
        if catalog.get("trusted") is True and catalog.get("id") in project.get("catalogs", []):
            locations.append(
                (
                    Path(catalog["root"]).resolve(),
                    "catalog",
                    "catalog/" + catalog["id"],
                    catalog.get("namespace", ""),
                    catalog["id"],
                )
            )
    for root, scope, identity, namespace, origin in locations:
        try:
            for path in resources.files(root / "workflows", root, [], "workflow"):
                if path.suffix not in (".json", ".yaml", ".yml"):
                    continue
                try:
                    text = resources.read(path)
                    document = validate_workflow(parse_document(text, path.suffix))
                    resource_id = identity + "/" + path.resolve().relative_to(root).as_posix()
                    name = document.get("name") or document["id"]
                    if not isinstance(name, str) or not resources.NAME.fullmatch(name):
                        raise WorkflowError("workflow_invalid_name")
                    item = dict(
                        id=resource_id,
                        resource_id=resource_id,
                        revision=hashlib.sha256(text.encode()).hexdigest(),
                        kind="workflow",
                        name=name,
                        description=str(document.get("description", "Sequential workflow"))[:1000],
                        scope=scope,
                        origin=origin,
                        source=str(path),
                        namespace=namespace,
                        backend=backend,
                        model="",
                        effort="",
                        mode="delegated",
                        argument_hint="",
                        native_command=False,
                        maintenance=False,
                        group="Workflows",
                        selectable=True,
                        unavailable_reason="",
                        preflight_hint="Review the sequential workflow before execution.",
                        compatibility={},
                    )
                    if private:
                        item.update(_text=text, _body=text, _meta=document)
                    result["items"].append(item)
                    if len(result["items"]) >= resources.MAX_FILES:
                        result["warnings"].append("Catalog limited to 500 resources.")
                        return result
                except (WorkflowError, ValueError, OSError, TypeError):
                    result["warnings"].append("Could not read the workflow " + str(path))
        except (resources.ResourceError, OSError, RuntimeError):
            result["warnings"].append("Could not scan workflows in " + str(root))
    return result


def resolve_workflow(config, project_id, resource_id, available=None):
    from . import maestro, resources

    if available is None:
        available = maestro.candidates(config, project_id)
    found, selected = {}, None
    for backend in dict.fromkeys(choice["backend"] for choice in available):
        for item in resources.discover(config, project_id, backend, private=True)["items"]:
            found[(backend, item["resource_id"])] = item
            if item["resource_id"] == resource_id and item["kind"] == "workflow":
                selected = item
    if selected is None:
        raise WorkflowError("workflow_resource_unavailable")
    document = parse_document(selected["_text"], Path(selected["source"]).suffix)
    # Resolve each invocation against the backend it will actually execute with.
    items = []
    for raw in document.get("steps", []):
        invocation = raw.get("invocation", raw)
        backend = invocation.get("requested_backend") or raw.get("backend")
        item = found.get((backend, invocation.get("resource_id")))
        if item:
            items.append(item)
    result = validate_workflow(document, available, items)
    result["resource_id"] = resource_id
    result["revision"] = selected["revision"]
    return result


def save_chain_as_workflow(project, plan, workflow_id, *, successful, catalogs=()):
    if successful is not True:
        raise WorkflowError("workflow_requires_successful_chain")
    if (
        not isinstance(workflow_id, str)
        or not IDENTIFIER.fullmatch(workflow_id)
        or not project.get("root")
    ):
        raise WorkflowError("workflow_invalid_save_target")
    root = Path(project["root"]).resolve()
    folder = root / "workflows"
    if folder.is_symlink():
        raise WorkflowError("workflow_invalid_save_target")
    for catalog in catalogs:
        if catalog.get("trusted") or catalog.get("pin"):
            catalog_root = Path(catalog["root"]).resolve()
            if folder.resolve().is_relative_to(catalog_root):
                raise WorkflowError("workflow_catalog_read_only")
    # Keep declarations only; never persist checkpoints, output, or approval state.
    keys = {
        "id",
        "invocation",
        "kind",
        "resource_id",
        "args",
        "mode",
        "requested_backend",
        "backend",
        "model",
        "effort",
        "role",
        "task",
        "reason",
        "gate",
        "publish",
        "requires",
        "inputs",
        "outputs",
        "condition",
        "effect",
    }
    value = {
        "version": 1,
        "id": workflow_id,
        "steps": [
            {key: item for key, item in step.items() if key in keys}
            for step in plan.get("steps", [])
        ],
    }
    validate_workflow(value)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (workflow_id + ".json")
    folder_descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            descriptor = os.open(
                path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=folder_descriptor,
            )
        except FileExistsError:
            raise WorkflowError("workflow_already_exists") from None
        with os.fdopen(descriptor, "w") as stream:
            stream.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    finally:
        os.close(folder_descriptor)
    return path
