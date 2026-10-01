"""Bounded sequential workflow documents and project-owned saved chains."""

import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path

from .invocations import Invocation, InvocationError, normalize_legacy_step, validate_chain
from .tools import ToolError

MAX_DOCUMENT_BYTES = 262144
MAX_RESULT_BYTES = 16384
IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")


STEP_FIELDS = frozenset(
    {
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
)


class WorkflowError(ToolError):
    pass


def _json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _check_document(value):
    """Bound the expanded JSON tree before serializing shared YAML alias graphs."""
    pending = [(value, 0)]
    remaining = MAX_DOCUMENT_BYTES
    while pending:
        item, depth = pending.pop()
        if depth > 64:
            raise WorkflowError("workflow_invalid_document")
        if isinstance(item, (dict, list)):
            remaining -= 2 + max(0, len(item) - 1)
            if isinstance(item, dict):
                remaining -= len(item)
            if remaining < 0:
                raise WorkflowError("workflow_too_large")
            if isinstance(item, dict):
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise WorkflowError("workflow_invalid_document")
                    pending.extend(((key, depth + 1), (child, depth + 1)))
            else:
                pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            remaining -= len(item.encode()) + 2
        elif item is None or type(item) in (bool, int, float):
            if type(item) is float and not math.isfinite(item):
                raise WorkflowError("workflow_invalid_document")
            remaining -= len(json.dumps(item, allow_nan=False))
        else:
            raise WorkflowError("workflow_invalid_document")
        if remaining < 0:
            raise WorkflowError("workflow_too_large")


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
        _check_document(value)
        if len(_json(value).encode()) > MAX_DOCUMENT_BYTES:
            raise WorkflowError("workflow_too_large")
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


def validate_requirements(value, candidate):
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
                raise WorkflowError("workflow_requirement_denied")
    if "mode" in value:
        if not isinstance(value["mode"], str):
            raise WorkflowError("workflow_invalid_requirements")
        if candidate is not None and value["mode"] != candidate.get("mode"):
            raise WorkflowError("workflow_requirement_denied")


def json_equal(value, expected):
    """Compare JSON values recursively without Python's bool/number coercion."""
    if type(value) is not type(expected):
        return False
    if isinstance(value, dict):
        return value.keys() == expected.keys() and all(
            json_equal(item, expected[key]) for key, item in value.items()
        )
    if isinstance(value, list):
        return len(value) == len(expected) and all(
            json_equal(item, other) for item, other in zip(value, expected)
        )
    return value == expected


def validate_result(value, schema):
    """Check the supported JSON-schema subset without coercing model output."""
    types = {
        "null": type(None),
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
    }
    kind = schema.get("type")
    if kind in types and not isinstance(value, types[kind]):
        return False
    if kind in ("number", "integer") and isinstance(value, bool):
        return False
    if "enum" in schema and not any(json_equal(value, option) for option in schema["enum"]):
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


def validate_workflow(value, available=None, resources=None, *, retained=False):
    """Normalize declarations; available=None performs document validation only."""
    try:
        _check_document(value)
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
    document_fields = {"id", "version", "name", "description", "steps"}
    if retained:
        document_fields |= {"digest", "revision", "resource_id", "planner_revision"}
    if set(value) - document_fields:
        raise WorkflowError("workflow_unknown_field")
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
        allowed = STEP_FIELDS
        if retained:
            allowed = allowed | {
                "enforcement",
                "resource_revision",
                "deps_revisions",
                "resource_selections",
                "resource_snapshots",
            }
        if set(raw) - allowed:
            raise WorkflowError("workflow_unknown_step_field")
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
        validate_requirements(requires, candidate)
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
            if (
                gate.get("multi_select", False) is not False
                or any(
                    option["id"] not in {"approve", "continue", "skip", "deny"}
                    for option in options
                )
                or not any(option["id"] in {"approve", "continue", "skip"} for option in options)
            ):
                raise WorkflowError("workflow_invalid_gate")
            gate = {**gate, "options": options}
        if type(step.get("publish", False)) is not bool:
            raise WorkflowError("workflow_invalid_publish")
        if "effect" in step and (not isinstance(step["effect"], dict) or not step.get("publish")):
            raise WorkflowError("workflow_invalid_effect")
        for key in ("inputs", "outputs"):
            if key in step:
                _schema(step[key])
                if key == "outputs" and step[key].get("type") not in (None, "object"):
                    raise WorkflowError("workflow_invalid_schema")
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
    blocks, content = [], []
    marker = None
    result_block = False
    for line in text.splitlines(keepends=True):
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if match:
            fence, suffix = match.groups()
            if marker is None:
                if fence[0] != "`" or "`" not in suffix:
                    marker = fence
                    result_block = suffix.strip() == "harness-result"
                    content = []
                    continue
            elif fence[0] == marker[0] and len(fence) >= len(marker) and not suffix.strip():
                if result_block:
                    blocks.append("".join(content))
                marker = None
                result_block = False
                continue
        if marker and result_block:
            content.append(line)
    if marker and result_block or len(blocks) != 1 or len(blocks[0].encode()) > MAX_RESULT_BYTES:
        return None
    try:
        value = json.loads(blocks[0])
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
    return json_equal(value, expected)


def dependency_catalog(config, project_id, *, execution_mode=None):
    from . import maestro, resources

    available = maestro.candidates(config, project_id, execution_mode=execution_mode)
    items = [
        item
        for backend in dict.fromkeys(choice["backend"] for choice in available)
        for item in resources.discover(
            config,
            project_id,
            backend,
            private=True,
            execution_mode=execution_mode,
            include_workflows=False,
        )["items"]
    ]
    return available, items


def discover_workflows(config, project_id, backend, *, private=False, execution_mode=None):
    from . import resources
    from .catalog_manifest import load_manifest, preflight
    from .catalog_pin import effective_catalogs, snapshot_catalogs
    from .integrations import integration_preflight

    result = {"items": [], "warnings": []}
    project = config["projects"][project_id]
    locations = []
    if project.get("root"):
        locations.append(
            (Path(project["root"]).resolve(), "project", "project/" + project_id, "", "project")
        )
    for catalog in effective_catalogs(config, project):
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
    catalogs = {item["id"]: item for item in effective_catalogs(config, project)}
    dependencies = None
    for root, scope, identity, namespace, origin in locations:
        manifest, problems, snapshot = None, [], {}
        if scope == "catalog":
            try:
                manifest = load_manifest(root)
                if manifest:
                    problems = preflight(
                        root,
                        manifest,
                        config.get("control_state_dir", config.get("state_dir", "state")),
                        origin,
                    )
                snapshot = snapshot_catalogs({**config, "catalogs": [catalogs[origin]]}, project)[0]
                if snapshot.get("error"):
                    problems.append("Catalog unavailable: " + snapshot["error"])
                    result["warnings"].append(
                        "Catalog " + origin + " unavailable: " + snapshot["error"]
                    )
                mode = execution_mode or config.get("services", {}).get(backend, {}).get("mode")
                problems.extend(
                    integration_preflight(
                        config,
                        project_id,
                        origin,
                        backend,
                        mode,
                        (manifest or {}).get("integrations", []),
                    )
                )
                if (mode != "native" or backend == "local") and any(
                    key in (manifest or {})
                    for key in ("cwd", "runtime", "writable_state", "allowed_hooks")
                ):
                    problems.append(
                        "Catalog runtime prerequisites require a supported native provider."
                    )
            except (ValueError, OSError) as error:
                problems = ["Invalid catalog manifest: " + str(error)]
        folders = ["workflows", *(manifest or {}).get("resources", {}).get("workflow", [])]
        seen_paths = set()
        try:
            for path in (
                path
                for folder in folders
                for path in resources.files(root / folder, root, [], "workflow")
            ):
                if path.resolve() in seen_paths:
                    continue
                seen_paths.add(path.resolve())
                if path.suffix not in (".json", ".yaml", ".yml"):
                    continue
                try:
                    text = resources.read(path)
                    document = validate_workflow(parse_document(text, path.suffix))
                    resource_id = identity + "/" + path.resolve().relative_to(root).as_posix()
                    name = document.get("name") or document["id"]
                    if not isinstance(name, str) or not resources.NAME.fullmatch(name):
                        raise WorkflowError("workflow_invalid_name")
                    if dependencies is None:
                        dependencies = dependency_catalog(
                            config, project_id, execution_mode=execution_mode
                        )
                    item_problems = list(problems)
                    try:
                        Invocation("workflow", resource_id)
                    except InvocationError:
                        item_problems.append(
                            "Resource path is not a portable invocation identity. Rename the source path."
                        )
                    try:
                        validate_workflow(parse_document(text, path.suffix), *dependencies)
                    except WorkflowError as error:
                        item_problems.append(
                            {
                                "workflow_resource_unavailable": "A workflow step resource is missing or unavailable. Restore its resource before running this workflow.",
                                "workflow_model_or_effort_denied": "A workflow step model or effort is unavailable. Update the workflow or enable its model.",
                            }.get(
                                str(error), "Workflow prerequisites are unavailable: " + str(error)
                            )
                        )
                    item = dict(
                        id=resource_id,
                        resource_id=resource_id,
                        revision=hashlib.sha256(text.encode()).hexdigest(),
                        kind="workflow",
                        catalog_commit=snapshot.get("commit"),
                        catalog_dirty=snapshot.get("dirty"),
                        catalog_pinned=snapshot.get("pinned", False),
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
                        selectable=not bool(item_problems),
                        unavailable_reason="; ".join(item_problems),
                        preflight_hint="; ".join(item_problems)
                        or "Review the sequential workflow before execution.",
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


def resolve_workflow(config, project_id, resource_id, available=None, *, execution_mode=None):
    from . import maestro, resources

    if available is None:
        available = maestro.candidates(config, project_id, execution_mode=execution_mode)
    found, selected = {}, None
    for backend in dict.fromkeys(choice["backend"] for choice in available):
        for item in resources.discover(
            config, project_id, backend, private=True, execution_mode=execution_mode
        )["items"]:
            found[(backend, item["resource_id"])] = item
            if item["resource_id"] == resource_id and item["kind"] == "workflow":
                selected = item
    if selected is None or not selected["selectable"]:
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


def save_chain_as_workflow(
    project, plan, workflow_id, *, successful, catalogs=(), dependencies=(None, None)
):
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
    value = {
        "version": 1,
        "id": workflow_id,
        "steps": [
            {key: item for key, item in step.items() if key in STEP_FIELDS}
            for step in plan.get("steps", [])
        ],
    }
    validate_workflow(value, *dependencies)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (workflow_id + ".json")
    folder_descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = ".workflow-" + uuid.uuid4().hex
    published = False
    try:
        from .resources import ResourceError, files, read

        try:
            existing_paths = list(files(folder, root, [], "workflow"))
        except ResourceError as error:
            raise WorkflowError(str(error)) from None
        for existing in existing_paths:
            if existing.suffix not in (".json", ".yaml", ".yml"):
                continue
            try:
                document = validate_workflow(parse_document(read(existing), existing.suffix))
            except WorkflowError as error:
                if str(error) == "workflow_yaml_unavailable_use_json":
                    raise
                continue
            except (ValueError, OSError):
                continue
            if workflow_id in (document["id"], document.get("name")):
                raise WorkflowError("workflow_already_exists")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=folder_descriptor,
        )
        with os.fdopen(descriptor, "w") as stream:
            stream.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(
                temporary,
                path.name,
                src_dir_fd=folder_descriptor,
                dst_dir_fd=folder_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            raise WorkflowError("workflow_already_exists") from None
        published = True
        os.unlink(temporary, dir_fd=folder_descriptor)
        os.fsync(folder_descriptor)
    except BaseException:
        if published:
            os.unlink(path.name, dir_fd=folder_descriptor)
        raise
    finally:
        try:
            os.unlink(temporary, dir_fd=folder_descriptor)
        except FileNotFoundError:
            pass
        os.close(folder_descriptor)
    return path
