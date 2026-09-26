"""A local server that trickles bytes cannot hold discovery (P5-24, F-08).

httpx timeouts apply per read, so only a total cap bounds a response that keeps sending
one byte just before each read timeout.
"""

import asyncio
import socket
import threading
import time
from unittest.mock import AsyncMock, patch

import pytest

from control import discovery, local_models


@pytest.fixture
def trickling_server():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    stop = threading.Event()

    def serve(connection):
        with connection:
            connection.recv(65536)
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n")
            connection.sendall(b"Content-Length: 100000\r\n\r\n{")
            while not stop.wait(0.2):
                try:
                    connection.sendall(b" ")
                except OSError:
                    return

    def accept():
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=serve, args=(connection,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    yield "http://127.0.0.1:%d" % listener.getsockname()[1]
    stop.set()
    listener.close()


def test_scan_finishes_although_ollama_and_llama_server_trickle(tmp_path, trickling_server):
    server = {"url": trickling_server, "key_file": None, "pid": 1}
    with (
        patch.object(discovery, "OLLAMA_TAGS_URL", trickling_server + "/api/tags"),
        patch.object(discovery, "PROBE_SECONDS", 0.5),
        patch.object(local_models, "PROBE_SECONDS", 0.5),
        patch.object(local_models, "processes", return_value=[server]),
        patch("control.discovery.command", AsyncMock(return_value=(1, ""))),
        patch("control.discovery.Path.home", return_value=tmp_path),
    ):
        started = time.monotonic()
        result = asyncio.run(asyncio.wait_for(discovery.scan(), 10))
        elapsed = time.monotonic() - started
    assert elapsed < 3, elapsed
    local = next(service for service in result["services"] if service["id"] == "local")
    assert local["runtimes"] == []
    assert local["models"] == []


def test_probe_caps_are_a_few_seconds():
    assert discovery.PROBE_SECONDS <= 5
    assert local_models.PROBE_SECONDS <= 5
