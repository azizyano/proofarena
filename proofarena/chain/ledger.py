"""Local ledger: the only chain adapter this build can execute.

It is a *simulator*, not NEAR. Every receipt it emits is prefixed ``local-`` and
labelled ``network="local-ledger"`` so no receipt can be mistaken for a testnet
transaction. The state machine it drives is the same one specified for
``contracts/escrow/src/lib.rs``.

Honesty boundary: `docs/adr/0004-chain-adapter.md` and the UI banner both state
that settlement here is local. The project also has a verified NEAR testnet
deployment (`docs/TESTNET_EVIDENCE.md`); this adapter is what the tests and the
offline demo use.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from ..canonical import hash_chain_link, sha256_hex
from ..errors import ChainUnavailable, NotFound
from .engine import ChainState, EscrowEngine, EscrowPolicy
from .models import (
    Attempt,
    Bounty,
    BountyState,
    CallContext,
    Claim,
    SettlementResult,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS chain_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    json TEXT NOT NULL,
    updated_at_ns INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
    seq INTEGER PRIMARY KEY,
    receipt_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    bounty_id INTEGER,
    attempt_id INTEGER,
    ts_ns INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_receipts_bounty ON receipts(bounty_id);
"""


def now_ns() -> int:
    return time.time_ns()


class ChainAdapter(Protocol):
    """The settlement surface consumed by the API and web layers."""

    contract_id: str
    network: str
    mode: str

    def create_bounty(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
    def commit_claim(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
    def approve_claim(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
    def request_payout(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
    def deliver_callback(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...
    def get_bounty(self, bounty_id: int) -> Bounty: ...


class LocalLedger:
    """SQLite-backed simulation of the escrow contract with hash-chained receipts."""

    mode = "local"

    def __init__(
        self,
        db_path: Path | str,
        *,
        contract_id: str,
        network: str,
        policy: EscrowPolicy = EscrowPolicy(),
        clock: Callable[[], int] = now_ns,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.contract_id = contract_id
        self.network = network
        self.policy = policy
        self._clock = clock
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(SCHEMA)
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO chain_state (id, json, updated_at_ns) VALUES (1, ?, ?)",
                ("{}", self._clock()),
            )

    # ------------------------------------------------------------------
    # persistence internals
    # ------------------------------------------------------------------
    def _read_state(self) -> ChainState:
        row = self._conn.execute("SELECT json FROM chain_state WHERE id = 1").fetchone()
        if not row or not row[0]:
            return ChainState()
        raw = json.loads(row[0])
        if not raw:
            return ChainState()
        return ChainState.from_dict(raw)

    def _last_hash(self) -> str:
        row = self._conn.execute("SELECT hash FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
        return row[0] if row else "0" * 64

    def _write(self, state: ChainState, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stamp = self._clock()
        payload = json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":"))
        self._conn.execute(
            "UPDATE chain_state SET json = ?, updated_at_ns = ? WHERE id = 1", (payload, stamp)
        )

        last_hash = self._last_hash()
        row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM receipts").fetchone()
        seq = int(row[0]) if row else 0
        written: list[dict[str, Any]] = []
        for event in events:
            seq += 1
            receipt_id = f"local-{seq:06d}"
            body = {
                "seq": seq,
                "receipt_id": receipt_id,
                "network": self.network,
                "contract_id": self.contract_id,
                "kind": event["event"],
                "bounty_id": event.get("bounty_id"),
                "attempt_id": event.get("attempt_id"),
                "ts_ns": event["ts_ns"],
                "data": {k: v for k, v in event.items() if k not in {"event", "ts_ns"}},
            }
            digest = hash_chain_link(last_hash, body)
            self._conn.execute(
                "INSERT INTO receipts (seq, receipt_id, kind, bounty_id, attempt_id, ts_ns,"
                " payload_json, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    seq,
                    receipt_id,
                    body["kind"],
                    body["bounty_id"],
                    body["attempt_id"],
                    body["ts_ns"],
                    json.dumps(body, sort_keys=True, separators=(",", ":")),
                    last_hash,
                    digest,
                ),
            )
            last_hash = digest
            written.append({"receipt_id": receipt_id, "hash": digest, **body})
        return written

    def _apply(self, operation: Callable[[EscrowEngine, int], Any]) -> tuple[Any, list[dict[str, Any]]]:
        """Run one atomic contract call: load state, mutate, persist, emit receipts."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                state = self._read_state()
                engine = EscrowEngine(state, self.policy)
                stamp = self._clock()
                result = operation(engine, stamp)
                receipts = self._write(engine.state, engine.drain_events())
                self._conn.execute("COMMIT")
                return result, receipts
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def _view(self) -> EscrowEngine:
        with self._lock:
            return EscrowEngine(self._read_state(), self.policy)

    # ------------------------------------------------------------------
    # contract calls
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
        def op(engine: EscrowEngine, stamp: int) -> Bounty:
            return engine.create_bounty(
                CallContext(signer=signer, attached_yocto=attached_yocto),
                title=title,
                reward_yocto=reward_yocto,
                metadata_hash_hex=metadata_hash_hex,
                deadline_ns=deadline_ns,
                review_deadline_ns=review_deadline_ns,
                now_ns=stamp,
            )

        bounty, receipts = self._apply(op)
        return {"bounty": bounty.to_dict(), "receipts": receipts}

    def commit_claim(
        self,
        *,
        signer: str,
        bounty_id: int,
        evidence_commitment_hex: str,
        manifest_sha256: str | None = None,
    ) -> dict[str, Any]:
        def op(engine: EscrowEngine, stamp: int) -> Claim:
            return engine.commit_claim(
                CallContext(signer=signer),
                bounty_id=bounty_id,
                evidence_commitment_hex=evidence_commitment_hex,
                manifest_sha256=manifest_sha256,
                now_ns=stamp,
            )

        claim, receipts = self._apply(op)
        return {"claim": claim.to_dict(), "receipts": receipts}

    def mark_review(self, *, signer: str, bounty_id: int) -> dict[str, Any]:
        def op(engine: EscrowEngine, stamp: int) -> Bounty:
            return engine.mark_review(CallContext(signer=signer), bounty_id=bounty_id, now_ns=stamp)

        bounty, receipts = self._apply(op)
        return {"bounty": bounty.to_dict(), "receipts": receipts}

    def approve_claim(
        self, *, signer: str, bounty_id: int, claim_id: int
    ) -> dict[str, Any]:
        def op(engine: EscrowEngine, stamp: int) -> Bounty:
            return engine.approve_claim(
                CallContext(signer=signer), bounty_id=bounty_id, claim_id=claim_id, now_ns=stamp
            )

        bounty, receipts = self._apply(op)
        return {"bounty": bounty.to_dict(), "receipts": receipts}

    def request_payout(self, *, signer: str, bounty_id: int) -> dict[str, Any]:
        """Commit PayoutPending and reserve the reward. No transfer has finalised."""

        def op(engine: EscrowEngine, stamp: int) -> Attempt:
            return engine.request_payout(
                CallContext(signer=signer), bounty_id=bounty_id, now_ns=stamp
            )

        attempt, receipts = self._apply(op)
        return {"attempt": attempt.to_dict(), "receipts": receipts, "status": "PayoutPending"}

    def deliver_callback(
        self,
        *,
        bounty_id: int,
        attempt_id: int,
        result: SettlementResult,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Simulate the private settlement callback dispatched to the contract.

        On NEAR this is a promise callback with ``predecessor == contract``. The
        guard is modelled by constructing a self-call context; external callers
        of the engine cannot do this.
        """

        def op(engine: EscrowEngine, stamp: int) -> Bounty:
            bounty = engine.get_bounty(bounty_id)
            attempt = bounty.attempt(attempt_id)
            if attempt is None:
                raise NotFound(f"attempt {attempt_id} not found on bounty {bounty_id}")
            if bounty.state == BountyState.PAYOUT_PENDING:
                return engine.settle_payout(
                    bounty_id=bounty_id,
                    attempt_id=attempt_id,
                    result=result,
                    ctx=CallContext(signer=self.contract_id, is_self=True),
                    now_ns=stamp,
                    note=note,
                )
            return engine.settle_refund(
                bounty_id=bounty_id,
                attempt_id=attempt_id,
                result=result,
                ctx=CallContext(signer=self.contract_id, is_self=True),
                now_ns=stamp,
                note=note,
            )

        bounty, receipts = self._apply(op)
        return {"bounty": bounty.to_dict(), "receipts": receipts}

    def refund_expired(self, *, signer: str, bounty_id: int) -> dict[str, Any]:
        def op(engine: EscrowEngine, stamp: int) -> Attempt:
            return engine.refund_expired(
                CallContext(signer=signer), bounty_id=bounty_id, now_ns=stamp
            )

        attempt, receipts = self._apply(op)
        return {"attempt": attempt.to_dict(), "receipts": receipts, "status": "RefundPending"}

    def reconcile(
        self,
        *,
        signer: str,
        bounty_id: int,
        resolution: Literal["paid", "refunded"],
        evidence_sha256: str,
        note: str,
    ) -> dict[str, Any]:
        def op(engine: EscrowEngine, stamp: int) -> Bounty:
            return engine.reconcile(
                CallContext(signer=signer),
                bounty_id=bounty_id,
                resolution=resolution,
                evidence_sha256=evidence_sha256,
                note=note,
                now_ns=stamp,
            )

        bounty, receipts = self._apply(op)
        return {"bounty": bounty.to_dict(), "receipts": receipts}

    def set_dispute(self, *, signer: str, bounty_id: int, disputed: bool) -> dict[str, Any]:
        def op(engine: EscrowEngine, stamp: int) -> Bounty:
            return engine.set_dispute(
                CallContext(signer=signer), bounty_id=bounty_id, disputed=disputed, now_ns=stamp
            )

        bounty, receipts = self._apply(op)
        return {"bounty": bounty.to_dict(), "receipts": receipts}

    # ------------------------------------------------------------------
    # views
    # ------------------------------------------------------------------
    def get_bounty(self, bounty_id: int) -> Bounty:
        return self._view().get_bounty(bounty_id)

    def get_claim(self, claim_id: int) -> Claim:
        return self._view().get_claim(claim_id)

    def list_bounties(self) -> list[Bounty]:
        return self._view().list_bounties()

    def list_claims(self, bounty_id: int) -> list[Claim]:
        return self._view().list_claims(bounty_id)

    def receipt(self, receipt_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT payload_json, hash, prev_hash FROM receipts WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()
        if not row:
            raise NotFound(f"receipt {receipt_id} not found")
        body = json.loads(row[0])
        return {**body, "hash": row[1], "prev_hash": row[2]}

    def receipts(self, *, bounty_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if bounty_id is None:
            rows = self._conn.execute(
                "SELECT payload_json, hash, prev_hash FROM receipts ORDER BY seq DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT payload_json, hash, prev_hash FROM receipts WHERE bounty_id = ?"
                " ORDER BY seq DESC LIMIT ?",
                (bounty_id, limit),
            ).fetchall()
        out = []
        for payload, digest, prev in rows:
            body = json.loads(payload)
            out.append({**body, "hash": digest, "prev_hash": prev})
        return list(reversed(out))

    def verify_receipt_chain(self) -> dict[str, Any]:
        """Recompute the receipt hash chain; a broken link is a tamper signal."""
        prev = "0" * 64
        count = 0
        for payload, digest, stored_prev in self._conn.execute(
            "SELECT payload_json, hash, prev_hash FROM receipts ORDER BY seq"
        ):
            body = json.loads(payload)
            expected = hash_chain_link(prev, body)
            if stored_prev != prev or expected != digest:
                return {"valid": False, "broken_at_seq": body.get("seq"), "checked": count}
            prev = digest
            count += 1
        return {"valid": True, "checked": count, "head": prev}


def open_chain(
    db_path: Path | str,
    *,
    contract_id: str,
    network: str,
    mode: str = "local",
) -> ChainAdapter:
    """Return the adapter for ``mode``.

    ``local`` is always available. ``near-testnet`` requires a deployed contract,
    a funded account and a credentials file; if any is missing the constructor
    raises :class:`ChainUnavailable` with the exact missing item — the app never
    silently degrades to the simulator.
    """
    normalized = (mode or "local").strip().lower()
    if normalized in {"local", "local-ledger", "simulator"}:
        return LocalLedger(db_path, contract_id=contract_id, network=network)
    if normalized in {"near-testnet", "testnet", "near"}:
        from .near_testnet import NearTestnetAdapter  # imported lazily: needs requests

        return NearTestnetAdapter(
            network="near-testnet",
            contract_id=contract_id,
            txlog_path=Path(db_path).with_name("near-txlog.sqlite3"),
        )
    raise ChainUnavailable(
        f"unknown chain mode {mode!r}; expected 'local' or 'near-testnet'"
    )


def fingerprint(db_path: Path | str) -> str:
    """Stable digest of the receipt head, used in evidence manifests."""
    return sha256_hex(str(db_path).encode("utf-8"))
