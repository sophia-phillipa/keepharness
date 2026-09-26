"""SQLite state of the agent service: connection, migrations and repositories.

Repositories share the service's connection, keep each statement verbatim and never
commit: every ``with db:`` transaction stays at its call site.
"""
