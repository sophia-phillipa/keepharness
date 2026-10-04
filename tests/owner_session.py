"""The local owner's browser in admin tests: it holds an owner session (decision D09)."""

from control import local_access


def sign_in(client, app=None):
    """Give ``client`` a session of its own, as ``keepharness open`` gives a browser."""
    app = app if app is not None else client.app
    client.cookies.set(local_access.COOKIE, local_access.issue_session(app.state.manager.state))
    return client
