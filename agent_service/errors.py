"""Harness exception hierarchy; a dependency-free leaf shared by every package."""


class HarnessError(Exception):
    """Base for harness errors that carry a client-facing code.

    ``super().__init__`` is deliberately not called: ``BaseException.__new__``
    already stores the positional arguments, so ``args`` and ``str()`` stay
    identical to a plain ``Exception`` built with the same arguments.
    """

    def __init__(self, code, status=422, retry_after=None):
        self.code, self.status, self.retry_after = code, status, retry_after


class APIError(HarnessError):
    """An HTTP API failure returned to the client as ``{code, message}``.

    ``field`` names the offending request field, ``owner`` the caller's own owner id (what
    ``keepharness approve-device --owner`` accepts) and ``login`` the caller's Tailscale login,
    the name the owner of the host knows them by; each is added to the body only when set.
    """

    def __init__(self, code, status=422, retry_after=None, field=None, owner=None, login=None):
        super().__init__(code, status, retry_after)
        self.field, self.owner, self.login = field, owner, login


class ToolError(HarnessError):
    """A tool, provider or adapter failure; ``str()`` is the reason."""


class UserMessageError(HarnessError, ValueError):
    """A deliberate, user-facing validation failure; ``str()`` is the sentence the panel shows.

    Plain ``ValueError`` and ``RuntimeError`` reaching an admin route are treated as unexpected.
    """

    def __init__(self, message):
        super().__init__(message, status=400)
