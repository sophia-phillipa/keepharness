"""Proof that a request with Tailscale identity headers came through Tailscale Serve (WP-19 S2a).

Serve's tailscaled (uid 0) opens the connection to 127.0.0.1:<port>. Another account on the
computer can send the same headers, but its socket is owned by its own uid in /proc/net/tcp.
"""

import asyncio
import functools
import struct
from pathlib import Path

from test_api_security import (
    GUEST_LOGIN,
    SERVE_HEADERS,
    loopback,
    owner_config,
    seeded_owner_app,
    who,
)

from control import local_access

HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode"
PORT = 8095
CLIENT_PORT = 4321


def hex_address(host: str, port: int) -> str:
    # The kernel prints the network-order address as a host-order word.
    word = struct.unpack("=I", bytes(int(part) for part in host.split(".")))[0]
    return f"{word:08X}:{port:04X}"


def row(index: int, local: str, remote: str, uid: int, state: str = "01") -> str:
    return (
        f"{index:4d}: {local} {remote} {state} 00000000:00000000 00:00000000 00000000 "
        f"{uid:5d}        0 1234{index} 1 0000000000000000 100 0 0 10 0"
    )


def proc_file(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "tcp"
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def peer_rows(uid: int) -> list[str]:
    client = hex_address("127.0.0.1", CLIENT_PORT)
    server = hex_address("127.0.0.1", PORT)
    return [
        # The listener and the server side of the same connection belong to the harness user.
        row(0, server, hex_address("0.0.0.0", 0), 1000, "0A"),
        row(1, server, client, 1000),
        row(2, client, server, uid),
    ]


def test_uid_zero_row_is_served(tmp_path):
    proc = proc_file(tmp_path, *peer_rows(0))
    assert local_access.served_by_tailscaled(("127.0.0.1", CLIENT_PORT), PORT, proc_net=proc)


def test_other_uid_or_no_row_is_not_served(tmp_path):
    client = ("127.0.0.1", CLIENT_PORT)
    user = proc_file(tmp_path, *peer_rows(1000))
    assert not local_access.served_by_tailscaled(client, PORT, proc_net=user)
    # No row for this source port, a different server port, or a non-loopback client.
    assert not local_access.served_by_tailscaled(
        ("127.0.0.1", CLIENT_PORT + 1), PORT, proc_net=user
    )
    root = proc_file(tmp_path, *peer_rows(0))
    assert not local_access.served_by_tailscaled(client, PORT + 1, proc_net=root)
    assert not local_access.served_by_tailscaled(("127.0.0.2", CLIENT_PORT), PORT, proc_net=root)
    assert not local_access.served_by_tailscaled(("::1", CLIENT_PORT), PORT, proc_net=root)
    assert not local_access.served_by_tailscaled(None, PORT, proc_net=root)
    # A uid-0 TIME_WAIT leftover beside a live user socket on the same 4-tuple is not a proof.
    source = hex_address("127.0.0.1", CLIENT_PORT)
    target = hex_address("127.0.0.1", PORT)
    mixed = proc_file(tmp_path, row(0, source, target, 0, "06"), row(1, source, target, 1000))
    assert not local_access.served_by_tailscaled(client, PORT, proc_net=mixed)


def test_malformed_proc_file_fails_closed(tmp_path):
    client = ("127.0.0.1", CLIENT_PORT)
    for text in (
        "",
        HEADER + "\n",
        "garbage\nmore garbage\n",
        HEADER + "\n   0: zz:zz zz:zz 01 x\n",
    ):
        path = tmp_path / "bad"
        path.write_text(text, encoding="utf-8")
        assert not local_access.served_by_tailscaled(client, PORT, proc_net=path)
    assert not local_access.served_by_tailscaled(client, PORT, proc_net=tmp_path / "missing")
    assert not local_access.served_by_tailscaled(client, PORT, proc_net=tmp_path)
    binary = tmp_path / "binary"
    binary.write_bytes(b"\xff\xfe\x00\x01")
    assert not local_access.served_by_tailscaled(client, PORT, proc_net=binary)


def test_forged_serve_headers_from_a_user_socket_get_401(tmp_path, caplog):
    cfg = owner_config(tmp_path)
    app = seeded_owner_app(cfg)
    forged = {"Tailscale-User-Login": GUEST_LOGIN, **SERVE_HEADERS}

    async def scenario():
        async with loopback(app) as client:
            service = app.state.service
            # The real check, pointed at a fixture: the connecting socket belongs to a user.
            service.serve_peer_check = functools.partial(
                local_access.served_by_tailscaled, proc_net=proc_file(tmp_path, *peer_rows(1000))
            )
            with caplog.at_level("WARNING"):
                assert await who(client, headers=forged) == (401, "authentication_required")
            assert "Tailscale identity headers ignored" in caplog.text
            # The same headers over a tailscaled (uid 0) socket map the listed login.
            service.serve_peer_check = functools.partial(
                local_access.served_by_tailscaled, proc_net=proc_file(tmp_path, *peer_rows(0))
            )
            assert await who(client, headers=forged) == (200, ["tailnet-guest-job"])
            # An unreadable /proc fails closed.
            service.serve_peer_check = functools.partial(
                local_access.served_by_tailscaled, proc_net=tmp_path / "missing"
            )
            assert await who(client, headers=forged) == (401, "authentication_required")

    try:
        asyncio.run(scenario())
    finally:
        app.state.service.db.close()


def test_default_serve_check_is_the_proc_lookup(tmp_path):
    app = seeded_owner_app(owner_config(tmp_path))
    try:
        assert app.state.service.serve_peer_check is local_access.served_by_tailscaled
    finally:
        app.state.service.db.close()
