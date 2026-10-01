"""Select catalog runtime prerequisites only for resources used by an execution."""


def runtime_config(config, project_id, resources):
    selected = set()
    for resource in resources:
        parts = resource.get("resource_id", "").split("/")
        if len(parts) > 2 and parts[0] == "catalog":
            selected.add(parts[1])
    project = config["projects"][project_id]
    project = {
        **project,
        "catalogs": [key for key in project.get("catalogs", []) if key in selected],
    }
    return {**config, "projects": {**config["projects"], project_id: project}}
