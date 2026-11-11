"""NEAR key handling and transaction construction/signing.

Keys come from the standard NEAR credentials file
(``~/.near-credentials/testnet/<account>.json``) which stores a 64-byte ed25519
secret key (seed ‖ public key) in base58, prefixed ``ed25519:``.

Safety properties:

* the private key never leaves this process — it is not logged, not stored in the
  database, and ``KeyPair.__repr__`` masks it;
* the derived public key is checked against the one embedded in the credentials
  file, so a corrupted key is caught before any transaction is signed;
* ``decode_signed_transaction`` proves the Borsh layout the chain will parse, and
  ``--dry-run`` prints it before anything is broadcast.

If the encoding were wrong the chain rejects the transaction *before* executing
it (``InvalidTxError``), so a mistake costs time, not funds.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .base58 import b58decode, b58encode
from .borsh import (
    BorshError,
    BorshWriter,
    read_fixed,
    read_string,
    read_u32,
    read_u64,
    read_u128,
    read_u8,
    read_vec,
)

ED25519_TAG = 0
DEPLOY_CONTRACT_TAG = 1
FUNCTION_CALL_TAG = 2
MAX_GAS = 300_000_000_000_000  # 300 Tgas, protocol cap
STORAGE_RESERVE_YOCTO = 1_000_000_000_000_000_000_000


class KeyFormatError(ValueError):
    pass


@dataclass
class KeyPair:
    _seed: bytes = field(repr=False)
    public_key: bytes = b""

    def __post_init__(self) -> None:
        if len(self._seed) != 32:
            raise KeyFormatError("ed25519 seed must be 32 bytes")
        derived = self._private().public_key().public_bytes_raw()
        if self.public_key and self.public_key != derived:
            raise KeyFormatError(
                "public key in the credentials file does not match the derived key; "
                "refusing to sign"
            )
        self.public_key = derived

    @classmethod
    def from_secret_key(cls, secret: str) -> "KeyPair":
        if not secret.startswith("ed25519:"):
            raise KeyFormatError("only ed25519 keys are supported")
        raw = b58decode(secret.split(":", 1)[1])
        if len(raw) == 64:
            seed, embedded = raw[:32], raw[32:]
        elif len(raw) == 32:
            seed, embedded = raw, b""
        else:
            raise KeyFormatError(f"unexpected ed25519 secret key length {len(raw)}")
        return cls(_seed=seed, public_key=embedded)

    @classmethod
    def from_seed(cls, seed: bytes) -> "KeyPair":
        return cls(_seed=seed)

    def _private(self) -> Ed25519PrivateKey:
        return Ed25519PrivateKey.from_private_bytes(self._seed)

    @property
    def public_key_b58(self) -> str:
        return b58encode(self.public_key)

    @property
    def public_key_ed25519(self) -> str:
        return f"ed25519:{self.public_key_b58}"

    def sign(self, message: bytes) -> bytes:
        return self._private().sign(message)

    def __repr__(self) -> str:  # never leak the seed
        return f"KeyPair(public_key_ed25519={self.public_key_ed25519}, seed=<redacted>)"


@dataclass
class SignedTransaction:
    payload: dict[str, Any]
    unsigned_borsh: bytes
    signed_borsh: bytes
    tx_hash: str
    signature_b58: str

    @property
    def tx_hash_b58(self) -> str:
        return self.tx_hash

    def explorer_url(self, network: str = "testnet") -> str:
        return f"https://explorer.{network}.near.org/transactions/{self.tx_hash}"


# ---------------------------------------------------------------------------
# action builders
# ---------------------------------------------------------------------------
def function_call_action(
    method_name: str, args: dict[str, Any] | bytes, *, gas: int, deposit: int
) -> dict[str, Any]:
    return {
        "type": "function_call",
        "method_name": method_name,
        "args": args,
        "gas": gas,
        "deposit": deposit,
    }


def deploy_contract_action(code: bytes) -> dict[str, Any]:
    return {"type": "deploy_contract", "code": code}


def _args_bytes(args: dict[str, Any] | bytes) -> bytes:
    import json

    if isinstance(args, bytes):
        return args
    return json.dumps(args, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode_actions(actions: list[dict[str, Any]]) -> bytes:
    if not actions:
        raise ValueError("a transaction needs at least one action")
    writer = BorshWriter()
    writer.u32(len(actions))
    for action in actions:
        kind = action.get("type")
        if kind == "function_call":
            gas = int(action["gas"])
            deposit = int(action["deposit"])
            if not 0 < gas <= MAX_GAS:
                raise ValueError(f"gas must be 1..{MAX_GAS}")
            if deposit < 0:
                raise ValueError("deposit cannot be negative")
            writer.u8(FUNCTION_CALL_TAG)
            writer.string(str(action["method_name"]))
            writer.bytes_vec(_args_bytes(action["args"]))
            writer.u64(gas)
            writer.u128(deposit)
        elif kind == "deploy_contract":
            code = action["code"]
            if not isinstance(code, (bytes, bytearray)) or not code:
                raise ValueError("deploy_contract needs non-empty code bytes")
            writer.u8(DEPLOY_CONTRACT_TAG)
            writer.bytes_vec(bytes(code))
        else:
            raise ValueError(f"unsupported action type {kind!r}")
    return writer.build()


def _encode_transaction(
    *,
    signer_id: str,
    public_key: bytes,
    nonce: int,
    receiver_id: str,
    block_hash: bytes,
    actions: list[dict[str, Any]],
) -> bytes:
    if len(block_hash) != 32:
        raise ValueError("block_hash must be 32 bytes")
    if len(public_key) != 32:
        raise ValueError("public_key must be 32 bytes")
    writer = BorshWriter()
    writer.string(signer_id)
    writer.u8(ED25519_TAG).bytes_fixed(public_key)
    writer.u64(nonce)
    writer.string(receiver_id)
    writer.bytes_fixed(block_hash)
    writer.bytes_fixed(_encode_actions(actions))
    return writer.build()


def _encode_signed(unsigned: bytes, signature: bytes) -> bytes:
    writer = BorshWriter()
    writer.bytes_fixed(unsigned)
    writer.u8(ED25519_TAG).bytes_fixed(signature)
    return writer.build()


def _json_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for action in actions:
        if action["type"] == "function_call":
            out.append(
                {
                    "FunctionCall": {
                        "method_name": str(action["method_name"]),
                        "args": base64.b64encode(_args_bytes(action["args"])).decode("ascii"),
                        "gas": int(action["gas"]),
                        "deposit": str(int(action["deposit"])),
                    }
                }
            )
        else:
            out.append(
                {
                    "DeployContract": {
                        "code": base64.b64encode(bytes(action["code"])).decode("ascii")
                    }
                }
            )
    return out


# ---------------------------------------------------------------------------
# public signing entry points
# ---------------------------------------------------------------------------
def sign_actions(
    *,
    key: KeyPair,
    signer_id: str,
    receiver_id: str,
    nonce: int,
    block_hash: bytes,
    actions: list[dict[str, Any]],
) -> SignedTransaction:
    unsigned = _encode_transaction(
        signer_id=signer_id,
        public_key=key.public_key,
        nonce=nonce,
        receiver_id=receiver_id,
        block_hash=block_hash,
        actions=actions,
    )
    signature = key.sign(hashlib.sha256(unsigned).digest())
    signed = _encode_signed(unsigned, signature)
    # NEAR's transaction hash is sha256 of the *unsigned* Borsh transaction.
    # Verified against testnet: the node's transaction_outcome.id equals this
    # value, not sha256 of the signed bytes.
    tx_hash = b58encode(hashlib.sha256(unsigned).digest())
    payload = {
        "signer_id": signer_id,
        "public_key": key.public_key_ed25519,
        "nonce": nonce,
        "receiver_id": receiver_id,
        "block_hash": b58encode(block_hash),
        "actions": _json_actions(actions),
        "signature": f"ed25519:{b58encode(signature)}",
    }
    return SignedTransaction(
        payload=payload,
        unsigned_borsh=unsigned,
        signed_borsh=signed,
        tx_hash=tx_hash,
        signature_b58=b58encode(signature),
    )


def sign_transaction(
    *,
    key: KeyPair,
    signer_id: str,
    receiver_id: str,
    nonce: int,
    block_hash: bytes,
    method_name: str,
    args: dict[str, Any] | bytes,
    gas: int,
    deposit: int,
) -> SignedTransaction:
    return sign_actions(
        key=key,
        signer_id=signer_id,
        receiver_id=receiver_id,
        nonce=nonce,
        block_hash=block_hash,
        actions=[function_call_action(method_name, args, gas=gas, deposit=deposit)],
    )


# ---------------------------------------------------------------------------
# decoding (round-trip proof + debugging)
# ---------------------------------------------------------------------------
def decode_actions(raw: bytes, offset: int) -> tuple[list[dict[str, Any]], int]:
    count, offset = read_u32(raw, offset)
    actions: list[dict[str, Any]] = []
    for _ in range(count):
        tag, offset = read_u8(raw, offset)
        if tag == FUNCTION_CALL_TAG:
            method_name, offset = read_string(raw, offset)
            args, offset = read_vec(raw, offset)
            gas, offset = read_u64(raw, offset)
            deposit, offset = read_u128(raw, offset)
            actions.append(
                {
                    "type": "function_call",
                    "method_name": method_name,
                    "args": args,
                    "gas": gas,
                    "deposit": deposit,
                }
            )
        elif tag == DEPLOY_CONTRACT_TAG:
            code, offset = read_vec(raw, offset)
            actions.append({"type": "deploy_contract", "code": code})
        else:
            raise BorshError(f"unsupported action tag {tag}")
    return actions, offset


def decode_transaction(raw: bytes) -> dict[str, Any]:
    """Decode a Borsh ``Transaction`` exactly as the chain would."""
    offset = 0
    signer_id, offset = read_string(raw, offset)
    tag, offset = read_u8(raw, offset)
    if tag != ED25519_TAG:
        raise BorshError(f"unsupported key tag {tag}")
    public_key, offset = read_fixed(raw, offset, 32)
    nonce, offset = read_u64(raw, offset)
    receiver_id, offset = read_string(raw, offset)
    block_hash, offset = read_fixed(raw, offset, 32)
    actions, offset = decode_actions(raw, offset)
    if offset != len(raw):
        raise BorshError(f"trailing {len(raw) - offset} bytes after transaction")
    return {
        "signer_id": signer_id,
        "public_key": public_key,
        "nonce": nonce,
        "receiver_id": receiver_id,
        "block_hash": block_hash,
        "actions": actions,
    }


def decode_signed_transaction(raw: bytes) -> dict[str, Any]:
    if len(raw) < 65:
        raise BorshError("signed transaction is too short")
    unsigned = raw[:-65]
    transaction = decode_transaction(unsigned)
    tag, offset = read_u8(raw, len(unsigned))
    if tag != ED25519_TAG:
        raise BorshError(f"unsupported signature tag {tag}")
    signature, _ = read_fixed(raw, offset, 64)
    transaction["signature"] = signature
    return transaction


def verify_signature(public_key: bytes, message: bytes, signature: bytes) -> None:
    Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)


def public_key_b64_to_ed25519(public_key_b64: str) -> str:
    """Convert a base64 ed25519 public key (browser/auth format) to ``ed25519:<b58>``."""
    raw = base64.b64decode(public_key_b64)
    if len(raw) != 32:
        raise ValueError("ed25519 public key must be 32 bytes")
    return f"ed25519:{b58encode(raw)}"
