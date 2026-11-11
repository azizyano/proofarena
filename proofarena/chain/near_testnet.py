"""NEAR testnet chain adapter.

Implements the same surface as :class:`proofarena.chain.ledger.LocalLedger`, so the
API, web layer and verification flow are unchanged — but every state change is a
real NEAR transaction signed by the owner's own key, and every "receipt" is a
real transaction hash with an explorer URL.

What this adapter does **not** trust:

* the local wallet's report of success — after each call the adapter re-reads the
  contract state over JSON-RPC and uses *that* as the result;
* the client's opinion about settlement — ``PayoutPending`` only becomes ``Paid``
  when the contract's own callback runs and the view call reports it.

Explorer URLs are built from the configured network; nothing is invented.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..canonical import is_hex64
from ..errors import ChainUnavailable, NotFound, ParamError, StateError
from ..near.wallet import WalletError, load_wallet, success_value
from .models import Attempt, Bounty, Claim, ClaimState

STORAGE_RESERVE_YOCTO = 1_000_000_000_000_000_000_000
CREATE_GAS = 100_000_000_000_000
CALL_GAS = 100_000_000_000_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS tx_records (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    network TEXT NOT NULL,
    tx_hash TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    bounty_id INTEGER,
    attempt_id INTEGER,
    ts_ns INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_bounty ON tx_records(bounty_id);
"""


class NearTestnetAdapter:
    mode = "near-testnet"

    def __init__(
        self,
        *,
        network: str = "near-testnet",
        contract_id: str | None = None,
        txlog_path: Path | str | None = None,
        settle_timeout_s: float = 30.0,
    ) -> None:
        try:
            self.wallet = load_wallet(contract_id=contract_id)
        except WalletError as exc:
            raise ChainUnavailable(str(exc)) from exc
        self.network = network
        self.contract_id = contract_id or self.wallet.contract_id()
        self.rpc = self.wallet.rpc
        self.settle_timeout_s = settle_timeout_s
        self.explorer_base = f"https://explorer.testnet.near.org/transactions"
        db_path = Path(txlog_path) if txlog_path else Path("evidence/near-testnet/txlog.sqlite3")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._last_integrity: tuple[float, dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # transaction plumbing
    # ------------------------------------------------------------------
    def _tx(
        self,
        kind: str,
        method: str,
        args: dict[str, Any],
        *,
        deposit_yocto: int = 0,
        bounty_id: int | None = None,
        attempt_id: int | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        try:
            outcome = self.wallet.call(method, args, deposit_yocto=deposit_yocto, gas=CALL_GAS)
        except WalletError as exc:
            raise ChainUnavailable(f"NEAR transaction failed: {exc}") from exc
        if not outcome.success:
            raise ChainUnavailable(f"NEAR transaction {method} failed: {outcome.error}")
        receipt = self._record(kind, outcome, bounty_id=bounty_id, attempt_id=attempt_id)
        return success_value(outcome), receipt

    def _record(
        self,
        kind: str,
        outcome: Any,
        *,
        bounty_id: int | None,
        attempt_id: int | None,
    ) -> dict[str, Any]:
        ts_ns = time.time_ns()
        payload = {
            "receipt_id": outcome.tx_hash,
            "network": self.network,
            "contract_id": self.contract_id,
            "kind": kind,
            "bounty_id": bounty_id,
            "attempt_id": attempt_id,
            "ts_ns": ts_ns,
            "data": {"logs": outcome.logs[-5:]},
            "explorer_url": f"{self.explorer_base}/{outcome.tx_hash}",
            "simulated": False,
        }
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO tx_records (network, tx_hash, kind, bounty_id,"
                " attempt_id, ts_ns, payload_json) VALUES (?,?,?,?,?,?,?)",
                (
                    self.network,
                    outcome.tx_hash,
                    kind,
                    bounty_id,
                    attempt_id,
                    ts_ns,
                    json.dumps(payload, sort_keys=True),
                ),
            )
        return payload

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------
    def create_bounty(
        self,
        *,
        signer: str,
        title: str,
        reward_yocto: int,
        metadata_hash_hex: str,
        deadline_ns: int,
        review_deadline_ns: int,
        attached_yocto: int,
    ) -> dict[str, Any]:
        self._assert_signer(signer)
        if attached_yocto != reward_yocto + STORAGE_RESERVE_YOCTO:
            raise ParamError("attached deposit must equal reward + storage reserve")
        value, receipt = self._tx(
            "bounty_created",
            "create_bounty",
            {
                "title": title,
                "metadata_hash_hex": metadata_hash_hex,
                "reward_yocto": str(reward_yocto),
                "deadline_ns": int(deadline_ns),
                "review_deadline_ns": int(review_deadline_ns),
            },
            deposit_yocto=attached_yocto,
        )
        bounty_id = _as_int(value) or self._latest_bounty_id()
        if bounty_id is None:
            raise ChainUnavailable(
                "transaction succeeded but the bounty id could not be read back from the chain"
            )
        receipt["bounty_id"] = bounty_id
        self._set_receipt_bounty(receipt["receipt_id"], bounty_id)
        self._annotate(receipt)
        return {"bounty": self.get_bounty(bounty_id).to_dict(), "receipts": [receipt]}

    def commit_claim(
        self,
        *,
        signer: str,
        bounty_id: int,
        evidence_commitment_hex: str,
        manifest_sha256: str | None = None,
    ) -> dict[str, Any]:
        self._assert_signer(signer)
        if not is_hex64(evidence_commitment_hex):
            raise ParamError("commitment must be 64 lowercase hex characters")
        value, receipt = self._tx(
            "claim_committed",
            "commit_claim",
            {
                "bounty_id": int(bounty_id),
                "evidence_commitment_hex": evidence_commitment_hex,
                "manifest_sha256_hex": manifest_sha256,
            },
            deposit_yocto=0,
            bounty_id=bounty_id,
        )
        claim_id = _as_int(value)
        if claim_id is None:
            claims = self.rpc.call_view_optional(self.contract_id, "get_claims", {"bounty_id": bounty_id}) or []
            claim_id = max((int(c["claim_id"]) for c in claims), default=None)
        if claim_id is None:
            raise ChainUnavailable("claim id could not be read back from the chain")
        receipt["attempt_id"] = None
        return {"claim": self.get_claim(claim_id).to_dict(), "receipts": [receipt]}

    def mark_review(self, *, signer: str, bounty_id: int) -> dict[str, Any]:
        self._assert_signer(signer)
        _, receipt = self._tx("review_started", "mark_review", {"bounty_id": int(bounty_id)}, bounty_id=bounty_id)
        return {"bounty": self.get_bounty(bounty_id).to_dict(), "receipts": [receipt]}

    def approve_claim(self, *, signer: str, bounty_id: int, claim_id: int) -> dict[str, Any]:
        self._assert_signer(signer)
        _, receipt = self._tx(
            "claim_approved",
            "approve_claim",
            {"bounty_id": int(bounty_id), "claim_id": int(claim_id)},
            bounty_id=bounty_id,
        )
        return {"bounty": self.get_bounty(bounty_id).to_dict(), "receipts": [receipt]}

    def request_payout(self, *, signer: str, bounty_id: int) -> dict[str, Any]:
        self._assert_signer(signer)
        value, receipt = self._tx(
            "payout_requested", "request_payout", {"bounty_id": int(bounty_id)}, bounty_id=bounty_id
        )
        attempt_id = _as_int(value)
        if attempt_id is None:
            attempt_id = self.get_bounty(bounty_id).pending_attempt_id
        receipt["attempt_id"] = attempt_id
        attempt = self._attempt(bounty_id, attempt_id)
        return {
            "attempt": attempt.to_dict() if attempt else {"attempt_id": attempt_id, "kind": "payout"},
            "receipts": [receipt],
            "status": "PayoutPending",
        }

    def refund_expired(self, *, signer: str, bounty_id: int) -> dict[str, Any]:
        self._assert_signer(signer)
        value, receipt = self._tx(
            "refund_requested", "refund_expired", {"bounty_id": int(bounty_id)}, bounty_id=bounty_id
        )
        attempt_id = _as_int(value)
        if attempt_id is None:
            attempt_id = self.get_bounty(bounty_id).pending_attempt_id
        receipt["attempt_id"] = attempt_id
        attempt = self._attempt(bounty_id, attempt_id)
        return {
            "attempt": attempt.to_dict() if attempt else {"attempt_id": attempt_id, "kind": "refund"},
            "receipts": [receipt],
            "status": "RefundPending",
        }

    def reconcile(
        self,
        *,
        signer: str,
        bounty_id: int,
        resolution: str,
        evidence_sha256: str,
        note: str,
    ) -> dict[str, Any]:
        self._assert_signer(signer)
        _, receipt = self._tx(
            "reconciled",
            "reconcile",
            {
                "bounty_id": int(bounty_id),
                "resolution": resolution,
                "evidence_sha256": evidence_sha256,
                "note": note,
            },
            bounty_id=bounty_id,
        )
        return {"bounty": self.get_bounty(bounty_id).to_dict(), "receipts": [receipt]}

    def set_dispute(self, *, signer: str, bounty_id: int, disputed: bool) -> dict[str, Any]:
        self._assert_signer(signer)
        _, receipt = self._tx(
            "dispute_changed",
            "set_dispute",
            {"bounty_id": int(bounty_id), "disputed": bool(disputed)},
            bounty_id=bounty_id,
        )
        return {"bounty": self.get_bounty(bounty_id).to_dict(), "receipts": [receipt]}

    def deliver_callback(
        self,
        *,
        bounty_id: int,
        attempt_id: int,
        result: Any = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Wait for the contract's own callback to settle the transfer.

        On NEAR the callback is dispatched by the contract, not by us. We poll the
        view method until the state leaves the pending set. If it is still pending
        when the timeout expires we return the pending state unchanged — the API
        will keep showing ``PayoutPending`` rather than inventing a payment.
        """
        deadline = time.monotonic() + self.settle_timeout_s
        last = self.get_bounty(bounty_id)
        while time.monotonic() < deadline:
            if last.state.value not in {"PayoutPending", "RefundPending"}:
                return {"bounty": last.to_dict(), "receipts": []}
            time.sleep(1.5)
            last = self.get_bounty(bounty_id)
        return {"bounty": last.to_dict(), "receipts": []}

    # ------------------------------------------------------------------
    # views
    # ------------------------------------------------------------------
    def _assert_signer(self, signer: str) -> None:
        if signer != self.wallet.account_id:
            raise ChainUnavailable(
                f"local signer holds {self.wallet.account_id!r}, not {signer!r}; "
                "the owner's key must be present on this machine"
            )

    def _latest_bounty_id(self) -> int | None:
        ids = self.rpc.call_view_optional(self.contract_id, "list_bounty_ids", {}) or []
        return max((int(i) for i in ids), default=None)

    def _raw_bounty(self, bounty_id: int) -> dict[str, Any] | None:
        try:
            raw = self.rpc.view_call(self.contract_id, "get_bounty", {"bounty_id": int(bounty_id)})
        except Exception:
            return None
        return raw if isinstance(raw, dict) else None

    def get_bounty(self, bounty_id: int) -> Bounty:
        raw = self._raw_bounty(bounty_id)
        if not raw:
            raise NotFound(f"bounty {bounty_id} not found on {self.contract_id}")
        return _map_bounty(raw)

    def get_claim(self, claim_id: int) -> Claim:
        raw = self.rpc.call_view_optional(self.contract_id, "get_claim", {"claim_id": int(claim_id)})
        if not raw:
            raise NotFound(f"claim {claim_id} not found on {self.contract_id}")
        return _map_claim(raw)

    def list_claims(self, bounty_id: int) -> list[Claim]:
        raw = self.rpc.call_view_optional(self.contract_id, "get_claims", {"bounty_id": int(bounty_id)}) or []
        return [_map_claim(item) for item in raw]

    def list_bounties(self) -> list[Bounty]:
        ids = self.rpc.call_view_optional(self.contract_id, "list_bounty_ids", {}) or []
        out: list[Bounty] = []
        for bounty_id in sorted(int(i) for i in ids):
            try:
                out.append(self.get_bounty(bounty_id))
            except NotFound:
                continue
        return out

    def _attempt(self, bounty_id: int, attempt_id: int | None) -> Attempt | None:
        if attempt_id is None:
            return None
        bounty = self.get_bounty(bounty_id)
        return bounty.attempt(int(attempt_id))

    # ------------------------------------------------------------------
    # receipts
    # ------------------------------------------------------------------
    def receipts(self, *, bounty_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if bounty_id is None:
            rows = self._conn.execute(
                "SELECT payload_json FROM tx_records ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT payload_json FROM tx_records WHERE bounty_id = ? ORDER BY seq ASC LIMIT ?",
                (bounty_id, limit),
            ).fetchall()
        items = [json.loads(row[0]) for row in rows]
        if bounty_id is None:
            items.reverse()
        return items

    def receipt(self, receipt_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT payload_json FROM tx_records WHERE tx_hash = ?", (receipt_id,)
        ).fetchone()
        if row is None:
            raise NotFound(f"transaction {receipt_id} is not in the local mirror")
        return json.loads(row[0])

    def verify_receipt_chain(self) -> dict[str, Any]:
        """Confirm the latest recorded transaction exists on chain.

        NEAR has no local hash chain to recompute; integrity means "this tx hash is
        retrievable from the network". The result is cached for 30 seconds so
        ``/health`` stays cheap.
        """
        now = time.monotonic()
        if self._last_integrity and now - self._last_integrity[0] < 30:
            return self._last_integrity[1]
        row = self._conn.execute(
            "SELECT tx_hash, payload_json FROM tx_records ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if row is None:
            result = {"valid": True, "checked": 0, "head": "", "mode": self.mode}
            self._last_integrity = (now, result)
            return result
        tx_hash = row[0]
        try:
            status = self.rpc.tx_status(tx_hash, self.wallet.account_id)
            valid = bool(status)
            error = None if valid else "transaction not found"
        except Exception as exc:  # network hiccup must not look like tampering
            valid = False
            error = str(exc)[:200]
        result = {
            "valid": valid,
            "checked": 1,
            "head": tx_hash,
            "mode": self.mode,
            "error": error,
            "note": "NEAR receipt: transaction hash retrievable via RPC (not a local hash chain)",
        }
        self._last_integrity = (now, result)
        return result

    def _set_receipt_bounty(self, receipt_id: str, bounty_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tx_records SET bounty_id = ? WHERE tx_hash = ?", (bounty_id, receipt_id)
            )

    def _annotate(self, receipt: dict[str, Any]) -> None:
        receipt["explorer_url"] = f"{self.explorer_base}/{receipt['receipt_id']}"

    # ------------------------------------------------------------------
    def balance_summary(self) -> dict[str, Any]:
        balance = self.wallet.balance_yocto()
        return {
            "account": self.wallet.account_id,
            "balance_yocto": str(balance),
            "balance_near": balance / 10**24,
            "contract_id": self.contract_id,
            "rpc": self.rpc.url,
        }


# ---------------------------------------------------------------------------
# mapping contract JSON -> domain objects
# ---------------------------------------------------------------------------
def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _map_bounty(raw: dict[str, Any]) -> Bounty:
    data = dict(raw)
    attempts = []
    for item in data.get("attempts", []) or []:
        attempts.append(
            {
                "attempt_id": int(item["attempt_id"]),
                "kind": str(item.get("kind", "Payout")).lower(),
                "status": str(item.get("status", "Pending")).lower(),
                "created_ns": int(item.get("created_ns", 0)),
                "state_before": item.get("state_before", "Approved"),
                "resolved_ns": item.get("resolved_ns"),
                "note": item.get("note"),
            }
        )
    data["attempts"] = attempts
    data["claims"] = [int(c) for c in data.get("claims", []) or []]
    for key in ("reward_yocto", "paid_yocto", "refunded_yocto", "escrow_balance_yocto", "storage_reserve_yocto"):
        data[key] = str(data.get(key, "0"))
    data.setdefault("disputed", False)
    return Bounty.from_dict(data)


def _map_claim(raw: dict[str, Any]) -> Claim:
    if raw.get("paid"):
        state = ClaimState.PAID
    elif raw.get("approved"):
        state = ClaimState.APPROVED
    else:
        state = ClaimState.SUBMITTED
    return Claim(
        claim_id=int(raw["claim_id"]),
        bounty_id=int(raw["bounty_id"]),
        submitter=str(raw["submitter"]),
        evidence_commitment_hex=str(raw["evidence_commitment_hex"]),
        state=state,
        submitted_ns=int(raw.get("submitted_ns", 0)),
        manifest_sha256=raw.get("manifest_sha256_hex"),
    )


def iso_utc(ns: int) -> str:
    return datetime.fromtimestamp(ns / 10**9, tz=timezone.utc).isoformat().replace("+00:00", "Z")
