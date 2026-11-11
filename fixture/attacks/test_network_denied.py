"""RUN-001 / RUN-002: the sandbox must deny network and must not carry secrets."""

from __future__ import annotations

import os
import socket

import pytest


def test_outbound_connection_is_denied():
    with pytest.raises(OSError):
        socket.create_connection(("example.com", 80), timeout=2)


def test_dns_resolution_is_denied():
    with pytest.raises(OSError):
        socket.getaddrinfo("example.com", 80)


def test_raw_socket_connect_is_denied():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            sock.connect(("93.184.216.34", 80))
    finally:
        sock.close()


def test_no_secret_bearing_environment_variables_are_forwarded():
    forbidden = ("SECRET", "PRIVATE", "SEED", "MNEMONIC", "PASSWORD", "WALLET", "TOKEN")
    leaked = [name for name in os.environ if any(hint in name.upper() for hint in forbidden)]
    assert leaked == [], f"secret-bearing env vars leaked into sandbox: {leaked}"


def test_wallet_key_file_is_not_present():
    for candidate in ("wallet.json", "owner.key", ".env", "session.secret"):
        assert not os.path.exists(candidate)
