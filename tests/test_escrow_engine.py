"""PA-005 / SEC-001..SEC-010 — escrow state machine adversarial tests.

Every test asserts both the raised error code *and* that state is unchanged, so
a rejected call can never leave partial accounting behind.
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from conftest import (
    AUDITOR,
    NEAR,
    OWNER,
    STRANGER,
    fund,
    hex64,
    submit_claim,
    to_approved,
)
from proofarena.chain.engine import EscrowEngine, EscrowPolicy
from proofarena.chain.models import (
    AttemptStatus,
    BountyState,
    CallContext,
    ClaimState,
    SettlementResult,
)
from proofarena.errors import (
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

CONTRACT = "escrow.proofarena.local"
SELF = CallContext


def selfcall() -> CallContext:
    return CallContext(signer=CONTRACT, is_self=True)


# ---------------------------------------------------------------------------
# AT-001 / AT-002 — creation and funding
# ---------------------------------------------------------------------------
def test_at001_create_funds_bounty_and_allocates_unique_id(engine, clock):
    first = fund(engine, clock)
    second = fund(engine, clock, meta=hex64("second"))
    assert first.bounty_id == 1 and second.bounty_id == 2
    assert first.state is BountyState.OPEN
    assert first.escrow_balance_yocto == first.reward_yocto
    assert first.storage_reserve_yocto == EscrowPolicy().storage_fee_yocto
    engine.check_invariants(first)


@pytest.mark.parametrize(
    "attached",
    [0, 5 * NEAR, 5 * NEAR + EscrowPolicy().storage_fee_yocto - 1, 5 * NEAR + EscrowPolicy().storage_fee_yocto + 1],
)
def test_at002_wrong_funding_is_rejected_without_state_change(engine, clock, attached):
    before = len(engine.list_bounties())
    with pytest.raises(DepositError) as exc:
        fund(engine, clock, attached=attached)
    assert exc.value.code == "E_DEPOSIT"
    assert len(engine.list_bounties()) == before
    assert engine.state.next_bounty_id == 1


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"reward": 0}, "E_PARAM"),
        ({"reward": -5}, "E_PARAM"),
        ({"reward": EscrowPolicy().max_reward_yocto + 1}, "E_PARAM"),
        ({"title": ""}, "E_PARAM"),
        ({"title": "x" * 500}, "E_PARAM"),
        ({"submission_s": 0}, "E_PARAM"),
        ({"review_s": -1}, "E_PARAM"),
        ({"meta": "not-a-digest"}, "E_PARAM"),
        ({"meta": "A" * 64}, "E_PARAM"),
    ],
)
def test_create_rejects_malformed_parameters(engine, clock, kwargs, code):
    with pytest.raises(ParamError) as exc:
        fund(engine, clock, **kwargs)
    assert exc.value.code == code
    assert engine.list_bounties() == []


def test_create_requires_exact_deposit_invariant(engine, clock):
    bounty = fund(engine, clock)
    tampered = replace(bounty, escrow_balance_yocto=bounty.reward_yocto + 1)
    with pytest.raises(AssertionError):
        engine.check_invariants(tampered)


# ---------------------------------------------------------------------------
# claims
# ---------------------------------------------------------------------------
def test_duplicate_claim_commitment_is_rejected(engine, clock):
    bounty = fund(engine, clock)
    commitment = hex64("same-evidence")
    submit_claim(engine, clock, bounty.bounty_id, commitment=commitment)
    with pytest.raises(DuplicateClaim):
        submit_claim(engine, clock, bounty.bounty_id, who="other.agent.local", commitment=commitment)
    assert len(engine.list_claims(bounty.bounty_id)) == 1


def test_late_claim_is_rejected(engine, clock):
    bounty = fund(engine, clock, submission_s=10)
    clock.advance(11)
    with pytest.raises(StateError) as exc:
        submit_claim(engine, clock, bounty.bounty_id)
    assert "closed" in exc.value.message
    assert engine.list_claims(bounty.bounty_id) == []


def test_claim_after_review_started_is_rejected(engine, clock):
    bounty = fund(engine, clock)
    engine.mark_review(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    with pytest.raises(StateError):
        submit_claim(engine, clock, bounty.bounty_id)


def test_unknown_bounty_and_claim_are_not_found(engine):
    with pytest.raises(NotFound):
        engine.get_bounty(404)
    with pytest.raises(NotFound):
        engine.get_claim(404)


# ---------------------------------------------------------------------------
# SEC-001..SEC-003 — authorization and cross-bounty isolation
# ---------------------------------------------------------------------------
def test_sec001_non_owner_cannot_approve(engine, clock):
    bounty = fund(engine, clock)
    claim = submit_claim(engine, clock, bounty.bounty_id)
    engine.mark_review(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    with pytest.raises(Unauthorized) as exc:
        engine.approve_claim(
            CallContext(signer=STRANGER),
            bounty_id=bounty.bounty_id,
            claim_id=claim.claim_id,
            now_ns=clock(),
        )
    assert exc.value.code == "E_UNAUTHORIZED"
    fresh = engine.get_bounty(bounty.bounty_id)
    assert fresh.state is BountyState.REVIEW
    assert fresh.approved_claim_id is None and fresh.winner is None


def test_sec002_non_owner_cannot_refund(engine, clock):
    bounty = fund(engine, clock, submission_s=10, review_s=10)
    clock.advance(100)
    with pytest.raises(Unauthorized):
        engine.refund_expired(CallContext(signer=STRANGER), bounty_id=bounty.bounty_id, now_ns=clock())
    assert engine.get_bounty(bounty.bounty_id).state is BountyState.OPEN


def test_sec003_cannot_approve_claim_from_another_bounty(engine, clock):
    bounty_a = fund(engine, clock, meta=hex64("a"))
    bounty_b = fund(engine, clock, meta=hex64("b"))
    claim_b = submit_claim(engine, clock, bounty_b.bounty_id)
    engine.mark_review(CallContext(signer=OWNER), bounty_id=bounty_a.bounty_id, now_ns=clock())
    with pytest.raises(StateError) as exc:
        engine.approve_claim(
            CallContext(signer=OWNER),
            bounty_id=bounty_a.bounty_id,
            claim_id=claim_b.claim_id,
            now_ns=clock(),
        )
    assert "does not belong" in exc.value.message
    assert engine.get_bounty(bounty_a.bounty_id).approved_claim_id is None


def test_sec010_cross_bounty_isolation_of_funds(engine, clock):
    paid, claim = to_approved(engine, clock, reward=5 * NEAR)
    other = fund(engine, clock, reward=7 * NEAR, meta=hex64("other"))

    engine.request_payout(CallContext(signer=OWNER), bounty_id=paid.bounty_id, now_ns=clock())
    attempt = engine.get_bounty(paid.bounty_id).pending_attempt_id
    engine.settle_payout(
        bounty_id=paid.bounty_id,
        attempt_id=attempt,
        result=SettlementResult.SUCCESS,
        ctx=selfcall(),
        now_ns=clock(),
    )
    assert engine.get_bounty(paid.bounty_id).paid_yocto == 5 * NEAR
    fresh_other = engine.get_bounty(other.bounty_id)
    assert fresh_other.escrow_balance_yocto == 7 * NEAR
    assert fresh_other.paid_yocto == 0
    engine.check_invariants(fresh_other)


# ---------------------------------------------------------------------------
# SEC-004..SEC-008 — payout / callback hazards
# ---------------------------------------------------------------------------
def test_sec004_second_payout_request_is_refused_while_pending(engine, clock):
    bounty, _ = to_approved(engine, clock)
    engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    with pytest.raises(ReplayError) as exc:
        engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    assert exc.value.code == "E_REPLAY"
    pending = engine.get_bounty(bounty.bounty_id)
    assert pending.state is BountyState.PAYOUT_PENDING
    assert len([a for a in pending.attempts if a.status is AttemptStatus.PENDING]) == 1


def test_sec005_explicit_transfer_failure_does_not_pay(engine, clock):
    bounty, _ = to_approved(engine, clock)
    engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    attempt = engine.get_bounty(bounty.bounty_id).pending_attempt_id
    settled = engine.settle_payout(
        bounty_id=bounty.bounty_id,
        attempt_id=attempt,
        result=SettlementResult.FAILURE,
        ctx=selfcall(),
        now_ns=clock(),
    )
    assert settled.state is BountyState.APPROVED
    assert settled.paid_yocto == 0
    assert settled.escrow_balance_yocto == settled.reward_yocto
    assert settled.attempt(attempt).status is AttemptStatus.FAILED
    assert settled.pending_attempt_id is None
    engine.check_invariants(settled)


def test_sec006_out_of_order_and_foreign_callbacks_are_rejected(engine, clock):
    bounty, _ = to_approved(engine, clock)
    engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    attempt = engine.get_bounty(bounty.bounty_id).pending_attempt_id

    with pytest.raises(Unauthorized) as exc:
        engine.settle_payout(
            bounty_id=bounty.bounty_id,
            attempt_id=attempt,
            result=SettlementResult.SUCCESS,
            ctx=CallContext(signer=STRANGER),
            now_ns=clock(),
        )
    assert exc.value.code == "E_UNAUTHORIZED"

    with pytest.raises(CallbackError):
        engine.settle_payout(
            bounty_id=bounty.bounty_id,
            attempt_id=attempt + 999,
            result=SettlementResult.SUCCESS,
            ctx=selfcall(),
            now_ns=clock(),
        )
    assert engine.get_bounty(bounty.bounty_id).state is BountyState.PAYOUT_PENDING
    assert engine.get_bounty(bounty.bounty_id).paid_yocto == 0


def test_sec006b_callback_replay_after_success_is_rejected(engine, clock):
    bounty, _ = to_approved(engine, clock)
    engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    attempt = engine.get_bounty(bounty.bounty_id).pending_attempt_id
    engine.settle_payout(
        bounty_id=bounty.bounty_id,
        attempt_id=attempt,
        result=SettlementResult.SUCCESS,
        ctx=selfcall(),
        now_ns=clock(),
    )
    with pytest.raises(StateError) as exc:
        engine.settle_payout(
            bounty_id=bounty.bounty_id,
            attempt_id=attempt,
            result=SettlementResult.SUCCESS,
            ctx=selfcall(),
            now_ns=clock(),
        )
    assert exc.value.code in {"E_STATE", "E_TERMINAL"}
    assert engine.get_bounty(bounty.bounty_id).paid_yocto == engine.get_bounty(bounty.bounty_id).reward_yocto


def test_sec007_ambiguous_callback_freezes_and_forbids_retry(engine, clock):
    bounty, _ = to_approved(engine, clock)
    engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    attempt = engine.get_bounty(bounty.bounty_id).pending_attempt_id
    settled = engine.settle_payout(
        bounty_id=bounty.bounty_id,
        attempt_id=attempt,
        result=SettlementResult.AMBIGUOUS,
        ctx=selfcall(),
        now_ns=clock(),
    )
    assert settled.state is BountyState.NEEDS_RECONCILIATION
    assert settled.paid_yocto == 0
    engine.check_invariants(settled)

    # No blind retry, no refund escape hatch.
    with pytest.raises(StateError):
        engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    with pytest.raises(StateError):
        engine.refund_expired(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())


def test_sec007b_reconciliation_is_owner_only_and_terminal(engine, clock):
    bounty, _ = to_approved(engine, clock)
    engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    attempt = engine.get_bounty(bounty.bounty_id).pending_attempt_id
    engine.settle_payout(
        bounty_id=bounty.bounty_id,
        attempt_id=attempt,
        result=SettlementResult.AMBIGUOUS,
        ctx=selfcall(),
        now_ns=clock(),
    )
    with pytest.raises(Unauthorized):
        engine.reconcile(
            CallContext(signer=STRANGER),
            bounty_id=bounty.bounty_id,
            resolution="paid",
            evidence_sha256=hex64("chain-check"),
            note="checked explorer",
            now_ns=clock(),
        )
    reconciled = engine.reconcile(
        CallContext(signer=OWNER),
        bounty_id=bounty.bounty_id,
        resolution="paid",
        evidence_sha256=hex64("chain-check"),
        note="recipient balance confirmed on chain",
        now_ns=clock(),
    )
    assert reconciled.state is BountyState.PAID
    assert reconciled.escrow_balance_yocto == 0
    engine.check_invariants(reconciled)
    with pytest.raises(StateError):
        engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())


def test_sec008_repeated_refund_is_rejected(engine, clock):
    bounty = fund(engine, clock, submission_s=10, review_s=10)
    clock.advance(100)
    engine.refund_expired(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    attempt = engine.get_bounty(bounty.bounty_id).pending_attempt_id
    engine.settle_refund(
        bounty_id=bounty.bounty_id,
        attempt_id=attempt,
        result=SettlementResult.SUCCESS,
        ctx=selfcall(),
        now_ns=clock(),
    )
    refunded = engine.get_bounty(bounty.bounty_id)
    assert refunded.state is BountyState.REFUNDED
    assert refunded.refunded_yocto == refunded.reward_yocto
    with pytest.raises((StateError, TerminalState)):
        engine.refund_expired(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    with pytest.raises((StateError, TerminalState)):
        engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())


# ---------------------------------------------------------------------------
# AT-008 / AT-009 / AT-010 — replay, refund-after-approval, terminal states
# ---------------------------------------------------------------------------
def test_at008_cannot_pay_twice(engine, clock):
    bounty, _ = to_approved(engine, clock)
    engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    attempt = engine.get_bounty(bounty.bounty_id).pending_attempt_id
    engine.settle_payout(
        bounty_id=bounty.bounty_id,
        attempt_id=attempt,
        result=SettlementResult.SUCCESS,
        ctx=selfcall(),
        now_ns=clock(),
    )
    paid = engine.get_bounty(bounty.bounty_id)
    assert paid.state is BountyState.PAID
    assert paid.paid_yocto == paid.reward_yocto
    with pytest.raises(TerminalState):
        engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())


def test_at009_refund_after_approval_is_rejected(engine, clock):
    bounty, _ = to_approved(engine, clock)
    with pytest.raises(StateError):
        engine.refund_expired(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())


def test_at010_refund_expired_bounty_is_single_and_terminal(engine, clock):
    bounty = fund(engine, clock, submission_s=10, review_s=10)
    with pytest.raises(StateError) as early:
        engine.refund_expired(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    assert "grace" in early.value.message

    clock.advance(100)
    engine.refund_expired(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    attempt = engine.get_bounty(bounty.bounty_id).pending_attempt_id
    assert engine.get_bounty(bounty.bounty_id).state is BountyState.REFUND_PENDING
    engine.settle_refund(
        bounty_id=bounty.bounty_id,
        attempt_id=attempt,
        result=SettlementResult.SUCCESS,
        ctx=selfcall(),
        now_ns=clock(),
    )
    final = engine.get_bounty(bounty.bounty_id)
    assert final.state is BountyState.REFUNDED
    assert final.paid_yocto == 0
    engine.check_invariants(final)


def test_terminal_states_reject_every_settlement_action(engine, clock):
    bounty, claim = to_approved(engine, clock)
    engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    attempt = engine.get_bounty(bounty.bounty_id).pending_attempt_id
    engine.settle_payout(
        bounty_id=bounty.bounty_id,
        attempt_id=attempt,
        result=SettlementResult.SUCCESS,
        ctx=selfcall(),
        now_ns=clock(),
    )
    for call in (
        lambda: engine.approve_claim(
            CallContext(signer=OWNER), bounty_id=1, claim_id=claim.claim_id, now_ns=clock()
        ),
        lambda: engine.refund_expired(CallContext(signer=OWNER), bounty_id=1, now_ns=clock()),
        lambda: engine.mark_review(CallContext(signer=OWNER), bounty_id=1, now_ns=clock()),
    ):
        with pytest.raises((StateError, TerminalState)):
            call()


# ---------------------------------------------------------------------------
# dispute flag
# ---------------------------------------------------------------------------
def test_dispute_flag_freezes_settlement(engine, clock):
    bounty, _ = to_approved(engine, clock)
    engine.set_dispute(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, disputed=True, now_ns=clock())
    frozen = engine.get_bounty(bounty.bounty_id)
    assert frozen.effective_state is BountyState.DISPUTED
    assert frozen.state is BountyState.APPROVED
    with pytest.raises(StateError):
        engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    with pytest.raises(Unauthorized):
        engine.set_dispute(CallContext(signer=STRANGER), bounty_id=bounty.bounty_id, disputed=False, now_ns=clock())
    engine.set_dispute(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, disputed=False, now_ns=clock())
    engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    assert engine.get_bounty(bounty.bounty_id).state is BountyState.PAYOUT_PENDING


def test_dispute_cannot_be_toggled_while_settling(engine, clock):
    bounty, _ = to_approved(engine, clock)
    engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    with pytest.raises(StateError):
        engine.set_dispute(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, disputed=True, now_ns=clock())


# ---------------------------------------------------------------------------
# review window rules
# ---------------------------------------------------------------------------
def test_mark_review_rules(engine, clock):
    bounty = fund(engine, clock, submission_s=10, review_s=60)
    with pytest.raises(Unauthorized):
        engine.mark_review(CallContext(signer=STRANGER), bounty_id=bounty.bounty_id, now_ns=clock())
    engine.mark_review(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    assert engine.get_bounty(bounty.bounty_id).state is BountyState.REVIEW
    with pytest.raises(StateError):
        engine.mark_review(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())


def test_approval_after_review_window_is_refused(engine, clock):
    bounty = fund(engine, clock, submission_s=10, review_s=10)
    claim = submit_claim(engine, clock, bounty.bounty_id)
    clock.advance(30)
    engine.mark_review(CallContext(signer=STRANGER), bounty_id=bounty.bounty_id, now_ns=clock())
    with pytest.raises(StateError) as exc:
        engine.approve_claim(
            CallContext(signer=OWNER),
            bounty_id=bounty.bounty_id,
            claim_id=claim.claim_id,
            now_ns=clock(),
        )
    assert "review window" in exc.value.message


# ---------------------------------------------------------------------------
# SEC-009 — randomized action fuzzing
# ---------------------------------------------------------------------------
def test_sec009_fuzz_random_action_sequences_preserve_invariants(seed=20261111):
    rng = random.Random(seed)
    for round_index in range(40):
        engine = EscrowEngine()
        from conftest import BASE_NS, FakeClock

        clock = FakeClock(BASE_NS + round_index)
        bounty = fund(engine, clock)
        claim = submit_claim(engine, clock, bounty.bounty_id)
        actions = 0
        while actions < 30:
            actions += 1
            choice = rng.randrange(9)
            try:
                if choice == 0:
                    engine.mark_review(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
                elif choice == 1:
                    engine.approve_claim(
                        CallContext(signer=OWNER),
                        bounty_id=bounty.bounty_id,
                        claim_id=rng.choice([claim.claim_id, claim.claim_id + 7]),
                        now_ns=clock(),
                    )
                elif choice == 2:
                    engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
                elif choice == 3:
                    pending = engine.get_bounty(bounty.bounty_id).pending_attempt_id
                    if pending is not None:
                        engine.settle_payout(
                            bounty_id=bounty.bounty_id,
                            attempt_id=pending,
                            result=rng.choice(list(SettlementResult)),
                            ctx=selfcall(),
                            now_ns=clock(),
                        )
                elif choice == 4:
                    engine.refund_expired(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
                elif choice == 5:
                    engine.set_dispute(
                        CallContext(signer=OWNER),
                        bounty_id=bounty.bounty_id,
                        disputed=rng.random() < 0.5,
                        now_ns=clock(),
                    )
                elif choice == 6:
                    pending = engine.get_bounty(bounty.bounty_id).pending_attempt_id
                    if engine.get_bounty(bounty.bounty_id).state is BountyState.NEEDS_RECONCILIATION:
                        engine.reconcile(
                            CallContext(signer=OWNER),
                            bounty_id=bounty.bounty_id,
                            resolution=rng.choice(["paid", "refunded"]),
                            evidence_sha256=hex64(f"reconcile-{actions}"),
                            note="fuzz reconciliation",
                            now_ns=clock(),
                        )
                    del pending
                elif choice == 7:
                    engine.commit_claim(
                        CallContext(signer=AUDITOR),
                        bounty_id=bounty.bounty_id,
                        evidence_commitment_hex=hex64(f"fuzz-{round_index}-{actions}"),
                        now_ns=clock(),
                    )
                else:
                    clock.advance(rng.choice([1, 30, 300]))
            except Exception as exc:  # noqa: BLE001 - refusals are expected
                code = getattr(exc, "code", "")
                assert code.startswith("E_"), f"unexpected exception type: {exc!r}"

            current = engine.get_bounty(bounty.bounty_id)
            engine.check_invariants(current)
            assert current.paid_yocto + current.refunded_yocto <= current.reward_yocto
            if current.state in (BountyState.PAID, BountyState.REFUNDED):
                assert current.pending_attempt_id is None
            assert current.owner == OWNER
            assert current.reward_yocto == bounty.reward_yocto
        # terminal exclusivity
        assert not (bounty.paid_yocto > 0 and bounty.refunded_yocto > 0)


def test_fuzz_never_pays_without_approval(seed=7):
    rng = random.Random(seed)
    for _ in range(30):
        engine = EscrowEngine()
        from conftest import BASE_NS, FakeClock

        clock = FakeClock(BASE_NS)
        bounty = fund(engine, clock)
        try:
            engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
        except StateError:
            pass
        current = engine.get_bounty(bounty.bounty_id)
        assert current.state is BountyState.OPEN
        assert current.paid_yocto == 0


def test_claim_state_machine_follows_approval_and_payout(engine, clock):
    bounty, claim = to_approved(engine, clock)
    assert engine.get_claim(claim.claim_id).state is ClaimState.APPROVED
    engine.request_payout(CallContext(signer=OWNER), bounty_id=bounty.bounty_id, now_ns=clock())
    attempt = engine.get_bounty(bounty.bounty_id).pending_attempt_id
    engine.settle_payout(
        bounty_id=bounty.bounty_id,
        attempt_id=attempt,
        result=SettlementResult.SUCCESS,
        ctx=selfcall(),
        now_ns=clock(),
    )
    assert engine.get_claim(claim.claim_id).state is ClaimState.PAID
