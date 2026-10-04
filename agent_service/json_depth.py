"""Nesting bound for parsed JSON; Python 3.13+ no longer raises RecursionError on deep input."""

MAX_JSON_DEPTH = 64


def too_deep(data):
    """Iterative nesting check, so it holds on every Python version."""
    level = [data]
    for _ in range(MAX_JSON_DEPTH):
        level = [
            child
            for node in level
            for child in (node.values() if isinstance(node, dict) else node)
            if isinstance(child, (dict, list))
        ]
        if not level:
            return False
    return True
