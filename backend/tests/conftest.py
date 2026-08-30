"""Make the deterministic provider and offline test boundary unavoidable."""

from __future__ import annotations

from collections.abc import Iterator
import ipaddress
import socket

import pytest

from app.config import get_settings


def _is_loopback(address: object) -> bool:
    if not isinstance(address, tuple) or not address:
        return False
    host = address[0]
    if not isinstance(host, str):
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def offline_stub_boundary(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """Block external sockets except in explicitly named Postgres integration tests."""
    monkeypatch.setenv("AI_PROVIDER", "stub")
    get_settings.cache_clear()
    if str(request.node.path).endswith("_db.py"):
        try:
            yield
        finally:
            get_settings.cache_clear()
        return

    real_socket = socket.socket
    real_getaddrinfo = socket.getaddrinfo

    class GuardedSocket(real_socket):
        def connect(self, address: object) -> None:
            if _is_loopback(address):
                super().connect(address)
                return
            raise AssertionError("external network access is forbidden in tests")

        def connect_ex(self, address: object) -> int:
            if _is_loopback(address):
                return super().connect_ex(address)
            raise AssertionError("external network access is forbidden in tests")

        def sendto(self, data: bytes, *args: object) -> int:
            address = args[-1] if args else None
            if _is_loopback(address):
                return super().sendto(data, *args)
            raise AssertionError("external network access is forbidden in tests")

    def blocked_connection(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("external network access is forbidden in tests")

    def guarded_getaddrinfo(host: object, *args: object, **kwargs: object) -> object:
        if host in {"localhost", "127.0.0.1", "::1"}:
            return real_getaddrinfo(host, *args, **kwargs)
        raise AssertionError("external network access is forbidden in tests")

    monkeypatch.setattr(socket, "socket", GuardedSocket)
    monkeypatch.setattr(socket, "create_connection", blocked_connection)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    try:
        yield
    finally:
        get_settings.cache_clear()
