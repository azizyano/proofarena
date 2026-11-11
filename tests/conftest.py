"""Shared pytest fixtures.

A controllable clock is used everywhere so deadline behaviour is deterministic
and tests never sleep.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TMP_ROOT = ROOT / ".pytest_tmp"

from proofarena.canonical import sha256_text  # noqa: E402
from proofarena.chain.engine import ChainState, EscrowEngine, EscrowPolicy  # noqa: E402
from proofarena.chain.ledger import LocalLedger  # noqa: E402

BASE_NS = 1_700_000_000 * 10**9
SEC = 10**9
NEAR = 10**24
OWNER = "owner.demo.local"
AUDITOR = "auditor.agent.local"
DEFENDER = "defender.agent.local"
STRANGER = "stranger.demo.local"
CONTRACT = "escrow.proofarena.local"


class FakeClock:
    def __init__(self, start_ns: int = BASE_NS) -> None:
        self.value = start_ns

    def __call__(self) -> int:
        return self.value

    def advance(self, seconds: float) -> int:
        self.value += int(seconds * SEC)
        return self.value


@pytest.fixture()
def workdir() -> Path:
    """Fresh per-test directory under the repo (sandbox-safe; never recycled)."""
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TMP_ROOT / f"case-{uuid.uuid4().hex[:12]}"
    path.mkdir()
    return path


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def engine(clock: FakeClock) -> EscrowEngine:
    return EscrowEngine(ChainState(), EscrowPolicy())


@pytest.fixture()
def ledger(workdir: Path, clock: FakeClock) -> LocalLedger:
    return LocalLedger(
        workdir / "chain.sqlite3",
        contract_id=CONTRACT,
        network="local-ledger",
        clock=clock,
    )


def hex64(seed: str) -> str:
    return sha256_text(seed)


def fund(
    engine: EscrowEngine,
    clock: FakeClock,
    *,
    owner: str = OWNER,
    reward: int = 5 * NEAR,
    submission_s: int = 60,
    review_s: int = 120,
    title: str = "Allowance ledger invariant challenge",
    meta: str | None = None,
    attached: int | None = None,
):
    from proofarena.chain.models import CallContext

    return engine.create_bounty(
        CallContext(
            signer=owner,
            attached_yocto=attached if attached is not None else reward + EscrowPolicy().storage_fee_yocto,
        ),
        title=title,
        reward_yocto=reward,
        metadata_hash_hex=meta or hex64(f"meta-{owner}-{reward}"),
        deadline_ns=clock() + submission_s * SEC,
        review_deadline_ns=clock() + (submission_s + review_s) * SEC,
        now_ns=clock(),
    )


def submit_claim(engine: EscrowEngine, clock: FakeClock, bounty_id: int, *, who: str = AUDITOR, commitment: str | None = None):
    from proofarena.chain.models import CallContext

    return engine.commit_claim(
        CallContext(signer=who),
        bounty_id=bounty_id,
        evidence_commitment_hex=commitment or hex64(f"evidence-{bounty_id}-{who}"),
        now_ns=clock(),
    )


def to_approved(engine: EscrowEngine, clock: FakeClock, *, reward: int = 5 * NEAR):
    """Drive a fresh bounty to Approved with one claim and return (bounty, claim)."""
    from proofarena.chain.models import CallContext

    bounty = fund(engine, clock, reward=reward)
    claim = submit_claim(engine, clock, bounty.bounty_id)
    clock.advance(61)
    engine.mark_review(CallContext(signer=STRANGER), bounty_id=bounty.bounty_id, now_ns=clock())
    engine.approve_claim(
        CallContext(signer=OWNER),
        bounty_id=bounty.bounty_id,
        claim_id=claim.claim_id,
        now_ns=clock(),
    )
    return bounty, claim


@pytest.fixture(autouse=True)
def _isolated_data_dir(workdir: Path, monkeypatch: pytest.MonkeyPatch):
    """Never let a test touch the developer's real local database."""
    monkeypatch.setenv("PROOFARENA_DATA_DIR", str(workdir / "data"))
    monkeypatch.setenv("PROOFARENA_DB", str(workdir / "data" / "proofarena.db"))
    yield
