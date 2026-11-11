"""Wallet authentication and signed intent verification.

The backend never holds a wallet key. An owner proves control of an Ed25519 key
(the curve NEAR accounts use) by signing a canonical message. Two message kinds:

* ``login`` — establishes an API session.
* ``intent`` — authorizes exactly one privileged action (fund, approve, payout,
  refund, reconcile, dispute). The intent binds action, bounty/claim ids,
  contract id and network, so a signature for one bounty cannot be replayed
  against another, and it is single-use via the nonce table.

This is the local stand-in for a browser wallet signing a NEAR transaction. The
real chain adapter would take the same intent and turn it into an ``Action``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..canonical import b64decode, b64encode, canonical_bytes
from ..errors import AuthError
from ..store import Store

LOGIN_PURPOSE = "login"
INTENT_ACTIONS = frozenset(
    {
        "fund_bounty",
        "approve_claim",
        "request_payout",
        "refund_expired",
        "reconcile",
        "set_dispute",
    }
)


def _message(
    *,
    purpose: str,
    account: str,
    nonce: str,
    domain: str,
    network: str,
    contract_id: str,
    issued_ns: int,
    expires_ns: int,
    action: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    body: dict[str, Any] = {
        "purpose": purpose,
        "account": account,
        "nonce": nonce,
        "domain": domain,
        "network": network,
        "contract_id": contract_id,
        "issued_ns": issued_ns,
        "expires_ns": expires_ns,
    }
    if action:
        body["action"] = action
        body["payload"] = payload or {}
    return canonical_bytes(body).decode("utf-8")


def parse_message(message: str) -> dict[str, Any]:
    try:
        parsed = json.loads(message)
    except json.JSONDecodeError as exc:
        raise AuthError("signed message is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise AuthError("signed message must be a JSON object")
    return parsed


def verify_signature(public_key_b64: str, message: str, signature_b64: str) -> None:
    try:
        raw_key = b64decode(public_key_b64)
        raw_sig = b64decode(signature_b64)
    except (ValueError, TypeError) as exc:
        raise AuthError("public key or signature is not valid base64") from exc
    try:
        key = Ed25519PublicKey.from_public_bytes(raw_key)
        key.verify(raw_sig, message.encode("utf-8"))
    except (InvalidSignature, ValueError) as exc:
        raise AuthError("signature does not verify for this account key") from exc


@dataclass
class WalletAuth:
    store: Store
    domain: str
    network: str
    contract_id: str
    login_ttl_s: int = 300
    intent_ttl_s: int = 300

    # ------------------------------------------------------------------
    # registration (proof of possession)
    # ------------------------------------------------------------------
    def registration_message(self, account: str, public_key_b64: str, nonce: str) -> str:
        """Canonical proof-of-possession message.

        The signing process (``wallet/signer.py``) builds this exact object, and
        ``tests/test_signer.py`` asserts byte equality so the two can never drift.
        """
        body: dict[str, Any] = {
            "purpose": "register",
            "account": account,
            "nonce": nonce,
            "domain": self.domain,
            "network": self.network,
            "contract_id": self.contract_id,
            "issued_ns": 0,
            "expires_ns": 0,
            "payload": {"public_key_b64": public_key_b64},
        }
        return canonical_bytes(body).decode("utf-8")

    def register_key(self, *, account: str, public_key_b64: str, nonce: str, signature_b64: str) -> str:
        if not account or len(account) > 128:
            raise AuthError("invalid account id")
        message = self.registration_message(account, public_key_b64, nonce)
        verify_signature(public_key_b64, message, signature_b64)
        self.store.register_key(account, public_key_b64)
        self.store.audit(actor=account, action="wallet_key_registered", entity="user", entity_id=account)
        return message

    def public_key_for(self, account: str) -> str:
        key = self.store.get_key(account)
        if not key:
            raise AuthError(f"account {account!r} has no registered wallet key")
        return key

    # ------------------------------------------------------------------
    # login
    # ------------------------------------------------------------------
    def issue_login(self, account: str) -> dict[str, Any]:
        self.public_key_for(account)  # fail early if unknown
        nonce = secrets.token_hex(16)
        issued = time.time_ns()
        expires = issued + self.login_ttl_s * 10**9
        message = _message(
            purpose=LOGIN_PURPOSE,
            account=account,
            nonce=nonce,
            domain=self.domain,
            network=self.network,
            contract_id=self.contract_id,
            issued_ns=issued,
            expires_ns=expires,
        )
        self.store.put_nonce(
            nonce=nonce,
            account=account,
            domain=self.domain,
            purpose=LOGIN_PURPOSE,
            message=message,
            expires_ns=expires,
        )
        return {"nonce": nonce, "message": message, "expires_ns": expires}

    def verify_login(self, *, message: str, signature_b64: str) -> str:
        parsed = parse_message(message)
        self._check_envelope(parsed, purpose=LOGIN_PURPOSE)
        account = str(parsed["account"])
        public_key = self.public_key_for(account)
        verify_signature(public_key, message, signature_b64)
        consumed = self.store.consume_nonce(
            str(parsed["nonce"]),
            purpose=LOGIN_PURPOSE,
            domain=self.domain,
            now=time.time_ns(),
        )
        if consumed is None:
            raise AuthError("login nonce is expired, unknown or already used")
        self.store.audit(actor=account, action="wallet_login", entity="user", entity_id=account)
        return account

    # ------------------------------------------------------------------
    # privileged intents
    # ------------------------------------------------------------------
    def issue_intent(self, *, account: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action not in INTENT_ACTIONS:
            raise AuthError(f"unknown intent action {action!r}")
        self.public_key_for(account)
        nonce = secrets.token_hex(16)
        issued = time.time_ns()
        expires = issued + self.intent_ttl_s * 10**9
        message = _message(
            purpose="intent",
            account=account,
            nonce=nonce,
            domain=self.domain,
            network=self.network,
            contract_id=self.contract_id,
            issued_ns=issued,
            expires_ns=expires,
            action=action,
            payload=payload,
        )
        self.store.put_nonce(
            nonce=nonce,
            account=account,
            domain=self.domain,
            purpose=f"intent:{action}",
            message=message,
            expires_ns=expires,
        )
        return {"nonce": nonce, "message": message, "expires_ns": expires}

    def verify_intent(
        self, *, message: str, signature_b64: str, action: str
    ) -> tuple[str, dict[str, Any]]:
        parsed = parse_message(message)
        self._check_envelope(parsed, purpose="intent")
        if parsed.get("action") != action:
            raise AuthError(f"intent is for action {parsed.get('action')!r}, expected {action!r}")
        account = str(parsed["account"])
        public_key = self.public_key_for(account)
        verify_signature(public_key, message, signature_b64)
        consumed = self.store.consume_nonce(
            str(parsed["nonce"]),
            purpose=f"intent:{action}",
            domain=self.domain,
            now=time.time_ns(),
        )
        if consumed is None:
            raise AuthError("intent nonce is expired, unknown or already used")
        payload = parsed.get("payload") or {}
        if not isinstance(payload, dict):
            raise AuthError("intent payload must be an object")
        return account, payload

    # ------------------------------------------------------------------
    def _check_envelope(self, parsed: dict[str, Any], *, purpose: str) -> None:
        if parsed.get("purpose") != purpose:
            raise AuthError(f"message purpose must be {purpose!r}")
        if parsed.get("domain") != self.domain:
            raise AuthError("signed message is for a different domain")
        if parsed.get("network") != self.network:
            raise AuthError("signed message is for a different network")
        if parsed.get("contract_id") != self.contract_id:
            raise AuthError("signed message is for a different contract")
        try:
            expires = int(parsed["expires_ns"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthError("signed message has no valid expiry") from exc
        if expires < time.time_ns():
            raise AuthError("signed message has expired")


def new_agent_token() -> tuple[str, str]:
    """Return ``(token, sha256(token))``; only the hash is persisted."""
    token = "pa_agent_" + secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode("utf-8")).hexdigest()


def encode_public_key(raw: bytes) -> str:
    return b64encode(raw)
