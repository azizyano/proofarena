"""Chain package: domain models, escrow state machine and adapters.

Two adapters implement the same surface:

* :class:`~proofarena.chain.ledger.LocalLedger` — SQLite simulator, receipt hash
  chain, no network. Used for tests, CI and the offline demo.
* :class:`~proofarena.chain.near_testnet.NearTestnetAdapter` — real NEAR testnet
  transactions signed by the owner's key, state read back over JSON-RPC.

The rest of the application cannot tell them apart; that is the point.
"""

from .engine import ChainState, EscrowEngine, EscrowPolicy
from .ledger import ChainAdapter, LocalLedger, open_chain
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
)

__all__ = [
    "Attempt",
    "AttemptKind",
    "AttemptStatus",
    "Bounty",
    "BountyState",
    "CallContext",
    "ChainAdapter",
    "ChainState",
    "Claim",
    "ClaimState",
    "EscrowEngine",
    "EscrowPolicy",
    "LocalLedger",
    "SettlementResult",
    "open_chain",
]
