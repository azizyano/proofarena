"""Canonical serialization and hashing.

Every digest in ProofArena is SHA-256 over a canonical byte string so that the
same inputs produce the same commitment on any machine. The canonical form is
UTF-8 JSON with sorted keys, no insignificant whitespace and no NaN/Infinity.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

HEX_CHARS = set("0123456789abcdef")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_hex(text.encode("utf-8"))


def sha256_json(value: Any) -> str:
    return sha256_hex(canonical_bytes(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_hex64(value: object) -> bool:
    """True only for a lowercase 64-character SHA-256 hex digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value) <= HEX_CHARS
    )


def is_hex(value: object, min_len: int = 32, max_len: int = 128) -> bool:
    return (
        isinstance(value, str)
        and min_len <= len(value) <= max_len
        and len(value) % 2 == 0
        and set(value) <= HEX_CHARS
    )


def b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def b64decode(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def hmac_sign(key: bytes, message: bytes) -> str:
    return b64url_encode(hmac.new(key, message, hashlib.sha256).digest())


def hmac_verify(key: bytes, message: bytes, signature: str) -> bool:
    expected = hmac_sign(key, message)
    return hmac.compare_digest(expected, signature)


def hash_chain_link(prev_hash: str, payload: dict[str, Any]) -> str:
    """Append-only receipt chain: each hash commits to the previous one."""
    return sha256_hex(prev_hash.encode("ascii") + canonical_bytes(payload))
