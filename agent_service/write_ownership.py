"""Event-loop-owned leases for competing project/work-item writers."""

from pathlib import Path


def resolve_root(root):
    """Resolve a root; a symlink loop raises on every Python (3.13+ stopped raising in resolve)."""
    resolved = Path(root).resolve()
    try:
        resolved.stat()
    except FileNotFoundError:
        pass
    return resolved


class WriteOwnership:
    def __init__(self):
        self.leases = {}

    def acquire(self, owner, project, work_item, roots):
        roots = tuple(resolve_root(root) for root in roots if root)
        for other, (other_project, other_item, other_roots) in self.leases.items():
            if other == owner:
                continue
            if work_item and project == other_project and work_item == other_item:
                return "work_item"
            if any(a.is_relative_to(b) or b.is_relative_to(a) for a in roots for b in other_roots):
                return "writable_root"
        self.leases[owner] = (project, work_item, roots)
        return None

    def release(self, owner):
        self.leases.pop(owner, None)
