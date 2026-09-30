"""Bounded, fresh native resource metadata. Discovery never executes templates."""

import hashlib
import json
import os
import re
import shlex
import tomllib
from itertools import islice
from pathlib import Path

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
NAME = re.compile(r"^[\w.:-]{1,160}$")


class ResourceError(ValueError):
    pass


def read(path):
    with path.open("rb") as stream:
        value = stream.read(MAX_BODY_BYTES + 1)
    if len(value) > MAX_BODY_BYTES:
        raise ValueError("resource_too_large")
    return value.decode("utf-8")


def markdown(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta = {}
    key = None
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            if len("\n".join(lines[: index + 1]).encode()) > MAX_METADATA_BYTES:
                raise ValueError("metadata_too_large")
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
    raise ValueError("invalid_frontmatter")


def unfenced(text):
    """Return prose outside Markdown fences for static expansion checks."""
    output = []
    marker = None
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            fence = match.group(1)
            if marker is None:
                marker = fence[0]
            elif fence[0] == marker:
                marker = None
            continue
        if marker is None:
            output.append(line)
    return "\n".join(output)


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
    if "automatically" in reason.lower():
        return "This resource is shown for context and is loaded by its native engine."
    return "Choose a compatible provider, model, and execution mode."


def roots(engine):
    home = Path.home()
    if engine == "codex":
        return Path(os.environ.get("CODEX_HOME", home / ".codex")), [home / ".agents/skills"]
    if engine == "claude":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")), []
    if engine == "gemini":
        return Path(os.environ.get("GEMINI_CLI_HOME", home)) / ".gemini", [home / ".agents/skills"]
    raise ValueError("unsupported_resource_engine")


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


def discover(config, project_id, backend, model=None, *, private=False, execution_mode=None):
    engine = ENGINES.get(backend)
    result = {"engine": engine, "items": [], "warnings": []}
    if engine is None:
        result["warnings"].append("Choose a concrete engine to query its resources.")
        return result
    if (execution_mode or config.get("services", {}).get(backend, {}).get("mode")) != "native":
        result["warnings"].append("Native resources require a native-mode execution.")
        return result
    project = config["projects"][project_id]
    root = Path(project["root"]).resolve() if project.get("root") else None
    global_base, shared = roots(engine)
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
            add(
                folder / ("." + engine),
                "project",
                engine,
                folder,
                "project/" + project_id,
                folder,
            )
            if engine in ("codex", "gemini"):
                source(
                    folder / ".agents/skills",
                    "project",
                    "agents",
                    folder,
                    "skill",
                    "project/" + project_id,
                    folder,
                )
            if engine == "codex":
                source(
                    folder / ".codex/prompts",
                    "project",
                    "codex",
                    folder,
                    "command",
                    "project/" + project_id,
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
    for catalog in config.get("catalogs", []):
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

    add(global_base, "user", engine, None, "user/" + engine, global_base)
    if engine == "codex":
        source(
            global_base / "prompts",
            "user",
            "codex",
            None,
            "command",
            "user/codex",
            global_base,
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
    disabled = set()
    if engine == "codex":
        for path in [global_base / "config.toml", *([root / ".codex/config.toml"] if root else [])]:
            try:
                settings = tomllib.loads(read(path))
                for item in settings.get("skills", {}).get("config", []):
                    if isinstance(item, dict) and item.get("enabled") is False and item.get("path"):
                        disabled.add(str(Path(item["path"]).expanduser().resolve()))
            except FileNotFoundError:
                pass
            except (ValueError, OSError, TypeError, AttributeError):
                result["warnings"].append("Could not check the Codex skills configuration.")
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
                        raise ValueError("invalid_resource_body")
                    name = meta.get("name") or (path.parent.name if kind == "skill" else path.stem)
                    if kind == "command":
                        name = str(path.relative_to(base).with_suffix("")).replace(os.sep, ":")
                    namespace = source_spec["namespace"]
                    if kind == "agent" and namespace and not str(name).startswith(namespace + "--"):
                        name = namespace + "--" + str(name)
                    if not isinstance(name, str) or not NAME.fullmatch(name):
                        raise ValueError("invalid_name")
                    if (
                        kind == "agent"
                        and engine == "codex"
                        and not isinstance(meta.get("developer_instructions"), str)
                    ):
                        raise ValueError("invalid_agent")
                    reason = ""
                    if canonical in disabled:
                        reason = "Skill disabled in the engine configuration."
                    if engine == "gemini" and kind in ("agent", "skill"):
                        reason = (
                            "The Gemini adapter still disables agents and skills in this execution."
                        )
                    if backend in ("local", "deepseek") and (scope == "user" or kind == "agent"):
                        reason = "This resource is not available in the isolated environment of this executor."
                    if kind in ("rule", "context"):
                        reason = "This resource is loaded automatically and cannot be selected."
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
                    relative = path.resolve().relative_to(
                        source_spec["identity_root"].resolve()
                    ).as_posix()
                    identity = source_spec["identity"] + "/" + relative
                    description = str(meta.get("description", "")).strip()
                    if not description:
                        description = first_sentence(body)
                    mode = str(meta.get("mode", "")).strip()
                    if mode not in ("inline", "conversational", "delegated"):
                        mode = "delegated" if kind == "agent" else "inline"
                    maintenance = kind == "command" and name.rsplit(":", 1)[-1].casefold() in {
                        "install",
                        "update",
                        "uninstall",
                    }
                    item = {
                        "id": identity,
                        "resource_id": identity,
                        "revision": hashlib.sha256(text.encode()).hexdigest(),
                        "kind": kind,
                        "name": name,
                        "description": description[:1000],
                        "scope": scope,
                        "origin": origin,
                        "source": str(path),
                        "namespace": namespace,
                        "argument_hint": argument_hint(meta, body, name),
                        "backend": str(meta.get("backend", backend)),
                        "model": str(meta.get("model", model or "")),
                        "effort": str(
                            meta.get("effort", meta.get("model_reasoning_effort", ""))
                        ),
                        "mode": mode,
                        "native_command": engine == "claude" and kind == "command",
                        "maintenance": maintenance,
                        "group": "Maintenance" if maintenance else kind.title() + "s",
                        "selectable": not bool(reason),
                        "unavailable_reason": reason,
                        "preflight_hint": preflight_hint(reason),
                        "compatibility": (
                            {"claude_ai_connectors": "unknown"} if engine == "claude" else {}
                        ),
                    }
                    if private:
                        item.update(_text=text, _body=body, _meta=meta)
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


def resolve(config, data):
    prompt = data.get("prompt", "")
    if re.search(r"(?<!\S)(@@|//)[\w:-]*", prompt):
        raise ResourceError("tail_resources_unavailable")
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
        )["items"]
    }
    result = []
    tokens = {}
    for selection in selections:
        if not isinstance(selection, dict) or not all(
            isinstance(selection.get(key), str) and len(selection[key]) <= 200
            for key in ("id", "revision", "token")
        ):
            raise ResourceError("invalid_resource_selections")
        item = found.get(selection.get("id"))
        if item is None or not item["selectable"]:
            raise ResourceError("resource_unavailable")
        if item["revision"] != selection.get("revision"):
            raise ResourceError("resource_changed")
        accepted_tokens = (
            ("/" + item["name"], "@" + item["name"])
            if item["kind"] == "agent"
            else ("/" + item["name"],)
        )
        token = selection.get("token")
        if token not in accepted_tokens or not re.search(
            r"(?<!\S)" + re.escape(token) + r"(?=\s|$)", prompt
        ):
            raise ResourceError("resource_selection_missing")
        if token in tokens and tokens[token] != item["id"]:
            raise ResourceError("resource_name_ambiguous")
        tokens[token] = item["id"]
        if not any(value["id"] == item["id"] for value in result):
            result.append({**item, "_token": token})
    return result


def prepare_prompt(prompt, items):
    notes = []
    commands = {}
    skills = {}
    for item in items:
        name = item["name"]
        token = item.get("_token", "/" + name)
        if item["kind"] == "agent":
            if item.get("mode") == "conversational":
                notes.append(
                    "Adopt the conversational agent "
                    + json.dumps(name)
                    + " defined at "
                    + json.dumps(item["source"])
                    + " for this main-thread conversation until it is released.\n"
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
        patterns.append(
            r"(?P<command>" + "|".join(map(re.escape, commands)) + r")(?=\s|$)(?P<args>[^\n]*)"
        )
    if skills:
        patterns.append(r"(?P<skill>" + "|".join(map(re.escape, skills)) + r")(?=\s|$)")
    if patterns:

        def expand(match):
            groups = match.groupdict()
            if groups.get("skill") is not None:
                return skills[groups["skill"]]
            args = groups["args"]
            if args.startswith(" "):
                args = args[1:]
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
                commands[groups["command"]]["_body"],
            )

        # Match the original text once; arguments and inserted bodies remain data.
        prompt = re.sub(r"(?<!\S)(?:" + "|".join(patterns) + ")", expand, prompt)
    prompt += "\n\nEXPLICIT RESOURCE SELECTIONS:\n" + "\n".join(notes) if notes else ""
    if len(prompt) > 150000:
        raise ResourceError("resource_prompt_limit")
    return prompt
