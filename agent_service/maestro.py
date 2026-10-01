"""Bounded sequential workflows using enabled project policies and durable checkpoints."""

import asyncio
import hashlib
import json
import uuid
from contextlib import contextmanager
from pathlib import Path

from .checkpoints import Checkpoints, digest, write_json
from .errors import HarnessError
from .invocations import InvocationError, normalize_legacy_step, validate_chain
from .tools import ToolError


@contextmanager
def execution(service, row, kind, metadata):
    """Pair every started stage with its explicit terminal outcome, even on cancellation."""
    metadata = {
        **metadata,
        "execution_id": uuid.uuid4().hex,
        "parent_execution_id": row["id"],
        "attempt": 1,
    }
    service.event(row["id"], kind, metadata)
    outcome = "completed"
    try:
        yield metadata
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception as exc:
        metadata["error_type"] = type(exc).__name__
        outcome = "failed"
        raise
    finally:
        service.event(row["id"], kind + "_completed", {**metadata, "outcome": outcome})


def execution_payload(metadata):
    return {
        "_execution_id": metadata["execution_id"],
        "_parent_execution_id": metadata["parent_execution_id"],
        "_attempt": metadata["attempt"],
    }


def model_permissions(config, provider, model, project_id=None):
    spec = config.get("services", {}).get(provider, {})
    if provider == "local" and "model_permissions" in spec:
        permissions = dict(spec["model_permissions"].get(model, {}))
    else:
        permissions = dict(spec.get("permissions", {}))
    if project_id is not None:
        for name, granted in (
            config.get("projects", {}).get(project_id, {}).get("permissions", {}).items()
        ):
            if granted is True:
                permissions[name] = True
    return permissions


def model_efforts(config, provider, model):
    if provider not in ("codex", "deepseek", "claude"):
        return ["configured"]
    catalog = config.get(provider + "_models", {})
    default = ["configured"] if provider == "claude" else []
    return catalog.get(model, default) if isinstance(catalog, dict) else default


def candidates(config, project, uploads=False):
    from .effect_transport import transport_support
    from .integrations import integration_contract

    result = []
    for provider, spec in config.get("services", {}).items():
        if not spec.get("enabled") or project not in spec.get("projects", []):
            continue
        for model in spec.get("models", []):
            permissions = model_permissions(config, provider, model, project)
            if uploads and not permissions.get("upload"):
                continue
            efforts = model_efforts(config, provider, model)
            if efforts:
                operations = []
                if transport_support(provider, spec.get("mode", "native"))["supported"]:
                    for contract in config.get("effect_integrations", []):
                        try:
                            valid = integration_contract(config, contract.get("integration"))
                            operations.append(valid["operation"])
                        except (HarnessError, AttributeError):
                            continue
                result.append(
                    {
                        "backend": provider,
                        "model": model,
                        "efforts": efforts,
                        "permissions": permissions,
                        "integrations": []
                        if provider == "local" and "model_permissions" in spec
                        else spec.get("integrations", []),
                        "mode": spec.get("mode", "native"),
                        "operations": sorted(set(operations)),
                    }
                )
    return result


def coordinator(config, project, *, workspace=False):
    if config.get("maestro_enabled", True) is not True:
        raise ToolError("maestro_disabled")
    configured = (
        config.get("projects", {})
        .get(project, {})
        .get("maestro_coordinator", config.get("maestro_coordinator"))
    )
    if configured is not None and not isinstance(configured, dict):
        raise ToolError("maestro_coordinator_unavailable")
    choice = configured or {"backend": "codex"}
    models = [
        m
        for m in candidates(config, project)
        if m["backend"] == choice.get("backend", "codex")
        and (not choice.get("model") or m["model"] == choice["model"])
    ]
    if not models:
        raise ToolError(
            "maestro_coordinator_unavailable"
            if configured
            else "maestro_requires_enabled_codex_for_project"
        )
    selected = models[0]
    effort = choice.get("effort") or (
        "low" if "low" in selected["efforts"] else selected["efforts"][0]
    )
    if effort not in selected["efforts"]:
        raise ToolError("maestro_coordinator_unavailable")
    if workspace and not all(selected["permissions"].get(key) for key in ("read", "upload")):
        raise ToolError("maestro_coordinator_workspace_denied")
    return {**selected, "effort": effort}


def validate_plan(raw, available):
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        plan = json.loads(text)
    except (ValueError, TypeError):
        raise ToolError("maestro_invalid_plan_json")
    if (
        not isinstance(plan, dict)
        or not isinstance(plan.get("steps"), list)
        or not 1 <= len(plan["steps"]) <= 12
    ):
        raise ToolError("maestro_invalid_steps")
    for step in plan["steps"]:
        if not isinstance(step, dict):
            raise ToolError("maestro_invalid_step")
        choice = next(
            (
                m
                for m in available
                if m["backend"] == step.get("backend")
                and m["model"] == step.get("model")
                and step.get("effort") in m["efforts"]
            ),
            None,
        )
        if not choice:
            raise ToolError("maestro_model_or_effort_denied")
        from .workflows import validate_requirements

        validate_requirements(step.get("requires", {}), choice)
        for key in ("task", "role", "reason"):
            if not isinstance(step.get(key), str) or not 1 <= len(step[key]) <= 8000:
                raise ToolError("maestro_invalid_step_description")
    try:
        invocations = [
            normalize_legacy_step(step, index) for index, step in enumerate(plan["steps"])
        ]
        validate_chain(invocations)
    except InvocationError as error:
        raise ToolError(str(error)) from None
    for step, invocation in zip(plan["steps"], invocations):
        step["invocation"] = invocation.to_dict()
    return plan


async def plan(service, row, data):
    available = candidates(
        service.config, row["project"], bool(data.get("file_ids") or data.get("workspace_id"))
    )
    if data.get("workspace_id"):
        available = [m for m in available if m["permissions"].get("read")]
    if not available:
        raise ToolError("maestro_no_eligible_agents")
    lead = coordinator(service.config, row["project"], workspace=bool(data.get("workspace_id")))
    manifest = []
    if data.get("workspace_id"):
        record = service.workspace(
            (row["owner"], service.config["clients"][row["owner"]]),
            data["workspace_id"],
            row["project"],
        )
        manifest = json.loads(record["manifest"])[:200]
    history = [
        {"request": p.get("prompt", "")[:3000], "answer": r.get("answer", "")[:6000]}
        for p, r in service.context_turns(row, data)[-3:]
    ]
    planner_text = (Path(__file__).parent / "prompts" / "maestro-planner.md").read_text()
    planner_revision = hashlib.sha256(planner_text.encode()).hexdigest()
    planner = (
        planner_text
        + "\nPOLICY OF THIS INSTALLATION:\n"
        + service.config.get("maestro_instructions", "")
        + "\n"
        + json.dumps(
            {
                "request": data.get("prompt", ""),
                "available_agents": available,
                "files": manifest,
                "history": history,
            },
            ensure_ascii=False,
        )
    )
    with execution(
        service,
        row,
        "maestro_planning",
        {
            "backend": lead["backend"],
            "model": lead["model"],
            "effort": lead["effort"],
            "work_item": data.get("work_item"),
        },
    ) as metadata:
        planning = await service.infer(
            row,
            {
                **data,
                **execution_payload(metadata),
                "backend": lead["backend"],
                "model": lead["model"],
                "effort": lead["effort"],
                "prompt": planner,
                "file_ids": [],
                "workspace_id": None,
                "parent_job_id": None,
                "_maestro_stage": "plan",
                "_planning_only": True,
            },
        )
        if planning.get("incomplete"):
            raise ToolError("maestro_incomplete_plan")
        plan = validate_plan(planning.get("answer", ""), available)
        plan["planner_revision"] = planner_revision
    return {"plan": plan, "planning_result": planning, "coordinator": lead}


def saved_plan(service, job_id):
    try:
        return json.loads((service.root / "maestro" / job_id / "plan.json").read_text())["plan"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ToolError("workflow_checkpoint_missing") from None


def declaration(plan):
    """Retention is execution state, not part of a bounded workflow declaration."""
    return {
        **{key: value for key, value in plan.items() if key != "workflow_snapshot"},
        "steps": [
            {key: value for key, value in step.items() if key != "resource_snapshots"}
            for step in plan.get("steps", [])
        ],
    }


def recovery_ancestors(service, job_id):
    """Walk durable recovery parents once, including the requested source."""
    seen = set()
    while job_id and job_id not in seen:
        seen.add(job_id)
        yield job_id
        row = service.conversation_repository.get(job_id)
        job_id = json.loads(row["payload"]).get("_workflow_parent_job_id") if row else None


def ensure_recovery_safe(service, job_id):
    """A child run cannot silently replay an ancestor's uncertain publication."""
    for ancestor in recovery_ancestors(service, job_id):
        if service.db.execute(
            "SELECT 1 FROM effects WHERE job_id=? AND status IN ('unknown','executing')",
            (ancestor,),
        ).fetchone():
            raise ToolError("workflow_effect_outcome_unknown")


def retain_resources(service, data, plan):
    if plan.get("resource_id"):
        from . import resources

        items = resources.discover(
            service.config, data["project_id"], plan["steps"][0]["backend"], private=True
        )["items"]
        workflow = next(
            (item for item in items if item["resource_id"] == plan["resource_id"]), None
        )
        if workflow is None:
            raise ToolError("workflow_resource_unavailable")
        plan["workflow_snapshot"] = {
            key: workflow.get(key) for key in ("resource_id", "revision", "_text")
        }
    for step in plan["steps"]:
        if not step.get("resource_selections"):
            continue
        selected = service.selected_resources({**data, **step, "prompt": step["task"]})
        step["resource_snapshots"] = [
            {
                key: item.get(key)
                for key in (
                    "resource_id",
                    "revision",
                    "deps_revisions",
                    "_text",
                    "_body",
                    "source",
                    "_dependency_texts",
                )
            }
            for item in selected
        ]


def resources_unchanged(service, data, plan):
    try:
        if plan.get("workflow_snapshot"):
            from . import resources

            items = resources.discover(
                service.config, data["project_id"], plan["steps"][0]["backend"], private=True
            )["items"]
            current = next(
                (item for item in items if item["resource_id"] == plan["resource_id"]), {}
            )
            if {key: current.get(key) for key in ("resource_id", "revision", "_text")} != plan[
                "workflow_snapshot"
            ]:
                return False
        for step in plan["steps"]:
            if step.get("resource_selections"):
                selected = service.selected_resources({**data, **step, "prompt": step["task"]})
                current = [
                    {
                        key: item.get(key)
                        for key in (
                            "resource_id",
                            "revision",
                            "deps_revisions",
                            "_text",
                            "_body",
                            "source",
                            "_dependency_texts",
                        )
                    }
                    for item in selected
                ]
                if current != step.get("resource_snapshots"):
                    return False
        return True
    except (ValueError, HarnessError, OSError):
        return False


def source_digest(root, name, maximum):
    path = root / name
    if (
        Path(name).is_absolute()
        or ".." in Path(name).parts
        or any(part.is_symlink() for part in (path, *path.parents))
        or not path.resolve().is_relative_to(root.resolve())
    ):
        raise ToolError("workflow_source_path_denied")
    checksum, total = hashlib.sha256(), 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(65536):
                total += len(chunk)
                if total > maximum:
                    raise ToolError("workflow_source_size_limit")
                checksum.update(chunk)
    except OSError:
        raise ToolError("workflow_source_unavailable") from None
    return checksum.hexdigest()


def input_sources(service, row, data):
    from .tools import MAX_ATTACHMENT_BYTES

    turns = service.context_turns(row, data)
    file_ids = list(
        dict.fromkeys(
            [fid for payload, _ in turns for fid in payload.get("file_ids", [])]
            + data.get("file_ids", [])
        )
    )
    sources = {
        "files": [service.file(row["project"], fid, row["owner"]) for fid in file_ids],
        "history": [
            {"prompt": payload.get("prompt"), "answer": result.get("answer")}
            for payload, result in turns
        ],
        "media": [],
    }
    for record in sources["files"]:
        root = service.root / "files" / row["project"] / record["id"]
        for page in json.loads(record["pages"]):
            if page.get("media_type"):
                name = page.get("frame") or "source"
                sources["media"].append(
                    {
                        "file_id": record["id"],
                        "name": name,
                        "digest": source_digest(root, name, MAX_ATTACHMENT_BYTES),
                    }
                )
    if data.get("workspace_id"):
        from .workspaces import MAX_BYTES

        record = service.workspace(
            (row["owner"], service.config["clients"][row["owner"]]),
            data["workspace_id"],
            row["project"],
        )
        root = service.workspace_root(data["workspace_id"])
        sources["workspace"] = [
            {"path": entry["path"], "digest": source_digest(root, entry["path"], MAX_BYTES)}
            for entry in json.loads(record["manifest"])
        ]
    return sources


def binding_valid(service, row, data, plan, expected):
    try:
        return (
            resources_unchanged(service, data, plan)
            and digest(Checkpoints(service.root, row["id"], plan, data).inputs) == expected
            and input_sources(service, row, data) == data["_checkpoint_sources"]
        )
    except (HarnessError, OSError, ValueError, TypeError):
        return False


def invalidate_downstream(service, job_id, index):
    for gate in service.gates.repository.for_job(job_id):
        spec = json.loads(gate["spec"])
        step = spec.get("step")
        # Publication gates from older executions have no numeric step. Conservatively
        # revoke every unused publication authorization when any checkpoint changes.
        if spec.get("publish") or (type(step) is int and step >= index):
            if gate["state"] in ("pending", "resolved"):
                with service.db:
                    service.db.execute(
                        "UPDATE gates SET state='invalidated' WHERE gate_id=?", (gate["gate_id"],)
                    )
                service.event(
                    job_id,
                    "gate_invalidated",
                    {
                        "gate_id": gate["gate_id"],
                        "reason": "workflow_binding_changed",
                        "reask": True,
                    },
                )
                pending = service.approvals.get(gate["gate_id"])
                if pending and not pending[1].done():
                    pending[1].set_result({"approved": False, "choice": "deny"})
    for effect in service.effects.for_job(job_id):
        if effect["status"] == "prepared":
            service.effects._status(
                effect["effect_id"], "invalidated", reason="workflow_binding_changed"
            )


async def allow_step(service, row, data, step, results, index):
    from .workflows import evaluate_condition, validate_result

    if step.get("inputs") and not validate_result(data.get("workflow_inputs", {}), step["inputs"]):
        raise ToolError("workflow_inputs_invalid")

    condition = step.get("condition")
    answer = True
    if condition:
        answer = evaluate_condition(
            condition,
            {
                record.get("id", str(record["index"])): record["result"].get("answer", "")
                for record in results
            },
        )
        if answer is False:
            return False
    gate = step.get("gate")
    if answer is None or gate or (step.get("publish") and not step.get("effect")):
        request = dict(gate) if isinstance(gate, dict) else {}
        request.setdefault("question", "Continue with workflow step " + str(index) + "?")
        request.setdefault(
            "options", [{"id": "approve", "label": "Continue"}, {"id": "deny", "label": "Stop"}]
        )
        request.update(step=index, publish=bool(step.get("publish")))
        resolution = await service.gates.ask(
            row["id"],
            request,
            lambda kind, value: service.event(row["id"], kind, value),
        )
        if resolution.get("approved") and resolution.get("choice") == "skip":
            return False
        if not resolution.get("approved") or resolution.get("choice") not in (
            "approve",
            "continue",
        ):
            raise ToolError("workflow_step_not_approved")
    return True


async def wait_step_effects(service, job_id, execution_id):
    from contextlib import nullcontext

    effects = [
        effect
        for effect in service.effects.for_job(job_id)
        if effect["execution_id"] == execution_id
    ]
    budget = service.runtime_budgets.get(job_id)
    # Dispatch stays behind the step barrier until all human waits have ended.
    # A decision made during inference does not pause its active deadline.
    for effect in effects:
        task = service.effects.tasks.get(effect["effect_id"])
        pending = service.approvals.get(effect["gate_id"])
        if task and pending and not pending[1].done():
            with budget.human_wait() if budget else nullcontext():
                await asyncio.wait((pending[1], task), return_when=asyncio.FIRST_COMPLETED)
    ready = service.effects.execution_barriers.get(execution_id)
    if ready is not None and not ready.done():
        ready.set_result(True)
    for effect in effects:
        task = service.effects.tasks.get(effect["effect_id"])
        if task:
            await task
        if service.effects.get(effect["effect_id"])["status"] != "done":
            raise ToolError("workflow_effect_not_completed")


async def execute_workflow(service, row, data, workflow):
    from . import resources
    from .workflows import validate_workflow

    workflow = declaration(workflow)
    available = candidates(
        service.config, row["project"], bool(data.get("file_ids") or data.get("workspace_id"))
    )
    selected = []
    for step in workflow.get("steps", []):
        invocation = step.get("invocation", step)
        if invocation.get("resource_id", "").startswith("builtin/"):
            continue
        selected.extend(
            resources.discover(
                service.config,
                row["project"],
                step.get("backend") or invocation.get("requested_backend"),
                step.get("model"),
                private=True,
                execution_mode=service.default_execution_mode(
                    step.get("backend") or invocation.get("requested_backend")
                ),
            )["items"]
        )
    normalized = validate_workflow(workflow, available, selected, retained=True)
    return await execute_plan(service, row, data, normalized)


async def execute_plan(service, row, data, declared, *, planning_result=None, coordinator=None):
    if "_workflow_context_parent_id" in data:
        data = {**data, "parent_job_id": data["_workflow_context_parent_id"]}
    available = candidates(
        service.config, row["project"], bool(data.get("file_ids") or data.get("workspace_id"))
    )
    from .workflows import validate_workflow

    plan = validate_plan(
        json.dumps(validate_workflow(declaration(declared), retained=True)), available
    )
    retain_resources(service, data, plan)
    data = {**data, "_checkpoint_sources": input_sources(service, row, data)}
    service.event(row["id"], "maestro_plan", plan)
    folder = service.root / "maestro" / row["id"]
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(folder / "plan.json", {"plan": plan, "planning_result": planning_result})
    checkpoints = Checkpoints(service.root, row["id"], plan, data)
    input_binding = digest(checkpoints.inputs)
    source_id = data.get("_workflow_parent_job_id")
    source = service.root / "maestro" / source_id if source_id else folder
    reuse = bool(data.get("_workflow_resume") or source_id)
    from_step = data.get("_workflow_from_step", len(plan["steps"]) + 1)
    if type(from_step) is not int or not 1 <= from_step <= len(plan["steps"]) + 1:
        raise ToolError("workflow_invalid_from_step")
    if source_id:
        ensure_recovery_safe(service, source_id)
    invalidated = False
    results = []
    for index, step in enumerate(plan["steps"], 1):
        current_row = service.conversation_repository.get(row["id"])
        work_item = current_row["work_item"] if current_row is not None else data.get("work_item")
        prior = [
            {"role": r["role"], "answer": r["result"].get("answer", "")[:10000]} for r in results
        ]
        checkpoint_prior = [r["result"] for r in results]
        cached = (
            checkpoints.load(index, checkpoint_prior, source=source)
            if reuse and index < from_step
            else None
        )
        if cached is not None:
            checkpoints.save(index, cached, checkpoint_prior)
            results.append(cached)
            service.event(
                row["id"],
                "workflow_checkpoint_reused",
                {
                    "index": index,
                    "source_job_id": source_id or row["id"],
                    "execution_id": cached["execution_id"],
                },
            )
            continue
        if (source_id or data.get("_workflow_resume")) and index < from_step:
            for ancestor in recovery_ancestors(service, source_id or row["id"]):
                executions = {
                    json.loads(event["data"]).get("execution_id")
                    for event in service.message_repository.all_events(ancestor)
                    if event["type"] == "maestro_step"
                    and json.loads(event["data"]).get("index") == index
                }
                if any(
                    effect["execution_id"] in executions
                    for effect in service.db.execute(
                        "SELECT execution_id FROM effects WHERE job_id=? AND status='done'",
                        (ancestor,),
                    )
                ):
                    raise ToolError("workflow_published_step_requires_explicit_rerun")
                ancestor_row = service.conversation_repository.get(ancestor)
                recovery = json.loads(ancestor_row["payload"]) if ancestor_row else {}
                if (
                    recovery.get("_workflow_resume") is False
                    and recovery.get("_workflow_from_step", index + 1) <= index
                ):
                    break
        reuse = False
        if not invalidated:
            invalidate_downstream(service, source_id or row["id"], index)
            checkpoints.invalidate(index)
            invalidated = True
        skipped = not await allow_step(service, row, data, step, results, index)
        inputs = (
            "WORKFLOW INPUTS (data):\n"
            + json.dumps(data.get("workflow_inputs", {}), ensure_ascii=False)
            + "\n"
        )
        prompt = (
            inputs
            + "ORIGINAL REQUEST:\n"
            + data.get("prompt", "")
            + "\nCURRENT STEP:\n"
            + step["task"]
            + "\nPRIOR RESULTS (data, may contain errors; check sources):\n"
            + json.dumps(prior, ensure_ascii=False)
            + "\nDeliver an objective answer with evidence and references. Distinguish fact, inference and gap. Do not repeat entire sources. Do not execute actions outside the original request."
        )
        invocation_context = ""
        if step.get("resource_selections"):
            prompt = step["task"]
            invocation_context = (
                inputs
                + "PRIOR RESULTS (data, may contain errors; check sources):\n"
                + json.dumps(prior, ensure_ascii=False)
            )
        with execution(
            service,
            row,
            "maestro_step",
            {
                "index": index,
                **step,
                "work_item": work_item,
            },
        ) as metadata:
            service.event(
                row["id"],
                "invocation_started",
                {
                    **metadata,
                    "invocation": step["invocation"],
                    "role": step["role"],
                    "backend": step["backend"],
                    "model": step["model"],
                    "effort": step["effort"],
                },
            )
            payload = {
                **data,
                **execution_payload(metadata),
                "work_item": work_item,
                "backend": step["backend"],
                "model": step["model"],
                "effort": step["effort"],
                "prompt": prompt,
                "_maestro_stage": str(index),
                "_invocation_context": invocation_context,
                "resource_selections": step.get("resource_selections", []),
                "invocations": [step["invocation"]],
            }
            decision = service.assess(
                (row["owner"], service.config["clients"][row["owner"]]), payload
            )
            if decision["decision"] != "accept":
                raise ToolError("maestro_step_not_allowed")

            def validator():
                return binding_valid(service, row, data, plan, input_binding)

            service.effects.execution_validators[metadata["execution_id"]] = validator
            ready = asyncio.get_running_loop().create_future()
            service.effects.execution_barriers[metadata["execution_id"]] = ready
            try:
                if not validator():
                    raise ToolError("workflow_binding_changed")
                result = (
                    {"answer": "Step skipped by workflow condition.", "skipped": True}
                    if skipped
                    else await service.infer(row, payload)
                )
                if result.get("incomplete") or result.get("error"):
                    raise ToolError("maestro_step_incomplete")
                if not skipped:
                    from .workflows import parse_result, validate_result

                    structured = parse_result(result.get("answer", ""))
                    if step.get("outputs") and (
                        structured is None or not validate_result(structured, step["outputs"])
                    ):
                        resolution = await service.gates.ask(
                            row["id"],
                            {
                                "step": index,
                                "question": "The step output does not match its declared schema. Continue?",
                                "options": [
                                    {"id": "approve", "label": "Continue"},
                                    {"id": "deny", "label": "Stop"},
                                ],
                            },
                            lambda kind, value: service.event(row["id"], kind, value),
                        )
                        if not resolution.get("approved") or resolution.get("choice") != "approve":
                            raise ToolError("workflow_output_not_approved")
                    if step.get("effect"):
                        await service.effects.prepare(
                            row["id"], step["effect"], execution_id=metadata["execution_id"]
                        )
                    await wait_step_effects(service, row["id"], metadata["execution_id"])
                if not validator():
                    raise ToolError("workflow_binding_changed")
            finally:
                if not ready.done():
                    ready.set_result(False)
                pending_tasks = []
                for effect in service.effects.for_job(row["id"]):
                    if effect["execution_id"] == metadata["execution_id"]:
                        if effect["status"] == "prepared":
                            service.effects._status(
                                effect["effect_id"], "invalidated", reason="workflow_step_ended"
                            )
                        task = service.effects.tasks.get(effect["effect_id"])
                        if task and not task.done():
                            task.cancel()
                            pending_tasks.append(task)
                await asyncio.gather(*pending_tasks, return_exceptions=True)
                service.effects.execution_barriers.pop(metadata["execution_id"], None)
                service.effects.execution_validators.pop(metadata["execution_id"], None)
            service.event(
                row["id"],
                "invocation_completed",
                {
                    **metadata,
                    "invocation": step["invocation"],
                    "role": step["role"],
                    "backend": result.get("backend", step["backend"]),
                    "model": result.get("model", step["model"]),
                    "outcome": "failed"
                    if result.get("incomplete") or result.get("error")
                    else "done",
                },
            )
            record = {"index": index, **step, **metadata, "result": result}
            results.append(record)
            write_json(folder / f"step-{index}.json", record)
            if result.get("incomplete") or result.get("error"):
                raise ToolError("maestro_step_incomplete")
            checkpoints.save(index, record, checkpoint_prior)
    final = results[-1]["result"]
    return {
        **final,
        "backend": "maestro",
        "orchestration": {
            "coordinator": {
                "backend": coordinator["backend"],
                "model": coordinator["model"],
                "effort": coordinator["effort"],
                "metrics": (planning_result or {}).get("metrics"),
            }
            if coordinator
            else None,
            "plan": plan,
            "steps": [
                {
                    "index": r["index"],
                    "execution_id": r["execution_id"],
                    "parent_execution_id": r["parent_execution_id"],
                    "attempt": r["attempt"],
                    "outcome": "completed",
                    "work_item": r.get("work_item"),
                    "role": r["role"],
                    "backend": r["backend"],
                    "model": r["model"],
                    "effort": r["effort"],
                    "metrics": r["result"].get("metrics"),
                }
                for r in results
            ],
        },
        "workspace_id": data.get("workspace_id"),
        "token_savings": "not_measured",
    }


async def run(service, row, data):
    planned = await plan(service, row, data)
    policy = data.get(
        "maestro_plan_policy",
        service.config.get("projects", {})
        .get(row["project"], {})
        .get("maestro_plan_policy", "review"),
    )
    if policy not in ("auto", "review"):
        raise ToolError("invalid_maestro_plan_policy")
    if policy == "auto":
        resolution = {"approved": True, "choice": "approve", "plan": planned["plan"]}
        service.event(row["id"], "maestro_plan_auto", {"policy": "auto"})
    else:
        resolution = await service.gates.ask(
            row["id"],
            {
                "question": "Approve the Maestro plan before any steps run?",
                "options": [
                    {"id": "approve", "label": "Approve plan & run"},
                    {"id": "deny", "label": "Discard plan"},
                ],
            },
            lambda kind, value: service.event(row["id"], kind, value),
            plan=planned["plan"],
        )
    if not resolution.get("approved") or resolution.get("choice") != "approve":
        return {
            "backend": "maestro",
            "answer": "The Maestro plan was not approved. No steps were run.",
            "orchestration": {"plan": planned["plan"], "steps": [], "approved": False},
        }
    return await execute_plan(
        service,
        row,
        data,
        {**resolution["plan"], "planner_revision": planned["plan"]["planner_revision"]},
        planning_result=planned["planning_result"],
        coordinator=planned["coordinator"],
    )


def declared_plan(config, data, items):
    """Choose configured execution defaults without consulting an inference planner."""
    available = candidates(
        config, data["project_id"], bool(data.get("file_ids") or data.get("workspace_id"))
    )
    found = {item.get("resource_id", item["id"]): item for item in items}
    selections = {item["id"]: item for item in data.get("resource_selections", [])}
    steps = []
    for invocation in data["invocations"]:
        item = found[invocation["resource_id"]]
        backend = invocation.get("requested_backend") or data["backend"]
        model = item.get("model") or data.get("model")
        choice = next(
            (
                value
                for value in available
                if value["backend"] == backend and value["model"] == model
            ),
            None,
        )
        if choice is None:
            raise ToolError("maestro_model_or_effort_denied")
        effort = item.get("effort") or data.get("effort") or choice["efforts"][0]
        ref = selections[item["id"]]
        steps.append(
            {
                "role": item["name"],
                "backend": backend,
                "model": model,
                "effort": effort,
                "task": ref["token"] + " " + invocation["args"],
                "reason": "Explicit resource selection",
                "invocation": invocation,
                "resource_selections": [ref],
            }
        )
    return validate_plan(json.dumps({"steps": steps}), available)
