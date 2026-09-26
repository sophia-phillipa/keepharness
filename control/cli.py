"""Console entry point, usable from an installed wheel or a source checkout."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from .discovery import scan


def main(argv=None):
    parser = argparse.ArgumentParser(description="Tail Harness — local AI services")
    parser.add_argument("--scan", action="store_true", help="Read-only local inventory")
    parser.add_argument("--port", type=int, default=8094)
    parser.add_argument("--state", default=str(Path.home() / ".local/share/tail-harness"))
    args = parser.parse_args(argv)
    os.umask(0o077)
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    if args.scan:
        print(json.dumps(asyncio.run(scan()), indent=2, ensure_ascii=False))
        return
    import uvicorn

    from .server import create_app

    print(f"Local management: http://127.0.0.1:{args.port}/", flush=True)
    uvicorn.run(
        create_app(args.state, args.port),
        host="127.0.0.1",
        port=args.port,
        proxy_headers=False,
        access_log=False,
    )
