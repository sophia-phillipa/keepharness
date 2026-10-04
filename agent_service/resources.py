"""Bounded, fresh native resource metadata. Discovery never executes templates."""

import hashlib
import json
import os
import re
import shlex
import tomllib
from itertools import islice
from pathlib import Path, PurePosixPath

from adapters.shared.provider_setup import homes_root, personal_setup_on

from .errors import UserMessageError
from .invocations import Invocation, InvocationError

MAX_METADATA_BYTES = 65536
MAX_BODY_BYTES = 262144
MAX_FILES = 500
ENGINES = {
    "codex": "codex",
    "local": "codex",
    "deepseek": "codex",
    "claude": "claude",
    "gemini": "gemini",
}
# Claude reads user skills and commands only with the personal setup and hooks on; the folder
# the hint names is the one the project source reads.
CLAUDE_UNLOADED = {
    kind: (
        f"Claude loads user {kind}s only with the personal setup and hooks on.",
        f"Copy the {kind} into the project's .claude/{kind}s folder, or turn on the personal "
        "setup and hooks.",
    )
    for kind in ("skill", "command")
}
OWNER_UNLOADED = "The run reads the harness-owned provider home, not the owner's own folders."
UNLOADED_HINTS = {
    **dict(CLAUDE_UNLOADED.values()),
    OWNER_UNLOADED: "Copy the resource into the project's folders or the harness-owned home.",
}
NAME = re.compile(r"^[\w.:-]{1,160}$")


class ResourceError(ValueError):
    pass


def read(path):
    with path.open("rb") as stream:
        value = stream.read(MAX_BODY_BYTES + 1)
    if len(value) > MAX_BODY_BYTES:
        raise UserMessageError("resource_too_large")
    return value.decode("utf-8")


def dependency_snapshot(path, meta, boundary):
    """Hash explicitly declared, resource-relative files within the owning root."""
    declared = meta.get("dependencies", [])
    if isinstance(declared, str):
        declared = json.loads(declared)
    if not isinstance(declared, list) or len(declared) > 32:
        raise UserMessageError("invalid_resource_dependencies")
    revisions, contents = {}, {}
    size = 0
    for relative in declared:
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise UserMessageError("invalid_resource_dependencies")
        dependency = (path.parent / relative).resolve()
        if not dependency.is_relative_to(boundary.resolve()):
            raise UserMessageError("resource_dependency_outside_root")
        text = read(dependency)
        size += len(text.encode())
        if size > MAX_BODY_BYTES:
            raise UserMessageError("resource_dependencies_too_large")
        name = dependency.relative_to(boundary.resolve()).as_posix()
        revisions[name] = hashlib.sha256(text.encode()).hexdigest()
        contents[name] = text
    return revisions, contents


def markdown(text):
    lines = text.splitlines()
    raw_lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta = {}
    key = None
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            if len("".join(raw_lines[: index + 1]).encode()) > MAX_METADATA_BYTES:
                raise UserMessageError("metadata_too_large")
            return meta, "\n".join(lines[index + 1 :])
        if line.startswith((" ", "\t")) and key:
            meta[key] += " " + line.strip()
        elif ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            meta[key] = value.strip().strip("\"'")
            if meta[key] in ("|", ">", "|-", ">-"):
                meta[key] = ""
        else:
            key = None
    raise UserMessageError("invalid_frontmatter")


def unfenced(text, *, preserve_offsets=False, keep_language=None):
    """Exclude fenced/indented code while retaining Markdown container boundaries."""
    output = []
    marker = None
    marker_depth = marker_indent = list_indent = 0
    previous_blank = True
    indented = False
    quote_in_list = False
    keep_block = False
    for line in text.splitlines(keepends=True):
        source = line
        line = line.expandtabs(4)
        if quote_in_list:
            if line.startswith(" " * list_indent):
                line = line[list_indent:]
            elif line.strip():
                quote_in_list = False
                list_indent = 0
        prefix = re.match(r"^(?: {0,3}>[ \t]?)+", line)
        depth = prefix.group().count(">") if prefix else 0
        content = line[prefix.end() :] if prefix else line
        blank = not content.strip()
        indentation = len(content) - len(content.lstrip(" "))
        if marker is not None and depth < marker_depth:
            marker = None
        if not blank and indentation < list_indent and not quote_in_list:
            list_indent = 0
            if marker_indent:
                marker = None
        if marker is None:
            item = re.match(r"^ {0,3}(?:[-+*]|[0-9]+[.)]) +", content)
            if item:
                list_indent = 0
                while item:
                    list_indent += item.end()
                    content = content[item.end() :]
                    item = re.match(r"^ {0,3}(?:[-+*]|[0-9]+[.)]) +", content)
            elif list_indent and not quote_in_list:
                content = content[list_indent:]
        elif marker_indent and not quote_in_list:
            content = content[marker_indent:]
        nested_quote = re.match(r"^(?: {0,3}>[ \t]?)+", content)
        if nested_quote:
            quote_in_list = bool(list_indent)
            depth += nested_quote.group().count(">")
            content = content[nested_quote.end() :]
        keep_line = keep_block and marker is not None
        code_indent = bool(re.match(r"^(?: {4}|\t)", content))
        indented = marker is None and (
            code_indent and (previous_blank or indented) or blank and indented
        )
        hidden = bool(marker) or indented
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", content)
        if match and not indented:
            fence, suffix = match.groups()
            if marker is None and (fence[0] != "`" or "`" not in suffix):
                marker, marker_depth, marker_indent = fence, depth, list_indent
                keep_block = (
                    keep_language is not None
                    and not depth
                    and not list_indent
                    and suffix.strip() == keep_language
                )
                keep_line = keep_block
                hidden = True
            elif (
                marker is not None
                and depth == marker_depth
                and fence[0] == marker[0]
                and len(fence) >= len(marker)
                and not suffix.strip()
            ):
                marker = None
                hidden = True
        if not hidden or keep_line:
            output.append(
                source if preserve_offsets or keep_language is not None else source.rstrip("\r\n")
            )
        elif preserve_offsets:
            output.append(" " * len(source))
        previous_blank = blank
    return ("" if preserve_offsets or keep_language is not None else "\n").join(output)


def first_sentence(body):
    prose = re.sub(r"^[#>*\s-]+", "", unfenced(body).strip())
    match = re.search(r".+?(?:[.!?](?=\s|$)|$)", prose, re.S)
    return re.sub(r"\s+", " ", match.group(0)).strip() if match else ""


def argument_hint(meta, body, name):
    hint = meta.get("argument_hint", meta.get("argument-hint", ""))
    if isinstance(hint, str) and hint.strip():
        return hint.strip()[:500]
    example_text = str(meta.get("description", "")) + "\n" + unfenced(body)
    match = re.search(r"(?<!\S)/" + re.escape(name) + r"\s+([^\n.!?]+)", example_text)
    return match.group(1).strip()[:500] if match else ""


def preflight_hint(reason):
    if reason in UNLOADED_HINTS:
        return UNLOADED_HINTS[reason]
    if not reason:
        return "Ready to invoke with the current provider and execution mode."
    if "disabled" in reason.lower():
        return "Enable this resource in the provider configuration."
    if "delegation" in reason.lower():
        return "Choose a provider and mode that supports native agent delegation."
    if "expansion" in reason.lower():
        return "Remove unsupported expansion syntax or run the command in its native provider."
    if "user" in reason.lower():
        return "Mark the resource as user-invocable to select it from chat."
    if "reference resource" in reason.lower():
        return "Open the source to review how this reference applies to the project."
    return "Choose a compatible provider, model, and execution mode."


def roots(backend, engine, config, owner):
    """User-scope roots: the home the provider CLI reads, or none (decision D01).

    With a state folder, Codex and Claude read their harness-owned home whatever the opt-in
    says, and DeepSeek its own Codex home. Gemini keeps the owner's home (its commands are
    expanded by the harness), listed only when the caller is positively the owner.
    """
    home = Path.home()
    state = engine in ("codex", "claude") and config.get("control_state_dir")
    if state and backend == "deepseek":
        return homes_root(state) / "deepseek", [homes_root(state) / "home/.agents/skills"]
    if state:
        home = homes_root(state) / "home"
        return home / ("." + engine), ([home / ".agents/skills"] if engine == "codex" else [])
    if engine == "codex":
        return Path(os.environ.get("CODEX_HOME", home / ".codex")), [home / ".agents/skills"]
    if engine == "claude":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")), []
    if engine == "gemini":
        if not owner:
            return None, []
        return Path(os.environ.get("GEMINI_CLI_HOME", home)) / ".gemini", [home / ".agents/skills"]
    raise UserMessageError("unsupported_resource_engine")


def owner_root(backend, engine, config, personal):
    """The owner's own config folder, listed beside the harness home under the opt-in."""
    if not personal or not config.get("control_state_dir"):
        return None
    if backend == "deepseek" or engine not in ("codex", "claude"):
        return None
    variable = "CODEX_HOME" if engine == "codex" else "CLAUDE_CONFIG_DIR"
    return Path(os.environ.get(variable, Path.home() / ("." + engine)))


def unloaded_user_resource(engine, kind, owned, personal, hooks):
    """Why the provider CLI will not read this user-scope resource itself, or an empty string.

    Only skills and Claude's native commands are loaded by the CLI; the harness expands the
    other commands into the prompt. Claude reads its config folder only when a run starts with
    ``--setting-sources user,project``: the personal setup plus the hooks grant.
    """
    if kind != "skill" and not (engine == "claude" and kind == "command"):
        return ""
    if owned:
        return OWNER_UNLOADED
    return CLAUDE_UNLOADED[kind][0] if engine == "claude" and not (personal and hooks) else ""


def files(base, boundary, global_roots, kind):
    """Follow skill aliases only inside explicitly known resource roots; cap cycles."""
    pending = [base]
    seen = set()
    count = 0
    while pending and count < MAX_FILES:
        path = pending.pop()
        count += 1
        resolved = path.resolve()
        if resolved in seen:
            continue
        if not any(
            resolved.is_relative_to(root.resolve())
            for root in ([boundary] if boundary else global_roots)
        ):
            continue
        seen.add(resolved)
        if path.is_dir():
            if kind == "skill" and (path / "SKILL.md").is_file():
                pending.append(path / "SKILL.md")
            else:
                children = list(islice(path.iterdir(), MAX_FILES + 1))
                if len(children) > MAX_FILES:
                    raise ResourceError("resource_scan_limit")
                pending.extend(sorted(children, reverse=True))
        elif path.is_file():
            yield path
    if pending:
        raise ResourceError("resource_scan_limit")


def without_host_paths(result: dict) -> dict:
    """The listing for a client other than the local owner: each item's logical id stands in
    for its absolute source path, so no home folder or account name leaves the host."""
    ids = {item["source"]: item["resource_id"] for item in result.get("items", [])}
    for item in result.get("items", []):
        item["source"] = item["resource_id"]
    for key in ("agents", "skills"):
        for entry in result.get(key, []):
            entry["source"] = ids.get(entry["source"], entry["source"])
    return result


def discover(
    config,
    project_id,
    backend,
    model=None,
    *,
    private=False,
    execution_mode=None,
    include_workflows=True,
    owner=False,
    access_mode=None,
):
    """The caller's resources; ``owner`` must be True for the owner's personal ones to show."""
    from .approval_policy import effective_permissions, hooks_allowed
    from .catalog_manifest import load_manifest, preflight
    from .catalog_pin import effective_catalogs, snapshot_catalogs
    from .harness_agents import add_resources
    from .integrations import integration_preflight
    from .maestro import model_permissions
    from .workflows import discover_workflows

    engine = ENGINES.get(backend)
    result = {
        "engine": engine,
        **(
            discover_workflows(
                config,
                project_id,
                backend,
                private=private,
                execution_mode=execution_mode,
                owner=owner,
            )
            if include_workflows
            else {"items": [], "warnings": []}
        ),
    }
    if include_workflows:
        # Harness-owned resources need no engine, so they come with the workflows; plan
        # dependency lookups (include_workflows=False) read native resources only.
        add_resources(result, config, project_id, private=private)
    if engine is None:
        result["warnings"].append("Choose a concrete engine to query its resources.")
        return result
    if (execution_mode or config.get("services", {}).get(backend, {}).get("mode")) != "native":
        result["warnings"].append("Native resources require a native-mode execution.")
        return result
    project = config["projects"][project_id]
    permissions = effective_permissions(
        model_permissions(config, backend, model, project_id), access_mode
    )
    root = Path(project["root"]).resolve() if project.get("root") else None
    personal = personal_setup_on(config, owner=owner)
    global_base, shared = roots(backend, engine, config, owner)
    sources = []

    def source(base, scope, origin, boundary, kind, identity, identity_root, namespace=""):
        sources.append(
            {
                "base": base,
                "scope": scope,
                "origin": origin,
                "boundary": boundary,
                "kind": kind,
                "identity": identity,
                "identity_root": identity_root,
                "namespace": namespace,
            }
        )

    def add(base, scope, origin, boundary, identity, identity_root, namespace=""):
        for folder, kind in (("agents", "agent"), ("skills", "skill"), ("commands", "command")):
            if engine == "codex" and kind == "command" and scope != "catalog":
                continue
            source(
                base / folder,
                scope,
                origin,
                boundary,
                kind,
                identity,
                identity_root,
                namespace,
            )

    if root:
        # Nearest project config first, bounded by the Git root when nested.
        ancestors = [root]
        pending = []
        for parent in root.parents:
            if (ancestors[-1] / ".git").exists():
                break
            if parent == Path.home() or parent == Path("/"):
                break
            pending.append(parent)
            if (parent / ".git").exists():
                ancestors.extend(pending)
                break
        for folder in ancestors:
            identity = "project/" + project_id
            if folder != root:
                identity += "/ancestor/" + str(len(root.relative_to(folder).parts))
            add(
                folder / ("." + engine),
                "project",
                engine,
                folder,
                identity,
                folder,
            )
            if engine in ("codex", "gemini"):
                source(
                    folder / ".agents/skills",
                    "project",
                    "agents",
                    folder,
                    "skill",
                    identity,
                    folder,
                )
            if engine == "codex":
                source(
                    folder / ".codex/prompts",
                    "project",
                    "codex",
                    folder,
                    "command",
                    identity,
                    folder,
                )
        if engine == "claude":
            source(
                root / ".claude/rules",
                "project",
                "claude",
                root,
                "rule",
                "project/" + project_id,
                root,
            )

    enabled_catalogs = set(project.get("catalogs", []))
    catalog_details = {}
    for catalog in effective_catalogs(config, project):
        if (
            not isinstance(catalog, dict)
            or catalog.get("trusted") is not True
            or catalog.get("id") not in enabled_catalogs
        ):
            continue
        catalog_root = Path(catalog.get("root", ""))
        try:
            catalog_root = catalog_root.resolve()
        except OSError:
            continue
        catalog_id = str(catalog.get("id", ""))
        identity = "catalog/" + catalog_id
        try:
            manifest = load_manifest(catalog_root)
            problems = (
                preflight(
                    catalog_root,
                    manifest,
                    config.get("control_state_dir", config.get("state_dir", "state")),
                    catalog_id,
                )
                if manifest
                else []
            )
        except (ValueError, OSError) as error:
            manifest, problems = None, ["Invalid catalog manifest: " + str(error)]
        snapshot = snapshot_catalogs({**config, "catalogs": [catalog]}, project)[0]
        if snapshot.get("error"):
            problems.append("Catalog unavailable: " + snapshot["error"])
            result["warnings"].append(
                "Catalog " + catalog_id + " unavailable: " + snapshot["error"]
            )
        problems.extend(
            integration_preflight(
                config,
                project_id,
                catalog_id,
                backend,
                execution_mode or config.get("services", {}).get(backend, {}).get("mode"),
                (manifest or {}).get("integrations", []),
            )
        )
        catalog_details[catalog_id] = (manifest, problems, snapshot)
        namespace = str(catalog.get("namespace", ""))
        add(
            catalog_root,
            "catalog",
            catalog_id,
            catalog_root,
            identity,
            catalog_root,
            namespace,
        )
        for folder, kind in (("rules", "rule"), ("context", "context"), ("contexts", "context")):
            source(
                catalog_root / folder,
                "catalog",
                catalog_id,
                catalog_root,
                kind,
                identity,
                catalog_root,
                namespace,
            )
        if manifest:
            declared = dict(manifest.get("resources", {}))
            for field, kind in (("context", "context"), ("rules", "rule")):
                declared.setdefault(kind, []).extend(manifest.get(field, []))
            for kind, folders in declared.items():
                if kind != "workflow":
                    for folder in folders:
                        source(
                            catalog_root / folder,
                            "catalog",
                            catalog_id,
                            catalog_root,
                            kind,
                            identity,
                            catalog_root,
                            namespace,
                        )
        source(
            catalog_root / "skills",
            "catalog",
            catalog_id,
            catalog_root,
            "context",
            identity,
            catalog_root,
            namespace,
        )

    if global_base is not None:
        add(global_base, "user", engine, None, "user/" + engine, global_base)
    if owner_home := owner_root(backend, engine, config, personal):
        add(owner_home, "user", engine, None, "owner/" + engine, owner_home)
    if engine == "codex":
        for prompts_base, identity in ((global_base, "user/codex"), (owner_home, "owner/codex")):
            if prompts_base:
                source(
                    prompts_base / "prompts", "user", "codex", None, "command", identity, prompts_base
                )
    for shared_root in shared:
        source(
            shared_root,
            "user",
            "agents" if shared_root.parent.name == ".agents" else engine,
            None,
            "skill",
            "user/agents",
            shared_root.parent.parent,
        )
    global_roots = [item["base"] for item in sources if item["scope"] == "user"]
    native_skill_paths = set()
    if engine == "codex":
        for entry in sources:
            if entry["kind"] != "skill" or entry["scope"] == "catalog":
                continue
            try:
                native_skill_paths.update(
                    (folder / "SKILL.md").resolve()
                    for folder in islice(entry["base"].iterdir(), MAX_FILES)
                    if (folder / "SKILL.md").is_file() and not (folder / "SKILL.md").is_symlink()
                )
            except (OSError, RuntimeError, ResourceError):
                pass
    disabled = set()
    if engine == "codex":
        for path in [
            *([global_base / "config.toml"] if global_base else []),
            *([root / ".codex/config.toml"] if root else []),
        ]:
            try:
                settings = tomllib.loads(read(path))
                for item in settings.get("skills", {}).get("config", []):
                    if isinstance(item, dict) and item.get("enabled") is False and item.get("path"):
                        disabled.add(str(Path(item["path"]).expanduser().resolve()))
            except FileNotFoundError:
                pass
            except (ValueError, OSError, TypeError, AttributeError):
                result["warnings"].append("Could not check the Codex skills configuration.")
    # The run's own rule. The palette cannot know which catalog resources a run will select, so
    # any catalog of the project counts (the conservative answer); a scheduled run also drops the
    # opt-in, which this view cannot know either.
    hooks = hooks_allowed(permissions, catalog_details)
    seen_paths = set()
    seen_names = set()
    for source_spec in sources:
        base = source_spec["base"]
        scope = source_spec["scope"]
        origin = source_spec["origin"]
        boundary = source_spec["boundary"]
        kind = source_spec["kind"]
        try:
            for path in files(base, boundary, global_roots, kind):
                if kind == "skill" and path.name != "SKILL.md":
                    continue
                if (
                    kind == "context"
                    and base.name == "skills"
                    and not path.stem.startswith(("SKILL-", "KB-", "RL-"))
                ):
                    continue
                expected_suffix = (
                    ".toml"
                    if (engine == "codex" and kind == "agent")
                    or (engine == "gemini" and kind == "command")
                    else ".md"
                )
                if kind != "skill" and path.suffix != expected_suffix:
                    continue
                canonical = str(path.resolve())
                if (kind, canonical) in seen_paths:
                    continue
                try:
                    text = read(path)
                    meta = tomllib.loads(text) if path.suffix == ".toml" else markdown(text)[0]
                    body = (
                        meta.get("prompt", "")
                        if kind == "command" and path.suffix == ".toml"
                        else markdown(text)[1]
                        if path.suffix == ".md"
                        else text
                    )
                    if not isinstance(body, str):
                        raise UserMessageError("invalid_resource_body")
                    name = meta.get("name") or (path.parent.name if kind == "skill" else path.stem)
                    if kind == "command":
                        name = str(path.relative_to(base).with_suffix("")).replace(os.sep, ":")
                    source_name = name
                    namespace = source_spec["namespace"]
                    if kind == "agent" and namespace and not str(name).startswith(namespace + "--"):
                        name = namespace + "--" + str(name)
                    if not isinstance(name, str) or not NAME.fullmatch(name):
                        raise UserMessageError("invalid_name")
                    if (
                        kind == "agent"
                        and engine == "codex"
                        and not isinstance(meta.get("developer_instructions"), str)
                    ):
                        raise UserMessageError("invalid_agent")
                    reason = ""
                    if canonical in disabled:
                        reason = "Skill disabled in the engine configuration."
                    if engine == "gemini" and kind in ("agent", "skill"):
                        reason = (
                            "The Gemini adapter still disables agents and skills in this execution."
                        )
                    if backend in ("local", "deepseek") and (
                        (backend == "local" and scope == "user") or kind == "agent"
                    ):
                        reason = "This resource is not available in the isolated environment of this executor."
                    if scope == "user":
                        owned = source_spec["identity"].startswith("owner/")
                        reason = unloaded_user_resource(engine, kind, owned, personal, hooks) or reason
                    delegate_allowed = project.get("permissions", {}).get("delegate") is True
                    declared_mode = str(meta.get("mode", "")).strip()
                    if (
                        engine == "claude"
                        and kind == "agent"
                        and declared_mode != "conversational"
                        and not delegate_allowed
                    ):
                        reason = "Agent delegation is disabled for this project and provider."
                    if kind in ("rule", "context"):
                        reason = "Reference resource; cannot be invoked directly."
                    if kind == "command" and re.search(r"!\{|!`|@\{", unfenced(body)):
                        reason = "This command requires native expansion features that are not yet supported."
                    if (
                        kind == "skill"
                        and str(meta.get("user-invocable", "true")).lower() == "false"
                    ):
                        reason = "Skill not available for invocation by the user."
                    key = (kind, name.casefold())
                    if key in seen_names:
                        continue
                    relative = (
                        path.resolve()
                        .relative_to(source_spec["identity_root"].resolve())
                        .as_posix()
                    )
                    identity = source_spec["identity"] + "/" + relative
                    if len(identity) > 1000:
                        reason = "Resource path exceeds the invocation identity limit."
                    elif kind in ("agent", "command", "skill"):
                        try:
                            Invocation(kind, identity)
                        except InvocationError:
                            reason = "Resource path is not a portable invocation identity. Rename the source path."
                    deps_revisions, dependency_texts = dependency_snapshot(
                        path, meta, source_spec["identity_root"]
                    )
                    description = str(meta.get("description", "")).strip()
                    if not description:
                        description = first_sentence(body)
                    mode = str(meta.get("mode", "")).strip()
                    if mode not in ("inline", "conversational", "delegated"):
                        mode = "delegated" if kind == "agent" else "inline"
                    maintenance_name = name.rsplit(":", 1)[-1].rsplit("--", 1)[-1]
                    maintenance = kind == "command" and maintenance_name.casefold() in {
                        "install",
                        "update",
                        "uninstall",
                    }
                    manifest, problems, snapshot = catalog_details.get(origin, (None, [], {}))
                    if (
                        maintenance
                        and manifest
                        and manifest.get("provisions_maintenance")
                        and not problems
                    ):
                        continue
                    if (
                        engine == "codex"
                        and kind == "skill"
                        and scope == "catalog"
                        and path.resolve() not in native_skill_paths
                    ):
                        reason = "Register this catalog skill in a native skill directory before invoking it."
                    if problems:
                        reason = "; ".join(problems)
                    elif (
                        maintenance
                        and snapshot.get("pinned")
                        and maintenance_name.casefold() == "update"
                    ):
                        reason = "Pinned catalogs update only through Admin."
                    if (
                        not reason
                        and kind == "command"
                        and not (engine == "claude" and scope != "catalog")
                    ):
                        try:
                            prepare_prompt(
                                "/" + name, [{"kind": kind, "name": name, "_body": body}]
                            )
                        except ResourceError as error:
                            if str(error) != "resource_prompt_limit":
                                raise
                            reason = "Command exceeds the executable prompt limit."
                    item = {
                        "id": identity,
                        "resource_id": identity,
                        "revision": hashlib.sha256(text.encode()).hexdigest(),
                        "catalog_commit": snapshot.get("commit"),
                        "catalog_dirty": snapshot.get("dirty"),
                        "catalog_pinned": snapshot.get("pinned", False),
                        "deps_revisions": deps_revisions,
                        "kind": kind,
                        "name": name,
                        "description": description[:1000],
                        "scope": scope,
                        "origin": origin,
                        "source": str(path),
                        "namespace": namespace,
                        "argument_hint": argument_hint(meta, body, source_name),
                        "backend": str(meta.get("backend", backend)),
                        "model": str(meta.get("model", model or "")),
                        "effort": str(meta.get("effort", meta.get("model_reasoning_effort", ""))),
                        "mode": mode,
                        "native_command": (
                            engine == "claude" and kind == "command" and scope != "catalog"
                        ),
                        "maintenance": maintenance,
                        "group": "Maintenance" if maintenance else kind.title() + "s",
                        "selectable": not bool(reason),
                        "unavailable_reason": reason,
                        "preflight_hint": reason if problems else preflight_hint(reason),
                        "compatibility": (
                            {"claude_ai_connectors": "unknown"} if engine == "claude" else {}
                        ),
                    }
                    if private:
                        item.update(
                            _text=text, _body=body, _meta=meta, _dependency_texts=dependency_texts
                        )
                    result["items"].append(item)
                    seen_paths.add((kind, canonical))
                    seen_names.add(key)
                except (ValueError, OSError, TypeError):
                    result["warnings"].append("Could not read the resource " + str(path))
                if len(result["items"]) >= MAX_FILES:
                    result["warnings"].append("Catalog limited to 500 resources.")
                    return result
        except ResourceError:
            result["warnings"].append("Read limited to 500 entries in " + str(base))
        except (OSError, RuntimeError):
            result["warnings"].append("Could not access " + str(base))
    result["items"].sort(
        key=lambda i: (
            {"project": 0, "catalog": 1, "user": 2}.get(i["scope"], 3),
            i["origin"],
            i["name"].casefold(),
            i["kind"],
        )
    )
    return result


def accepted_tokens(item):
    """Spellings a selection may use for ``item``; the last is the canonical one."""
    name = item["name"]
    if item["scope"] == "harness":
        return ("@@" + name,)
    return ("/" + name, "@" + name) if item["kind"] == "agent" else ("/" + name,)


TITLE_LIMIT = 100
RESERVED_MARKER = re.compile(r"(?<!\S)@@[\w:-]+(?=\s|$)")
SENTENCE_END = re.compile(r"(?<=[.!?])\s|\n")


def conversation_title(prompt):
    """A readable default title: the first sentence of the prompt without its ``@@`` markers."""
    text = RESERVED_MARKER.sub(" ", str(prompt)).strip()
    sentence = " ".join(SENTENCE_END.split(text, 1)[0].split())
    # "e.g." or "Hi." is not a title: a very short first sentence falls back to the whole prompt.
    return (sentence if len(sentence) > 11 else " ".join(text.split()))[:TITLE_LIMIT] or "Conversation"


def reserved_markers(prompt, selections):
    """The ``@@`` markers in the prompt; ``//`` or a marker nothing was selected for is refused."""
    prose = unfenced(prompt, preserve_offsets=True)
    markers = set(RESERVED_MARKER.findall(prose))
    if re.search(r"^\s*//[A-Za-z_][\w:-]*(?=\s|$)", prose) or (markers and not selections):
        raise ResourceError("harness_resources_unavailable")
    return markers


def run_config(config, data, *, owner):
    """The config a run resolves resources with: its effective personal setup (decision D01)."""
    personal = personal_setup_on(config, owner=owner, schedule_id=data.get("schedule_id"))
    return {**config, "personal_setup": personal}


def resolve(config, data, *, owner=False):
    config = run_config(config, data, owner=owner)
    prompt = data.get("prompt", "")
    reserved = reserved_markers(prompt, data.get("resource_selections"))
    selections = data.get("resource_selections", [])
    if not isinstance(selections, list) or len(selections) > 20:
        raise ResourceError("invalid_resource_selections")
    if not selections:
        return []
    if data.get("workspace_id"):
        raise ResourceError("resources_unavailable_in_workspace")
    found = {
        i["id"]: i
        for i in discover(
            config,
            data["project_id"],
            data["backend"],
            data.get("model"),
            private=True,
            execution_mode=data.get("execution_mode"),
            owner=owner,
            access_mode=data.get("access_mode"),
        )["items"]
    }
    result = []
    for selection in selections:
        if not isinstance(selection, dict) or not all(
            isinstance(selection.get(key), str)
            and len(selection[key]) <= (1000 if key == "id" else 200)
            for key in ("id", "revision", "token")
        ):
            raise ResourceError("invalid_resource_selections")
        item = found.get(selection.get("id"))
        if item is None or not item["selectable"]:
            raise ResourceError("resource_unavailable")
        if item["revision"] != selection.get("revision"):
            raise ResourceError("resource_changed")
        token = selection.get("token")
        if token not in accepted_tokens(item) or not re.search(
            r"(?<!\S)" + re.escape(token) + r"(?=\s|$)", prompt
        ):
            raise ResourceError("resource_selection_missing")
        if not any(value["id"] == item["id"] for value in result):
            result.append({**item, "_token": token})
    # An @@ marker is honored only for a Harness agent that was selected with that token.
    if reserved - {value["_token"] for value in result}:
        raise ResourceError("harness_resources_unavailable")
    return result


def release_notice(previous):
    """The line telling the model that the previous turn's agent was released, or ``""``.

    A native provider session keeps the persona text for good, so releasing the agent in the
    harness alone would leave the model playing it.
    """
    persona = previous.get("invocations", [])
    if len(persona) != 1 or persona[0].get("mode") != "conversational":
        return ""
    name = PurePosixPath(persona[0]["resource_id"]).stem
    return (
        "The conversational agent "
        + json.dumps(name)
        + " was released; answer normally from now on.\n"
    )


def prepare_prompt(prompt, items, selections=None):
    if selections and len(selections) > 1:
        from .invocations import normalize_chips

        by_id = {item.get("resource_id", item["id"]): item for item in items}
        for invocation in normalize_chips(prompt, selections, items):
            item = by_id[invocation.resource_id]
            prepare_prompt("/" + item["name"] + " " + invocation.args, [item])
        # Chains execute each canonical invocation separately; this pass is preflight.
        return prompt

    notes = []
    commands = {}
    skills = {}
    for item in items:
        name = item["name"]
        token = item.get("_token", "/" + name)
        if item["kind"] == "agent":
            if item.get("mode") == "conversational":
                # A Harness agent has no file in the project: naming one would invite the model to look.
                harness_agent = item.get("scope") == "harness"
                notes.append(
                    "Adopt the conversational agent "
                    + json.dumps(name)
                    + (
                        " defined by the user in KeepHarness"
                        if harness_agent
                        else " defined at " + json.dumps(item["source"])
                    )
                    + " for this main-thread conversation until it is released."
                    + (" Its definition follows inline." if harness_agent else "")
                    + "\n"
                    + item.get("_body", "")
                )
            else:
                notes.append(
                    "Delegate this task using the native agent "
                    + json.dumps(name)
                    + " defined at "
                    + json.dumps(item["source"])
                    + ". Use actual native delegation, not role-play. If unavailable, report that limitation without claiming delegation."
                )
        elif item["kind"] == "skill":
            if item["origin"] in ("codex", "agents"):
                skills[token] = "$" + name
            notes.append(
                "Explicitly invoke the selected skill "
                + json.dumps(name)
                + " at "
                + json.dumps(item["source"])
                + ". Preserve its native instructions and dependencies; report unavailable tools instead of substituting silently."
            )
        else:
            if not item.get("native_command"):
                commands[token] = item
    patterns = []
    if commands:
        command_args = r"[^\n]*"
        if len(items) == 1 and len(commands) == 1:
            token = next(iter(commands))
            if re.match(r"^" + re.escape(token) + r"(?=\s|$)", prompt):
                command_args = r"[\s\S]*"
        patterns.append(
            r"(?P<command>"
            + "|".join(map(re.escape, commands))
            + r")(?=\s|$)(?P<args>"
            + command_args
            + ")"
        )
    if skills:
        patterns.append(r"(?P<skill>" + "|".join(map(re.escape, skills)) + r")(?=\s|$)")
    if patterns:

        def expand(match):
            groups = match.groupdict()
            if groups.get("skill") is not None:
                return skills[groups["skill"]]
            args = prompt[match.start("args") : match.end("args")]
            if args.startswith(" "):
                args = args[1:]
            command_body = commands[groups["command"]]["_body"]
            positional = []
            if re.search(r"\$[1-9](?!\d)", command_body):
                try:
                    positional = shlex.split(args)
                except ValueError:
                    raise ResourceError("invalid_command_arguments") from None

            def substitute(placeholder):
                if placeholder.group(1) is None:
                    return args
                index = int(placeholder.group(1)) - 1
                return positional[index] if index < len(positional) else ""

            return re.sub(
                r"\{\{args\}\}|\$ARGUMENTS|\$([1-9])(?!\d)",
                substitute,
                command_body,
            )

        # Match the original text once; arguments and inserted bodies remain data.
        pattern = re.compile(r"(?<!\S)(?:" + "|".join(patterns) + ")")
        pieces, end = [], 0
        for match in pattern.finditer(unfenced(prompt, preserve_offsets=True)):
            pieces.extend((prompt[end : match.start()], expand(match)))
            end = match.end()
        prompt = "".join(pieces) + prompt[end:]
    prompt += "\n\nEXPLICIT RESOURCE SELECTIONS:\n" + "\n".join(notes) if notes else ""
    if len(prompt) > 150000:
        raise ResourceError("resource_prompt_limit")
    return prompt
