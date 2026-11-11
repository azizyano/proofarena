"""Escrow state machine — the authoritative settlement rules.

This module is the executable specification for ``contracts/escrow/src/lib.rs``.
It is deliberately free of I/O so that every rule can be tested directly:

* owner-only settlement authority,
* at most one approved winner,
* fully exclusive payout/refund,
* monotonic settlement attempts that are written *before* any transfer,
* explicit ``PayoutPending`` / ``RefundPending`` / ``NeedsReconciliation`` states,
* no automatic retry of an ambiguous transfer.

NEAR transfers are asynchronous. A committed caller state does not prove the
outgoing transfer finalised, so ``settle_*`` accepts three outcomes and treats
``AMBIGUOUS`` as "freeze, do not retry".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..canonical import is_hex64
from ..errors import (
    CallbackError,
    DepositError,
    DuplicateClaim,
    NotFound,
    ParamError,
    ReplayError,
    StateError,
    TerminalState,
    Unauthorized,
)
from .models import (
    Attempt,
    AttemptKind,
    AttemptStatus,
    Bounty,
    BountyState,
    CallContext,
    Claim,
    ClaimState,
    SettlementResult,
    SETTLING_STATES,
    TERMINAL_STATES,
)


@dataclass(frozen=True)
class EscrowPolicy:
    storage_fee_yocto: int = 1_000_000_000_000_000_000_000
    #: 100 NEAR — a local demo cap, chosen so the UI cannot be used to move
    #: meaningful amounts even if a real network adapter is plugged in later.
    max_reward_yocto: int = 100_000_000_000_000_000_000_000_000
    max_title_len: int = 120
    max_claims_per_bounty: int = 32
    max_attempts_per_bounty: int = 16


DEFAULT_POLICY = EscrowPolicy()


@dataclass
class ChainState:
    """Everything the escrow keeps. Small enough to persist atomically."""

    bounties: dict[int, Bounty] = field(default_factory=dict)
    claims: dict[int, Claim] = field(default_factory=dict)
    next_bounty_id: int = 1
    next_claim_id: int = 1
    next_attempt_id: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "bounties": [b.to_dict() for b in self.bounties.values()],
            "claims": [c.to_dict() for c in self.claims.values()],
            "next_bounty_id": self.next_bounty_id,
            "next_claim_id": self.next_claim_id,
            "next_attempt_id": self.next_attempt_id,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ChainState":
        state = cls(
            next_bounty_id=raw.get("next_bounty_id", 1),
            next_claim_id=raw.get("next_claim_id", 1),
            next_attempt_id=raw.get("next_attempt_id", 1),
        )
        for item in raw.get("bounties", []):
            bounty = Bounty.from_dict(item)
            state.bounties[bounty.bounty_id] = bounty
        for item in raw.get("claims", []):
            claim = Claim.from_dict(item)
            state.claims[claim.claim_id] = claim
        return state


def _require_owner(ctx: CallContext, bounty: Bounty, action: str) -> None:
    if ctx.signer != bounty.owner:
        raise Unauthorized(f"only the bounty owner may {action}")


def _require_settleable(bounty: Bounty, action: str) -> None:
    if bounty.state in TERMINAL_STATES:
        raise TerminalState(f"bounty is already terminal ({bounty.state.value}); cannot {action}")
    if bounty.state == BountyState.NEEDS_RECONCILIATION:
        raise StateError(
            "bounty needs reconciliation before any further settlement action; "
            "resolve the outstanding transfer explicitly"
        )
    if bounty.disputed:
        raise StateError("bounty is disputed; payout and refund are frozen")


class EscrowEngine:
    """Pure state machine. Callers supply ``now_ns`` so time is testable."""

    def __init__(
        self,
        state: ChainState | None = None,
        policy: EscrowPolicy = DEFAULT_POLICY,
    ) -> None:
        self.state = state or ChainState()
        self.policy = policy
        self.events: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # event journal (drained by the ledger into hash-chained receipts)
    # ------------------------------------------------------------------
    def _emit(self, event: str, now_ns: int, **data: Any) -> None:
        self.events.append({"event": event, "ts_ns": now_ns, **data})

    def drain_events(self) -> list[dict[str, Any]]:
        drained, self.events = self.events, []
        return drained

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _bounty(self, bounty_id: int) -> Bounty:
        bounty = self.state.bounties.get(bounty_id)
        if bounty is None:
            raise NotFound(f"bounty {bounty_id} does not exist")
        return bounty

    def _claim(self, claim_id: int) -> Claim:
        claim = self.state.claims.get(claim_id)
        if claim is None:
            raise NotFound(f"claim {claim_id} does not exist")
        return claim

    def _new_attempt(
        self,
        bounty: Bounty,
        kind: AttemptKind,
        now_ns: int,
    ) -> Attempt:
        if len(bounty.attempts) >= self.policy.max_attempts_per_bounty:
            raise StateError("settlement attempt limit reached for this bounty")
        attempt = Attempt(
            attempt_id=self.state.next_attempt_id,
            kind=kind,
            status=AttemptStatus.PENDING,
            created_ns=now_ns,
            state_before=bounty.state,
        )
        self.state.next_attempt_id += 1
        bounty.attempts.append(attempt)
        bounty.pending_attempt_id = attempt.attempt_id
        bounty.last_attempt_id = attempt.attempt_id
        return attempt

    def check_invariants(self, bounty: Bounty) -> None:
        """Economic invariants from WHITEPAPER §6 / NEAR_ESCROW_SPEC."""
        problems: list[str] = []
        if bounty.paid_yocto < 0 or bounty.refunded_yocto < 0:
            problems.append("negative settlement total")
        if bounty.paid_yocto > 0 and bounty.refunded_yocto > 0:
            problems.append("bounty both paid and refunded")
        if bounty.paid_yocto + bounty.refunded_yocto > bounty.reward_yocto:
            problems.append("settled total exceeds funded reward")
        expected_escrow = bounty.reward_yocto - bounty.paid_yocto - bounty.refunded_yocto
        if bounty.escrow_balance_yocto != expected_escrow:
            problems.append(
                f"escrow balance {bounty.escrow_balance_yocto} != expected {expected_escrow}"
            )
        if bounty.state == BountyState.PAID and bounty.paid_yocto != bounty.reward_yocto:
            problems.append("Paid state without full reward settlement")
        if bounty.state == BountyState.REFUNDED and bounty.refunded_yocto != bounty.reward_yocto:
            problems.append("Refunded state without full refund settlement")
        if bounty.pending_attempt_id is not None:
            pending = [a for a in bounty.attempts if a.status == AttemptStatus.PENDING]
            if len(pending) != 1:
                problems.append("pending attempt pointer does not match exactly one pending attempt")
        if bounty.state in TERMINAL_STATES and bounty.pending_attempt_id is not None:
            problems.append("terminal bounty still has a pending attempt")
        if problems:
            raise AssertionError(f"escrow invariant violation on bounty {bounty.bounty_id}: {problems}")

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def create_bounty(
        self,
        ctx: CallContext,
        *,
        title: str,
        reward_yocto: int,
        metadata_hash_hex: str,
        deadline_ns: int,
        review_deadline_ns: int,
        now_ns: int,
    ) -> Bounty:
        title = (title or "").strip()
        if not title or len(title) > self.policy.max_title_len:
            raise ParamError(f"title must be 1..{self.policy.max_title_len} characters")
        if isinstance(reward_yocto, bool) or not isinstance(reward_yocto, int):
            raise ParamError("reward must be an integer amount in yoctoNEAR")
        if reward_yocto <= 0:
            raise ParamError("reward must be greater than zero")
        if reward_yocto > self.policy.max_reward_yocto:
            raise ParamError("reward exceeds the local MVP cap")
        if not is_hex64(metadata_hash_hex):
            raise ParamError("metadata hash must be a 64-character lowercase hex digest")
        if deadline_ns <= now_ns:
            raise ParamError("submission deadline must be in the future")
        if review_deadline_ns <= deadline_ns:
            raise ParamError("review deadline must be after the submission deadline")

        required = reward_yocto + self.policy.storage_fee_yocto
        if ctx.attached_yocto != required:
            raise DepositError(
                "attached deposit must equal reward + storage reserve "
                f"({required} yoctoNEAR), got {ctx.attached_yocto}"
            )

        bounty = Bounty(
            bounty_id=self.state.next_bounty_id,
            owner=ctx.signer,
            title=title,
            reward_yocto=reward_yocto,
            metadata_hash_hex=metadata_hash_hex,
            deadline_ns=deadline_ns,
            review_deadline_ns=review_deadline_ns,
            created_ns=now_ns,
            escrow_balance_yocto=reward_yocto,
            storage_reserve_yocto=self.policy.storage_fee_yocto,
        )
        self.state.next_bounty_id += 1
        self.state.bounties[bounty.bounty_id] = bounty
        self._emit(
            "bounty_created",
            now_ns,
            bounty_id=bounty.bounty_id,
            owner=bounty.owner,
            reward_yocto=bounty.reward_yocto,
            metadata_hash_hex=bounty.metadata_hash_hex,
        )
        self.check_invariants(bounty)
        return bounty

    def commit_claim(
        self,
        ctx: CallContext,
        *,
        bounty_id: int,
        evidence_commitment_hex: str,
        manifest_sha256: str | None = None,
        now_ns: int,
    ) -> Claim:
        bounty = self._bounty(bounty_id)
        if bounty.state != BountyState.OPEN:
            raise StateError(f"claims are closed for bounty {bounty_id} ({bounty.state.value})")
        if now_ns > bounty.deadline_ns:
            raise StateError("submission window has closed for this bounty")
        if not is_hex64(evidence_commitment_hex):
            raise ParamError("evidence commitment must be a 64-character lowercase hex digest")
        if manifest_sha256 is not None and not is_hex64(manifest_sha256):
            raise ParamError("manifest digest must be a 64-character lowercase hex digest")
        if len(bounty.claims) >= self.policy.max_claims_per_bounty:
            raise StateError("claim limit reached for this bounty")
        for claim_id in bounty.claims:
            existing = self.state.claims[claim_id]
            if existing.evidence_commitment_hex == evidence_commitment_hex:
                raise DuplicateClaim("an identical claim commitment was already submitted")

        claim = Claim(
            claim_id=self.state.next_claim_id,
            bounty_id=bounty_id,
            submitter=ctx.signer,
            evidence_commitment_hex=evidence_commitment_hex,
            state=ClaimState.SUBMITTED,
            submitted_ns=now_ns,
            manifest_sha256=manifest_sha256,
        )
        self.state.next_claim_id += 1
        self.state.claims[claim.claim_id] = claim
        bounty.claims.append(claim.claim_id)
        self._emit(
            "claim_committed",
            now_ns,
            bounty_id=bounty_id,
            claim_id=claim.claim_id,
            submitter=claim.submitter,
            evidence_commitment_hex=claim.evidence_commitment_hex,
        )
        return claim

    def mark_review(self, ctx: CallContext, *, bounty_id: int, now_ns: int) -> Bounty:
        bounty = self._bounty(bounty_id)
        if bounty.state != BountyState.OPEN:
            raise StateError(f"only an Open bounty can move to Review (current {bounty.state.value})")
        if ctx.signer != bounty.owner and now_ns <= bounty.deadline_ns:
            raise Unauthorized("only the owner may close submissions before the deadline")
        if bounty.pending_attempt_id is not None:
            raise StateError("cannot change state while a settlement attempt is pending")
        bounty.state = BountyState.REVIEW
        self._emit("review_started", now_ns, bounty_id=bounty_id, actor=ctx.signer)
        self.check_invariants(bounty)
        return bounty

    def approve_claim(
        self,
        ctx: CallContext,
        *,
        bounty_id: int,
        claim_id: int,
        now_ns: int,
    ) -> Bounty:
        bounty = self._bounty(bounty_id)
        _require_settleable(bounty, "approve a claim")
        _require_owner(ctx, bounty, "approve a claim")
        if bounty.state != BountyState.REVIEW:
            raise StateError(f"claims can only be approved during Review (current {bounty.state.value})")
        if now_ns > bounty.review_deadline_ns:
            raise StateError("review window has closed; only a refund is possible")
        if bounty.approved_claim_id is not None:
            raise StateError("a winner is already approved for this bounty")

        claim = self._claim(claim_id)
        if claim.bounty_id != bounty_id:
            raise StateError("claim does not belong to this bounty")
        if claim.state != ClaimState.SUBMITTED:
            raise StateError(f"claim {claim_id} is not eligible (state {claim.state.value})")

        bounty.approved_claim_id = claim.claim_id
        bounty.winner = claim.submitter
        bounty.state = BountyState.APPROVED
        claim.state = ClaimState.APPROVED
        self._emit(
            "claim_approved",
            now_ns,
            bounty_id=bounty_id,
            claim_id=claim_id,
            winner=claim.submitter,
            approved_by=ctx.signer,
        )
        self.check_invariants(bounty)
        return bounty

    def request_payout(self, ctx: CallContext, *, bounty_id: int, now_ns: int) -> Attempt:
        bounty = self._bounty(bounty_id)
        _require_settleable(bounty, "request payout")
        _require_owner(ctx, bounty, "request payout")
        if bounty.pending_attempt_id is not None:
            raise ReplayError("a settlement attempt is already pending for this bounty")
        if bounty.state != BountyState.APPROVED:
            raise StateError(f"payout requires Approved state (current {bounty.state.value})")
        if bounty.approved_claim_id is None:
            raise StateError("no approved claim to pay")
        if any(
            a.kind == AttemptKind.PAYOUT and a.status == AttemptStatus.AMBIGUOUS
            for a in bounty.attempts
        ):
            raise StateError("a previous payout attempt is ambiguous; reconciliation is required")

        attempt = self._new_attempt(bounty, AttemptKind.PAYOUT, now_ns)
        bounty.state = BountyState.PAYOUT_PENDING
        self._emit(
            "payout_requested",
            now_ns,
            bounty_id=bounty_id,
            attempt_id=attempt.attempt_id,
            amount_yocto=bounty.reward_yocto,
            recipient=bounty.winner,
        )
        self.check_invariants(bounty)
        return attempt

    def settle_payout(
        self,
        *,
        bounty_id: int,
        attempt_id: int,
        result: SettlementResult,
        ctx: CallContext,
        now_ns: int,
        note: str | None = None,
    ) -> Bounty:
        if not ctx.is_self:
            raise Unauthorized("settlement callbacks may only be dispatched by the contract itself")
        bounty = self._bounty(bounty_id)
        if bounty.state != BountyState.PAYOUT_PENDING:
            raise StateError(f"no payout is pending (current {bounty.state.value})")

        attempt = bounty.attempt(attempt_id)
        if attempt is None or attempt.kind != AttemptKind.PAYOUT:
            raise CallbackError("callback attempt id does not match a payout attempt for this bounty")
        if attempt.attempt_id != bounty.pending_attempt_id:
            raise CallbackError("callback attempt id is not the outstanding attempt")
        if attempt.status != AttemptStatus.PENDING:
            raise ReplayError("this settlement attempt was already resolved")

        claim = self._claim(bounty.approved_claim_id) if bounty.approved_claim_id else None

        if result == SettlementResult.SUCCESS:
            attempt.status = AttemptStatus.SUCCEEDED
            attempt.resolved_ns = now_ns
            attempt.note = note
            bounty.paid_yocto = bounty.reward_yocto
            bounty.escrow_balance_yocto = 0
            bounty.state = BountyState.PAID
            bounty.pending_attempt_id = None
            if claim is not None:
                claim.state = ClaimState.PAID
            self._emit(
                "payout_completed",
                now_ns,
                bounty_id=bounty_id,
                attempt_id=attempt_id,
                amount_yocto=bounty.reward_yocto,
            )
        elif result == SettlementResult.FAILURE:
            attempt.status = AttemptStatus.FAILED
            attempt.resolved_ns = now_ns
            attempt.note = note or "explicit transfer failure reported by callback"
            # Recovery is only to the pre-attempt state, and only after an
            # explicit failure. An ambiguous outcome never lands here.
            bounty.state = attempt.state_before
            bounty.pending_attempt_id = None
            self._emit(
                "payout_failed",
                now_ns,
                bounty_id=bounty_id,
                attempt_id=attempt_id,
                reason="explicit_failure",
            )
        else:
            attempt.status = AttemptStatus.AMBIGUOUS
            attempt.note = note or "callback outcome ambiguous; retries halted"
            bounty.state = BountyState.NEEDS_RECONCILIATION
            bounty.pending_attempt_id = None
            self._emit(
                "settlement_ambiguous",
                now_ns,
                bounty_id=bounty_id,
                attempt_id=attempt_id,
                reason="ambiguous_outcome",
            )

        self.check_invariants(bounty)
        return bounty

    def refund_expired(self, ctx: CallContext, *, bounty_id: int, now_ns: int) -> Attempt:
        bounty = self._bounty(bounty_id)
        _require_settleable(bounty, "refund")
        _require_owner(ctx, bounty, "refund")
        if bounty.pending_attempt_id is not None:
            raise ReplayError("a settlement attempt is already pending for this bounty")
        if bounty.state not in (BountyState.OPEN, BountyState.REVIEW):
            raise StateError(f"refund is not available from {bounty.state.value}")
        if bounty.approved_claim_id is not None:
            raise StateError("refund is refused: a claim has already been approved")
        if now_ns <= bounty.review_deadline_ns:
            raise StateError("refund is not available until the review grace period ends")
        if any(
            a.kind == AttemptKind.REFUND and a.status == AttemptStatus.AMBIGUOUS
            for a in bounty.attempts
        ):
            raise StateError("a previous refund attempt is ambiguous; reconciliation is required")

        attempt = self._new_attempt(bounty, AttemptKind.REFUND, now_ns)
        bounty.state = BountyState.REFUND_PENDING
        self._emit(
            "refund_requested",
            now_ns,
            bounty_id=bounty_id,
            attempt_id=attempt.attempt_id,
            amount_yocto=bounty.reward_yocto,
            recipient=bounty.owner,
        )
        self.check_invariants(bounty)
        return attempt

    def settle_refund(
        self,
        *,
        bounty_id: int,
        attempt_id: int,
        result: SettlementResult,
        ctx: CallContext,
        now_ns: int,
        note: str | None = None,
    ) -> Bounty:
        if not ctx.is_self:
            raise Unauthorized("settlement callbacks may only be dispatched by the contract itself")
        bounty = self._bounty(bounty_id)
        if bounty.state != BountyState.REFUND_PENDING:
            raise StateError(f"no refund is pending (current {bounty.state.value})")

        attempt = bounty.attempt(attempt_id)
        if attempt is None or attempt.kind != AttemptKind.REFUND:
            raise CallbackError("callback attempt id does not match a refund attempt for this bounty")
        if attempt.attempt_id != bounty.pending_attempt_id:
            raise CallbackError("callback attempt id is not the outstanding attempt")
        if attempt.status != AttemptStatus.PENDING:
            raise ReplayError("this settlement attempt was already resolved")

        if result == SettlementResult.SUCCESS:
            attempt.status = AttemptStatus.SUCCEEDED
            attempt.resolved_ns = now_ns
            attempt.note = note
            bounty.refunded_yocto = bounty.reward_yocto
            bounty.escrow_balance_yocto = 0
            bounty.state = BountyState.REFUNDED
            bounty.pending_attempt_id = None
            self._emit(
                "refund_completed",
                now_ns,
                bounty_id=bounty_id,
                attempt_id=attempt_id,
                amount_yocto=bounty.reward_yocto,
            )
        elif result == SettlementResult.FAILURE:
            attempt.status = AttemptStatus.FAILED
            attempt.resolved_ns = now_ns
            attempt.note = note or "explicit transfer failure reported by callback"
            bounty.state = attempt.state_before
            bounty.pending_attempt_id = None
            self._emit(
                "refund_failed",
                now_ns,
                bounty_id=bounty_id,
                attempt_id=attempt_id,
                reason="explicit_failure",
            )
        else:
            attempt.status = AttemptStatus.AMBIGUOUS
            attempt.note = note or "callback outcome ambiguous; retries halted"
            bounty.state = BountyState.NEEDS_RECONCILIATION
            bounty.pending_attempt_id = None
            self._emit(
                "settlement_ambiguous",
                now_ns,
                bounty_id=bounty_id,
                attempt_id=attempt_id,
                reason="ambiguous_outcome",
            )

        self.check_invariants(bounty)
        return bounty

    def reconcile(
        self,
        ctx: CallContext,
        *,
        bounty_id: int,
        resolution: Literal["paid", "refunded"],
        evidence_sha256: str,
        note: str,
        now_ns: int,
    ) -> Bounty:
        """Manual, owner-signed resolution of a stuck transfer.

        Only reachable from ``NeedsReconciliation``. The owner must have checked
        the actual chain outcome; this function never guesses.
        """
        bounty = self._bounty(bounty_id)
        _require_owner(ctx, bounty, "reconcile this bounty")
        if bounty.state != BountyState.NEEDS_RECONCILIATION:
            raise StateError(f"reconciliation requires NeedsReconciliation (current {bounty.state.value})")
        if not is_hex64(evidence_sha256):
            raise ParamError("reconciliation evidence digest must be 64-character lowercase hex")
        if not note or len(note) > 512:
            raise ParamError("reconciliation requires a 1..512 character note")

        if resolution == "paid":
            if bounty.winner is None:
                raise StateError("cannot resolve as paid without an approved winner")
            bounty.paid_yocto = bounty.reward_yocto
            bounty.escrow_balance_yocto = 0
            bounty.state = BountyState.PAID
            if bounty.approved_claim_id:
                self._claim(bounty.approved_claim_id).state = ClaimState.PAID
        elif resolution == "refunded":
            bounty.refunded_yocto = bounty.reward_yocto
            bounty.escrow_balance_yocto = 0
            bounty.state = BountyState.REFUNDED
        else:
            raise ParamError("resolution must be 'paid' or 'refunded'")

        for attempt in bounty.attempts:
            if attempt.status == AttemptStatus.AMBIGUOUS:
                attempt.status = (
                    AttemptStatus.SUCCEEDED if resolution == "paid" else AttemptStatus.FAILED
                )
                attempt.resolved_ns = now_ns
        self._emit(
            "reconciled",
            now_ns,
            bounty_id=bounty_id,
            resolution=resolution,
            evidence_sha256=evidence_sha256,
            note=note,
            actor=ctx.signer,
        )
        self.check_invariants(bounty)
        return bounty

    def set_dispute(
        self,
        ctx: CallContext,
        *,
        bounty_id: int,
        disputed: bool,
        now_ns: int,
    ) -> Bounty:
        bounty = self._bounty(bounty_id)
        _require_owner(ctx, bounty, "change the dispute flag")
        if bounty.state in TERMINAL_STATES or bounty.state in SETTLING_STATES:
            raise StateError(f"cannot change dispute flag in {bounty.state.value}")
        bounty.disputed = bool(disputed)
        self._emit(
            "dispute_flag_changed",
            now_ns,
            bounty_id=bounty_id,
            disputed=bounty.disputed,
            actor=ctx.signer,
        )
        self.check_invariants(bounty)
        return bounty

    # ------------------------------------------------------------------
    # views
    # ------------------------------------------------------------------
    def get_bounty(self, bounty_id: int) -> Bounty:
        return self._bounty(bounty_id)

    def get_claim(self, claim_id: int) -> Claim:
        return self._claim(claim_id)

    def list_claims(self, bounty_id: int) -> list[Claim]:
        bounty = self._bounty(bounty_id)
        return [self.state.claims[cid] for cid in bounty.claims]

    def list_bounties(self) -> list[Bounty]:
        return [self.state.bounties[k] for k in sorted(self.state.bounties)]
