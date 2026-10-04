"""The local owner's browser in admin tests: it holds the per-install secret (decision D09)."""

from control import local_access


def sign_in(client, app=None):
    """Give ``client`` the install secret, as ``keepharness open`` gives a browser."""
    app = app if app is not None else client.app
    client.cookies.set(local_access.COOKIE, app.state.manager.local_secret)
    return client
