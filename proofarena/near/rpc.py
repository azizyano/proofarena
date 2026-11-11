"""Minimal NEAR JSON-RPC client.

Only the calls ProofArena needs: view functions, access-key nonce, latest block
hash, account balance, transaction broadcast and status. No SDK, no CLI.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

import requests


class RpcError(RuntimeError):
    def __init__(self, message: str, *, code: str = "E_RPC", data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass
class TxOutcome:
    tx_hash: str
    success: bool
    status: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    block_height: int | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "tx_hash": self.tx_hash,
            "success": self.success,
            "status": self.status,
            "logs": self.logs,
            "error": self.error,
            "block_height": self.block_height,
        }


class NearRpc:
    def __init__(
        self,
        url: str = "https://rpc.testnet.near.org",
        *,
        timeout_s: float = 25.0,
        session: requests.Session | None = None,
    ) -> None:
        self.url = url
        self.timeout_s = timeout_s
        self._session = session or requests.Session()

    # ------------------------------------------------------------------
    def _post(self, method: str, params: Any) -> dict[str, Any]:
        body = {"jsonrpc": "2.0", "id": "proofarena", "method": method, "params": params}
        try:
            response = self._session.post(self.url, json=body, timeout=self.timeout_s)
        except requests.RequestException as exc:
            raise RpcError(f"RPC request failed: {exc}", code="E_RPC_TRANSPORT") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise RpcError(
                f"RPC returned non-JSON (HTTP {response.status_code})", code="E_RPC_FORMAT"
            ) from exc
        if "error" in payload:
            error = payload["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RpcError(f"RPC error: {message}", code="E_RPC", data=error)
        return payload.get("result", {})

    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return self._post("status", [])

    def block_hash(self, finality: str = "final") -> bytes:
        result = self._post("block", {"finality": finality})
        encoded = result.get("header", {}).get("hash")
        if not encoded:
            raise RpcError("block response had no hash", code="E_RPC_FORMAT")
        from .base58 import b58decode

        raw = b58decode(encoded)
        if len(raw) != 32:
            raise RpcError("block hash is not 32 bytes", code="E_RPC_FORMAT")
        return raw

    def view_access_key(self, account_id: str, public_key: str) -> dict[str, Any]:
        result = self._post(
            "query",
            {
                "request_type": "view_access_key",
                "finality": "final",
                "account_id": account_id,
                "public_key": public_key,
            },
        )
        if "nonce" not in result:
            raise RpcError(
                f"access key {public_key} is not authorised for {account_id}",
                code="E_KEY_NOT_AUTHORISED",
                data=result,
            )
        return result

    def account(self, account_id: str) -> dict[str, Any]:
        result = self._post(
            "query",
            {"request_type": "view_account", "finality": "final", "account_id": account_id},
        )
        if "amount" not in result:
            raise RpcError(f"account {account_id} does not exist or has no balance", data=result)
        return result

    def view_call(self, account_id: str, method_name: str, args: dict[str, Any] | None = None) -> Any:
        result = self._post(
            "query",
            {
                "request_type": "call",
                "finality": "final",
                "account_id": account_id,
                "method_name": method_name,
                "args_base64": base64.b64encode(
                    json.dumps(args or {}, separators=(",", ":"), sort_keys=True).encode("utf-8")
                ).decode("ascii"),
            },
        )
        raw = result.get("result")
        if raw is None:
            raise RpcError(f"view call {method_name} returned no result", data=result)
        if isinstance(raw, list):
            text = bytes(raw).decode("utf-8")
        else:
            text = str(raw)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def call_view_optional(self, account_id: str, method_name: str, args: dict[str, Any] | None = None) -> Any:
        """View call that returns ``None`` when the account or method is absent."""
        try:
            return self.view_call(account_id, method_name, args)
        except RpcError as exc:
            if exc.code in {"E_RPC", "E_RPC_FORMAT"} and "does not exist" in str(exc):
                return None
            if "MethodNotFound" in str(exc) or "does not exist" in str(exc):
                return None
            raise

    def broadcast_commit(self, signed: Any) -> TxOutcome:
        """Broadcast a borsh-serialized signed transaction and wait for execution."""
        try:
            result = self._post(
                "broadcast_tx_commit",
                {"signed_tx_base64": base64.b64encode(signed.signed_borsh).decode("ascii")},
            )
        except RpcError as exc:
            return TxOutcome(
                tx_hash=getattr(signed, "tx_hash", ""),
                success=False,
                status={},
                error=_explain_error(exc),
            )
        return _parse_commit(result, getattr(signed, "tx_hash", ""))

    def tx_status(self, tx_hash: str, sender_account_id: str) -> dict[str, Any]:
        return self._post(
            "tx",
            {"tx_hash": tx_hash, "sender_account_id": sender_account_id, "wait_until": "EXECUTED_OPTIMISTIC"},
        )


def _parse_commit(result: dict[str, Any], tx_hash: str) -> TxOutcome:
    status = result.get("status", {}) or {}
    logs: list[str] = []
    for outcome in result.get("receipts_outcome", []) or []:
        for log in outcome.get("outcome", {}).get("logs", []) or []:
            logs.append(log)
    success = "SuccessValue" in status or "SuccessReceiptId" in status
    error = None
    if not success:
        failure = status.get("Failure")
        error = json.dumps(failure) if failure is not None else json.dumps(status)
    node_id = (result.get("transaction_outcome") or {}).get("id")
    if node_id:
        tx_hash = node_id
    block_height = (result.get("transaction_outcome") or {}).get("block_hash")
    return TxOutcome(
        tx_hash=tx_hash,
        success=success,
        status=status,
        logs=logs,
        error=error,
        block_height=result.get("block_height"),
    )


def _explain_error(exc: RpcError) -> str:
    data = exc.data
    if isinstance(data, dict):
        cause = data.get("data")
        return f"{exc} :: {json.dumps(cause)[:400]}" if cause else str(exc)
    return str(exc)
