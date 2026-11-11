"""Stateless session tokens (HMAC-SHA256), plus a small in-memory rate limiter."""

from __future__ import annotations

import json
import secrets
import time
from collections import defaultdict, deque
from typing import Any

from ..canonical import b64url_decode, b64url_encode, canonical_bytes, hmac_sign, hmac_verify
from ..errors import AuthError


def issue_session(*, account: str, secret: bytes, ttl_s: int = 8 * 3600) -> str:
    body = {"sub": account, "exp": int(time.time()) + ttl_s, "jti": secrets.token_hex(8)}
    payload = b64url_encode(canonical_bytes(body))
    signature = hmac_sign(secret, payload.encode("ascii"))
    return f"{payload}.{signature}"


def verify_session(token: str, *, secret: bytes) -> str:
    if not token or "." not in token:
        raise AuthError("missing or malformed session token")
    payload, signature = token.rsplit(".", 1)
    if not hmac_verify(secret, payload.encode("ascii"), signature):
        raise AuthError("session token signature is invalid")
    try:
        body: dict[str, Any] = json.loads(b64url_decode(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("session token payload is invalid") from exc
    if int(body.get("exp", 0)) < int(time.time()):
        raise AuthError("session token has expired")
    account = body.get("sub")
    if not isinstance(account, str) or not account:
        raise AuthError("session token has no subject")
    return account


class RateLimiter:
    """Fixed-window limiter, per key. Enough for a single-process MVP."""

    def __init__(self, limit: int = 30, window_s: int = 60) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self.window_s:
            bucket.popleft()
        if len(bucket) >= self.limit:
            from ..errors import RateLimited

            raise RateLimited(f"rate limit exceeded for {key}; retry in {self.window_s}s")
        bucket.append(now)
