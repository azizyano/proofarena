"""Browser-wallet flow: NEP-413 authentication and wallet-sent transaction verification.

With a real wallet the server holds **no key at all**:

1. The browser signs a NEP-413 message to prove it controls the account
   (``POST /auth/wallet/*``). The signature is checked against the account's
   on-chain access keys.
2. For an owner action the server returns a *deterministic transaction plan*
   (contract, method, args, deposit, gas). The wallet signs **and broadcasts** it.
3. The browser reports the transaction hash. The server re-reads it from the
   chain and only then updates its mirror — the wallet's word is not evidence.

This module contains the pure logic so it can be tested without a browser.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any

from .errors import AuthError, ParamError, StateError
from .near.borsh import BorshWriter
from .near.signing import verify_signature

#: Transaction plan for one owner action. Returned to the browser, never signed
#: server-side.
@dataclass(frozen=True)
class TxPlan:
    action: str
    contract_id: str
    method: str
    args: dict[str, Any]
    deposit_yocto: int
    gas: int

    def to_public(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "contract_id": self.contract_id,
            "method": self.method,
            "args": self.args,
            "deposit_yocto": str(self.deposit_yocto),
            "gas": self.gas,
        }


GAS_100_TGAS = 100_000_000_000_000


# ---------------------------------------------------------------------------
# NEP-413
# ---------------------------------------------------------------------------
def nep413_payload(
    *, message: str, nonce: bytes, recipient: str, callback_url: str | None = None
) -> bytes:
    """Borsh-encode the NEP-413 sign-message payload."""
    if len(nonce) != 32:
        raise ValueError("NEP-413 nonce must be 32 bytes")
    writer = BorshWriter()
    writer.string(message)
    writer.bytes_fixed(nonce)
    writer.string(recipient)
    if callback_url is None:
        writer.u8(0)
    else:
        writer.u8(1)
        writer.string(callback_url)
    return writer.build()


def nep413_digest(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()


def verify_nep413_signature(
    *,
    public_key_ed25519: str,
    signature_b64: str,
    message: str,
    nonce: bytes,
    recipient: str,
    callback_url: str | None = None,
) -> None:
    """Verify a wallet ``signMessage`` result against the NEP-413 digest."""
    if not public_key_ed25519.startswith("ed25519:"):
        raise AuthError("only ed25519 wallet keys are supported")
    from .near.base58 import b58decode

    raw_key = b58decode(public_key_ed25519.split(":", 1)[1])
    if len(raw_key) != 32:
        raise AuthError("wallet public key must be 32 bytes")
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise AuthError("signature is not valid base64") from exc
    if len(signature) != 64:
        raise AuthError("signature must be 64 bytes")
    digest = nep413_digest(
        nep413_payload(message=message, nonce=nonce, recipient=recipient, callback_url=callback_url)
    )
    try:
        verify_signature(raw_key, digest, signature)
    except Exception as exc:  # InvalidSignature
        raise AuthError("wallet signature does not verify for this key") from exc


# ---------------------------------------------------------------------------
# wallet-sent transaction verification
# ---------------------------------------------------------------------------
def _action_of(tx: dict[str, Any]) -> dict[str, Any] | None:
    actions = tx.get("actions") or []
    for action in actions:
        if "FunctionCall" in action:
            return action["FunctionCall"]
    return None


def _args_match(sent_b64: str | None, expected: dict[str, Any]) -> bool:
    import json

    if sent_b64 is None:
        return False
    try:
        raw = base64.b64decode(sent_b64)
        sent = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    return sent == expected


def verify_wallet_tx(
    rpc: Any,
    *,
    tx_hash: str,
    contract_id: str,
    signer_account: str,
    plan: TxPlan,
) -> dict[str, Any]:
    """Confirm a wallet-sent transaction matches the plan and actually succeeded.

    Raises :class:`StateError` with a specific reason when it does not. Returns the
    raw ``tx`` result so the caller can read the new state.
    """
    if not tx_hash or len(tx_hash) < 32:
        raise ParamError("tx_hash looks invalid")
    try:
        result = rpc.tx_status(tx_hash, signer_account)
    except Exception as exc:  # RpcError
        raise StateError(f"transaction {tx_hash} is not visible on chain yet: {str(exc)[:200]}") from exc

    tx = result.get("transaction") or {}
    if tx.get("signer_id") != signer_account:
        raise StateError(
            f"transaction was signed by {tx.get('signer_id')!r}, not {signer_account!r}"
        )
    if tx.get("receiver_id") != contract_id:
        raise StateError(
            f"transaction targets {tx.get('receiver_id')!r}, not the contract {contract_id!r}"
        )
    action = _action_of(tx)
    if action is None:
        raise StateError("transaction has no FunctionCall action")
    if action.get("method_name") != plan.method:
        raise StateError(
            f"transaction called {action.get('method_name')!r}, expected {plan.method!r}"
        )
    if not _args_match(action.get("args"), plan.args):
        raise StateError("transaction arguments do not match the action plan")
    if action.get("deposit") is not None and int(action["deposit"]) != plan.deposit_yocto:
        raise StateError(
            f"transaction attached {action['deposit']} yoctoNEAR, expected {plan.deposit_yocto}"
        )

    status = result.get("status") or {}
    if "Failure" in status:
        raise StateError(f"transaction failed on chain: {_failure_reason(status)}")
    failures = [
        item["outcome"]
        for item in result.get("receipts_outcome", []) or []
        if "Failure" in (item.get("outcome", {}).get("status") or {})
    ]
    if failures:
        raise StateError(f"a receipt failed on chain: {_failure_reason(failures[0].get('status', {}))}")
    if result.get("final_execution_status") not in {"EXECUTED", "EXECUTED_OPTIMISTIC", "FINAL", None}:
        raise StateError(f"transaction status is {result.get('final_execution_status')!r}, not executed")
    return result


def _failure_reason(status: dict[str, Any]) -> str:
    import json

    failure = status.get("Failure")
    text = json.dumps(failure) if failure is not None else json.dumps(status)
    if "FunctionCallError" in text:
        return f"contract rejected the call ({text[:220]})"
    return text[:240]
