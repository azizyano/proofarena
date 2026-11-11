"""Adverse path on NEAR testnet: an expired bounty with no accepted claim is refunded.

    python scripts/testnet/refund_demo.py --reward-near 0.02 --window 30

Creates a bounty with a very short submission + review window, lets it expire without
a claim, then has the owner sign ``refund_expired``. The contract moves to
``RefundPending`` and its own callback settles to ``Refunded`` — the mirror image of
the payout path, on the real chain.

Kept short deliberately: the whole run is about a minute plus finality.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # legacy Windows consoles
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

from smoke import EXPLORER, Api, Wallet  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="On-chain refund demo")
    parser.add_argument("--api", default="http://127.0.0.1:8787")
    parser.add_argument("--signer", default="http://127.0.0.1:8788")
    parser.add_argument("--reward-near", type=float, default=0.02)
    parser.add_argument("--window", type=int, default=30, help="submission and review window (s)")
    parser.add_argument("--poll-timeout", type=float, default=90.0)
    args = parser.parse_args(argv)

    api = Api(args.api)
    wallet = Wallet(args.signer)
    account = wallet.status["account"]
    contract = wallet.status["contract_id"]

    meta = api.get("/api/meta")
    if meta.get("chain_mode") != "near-testnet":
        raise SystemExit(f"API is in {meta.get('chain_mode')!r}; start it with PROOFARENA_CHAIN_MODE=near-testnet")

    reward = Decimal(str(args.reward_near)) * Decimal(10) ** 24
    reward_yocto = int(reward)
    if reward != reward_yocto:
        raise SystemExit("reward must be an exact multiple of 1e-24 NEAR")

    print("=" * 72)
    print("ProofArena - refund path on NEAR testnet")
    print(f"owner={account}  contract={contract}  reward={args.reward_near} NEAR")
    print("=" * 72)

    print("\n[1] authenticate")
    wallet.register(api)
    challenge = api.post("/auth/challenge", {"account": account})
    api.token = api.post(
        "/auth/verify", {"message": challenge["message"], "signature": wallet.sign(challenge["message"])}
    )["token"]
    print(f"    session for {account}")

    print(f"\n[2] create + fund a {args.window}s bounty (no claim will be submitted)")
    created = api.post(
        "/v1/challenges",
        {
            "title": "Expired bounty refund demo",
            "repo_url": "fixture/baseline (authorized synthetic fixture)",
            "source_commit": "2" * 40,
            "scope": {"authorization": "synthetic fixture", "allowed_files": ["ledger.py"]},
            "reward_yocto": str(reward_yocto),
            "submission_seconds": args.window,
            "review_seconds": args.window,
        },
    )
    challenge_id = created["challenge"]["id"]
    metadata_hash = created["metadata_hash_hex"]
    intent = api.post(
        "/v1/intents",
        {"action": "fund_bounty", "payload": {"challenge_id": challenge_id, "metadata_hash_hex": metadata_hash}},
    )
    funded = api.post(
        f"/v1/challenges/{challenge_id}/fund",
        {"message": intent["message"], "signature": wallet.sign(intent["message"])},
    )
    tx = funded["receipts"][0]["receipt_id"]
    print(f"    challenge {challenge_id}  bounty #{funded['bounty']['bounty_id']}  state={funded['bounty']['state']}")
    print(f"    create_bounty {tx}")
    print(f"    {EXPLORER}/{tx}")

    print("\n[3] a refund before the window closes must be refused")
    intent = api.post("/v1/intents", {"action": "refund_expired", "payload": {"challenge_id": challenge_id}})
    try:
        api.post(
            f"/v1/challenges/{challenge_id}/refund",
            {"message": intent["message"], "signature": wallet.sign(intent["message"])},
        )
        print("    ERROR: early refund was accepted")
        return 2
    except RuntimeError as exc:
        print(f"    refused as expected: {str(exc)[:150]}")

    wait_s = args.window * 2 + 8
    print(f"\n[4] waiting {wait_s}s for the submission + review windows to expire")
    for remaining in range(wait_s, 0, -5):
        print(f"    {remaining}s…", flush=True)
        time.sleep(min(5, remaining))

    print("\n[5] owner signs refund_expired (transaction)")
    intent = api.post("/v1/intents", {"action": "refund_expired", "payload": {"challenge_id": challenge_id}})
    refund = api.post(
        f"/v1/challenges/{challenge_id}/refund",
        {"message": intent["message"], "signature": wallet.sign(intent["message"])},
    )
    receipts_now = refund.get("receipts") or []
    tx = receipts_now[0]["receipt_id"] if receipts_now else "(no receipt returned)"
    print(f"    refund_requested {tx}")
    if receipts_now:
        print(f"    {EXPLORER}/{tx}")
    print(f"    state={refund['state']}")

    print("\n[6] waiting for the contract callback to settle the refund")
    deadline = time.time() + args.poll_timeout
    final = None
    while time.time() < deadline:
        final = api.get(f"/v1/challenges/{challenge_id}")["challenge"]
        if final["state"] in {"Refunded", "NeedsReconciliation"}:
            break
        time.sleep(2)
    print(f"    state={final['state']} refunded={final['chain']['refunded_yocto']} escrow={final['chain']['escrow_balance_yocto']}")

    if final["state"] != "Refunded":
        print("\nNot settled yet - check the explorer before doing anything else.")
        return 3

    receipts = api.get(f"/v1/challenges/{challenge_id}/receipts")["receipts"]
    print("\nRESULT: refund path verified on chain")
    print(f"  challenge : {challenge_id}")
    print(f"  refunded  : {int(final['chain']['refunded_yocto']) / 10**24} NEAR")
    for receipt in receipts:
        print(f"  {receipt['kind']:<18} {receipt['receipt_id']}")
    summary = {
        "challenge_id": challenge_id,
        "final_state": final["state"],
        "refunded_yocto": final["chain"]["refunded_yocto"],
        "transactions": [{"kind": r["kind"], "tx_hash": r["receipt_id"]} for r in receipts],
    }
    out = ROOT / "evidence" / "testnet-smoke" / f"refund-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nsummary written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
