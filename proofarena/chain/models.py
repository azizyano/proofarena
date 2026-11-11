"""Serializable chain domain objects.

The shapes mirror ``contracts/escrow/src/lib.rs`` one-for-one so that the local
engine and the Rust contract can be reasoned about together. Anything that is
not listed here is deliberately not persisted on-chain: no exploit text, no
patch content, no logs — only commitments and settlement state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BountyState(str, Enum):
    OPEN = "Open"
    REVIEW = "Review"
    APPROVED = "Approved"
    PAYOUT_PENDING = "PayoutPending"
    PAID = "Paid"
    REFUND_PENDING = "RefundPending"
    REFUNDED = "Refunded"
    DISPUTED = "Disputed"  # displayed state; `disputed` flag gates settlement
    NEEDS_RECONCILIATION = "NeedsReconciliation"


TERMINAL_STATES = frozenset({BountyState.PAID, BountyState.REFUNDED})
SETTLING_STATES = frozenset({BountyState.PAYOUT_PENDING, BountyState.REFUND_PENDING})


class ClaimState(str, Enum):
    SUBMITTED = "Submitted"
    APPROVED = "Approved"
    PAID = "Paid"
    REJECTED = "Rejected"


class SettlementResult(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    #: Callback lost, out of gas, or the receipt could not be interpreted.
    #: This is *not* evidence that the transfer failed.
    AMBIGUOUS = "ambiguous"


class AttemptKind(str, Enum):
    PAYOUT = "payout"
    REFUND = "refund"


class AttemptStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


@dataclass
class Attempt:
    attempt_id: int
    kind: AttemptKind
    status: AttemptStatus
    created_ns: int
    state_before: BountyState
    resolved_ns: int | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "created_ns": self.created_ns,
            "state_before": self.state_before.value,
            "resolved_ns": self.resolved_ns,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Attempt":
        return cls(
            attempt_id=raw["attempt_id"],
            kind=AttemptKind(raw["kind"]),
            status=AttemptStatus(raw["status"]),
            created_ns=raw["created_ns"],
            state_before=BountyState(raw["state_before"]),
            resolved_ns=raw.get("resolved_ns"),
            note=raw.get("note"),
        )


@dataclass
class Claim:
    claim_id: int
    bounty_id: int
    submitter: str
    evidence_commitment_hex: str
    state: ClaimState
    submitted_ns: int
    #: Digest of the *private* claim manifest, stored on-chain as a reference.
    manifest_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "bounty_id": self.bounty_id,
            "submitter": self.submitter,
            "evidence_commitment_hex": self.evidence_commitment_hex,
            "state": self.state.value,
            "submitted_ns": self.submitted_ns,
            "manifest_sha256": self.manifest_sha256,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Claim":
        return cls(
            claim_id=raw["claim_id"],
            bounty_id=raw["bounty_id"],
            submitter=raw["submitter"],
            evidence_commitment_hex=raw["evidence_commitment_hex"],
            state=ClaimState(raw["state"]),
            submitted_ns=raw["submitted_ns"],
            manifest_sha256=raw.get("manifest_sha256"),
        )

    def public(self) -> dict[str, Any]:
        """Sanitized projection — safe for public timeline responses."""
        out = self.to_dict()
        out.pop("manifest_sha256", None)
        return out


@dataclass
class Bounty:
    bounty_id: int
    owner: str
    title: str
    reward_yocto: int
    metadata_hash_hex: str
    deadline_ns: int
    review_deadline_ns: int
    created_ns: int
    state: BountyState = BountyState.OPEN
    approved_claim_id: int | None = None
    winner: str | None = None
    paid_yocto: int = 0
    refunded_yocto: int = 0
    escrow_balance_yocto: int = 0
    storage_reserve_yocto: int = 0
    pending_attempt_id: int | None = None
    last_attempt_id: int | None = None
    disputed: bool = False
    claims: list[int] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)

    # -- projections -----------------------------------------------------
    @property
    def effective_state(self) -> BountyState:
        """UI-facing state: the dispute flag overrides the lifecycle label."""
        if self.disputed and self.state not in TERMINAL_STATES:
            return BountyState.DISPUTED
        return self.state

    def attempt(self, attempt_id: int) -> Attempt | None:
        for item in self.attempts:
            if item.attempt_id == attempt_id:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bounty_id": self.bounty_id,
            "owner": self.owner,
            "title": self.title,
            "reward_yocto": str(self.reward_yocto),
            "metadata_hash_hex": self.metadata_hash_hex,
            "deadline_ns": self.deadline_ns,
            "review_deadline_ns": self.review_deadline_ns,
            "created_ns": self.created_ns,
            "state": self.state.value,
            "approved_claim_id": self.approved_claim_id,
            "winner": self.winner,
            "paid_yocto": str(self.paid_yocto),
            "refunded_yocto": str(self.refunded_yocto),
            "escrow_balance_yocto": str(self.escrow_balance_yocto),
            "storage_reserve_yocto": str(self.storage_reserve_yocto),
            "pending_attempt_id": self.pending_attempt_id,
            "last_attempt_id": self.last_attempt_id,
            "disputed": self.disputed,
            "claims": list(self.claims),
            "attempts": [a.to_dict() for a in self.attempts],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Bounty":
        return cls(
            bounty_id=raw["bounty_id"],
            owner=raw["owner"],
            title=raw["title"],
            reward_yocto=int(raw["reward_yocto"]),
            metadata_hash_hex=raw["metadata_hash_hex"],
            deadline_ns=raw["deadline_ns"],
            review_deadline_ns=raw["review_deadline_ns"],
            created_ns=raw["created_ns"],
            state=BountyState(raw["state"]),
            approved_claim_id=raw.get("approved_claim_id"),
            winner=raw.get("winner"),
            paid_yocto=int(raw.get("paid_yocto", 0)),
            refunded_yocto=int(raw.get("refunded_yocto", 0)),
            escrow_balance_yocto=int(raw.get("escrow_balance_yocto", 0)),
            storage_reserve_yocto=int(raw.get("storage_reserve_yocto", 0)),
            pending_attempt_id=raw.get("pending_attempt_id"),
            last_attempt_id=raw.get("last_attempt_id"),
            disputed=bool(raw.get("disputed", False)),
            claims=list(raw.get("claims", [])),
            attempts=[Attempt.from_dict(a) for a in raw.get("attempts", [])],
        )

    def public(self) -> dict[str, Any]:
        return {
            "bounty_id": self.bounty_id,
            "owner": self.owner,
            "title": self.title,
            "reward_yocto": str(self.reward_yocto),
            "metadata_hash_hex": self.metadata_hash_hex,
            "deadline_ns": self.deadline_ns,
            "review_deadline_ns": self.review_deadline_ns,
            "created_ns": self.created_ns,
            "state": self.effective_state.value,
            "lifecycle_state": self.state.value,
            "approved_claim_id": self.approved_claim_id,
            "winner": self.winner,
            "paid_yocto": str(self.paid_yocto),
            "refunded_yocto": str(self.refunded_yocto),
            "escrow_balance_yocto": str(self.escrow_balance_yocto),
            "disputed": self.disputed,
            "claim_count": len(self.claims),
            "pending_attempt_id": self.pending_attempt_id,
        }


@dataclass
class CallContext:
    """Who is calling, and how much native token is attached.

    ``is_self`` is true only for the private settlement callback, which on NEAR
    is dispatched by the contract to itself. A human caller can never set it.
    """

    signer: str
    attached_yocto: int = 0
    is_self: bool = False
