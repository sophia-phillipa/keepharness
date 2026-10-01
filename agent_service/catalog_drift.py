"""Pure comparison of canonical resources across project/catalog snapshots."""

import json


def compare_resources(snapshots):
    groups = {}
    for location, items in sorted(snapshots.items()):
        for item in items:
            resource_id = item.get("resource_id")
            if not resource_id:
                continue
            groups.setdefault(resource_id, {})[location] = {
                key: item.get(key) for key in ("revision", "deps_revisions", "catalog_commit")
            }
    return [
        {
            "resource_id": resource_id,
            "locations": sorted(values),
            "revisions": values,
            "drift": len({json.dumps(value, sort_keys=True) for value in values.values()}) > 1,
        }
        for resource_id, values in sorted(groups.items())
    ]
