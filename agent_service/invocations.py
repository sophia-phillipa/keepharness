"""Portable invocation contract shared by composer selections and declared plans."""

import re
from dataclasses import asdict, dataclass
from urllib.parse import quote


class InvocationError(ValueError):
    pass


@dataclass(frozen=True)
class Invocation:
    kind: str
    resource_id: str
    args: str = ""
    order: int = 0
    mode: str = "inline"
    requested_backend: str | None = None
    id: str | None = None

    def __post_init__(self):
        if self.kind not in ("command", "agent", "skill", "workflow", "builtin"):
            raise InvocationError("invalid_invocation_kind")
        if (
            not isinstance(self.resource_id, str)
            or not self.resource_id
            or len(self.resource_id) > 1000
            or "\\" in self.resource_id
            or ":" in self.resource_id
            or any(part in ("", ".", "..") for part in self.resource_id.split("/"))
            or any(ord(char) < 32 for char in self.resource_id)
        ):
            raise InvocationError("invalid_invocation_resource_id")
        if not isinstance(self.args, str) or len(self.args) > 150000:
            raise InvocationError("invalid_invocation_args")
        if type(self.order) is not int or self.order < 0:
            raise InvocationError("invalid_invocation_order")
        if self.mode not in ("inline", "conversational", "delegated"):
            raise InvocationError("invalid_invocation_mode")
        if self.mode == "conversational" and self.kind != "agent":
            raise InvocationError("invalid_invocation_mode")
        if self.requested_backend is not None and self.requested_backend not in (
            "codex",
            "claude",
            "gemini",
            "deepseek",
            "local",
        ):
            raise InvocationError("invalid_invocation_backend")
        if self.id is not None and (
            not isinstance(self.id, str) or not self.id or len(self.id) > 200
        ):
            raise InvocationError("invalid_invocation_id")

    def to_dict(self):
        return {key: value for key, value in asdict(self).items() if value is not None}


def validate_chain(values):
    if len(values) > 12:
        raise InvocationError("invocation_chain_limit")
    if len(values) > 1 and any(value.mode == "conversational" for value in values):
        raise InvocationError("conversational_chain_unsupported")
    if [value.order for value in values] != list(range(len(values))):
        raise InvocationError("invalid_invocation_order")
    return values


def normalize_chips(prompt, selections, items):
    """Selection identity is resolved by resources before text is interpreted here."""
    from .resources import unfenced

    prose = unfenced(prompt, preserve_offsets=True)
    found = {item["id"]: item for item in items}
    matches = []
    consumed = set()
    for selection in selections:
        item = found.get(selection["id"])
        if item is None:
            raise InvocationError("resource_unavailable")
        token = selection["token"]
        match = next(
            (
                candidate
                for candidate in re.finditer(r"(?<!\S)" + re.escape(token) + r"(?=\s|$)", prose)
                if candidate.start() not in consumed
            ),
            None,
        )
        if match is None:
            raise InvocationError("resource_selection_missing")
        consumed.add(match.start())
        matches.append((match, item))
    matches.sort(key=lambda value: value[0].start())
    result = []
    for index, (match, item) in enumerate(matches):
        end = matches[index + 1][0].start() if index + 1 < len(matches) else len(prompt)
        start = match.end()
        # Consume one token delimiter only; every argument byte remains intact.
        if start < end and prompt[start] == " ":
            start += 1
        result.append(
            Invocation(
                kind=item["kind"],
                resource_id=item.get("resource_id", item["id"]),
                args=prompt[start:end],
                order=index,
                mode=item.get("mode") or ("delegated" if item["kind"] == "agent" else "inline"),
                requested_backend=item.get("backend"),
            )
        )
    return validate_chain(result)


def normalize_legacy_step(step, order):
    value = step.get("invocation")
    if value is not None:
        if not isinstance(value, dict):
            raise InvocationError("invalid_invocation")
        try:
            return Invocation(**value)
        except TypeError:
            raise InvocationError("invalid_invocation") from None
    return Invocation(
        kind="agent",
        resource_id="builtin/roles/" + quote(step["role"], safe=""),
        args=step["task"],
        order=order,
        mode="delegated",
        requested_backend=step["backend"],
    )
