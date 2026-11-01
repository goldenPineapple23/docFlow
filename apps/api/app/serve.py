"""
How the API starts on Fly (`apps/api/Dockerfile`): one socket that takes both
IPv4 and IPv6 (D-227).

Fly's edge dials a machine on IPv4 and `fly proxy` on its private IPv6 address,
and the API has to answer both. `uvicorn --host ::` can't: when uvicorn makes
the socket, asyncio sets IPV6_V6ONLY on it, so the edge is refused (found
2026-11-01; every request through a public address read 503). A socket handed
to uvicorn keeps its own setting, which is how the parse service binds too.

`app.main` is imported at the top so the parse-token refusal (Q12) still comes
before the port opens.
"""

from __future__ import annotations

import socket
import sys

import uvicorn

from app.main import app

PORT = 8000


def listening_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind(("::", port))
    return sock


def main(argv: list[str]) -> None:
    port = int(argv[0]) if argv else PORT
    server = uvicorn.Server(uvicorn.Config(app))
    # A failed startup needs no check here: uvicorn 0.53.0's Server.startup exits
    # 3 itself, `if self.lifespan.should_exit: sys.exit(STARTUP_FAILURE)`
    # (uvicorn/server.py:117-118). The test that guards a uvicorn change is
    # test_a_wrong_login_at_startup_exits_3_through_real_uvicorn.
    server.run(sockets=[listening_socket(port)])


if __name__ == "__main__":
    main(sys.argv[1:])
