"""NEAR testnet plumbing: base58, Borsh, transaction signing, JSON-RPC.

This package is deliberately dependency-light: only ``cryptography`` (already
required) and ``requests`` are used, so no NEAR CLI version can break it.
"""

from .base58 import b58decode, b58encode
from .rpc import NearRpc, RpcError, TxOutcome
from .signing import KeyPair, SignedTransaction, public_key_b64_to_ed25519, sign_transaction
from .wallet import (
    CliWallet,
    HttpWallet,
    NativeWallet,
    TestnetWallet,
    WalletError,
    load_wallet,
)

__all__ = [
    "CliWallet",
    "HttpWallet",
    "KeyPair",
    "NativeWallet",
    "NearRpc",
    "RpcError",
    "SignedTransaction",
    "TestnetWallet",
    "TxOutcome",
    "WalletError",
    "b58decode",
    "b58encode",
    "public_key_b64_to_ed25519",
    "sign_transaction",
    "load_wallet",
]
