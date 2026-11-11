"""Wallets that can submit NEAR transactions.

Two backends, same interface:

* :class:`NativeWallet` — signs Borsh transactions in-process with the key from
  the standard NEAR credentials file, then broadcasts via JSON-RPC. No CLI, no
  npm, no version roulette.
* :class:`CliWallet` — shells out to the official ``near`` CLI and lets *it* sign.
  Useful as a fallback if the native signer is ever rejected by the chain.

Neither backend is ever used by the HTTP API to sign for a human: the API asks the
local signer service, which owns the key. See ``docs/TESTNET_RUNBOOK.md``.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import requests

from .rpc import NearRpc, RpcError, TxOutcome
from .signing import (
    KeyPair,
    SignedTransaction,
    deploy_contract_action,
    function_call_action,
    sign_actions,
    sign_transaction,
)

DEFAULT_RPC = "https://rpc.testnet.near.org"
DEFAULT_GAS = 100_000_000_000_000  # 100 Tgas
DEFAULT_SIGNER_URL = "http://127.0.0.1:8788"
INIT_GAS = 30_000_000_000_000  # 30 Tgas for the #[init] call
NONCE_RETRY_CODES = ("InvalidNonce", "Nonce", "Expired")


class WalletError(RuntimeError):
    def __init__(self, message: str, *, outcome: TxOutcome | None = None) -> None:
        super().__init__(message)
        self.outcome = outcome


class TestnetWallet(Protocol):
    account_id: str
    rpc: NearRpc

    def call(
        self, method_name: str, args: dict[str, Any], *, deposit_yocto: int = 0, gas: int = DEFAULT_GAS
    ) -> TxOutcome: ...

    def view(self, method_name: str, args: dict[str, Any] | None = None) -> Any: ...

    def balance_yocto(self) -> int: ...


def credentials_path(account_id: str, *, network: str = "testnet") -> Path | None:
    explicit = os.environ.get("NEAR_CREDENTIALS")
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.exists() else None
    candidate = Path.home() / ".near-credentials" / network / f"{account_id}.json"
    return candidate if candidate.exists() else None


def load_key(account_id: str, *, network: str = "testnet", path: Path | None = None) -> KeyPair:
    resolved = path or credentials_path(account_id, network=network)
    if resolved is None or not resolved.exists():
        raise WalletError(
            f"no NEAR credentials found for {account_id!r}. Expected "
            f"~/.near-credentials/{network}/{account_id}.json or $NEAR_CREDENTIALS. "
            "See docs/TESTNET_RUNBOOK.md step 3."
        )
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WalletError(f"cannot read credentials file {resolved}: {exc}") from exc
    secret = payload.get("private_key") or payload.get("secret_key") or payload.get("privateKey")
    if not secret:
        raise WalletError(f"credentials file {resolved} has no private_key field")
    embedded_account = payload.get("account_id")
    if embedded_account and embedded_account != account_id:
        raise WalletError(
            f"credentials file belongs to {embedded_account!r}, not {account_id!r}"
        )
    return KeyPair.from_secret_key(secret)


@dataclass
class NativeWallet:
    account_id: str
    key: KeyPair
    rpc: NearRpc
    network: str = "testnet"

    def build(
        self,
        method_name: str,
        args: dict[str, Any],
        *,
        deposit_yocto: int = 0,
        gas: int = DEFAULT_GAS,
    ) -> SignedTransaction:
        try:
            nonce = self._next_nonce()
            block_hash = self.rpc.block_hash()
        except RpcError as exc:
            raise WalletError(
                f"cannot reach the NEAR RPC to build {method_name}: {exc}"
            ) from exc
        return sign_transaction(
            key=self.key,
            signer_id=self.account_id,
            receiver_id=self.contract_id(),
            nonce=nonce,
            block_hash=block_hash,
            method_name=method_name,
            args=args,
            gas=gas,
            deposit=deposit_yocto,
        )

    def contract_id(self) -> str:
        contract = os.environ.get("NEAR_CONTRACT_ID")
        if not contract:
            raise WalletError("NEAR_CONTRACT_ID is not set")
        return contract

    def _next_nonce(self, attempts: int = 3) -> int:
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                access = self.rpc.view_access_key(self.account_id, self.key.public_key_ed25519)
                return int(access["nonce"]) + 1
            except RpcError as exc:
                last = exc
                if attempt < attempts:
                    time.sleep(0.8 * attempt)
        raise RpcError(f"could not read the access-key nonce: {last}", code="E_RPC_NONCE")

    def call(
        self,
        method_name: str,
        args: dict[str, Any],
        *,
        deposit_yocto: int = 0,
        gas: int = DEFAULT_GAS,
        max_attempts: int = 4,
    ) -> TxOutcome:
        last: TxOutcome | None = None
        for attempt in range(1, max_attempts + 1):
            signed = self.build(method_name, args, deposit_yocto=deposit_yocto, gas=gas)
            outcome = self.rpc.broadcast_commit(signed)
            if outcome.success:
                return outcome
            last = outcome
            message = (outcome.error or "") + json.dumps(outcome.status)
            if attempt < max_attempts and any(code in message for code in NONCE_RETRY_CODES):
                time.sleep(0.8 * attempt)
                continue
            raise WalletError(
                f"{method_name} transaction failed: {outcome.error or outcome.status}", outcome=outcome
            )
        raise WalletError("nonce retries exhausted without a successful transaction", outcome=last)

    def view(self, method_name: str, args: dict[str, Any] | None = None) -> Any:
        return self.rpc.view_call(self.contract_id(), method_name, args or {})

    def balance_yocto(self) -> int:
        return int(self.rpc.account(self.account_id)["amount"])


@dataclass
class CliWallet:
    """Fallback backend: delegate signing to the official ``near`` CLI."""

    account_id: str
    rpc: NearRpc
    contract_id_value: str
    near_binary: str = "near"
    network: str = "testnet"

    def contract_id(self) -> str:
        return self.contract_id_value

    def call(
        self,
        method_name: str,
        args: dict[str, Any],
        *,
        deposit_yocto: int = 0,
        gas: int = DEFAULT_GAS,
    ) -> TxOutcome:
        command = [
            self.near_binary,
            "call",
            self.contract_id(),
            method_name,
            json.dumps(args, sort_keys=True, separators=(",", ":")),
            "--accountId",
            self.account_id,
            "--depositYocto",
            str(deposit_yocto),
            "--gas",
            str(gas),
            "--networkId",
            self.network,
        ]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=90)
        except FileNotFoundError as exc:
            raise WalletError(
                "the `near` CLI is not installed. Either install it (npm i -g near-cli) "
                "or use the native signer (PROOFARENA_NEAR_SIGNER=native)."
            ) from exc
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            raise WalletError(f"near CLI failed: {output.strip()[:600]}")
        match = re.search(r"Transaction Id\s+([1-9A-HJ-NP-Za-km-z]{32,})", output)
        if not match:
            match = re.search(r"([1-9A-HJ-NP-Za-km-z]{43,44})", output)
        if not match:
            raise WalletError(f"could not parse a transaction id from the CLI output: {output[:400]}")
        return TxOutcome(tx_hash=match.group(1), success=True, status={"cli": True})

    def view(self, method_name: str, args: dict[str, Any] | None = None) -> Any:
        return self.rpc.view_call(self.contract_id(), method_name, args or {})

    def balance_yocto(self) -> int:
        return int(self.rpc.account(self.account_id)["amount"])


    def deploy(
        self,
        wasm_bytes: bytes,
        *,
        init_args: dict[str, Any] | None = None,
        init_method: str = "new",
        gas: int = INIT_GAS,
    ) -> SignedTransaction:
        """Build a DeployContract (+ #[init] call) transaction without sending it."""
        actions = [deploy_contract_action(wasm_bytes)]
        if init_method:
            actions.append(
                function_call_action(init_method, init_args or {}, gas=gas, deposit=0)
            )
        nonce = self._next_nonce()
        block_hash = self.rpc.block_hash()
        return sign_actions(
            key=self.key,
            signer_id=self.account_id,
            receiver_id=self.contract_id(),
            nonce=nonce,
            block_hash=block_hash,
            actions=actions,
        )

    def deploy_and_send(
        self,
        wasm_bytes: bytes,
        *,
        init_args: dict[str, Any] | None = None,
        init_method: str = "new",
    ) -> tuple[SignedTransaction, TxOutcome]:
        signed = self.deploy(wasm_bytes, init_args=init_args, init_method=init_method)
        return signed, self.rpc.broadcast_commit(signed)


@dataclass
class HttpWallet:
    """Talks to the local signer service, which owns the key.

    This is the mode the API uses on testnet, so the web/API process never reads a
    private key. The signer enforces a contract/method allowlist and a deposit cap.
    """

    account_id: str
    rpc: NearRpc
    signer_url: str
    contract_id_value: str
    timeout_s: float = 90.0

    def contract_id(self) -> str:
        return self.contract_id_value

    def call(
        self,
        method_name: str,
        args: dict[str, Any],
        *,
        deposit_yocto: int = 0,
        gas: int = DEFAULT_GAS,
    ) -> TxOutcome:
        payload = {
            "contract_id": self.contract_id_value,
            "method": method_name,
            "args": args,
            "deposit_yocto": str(int(deposit_yocto)),
            "gas": int(gas),
        }
        try:
            response = requests.post(
                f"{self.signer_url.rstrip('/')}/call", json=payload, timeout=self.timeout_s
            )
        except requests.RequestException as exc:
            raise WalletError(
                f"signer service at {self.signer_url} is unreachable: {exc}. "
                "Start it with `python wallet/near_signer.py`."
            ) from exc
        try:
            body = response.json()
        except ValueError as exc:
            snippet = (response.text or "")[:160].replace("\n", " ")
            raise WalletError(
                f"signer returned non-JSON (HTTP {response.status_code}): {snippet}"
            ) from exc
        if response.status_code != 200:
            raise WalletError(f"signer refused the call: {body.get('message', body)}")
        if not body.get("success"):
            raise WalletError(f"transaction failed: {body.get('error') or body}")
        return TxOutcome(
            tx_hash=body["tx_hash"],
            success=True,
            status=body.get("status", {}),
            logs=body.get("logs", []),
        )

    def view(self, method_name: str, args: dict[str, Any] | None = None) -> Any:
        return self.rpc.view_call(self.contract_id(), method_name, args or {})

    def balance_yocto(self) -> int:
        return int(self.rpc.account(self.account_id)["amount"])


def load_wallet(
    *,
    account_id: str | None = None,
    rpc_url: str | None = None,
    contract_id: str | None = None,
    mode: str | None = None,
    credentials: Path | None = None,
    signer_url: str | None = None,
) -> TestnetWallet:
    account = account_id or os.environ.get("NEAR_TESTNET_ACCOUNT")
    contract = contract_id or os.environ.get("NEAR_CONTRACT_ID")
    if not account:
        raise WalletError(
            "NEAR_TESTNET_ACCOUNT is not set (export your testnet account, e.g. yourname.testnet)"
        )
    if not contract:
        raise WalletError(
            "NEAR_CONTRACT_ID is not set (export the deployed contract account, e.g. proofarena.yourname.testnet)"
        )
    rpc = NearRpc(rpc_url or os.environ.get("NEAR_RPC_URL", DEFAULT_RPC))
    selected = (mode or os.environ.get("PROOFARENA_NEAR_WALLET", "http")).lower()
    if selected == "cli":
        return CliWallet(account_id=account, rpc=rpc, contract_id_value=contract)
    if selected == "http":
        return HttpWallet(
            account_id=account,
            rpc=rpc,
            signer_url=signer_url or os.environ.get("PROOFARENA_SIGNER_URL", DEFAULT_SIGNER_URL),
            contract_id_value=contract,
        )
    if selected != "native":
        raise WalletError(f"unknown wallet mode {selected!r} (expected 'native', 'http' or 'cli')")
    return NativeWallet(account_id=account, key=load_key(account, path=credentials), rpc=rpc)


def success_value(outcome: TxOutcome) -> Any:
    """Decode the JSON/base64 return value of a successful function call."""
    value = (outcome.status or {}).get("SuccessValue")
    if value is None:
        return None
    try:
        raw = base64.b64decode(value)
    except (ValueError, TypeError):
        return None
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
