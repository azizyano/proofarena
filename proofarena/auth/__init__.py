"""Authentication package: wallet signatures, sessions and rate limiting."""

from .sessions import RateLimiter, issue_session, verify_session
from .wallet import (
    INTENT_ACTIONS,
    LOGIN_PURPOSE,
    WalletAuth,
    new_agent_token,
    parse_message,
    verify_signature,
)

__all__ = [
    "INTENT_ACTIONS",
    "LOGIN_PURPOSE",
    "RateLimiter",
    "WalletAuth",
    "issue_session",
    "new_agent_token",
    "parse_message",
    "verify_session",
    "verify_signature",
]
