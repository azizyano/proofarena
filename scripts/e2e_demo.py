"""End-to-end demonstration: owner funds -> auditor proves -> defender patches ->
independent verifier re-runs -> owner signs approval -> escrow settles exactly once.

    python scripts/e2e_demo.py

What is real in this run:

* the Ed25519 signatures over canonical intents (a key is generated per run),
* the runner subprocess that actually executes the failing and patched tests,
* the patch application onto the pinned baseline,
* the escrow state machine and its hash-chained receipts,
* every HTTP call through the ASGI application.

What is **simulated**: the chain itself. Receipts are prefixed ``local-`` and the
network label is ``local-ledger``. There is no NEAR testnet transaction here and
the script never claims one.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from starlette.testclient import TestClient

from proofarena.api import create_app
from proofarena.canonical import b64encode, sha256_text
from proofarena.patching import make_unified_diff
from proofarena.runner import SandboxSpec, materialize, run_pytest
from proofarena.schemas import validate_claim_manifest

OWNER = "owner.demo.local"
DOMAIN = "proofarena.local"
NETWORK = "local-ledger"
CONTRACT = "escrow.proofarena.local"
SOURCE_COMMIT = "1" * 40
REWARD_NEAR = 5
FIXTURE = ROOT / "fixture"


class Owner:
    def __init__(self, account: str, key_path: Path) -> None:
        self.account = account
        self.key_path = key_path
        if key_path.exists():
            raw = json.loads(key_path.read_text(encoding="utf-8"))
            import base64

            self.key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw["private_key_b64"]))
        else:
            self.key = Ed25519PrivateKey.generate()
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_text(
                json.dumps(
                    {
                        "account": account,
                        "private_key_b64": b64encode(
                            self.key.private_bytes(
                                encoding=Encoding.Raw,
                                format=PrivateFormat.Raw,
                                encryption_algorithm=NoEncryption(),
                            )
                        ),
                        "warning": "EPHEMERAL DEMO KEY. Never use for real funds.",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

    @property
    def public_key_b64(self) -> str:
        return b64encode(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    def sign(self, message: str) -> str:
        return b64encode(self.key.sign(message.encode("utf-8")))


def banner(text: str) -> None:
    print()
    print("=" * 78)
    print(text)
    print("=" * 78)


def step(number: int, text: str) -> None:
    print(f"\n[{number}] {text}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ProofArena end-to-end local demo")
    parser.add_argument("--data-dir", default=None, help="run directory (default: evidence/demo/<utc>)")
    parser.add_argument("--keep", action="store_true", help="keep the run directory even on failure")
    args = parser.parse_args(argv)

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = Path(args.data_dir) if args.data_dir else ROOT / "evidence" / "demo" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    banner("ProofArena — authorized bounty lifecycle (LOCAL CHAIN SIMULATOR, no real funds)")
    print(f"run directory : {run_dir}")
    print("chain         : local-ledger  (NOT NEAR testnet)")
    print("reward        : 5 NEAR-equivalent local units")

    app = create_app(
        db_path=run_dir / "app.db",
        chain_db_path=run_dir / "chain.db",
        artifacts_dir=run_dir / "artifacts",
        fixture_root=FIXTURE,
        settlement_mode="manual",
        session_secret=secrets.token_bytes(32),
        domain=DOMAIN,
        network=NETWORK,
        contract_id=CONTRACT,
        include_web=True,
    )
    client = TestClient(app)
    owner = Owner(OWNER, run_dir / "owner.key.json")

    health = client.get("/health").json()
    print(f"health        : {health['status']} (chain {health['chain']['mode']}, "
          f"receipts {health['chain']['receipts']})")

    # ---------------------------------------------------------------- auth
    step(1, "Owner registers an Ed25519 wallet key and signs a login challenge")
    auth = app.state.pa.auth
    message = auth.registration_message(owner.account, owner.public_key_b64, secrets.token_hex(16))
    registered = client.post(
        "/auth/register",
        json={
            "account": owner.account,
            "public_key_b64": owner.public_key_b64,
            "nonce": json.loads(message)["nonce"],
            "signature": owner.sign(message),
        },
    )
    assert registered.status_code == 200, registered.text
    challenge = client.post("/auth/challenge", json={"account": owner.account}).json()
    verified = client.post(
        "/auth/verify",
        json={"message": challenge["message"], "signature": owner.sign(challenge["message"])},
    )
    assert verified.status_code == 200, verified.text
    headers = {"Authorization": f"Bearer {verified.json()['token']}"}
    print(f"    login ok, session issued for {verified.json()['account']}")

    # ------------------------------------------------------------- create
    step(2, "Owner creates the challenge manifest and its digest")
    created = client.post(
        "/v1/challenges",
        headers=headers,
        json={
            "title": "Allowance ledger invariant challenge",
            "repo_url": "fixture/baseline (authorized synthetic fixture)",
            "source_commit": SOURCE_COMMIT,
            "scope": {
                "authorization": "synthetic fixture owned by the bounty owner",
                "allowed_files": ["ledger.py"],
                "allowed_commands": ["pytest fixture/tests"],
                "forbidden": ["network", "real targets", "funds"],
            },
            "reward_yocto": str(REWARD_NEAR * 10**24),
            "submission_seconds": 3600,
            "review_seconds": 3600,
        },
    )
    assert created.status_code == 201, created.text
    challenge = created.json()["challenge"]
    challenge_id = challenge["id"]
    metadata_hash = created.json()["metadata_hash_hex"]
    print(f"    challenge     : {challenge_id}")
    print(f"    metadata hash : {metadata_hash}")

    step(3, "Owner signs a fund_bounty intent; escrow is funded")
    intent = auth.issue_intent(
        account=owner.account,
        action="fund_bounty",
        payload={"challenge_id": challenge_id, "metadata_hash_hex": metadata_hash},
    )
    funded = client.post(
        f"/v1/challenges/{challenge_id}/fund",
        headers=headers,
        json={"message": intent["message"], "signature": owner.sign(intent["message"])},
    )
    assert funded.status_code == 201, funded.text
    bounty = funded.json()["bounty"]
    bounty_id = bounty["bounty_id"]
    print(f"    bounty #{bounty_id} state={bounty['state']} escrow={bounty['escrow_balance_yocto']}")
    print(f"    receipt: {funded.json()['receipts'][0]['receipt_id']}")

    # ------------------------------------------------------------- agents
    step(4, "Owner issues scoped agent tokens (auditor, defender)")
    auditor_token = client.post(
        f"/v1/challenges/{challenge_id}/agent-token",
        headers=headers,
        json={"role": "auditor", "agent_account": "auditor.agent.local"},
    ).json()["token"]
    defender_token = client.post(
        f"/v1/challenges/{challenge_id}/agent-token",
        headers=headers,
        json={"role": "defender", "agent_account": "defender.agent.local"},
    ).json()["token"]
    print("    auditor token : issued (hash stored only)")
    print("    defender token: issued (hash stored only)")

    step(5, "Auditor agent runs the pinned fixture and records a real failing test")
    spec = SandboxSpec(FIXTURE / "baseline", FIXTURE / "patched", FIXTURE / "tests", run_dir / "auditor")
    sandbox = materialize(spec, "baseline", "auditor")
    observed = run_pytest(sandbox, target="tests/test_invariant.py", label="auditor", timeout_s=90)
    print(f"    command   : python -m pytest tests/test_invariant.py (in sandbox)")
    print(f"    exit code : {observed.exit_code}   invariant_failed={observed.invariant_failed}")
    print(f"    stdout sha: {observed.stdout_sha256}")
    tail = [line for line in observed.stdout.splitlines() if "AssertionError" in line]
    for line in tail[:2]:
        print(f"    observed  : {line.strip()}")

    manifest = {
        "schema_version": "1.0",
        "claim_id": "claim-" + secrets.token_hex(4),
        "bounty_id": str(bounty_id),
        "source_commit": SOURCE_COMMIT,
        "scope_reference": "fixture/baseline#scope",
        "invariant": {
            "expected": "a withdrawal may never exceed the funded balance",
            "actual": "withdraw(400 units) succeeds against a 500 cent (5.00 unit) balance",
        },
        "reproduction": {
            "command": "python -m pytest tests/test_invariant.py",
            "exit_code": int(observed.exit_code),
            "stdout_sha256": observed.stdout_sha256,
        },
        "evidence_sha256": sha256_text(observed.stdout),
        "commitment_salt_hex": secrets.token_hex(16),
        "limitations": ["Synthetic fixture only", "No live deployment was tested"],
        "impact_trace": (
            "The unit mismatch lets a client debit 100x the locked balance and drive the "
            "account negative, breaking the accounting invariant."
        ),
    }
    validate_claim_manifest(manifest)

    step(6, "Auditor submits the claim commitment on chain (evidence stays private)")
    salt = secrets.token_hex(16)
    claim_response = client.post(
        f"/v1/challenges/{challenge_id}/claims",
        headers={"X-Agent-Token": auditor_token},
        json={"manifest": manifest, "commitment_salt_hex": salt},
    )
    assert claim_response.status_code == 201, claim_response.text
    claim = claim_response.json()["claim"]
    claim_id = claim["id"]
    print(f"    claim         : {claim_id} (on-chain #{claim['onchain_claim_id']})")
    print(f"    commitment    : {claim['commitment_hex']}")

    step(7, "Defender agent submits a minimal patch confined to ledger.py")
    diff = make_unified_diff(
        FIXTURE / "baseline" / "ledger.py", FIXTURE / "patched" / "ledger.py", label="ledger.py"
    )
    patch_response = client.post(
        f"/v1/claims/{claim_id}/patches",
        headers={"X-Agent-Token": defender_token},
        json={"diff": diff, "base_commit": SOURCE_COMMIT, "patched_commit": "3" * 40},
    )
    assert patch_response.status_code == 201, patch_response.text
    print(f"    patch digest  : {patch_response.json()['patch']['diff_sha256']}")
    print(f"    patch lines   : {len(diff.splitlines())}")

    step(8, "Independent verifier re-runs baseline (twice) and the patched revision")
    verified = client.post(
        f"/v1/claims/{claim_id}/verify",
        headers={**headers, "Idempotency-Key": "demo-verify-1"},
        json={},
    )
    assert verified.status_code == 201, verified.text
    result = verified.json()["result"]
    details = verified.json()["details"]
    print(f"    baseline exit : {result['baseline']['exit_code']} "
          f"invariant_failed={result['baseline']['invariant_failed']}")
    print(f"    patched  exit : {result['patched']['exit_code']} "
          f"invariant_failed={result['patched']['invariant_failed']}")
    print(f"    status        : {result['status']}")
    print(f"    deterministic : {details['deterministic_baseline']}")
    print(f"    manifest sha  : {result['manifest_sha256']}")
    print(f"    approval OK   : {verified.json()['approval_eligible']}")
    print(f"    artifacts     : {verified.json()['artifacts_dir']}")

    step(9, "Owner moves the bounty to Review and signs the approval")
    review = client.post(f"/v1/challenges/{challenge_id}/review", headers=headers)
    assert review.status_code == 200, review.text
    print(f"    state after review: {review.json()['bounty']['state']}")

    approve_intent = auth.issue_intent(
        account=owner.account,
        action="approve_claim",
        payload={"challenge_id": challenge_id, "claim_id": claim_id},
    )
    approved = client.post(
        f"/v1/challenges/{challenge_id}/approve",
        headers=headers,
        json={
            "message": approve_intent["message"],
            "signature": owner.sign(approve_intent["message"]),
        },
    )
    assert approved.status_code == 200, approved.text
    print(f"    state after approval: {approved.json()['bounty']['state']}")
    print(f"    winner: {approved.json()['bounty']['winner']}")

    step(10, "Owner signs request_payout -> PayoutPending (not yet paid)")
    payout_intent = auth.issue_intent(
        account=owner.account, action="request_payout", payload={"challenge_id": challenge_id}
    )
    payout = client.post(
        f"/v1/challenges/{challenge_id}/payout",
        headers=headers,
        json={
            "message": payout_intent["message"],
            "signature": owner.sign(payout_intent["message"]),
        },
    )
    assert payout.status_code == 202, payout.text
    attempt_id = payout.json()["attempt"]["attempt_id"]
    pending = client.get(f"/v1/challenges/{challenge_id}").json()["challenge"]
    print(f"    attempt #{attempt_id} state={pending['state']} paid={pending['chain']['paid_yocto']}")
    assert pending["state"] == "PayoutPending", pending["state"]
    assert pending["chain"]["paid_yocto"] == "0"

    step(11, "A second payout request is refused while the first is outstanding")
    dup_intent = auth.issue_intent(
        account=owner.account, action="request_payout", payload={"challenge_id": challenge_id}
    )
    duplicate = client.post(
        f"/v1/challenges/{challenge_id}/payout",
        headers=headers,
        json={
            "message": dup_intent["message"],
            "signature": owner.sign(dup_intent["message"]),
        },
    )
    print(f"    duplicate payout -> HTTP {duplicate.status_code} {duplicate.json().get('error')}")
    assert duplicate.status_code == 409

    step(12, "Settlement callback delivered (simulated network finality) -> Paid once")
    callback = client.post(
        f"/v1/challenges/{challenge_id}/deliver-callback",
        headers={"X-Internal-Token": app.state.pa.internal_token},
        json={"attempt_id": attempt_id, "result": "success"},
    )
    assert callback.status_code == 200, callback.text
    final = client.get(f"/v1/challenges/{challenge_id}").json()["challenge"]
    print(f"    final state   : {final['state']}")
    print(f"    paid          : {final['chain']['paid_yocto']}")
    print(f"    escrow left   : {final['chain']['escrow_balance_yocto']}")
    assert final["state"] == "Paid"

    step(13, "Receipt chain and evidence integrity")
    receipts = client.get(f"/v1/challenges/{challenge_id}/receipts").json()
    for item in receipts["receipts"]:
        print(f"    {item['receipt_id']}  {item['kind']:<20} {item['hash'][:16]}…")
    print(f"    chain valid   : {receipts['receipt_chain']['valid']} "
          f"({receipts['receipt_chain']['checked']} receipts)")
    assert receipts["receipt_chain"]["valid"] is True
    payout_events = [r for r in receipts["receipts"] if r["kind"] == "payout_completed"]
    assert len(payout_events) == 1, payout_events

    summary = {
        "run_directory": str(run_dir),
        "chain_mode": "local-ledger (simulated, not testnet)",
        "challenge_id": challenge_id,
        "bounty_id": bounty_id,
        "claim_id": claim_id,
        "verification_status": result["status"],
        "verification_manifest_sha256": result["manifest_sha256"],
        "final_state": final["state"],
        "paid_yocto": final["chain"]["paid_yocto"],
        "receipts": [r["receipt_id"] for r in receipts["receipts"]],
        "receipt_chain_valid": receipts["receipt_chain"]["valid"],
        "approval_eligible": verified.json()["approval_eligible"],
        "testnet": "NOT DEPLOYED - blocked on credentials and network access",
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    banner("DEMO RESULT: workflow complete on the local ledger simulator")
    print(f"verification : {result['status']}")
    print(f"settlement   : {final['state']} exactly once ({final['chain']['paid_yocto']} yocto units)")
    print(f"receipts     : {len(receipts['receipts'])} (hash chain valid)")
    print(f"evidence     : {run_dir}")
    print("testnet      : NOT DEPLOYED — see communications/STATUS_BOARD.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
