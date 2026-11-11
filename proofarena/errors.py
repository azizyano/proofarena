"""Typed error codes shared by the escrow engine, API and web layer.

Codes are stable identifiers used in tests and in user-facing messages, so a
refusal is never a bare stack trace and never silently degrades into success.
"""

from __future__ import annotations


class ProofArenaError(Exception):
    code = "E_UNKNOWN"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code

    def to_dict(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}


class EscrowError(ProofArenaError):
    """Raised by the escrow state machine. Nothing is mutated before a raise."""

    code = "E_ESCROW"


class Unauthorized(EscrowError):
    code = "E_UNAUTHORIZED"
    http_status = 403


class NotFound(EscrowError):
    code = "E_NOT_FOUND"
    http_status = 404


class StateError(EscrowError):
    code = "E_STATE"
    http_status = 409


class TerminalState(StateError):
    code = "E_TERMINAL"
    http_status = 409


class ParamError(EscrowError):
    code = "E_PARAM"
    http_status = 422


class DepositError(EscrowError):
    code = "E_DEPOSIT"
    http_status = 402


class DuplicateClaim(EscrowError):
    code = "E_DUPLICATE_CLAIM"
    http_status = 409


class ReplayError(EscrowError):
    code = "E_REPLAY"
    http_status = 409


class CallbackError(EscrowError):
    code = "E_CALLBACK"
    http_status = 409


class ChainUnavailable(ProofArenaError):
    code = "E_CHAIN_UNAVAILABLE"
    http_status = 503


class RunnerError(ProofArenaError):
    code = "E_RUNNER"
    http_status = 500


class SchemaError(ProofArenaError):
    code = "E_SCHEMA"
    http_status = 422


class AuthError(ProofArenaError):
    code = "E_AUTH"
    http_status = 401


class RateLimited(ProofArenaError):
    code = "E_RATE_LIMITED"
    http_status = 429
