"""Replay real NEAR testnet transactions through the wallet-verification logic.

Read-only: no keys, no transactions sent. Proves that the code the browser flow
depends on accepts genuine wallet-sent transactions and rejects tampered plans.

    python scripts/testnet/verify_wallet_txs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # legacy Windows consoles
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

from proofarena.errors import StateError
from proofarena.near.rpc import NearRpc
from proofarena.walletflow import TxPlan, verify_wallet_tx

CONTRACT = "proofarena-c23922.testnet"
OWNER = "coldlemon3275.testnet"
AUDITOR = "proofarena-auditor-0fbc4e.testnet"

# Transactions from the verified run (docs/TESTNET_EVIDENCE.md)
CASES = [
    (
        "approve_claim",
        "DfzioifsafKGibYi55Dh6Pijq6Be9XvWfR4sko2tDzQf",
        {"bounty_id": 3, "claim_id": 2},
        0,
    ),
    (
        "request_payout",
        "AVwQZ4mdAmSmw6YiKS34FNn1mdd3YgKFX5tW5unXkRap",
        {"bounty_id": 3},
        0,
    ),
    (
        "refund_expired",
        "H5eGNidaraf6akVhzggE7hNJzbaUynTGgptFVoZRe5bZ",
        {"bounty_id": 4},
        0,
    ),
]

# create_bounty carries the deposit, so it is checked separately below.
CREATE_HASH = "4WzDB89VMoJfQ5QGjS2zcsVR8zhySRQKQDN9MNSpg5ba"


def main() -> int:
    rpc = NearRpc("https://rpc.testnet.near.org")
    failures = 0

    print("=" * 74)
    print("Wallet-transaction verification against real NEAR testnet transactions")
    print("=" * 74)

    for action, tx_hash, args, deposit in CASES:
        plan = TxPlan(action, CONTRACT, action, args, deposit, 100_000_000_000_000)
        try:
            result = verify_wallet_tx(
                rpc,
                tx_hash=tx_hash,
                contract_id=CONTRACT,
                signer_account=OWNER,
                plan=plan,
            )
            status = result.get("final_execution_status")
            print(f"  [OK]   {action:<16} {tx_hash[:16]}…  final={status}")
        except StateError as exc:
            failures += 1
            print(f"  [FAIL] {action:<16} {tx_hash[:16]}…  {exc}")

    # A tampered plan must be refused even though the transaction is real.
    plan = TxPlan("request_payout", CONTRACT, "request_payout", {"bounty_id": 999}, 0, 100_000_000_000_000)
    try:
        verify_wallet_tx(
            rpc,
            tx_hash=CASES[1][1],
            contract_id=CONTRACT,
            signer_account=OWNER,
            plan=plan,
        )
        failures += 1
        print("  [FAIL] tampered plan was ACCEPTED (should be refused)")
    except StateError as exc:
        print(f"  [OK]   tampered plan refused: {str(exc)[:70]}")

    # Wrong signer claim must be refused too.
    plan = TxPlan("approve_claim", CONTRACT, "approve_claim", {"bounty_id": 3, "claim_id": 2}, 0, 100_000_000_000_000)
    try:
        verify_wallet_tx(
            rpc,
            tx_hash=CASES[0][1],
            contract_id=CONTRACT,
            signer_account=AUDITOR,
            plan=plan,
        )
        failures += 1
        print("  [FAIL] wrong signer was ACCEPTED (should be refused)")
    except StateError as exc:
        print(f"  [OK]   wrong signer refused: {str(exc)[:70]}")

    # Deposit-bearing create_bounty: verify the deposit is enforced.
    create_args = {
        "title": "Allowance ledger invariant challenge",
        "metadata_hash_hex": "",
        "reward_yocto": "100000000000000000000000",
        "deadline_ns": 0,
        "review_deadline_ns": 0,
    }
    result = rpc.tx_status(CREATE_HASH, OWNER)
    fc = result["transaction"]["actions"][0]["FunctionCall"]
    import base64
    import json

    sent = json.loads(base64.b64decode(fc["args"]))
    create_args.update({k: sent[k] for k in ("metadata_hash_hex", "deadline_ns", "review_deadline_ns")})
    plan = TxPlan("fund_bounty", CONTRACT, "create_bounty", create_args, int(fc["deposit"]), 100_000_000_000_000)
    try:
        verify_wallet_tx(
            rpc, tx_hash=CREATE_HASH, contract_id=CONTRACT, signer_account=OWNER, plan=plan
        )
        print(f"  [OK]   create_bounty     {CREATE_HASH[:16]}…  deposit={int(fc['deposit'])/10**24} NEAR")
    except StateError as exc:
        failures += 1
        print(f"  [FAIL] create_bounty     {CREATE_HASH[:16]}…  {exc}")

    print("=" * 74)
    print("failures:", failures)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
