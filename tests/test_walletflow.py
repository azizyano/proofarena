"""Tests for the browser-wallet flow: NEP-413 auth and wallet-sent transaction checks.

The point of these tests: with a real wallet the server signs nothing. It verifies
(1) that the browser controls the account, and (2) that the transaction the browser
claims to have sent is the one the server asked for — and actually succeeded.
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proofarena.errors import AuthError, ParamError, StateError
from proofarena.near.base58 import b58encode
from proofarena.walletflow import (
    TxPlan,
    nep413_digest,
    nep413_payload,
    verify_nep413_signature,
    verify_wallet_tx,
)

CONTRACT = "escrow.testnet"
OWNER = "owner.testnet"


class WalletKey:
    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes_raw()

    @property
    def public_b58(self) -> str:
        return "ed25519:" + b58encode(self.public)

    def sign_nep413(self, *, message: str, nonce: bytes, recipient: str) -> str:
        digest = nep413_digest(
            nep413_payload(message=message, nonce=nonce, recipient=recipient)
        )
        return base64.b64encode(self.key.sign(digest)).decode("ascii")


# ---------------------------------------------------------------------------
# NEP-413
# ---------------------------------------------------------------------------
def test_nep413_payload_layout():
    """message | nonce(32) | recipient | Option::None"""
    payload = nep413_payload(message="hi", nonce=b"\x01" * 32, recipient="app.test")
    assert payload[:6] == (2).to_bytes(4, "little") + b"hi"
    assert payload[6:38] == b"\x01" * 32
    assert payload[38:42] == len("app.test").to_bytes(4, "little")
    assert payload[42:] == b"app.test" + b"\x00"


def test_nep413_signature_verifies():
    wallet = WalletKey()
    nonce = bytes(range(32))
    signature = wallet.sign_nep413(message="Sign in", nonce=nonce, recipient="app.test")
    verify_nep413_signature(
        public_key_ed25519=wallet.public_b58,
        signature_b64=signature,
        message="Sign in",
        nonce=nonce,
        recipient="app.test",
    )


@pytest.mark.parametrize(
    "message,nonce,recipient",
    [
        ("Sign in ", bytes(range(32)), "app.test"),   # message changed
        ("Sign in", bytes(range(1, 33)), "app.test"),  # nonce changed
        ("Sign in", bytes(range(32)), "evil.test"),    # recipient changed
    ],
)
def test_nep413_rejects_tampering(message, nonce, recipient):
    wallet = WalletKey()
    signature = wallet.sign_nep413(
        message="Sign in", nonce=bytes(range(32)), recipient="app.test"
    )
    with pytest.raises(AuthError):
        verify_nep413_signature(
            public_key_ed25519=wallet.public_b58,
            signature_b64=signature,
            message=message,
            nonce=nonce,
            recipient=recipient,
        )


def test_nep413_rejects_other_key_and_bad_input():
    wallet, other = WalletKey(), WalletKey()
    nonce = bytes(32)
    signature = other.sign_nep413(message="m", nonce=nonce, recipient="app.test")
    with pytest.raises(AuthError):
        verify_nep413_signature(
            public_key_ed25519=wallet.public_b58,
            signature_b64=signature,
            message="m",
            nonce=nonce,
            recipient="app.test",
        )
    with pytest.raises(AuthError):
        verify_nep413_signature(
            public_key_ed25519="secp256k1:abc",
            signature_b64=signature,
            message="m",
            nonce=nonce,
            recipient="app.test",
        )
    with pytest.raises(AuthError):
        verify_nep413_signature(
            public_key_ed25519=wallet.public_b58,
            signature_b64="not base64!!",
            message="m",
            nonce=nonce,
            recipient="app.test",
        )


# ---------------------------------------------------------------------------
# wallet-sent transaction verification
# ---------------------------------------------------------------------------
PLAN = TxPlan(
    action="approve_claim",
    contract_id=CONTRACT,
    method="approve_claim",
    args={"bounty_id": 3, "claim_id": 2},
    deposit_yocto=0,
    gas=100_000_000_000_000,
)


def tx_result(*, signer=OWNER, receiver=CONTRACT, method="approve_claim", args=None, deposit="0"):
    encoded = base64.b64encode(json.dumps(args or PLAN.args).encode()).decode()
    return {
        "final_execution_status": "EXECUTED_OPTIMISTIC",
        "transaction": {
            "signer_id": signer,
            "receiver_id": receiver,
            "actions": [
                {
                    "FunctionCall": {
                        "method_name": method,
                        "args": encoded,
                        "gas": 100_000_000_000_000,
                        "deposit": deposit,
                    }
                }
            ],
        },
        "status": {"SuccessReceiptId": "abc"},
        "receipts_outcome": [
            {"outcome": {"status": {"SuccessValue": ""}, "logs": ['{"event":"claim_approved"}']}}
        ],
    }


class FakeRpc:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def tx_status(self, tx_hash, sender):
        if self.error:
            raise self.error
        return self.result


def test_matching_wallet_tx_is_accepted():
    result = verify_wallet_tx(
        FakeRpc(tx_result()),
        tx_hash="H" * 44,
        contract_id=CONTRACT,
        signer_account=OWNER,
        plan=PLAN,
    )
    assert result["transaction"]["receiver_id"] == CONTRACT


@pytest.mark.parametrize(
    "kwargs,fragment",
    [
        ({"signer": "someone.testnet"}, "signed by"),
        ({"receiver": "other.testnet"}, "targets"),
        ({"method": "request_payout"}, "called"),
        ({"args": {"bounty_id": 3, "claim_id": 99}}, "arguments"),
        ({"deposit": "5"}, "attached"),
    ],
)
def test_mismatched_wallet_tx_is_rejected(kwargs, fragment):
    with pytest.raises(StateError) as exc:
        verify_wallet_tx(
            FakeRpc(tx_result(**kwargs)),
            tx_hash="H" * 44,
            contract_id=CONTRACT,
            signer_account=OWNER,
            plan=PLAN,
        )
    assert fragment in str(exc.value)


def test_failed_wallet_tx_is_rejected():
    result = tx_result()
    result["status"] = {
        "Failure": {"ActionError": {"kind": {"FunctionCallError": {"ExecutionError": "boom"}}}}
    }
    with pytest.raises(StateError) as exc:
        verify_wallet_tx(
            FakeRpc(result),
            tx_hash="H" * 44,
            contract_id=CONTRACT,
            signer_account=OWNER,
            plan=PLAN,
        )
    assert "contract rejected" in str(exc.value)


def test_failed_receipt_is_rejected():
    result = tx_result()
    result["receipts_outcome"] = [{"outcome": {"status": {"Failure": {"ActionError": {}}}}}]
    with pytest.raises(StateError) as exc:
        verify_wallet_tx(
            FakeRpc(result), tx_hash="H" * 44, contract_id=CONTRACT, signer_account=OWNER, plan=PLAN
        )
    assert "receipt failed" in str(exc.value)


def test_unknown_transaction_is_rejected():
    with pytest.raises(StateError) as exc:
        verify_wallet_tx(
            FakeRpc(error=RuntimeError("UNKNOWN_TRANSACTION")),
            tx_hash="H" * 44,
            contract_id=CONTRACT,
            signer_account=OWNER,
            plan=PLAN,
        )
    assert "not visible on chain" in str(exc.value)


def test_invalid_tx_hash_is_rejected():
    with pytest.raises(ParamError):
        verify_wallet_tx(
            FakeRpc(tx_result()), tx_hash="short", contract_id=CONTRACT, signer_account=OWNER, plan=PLAN
        )


def test_tx_without_function_call_is_rejected():
    result = tx_result()
    result["transaction"]["actions"] = [{"Transfer": {"deposit": "1"}}]
    with pytest.raises(StateError):
        verify_wallet_tx(
            FakeRpc(result), tx_hash="H" * 44, contract_id=CONTRACT, signer_account=OWNER, plan=PLAN
        )


def test_plan_serializes_deposit_as_string():
    plan = TxPlan("fund_bounty", CONTRACT, "create_bounty", {"a": 1}, 10**23 + 10**21, 100)
    public = plan.to_public()
    assert public["deposit_yocto"] == str(10**23 + 10**21)
    assert public["args"] == {"a": 1}
