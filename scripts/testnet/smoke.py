"""Full ProofArena lifecycle on NEAR testnet, driven over HTTP.

Prerequisites (see docs/TESTNET_RUNBOOK.md):
  * contract deployed and NEAR_CONTRACT_ID exported
  * `python wallet/near_signer.py` running on 127.0.0.1:8788
  * `PROOFARENA_CHAIN_MODE=near-testnet python scripts/dev.py` running on 127.0.0.1:8787

    python scripts/testnet/smoke.py --reward-near 0.1

Every state change below is a real NEAR testnet transaction. The script prints the
transaction hash and explorer URL for each one. It never fabricates a receipt: if
a call fails, it stops and prints the error.

Amounts are deliberately small (default 0.1 NEAR) so a faucet balance can run the
demo several times.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys

try:  # make Unicode output safe on legacy Windows consoles
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - non-reconfigurable stream
    pass
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from proofarena.patching import make_unified_diff  # noqa: E402
from proofarena.runner import SandboxSpec, materialize, run_pytest  # noqa: E402

FIXTURE = ROOT / "fixture"
EXPLORER = "https://explorer.testnet.near.org/transactions"


class Api:
    def __init__(self, base: str, token: str | None = None) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.session = requests.Session()

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        if extra:
            headers.update(extra)
        return headers

    def request(self, method: str, path: str, body: dict | None = None, headers: dict | None = None):
        response = self.session.request(
            method, f"{self.base}{path}", json=body, headers=self.headers(headers), timeout=180
        )
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text[:500]}
        if not response.ok:
            raise RuntimeError(f"{method} {path} → HTTP {response.status_code}: {data}")
        return data

    def get(self, path: str):
        return self.request("GET", path)

    def post(self, path: str, body: dict | None = None, headers: dict | None = None):
        return self.request("POST", path, body if body is not None else {}, headers)


class Wallet:
    def __init__(self, signer_url: str) -> None:
        self.url = signer_url.rstrip("/")
        self.status = self._get("/status")

    def _get(self, path: str) -> dict:
        response = requests.get(f"{self.url}{path}", timeout=30)
        response.raise_for_status()
        return response.json() if response.content else {}

    def _post(self, path: str, body: dict) -> dict:
        response = requests.post(f"{self.url}{path}", json=body, timeout=120)
        data = response.json() if response.content else {}
        if not response.ok:
            raise RuntimeError(f"signer {path} → HTTP {response.status_code}: {data}")
        return data

    def sign(self, message: str) -> str:
        return self._post("/sign", {"message": message})["signature"]

    def register(self, api: Api) -> dict:
        proof = self._get("/register-proof")
        return api.post(
            "/auth/register",
            {
                "account": proof["account"],
                "public_key_b64": proof["public_key_b64"],
                "nonce": proof["nonce"],
                "signature": proof["signature"],
            },
        )


def banner(text: str) -> None:
    print(f"\n{text}\n" + "-" * len(text))


def step(n: int, text: str) -> None:
    print(f"\n[{n}] {text}")


def tx_line(label: str, receipt: dict) -> None:
    tx = receipt.get("receipt_id", "")
    print(f"    {label:<22} {tx}")
    print(f"    {'':<22} {EXPLORER}/{tx}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ProofArena testnet smoke test")
    parser.add_argument("--api", default="http://127.0.0.1:8787")
    parser.add_argument("--signer", default="http://127.0.0.1:8788")
    parser.add_argument("--reward-near", type=float, default=0.1)
    parser.add_argument("--submission-seconds", type=int, default=900)
    parser.add_argument("--review-seconds", type=int, default=900)
    parser.add_argument("--settle-timeout", type=float, default=90.0)
    args = parser.parse_args(argv)

    api = Api(args.api)
    wallet = Wallet(args.signer)
    account = wallet.status["account"]
    contract = wallet.status["contract_id"]
    network = wallet.status["network"]

    banner("ProofArena × NEAR testnet — real transactions, real receipts")
    print(f"api       : {args.api}")
    print(f"signer    : {args.signer} ({account})")
    print(f"contract  : {contract}")
    print(f"network   : {network}")
    meta = api.get("/api/meta")
    if meta.get("chain_mode") != "near-testnet":
        raise SystemExit(
            f"API is in chain_mode={meta.get('chain_mode')!r}; start it with "
            "PROOFARENA_CHAIN_MODE=near-testnet."
        )
    print(f"chain_mode: {meta['chain_mode']} (simulated={meta['chain_is_simulated']})")

    step(1, "Owner authenticates with the NEAR key (on-chain ownership is checked)")
    wallet.register(api)
    challenge = api.post("/auth/challenge", {"account": account})
    api.token = api.post(
        "/auth/verify",
        {
            "message": challenge["message"],
            "signature": wallet.sign(challenge["message"]),
        },
    )["token"]
    print(f"    session issued for {account}")

    step(2, "Owner creates and funds a bounty (transaction 1)")
    reward_yocto = int(args.reward_near * 10**24)
    created = api.post(
        "/v1/challenges",
        {
            "title": "Allowance ledger invariant challenge",
            "repo_url": "fixture/baseline (authorized synthetic fixture)",
            "source_commit": "1" * 40,
            "scope": {
                "authorization": "synthetic fixture owned by the bounty owner",
                "allowed_files": ["ledger.py"],
                "allowed_commands": ["pytest fixture/tests"],
                "forbidden": ["network", "real targets", "funds"],
            },
            "reward_yocto": str(reward_yocto),
            "submission_seconds": args.submission_seconds,
            "review_seconds": args.review_seconds,
        },
    )
    challenge_id = created["challenge"]["id"]
    metadata_hash = created["metadata_hash_hex"]
    print(f"    challenge   : {challenge_id}")
    print(f"    metadata    : {metadata_hash}")

    intent = api.post(
        "/v1/intents",
        {"action": "fund_bounty", "payload": {"challenge_id": challenge_id, "metadata_hash_hex": metadata_hash}},
    )
    funded = api.post(
        f"/v1/challenges/{challenge_id}/fund",
        {"message": intent["message"], "signature": wallet.sign(intent["message"])},
    )
    bounty_id = funded["bounty"]["bounty_id"]
    tx_line("create_bounty", funded["receipts"][0])
    print(f"    bounty #{bounty_id} state={funded['bounty']['state']}")

    step(3, "Owner issues scoped agent tokens")
    auditor = api.post(
        f"/v1/challenges/{challenge_id}/agent-token",
        {"role": "auditor", "agent_account": "auditor.agent.local"},
    )["token"]
    defender = api.post(
        f"/v1/challenges/{challenge_id}/agent-token",
        {"role": "defender", "agent_account": "defender.agent.local"},
    )["token"]
    print("    auditor + defender tokens issued")

    step(4, "Auditor re-runs the pinned fixture and records a real failing test")
    spec = SandboxSpec(FIXTURE / "baseline", FIXTURE / "patched", FIXTURE / "tests", ROOT / "evidence" / "testnet-smoke")
    sandbox = materialize(spec, "baseline", "auditor")
    observed = run_pytest(sandbox, target="tests/test_invariant.py", label="auditor", timeout_s=120)
    print(f"    exit={observed.exit_code} invariant_failed={observed.invariant_failed}")
    print(f"    stdout sha256={observed.stdout_sha256}")
    if not observed.invariant_failed:
        raise SystemExit("baseline fixture did not fail — refusing to submit a claim")

    manifest = {
        "schema_version": "1.0",
        "claim_id": "claim-" + secrets.token_hex(4),
        "bounty_id": str(bounty_id),
        "source_commit": "1" * 40,
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
        "evidence_sha256": observed.stdout_sha256,
        "commitment_salt_hex": secrets.token_hex(16),
        "limitations": ["Synthetic fixture only", "No live deployment was tested"],
        "impact_trace": "Unit mismatch allows debiting 100× the locked balance, driving the account negative.",
    }

    step(5, "Auditor commits the claim commitment on chain (transaction 2)")
    claim = api.post(
        f"/v1/challenges/{challenge_id}/claims",
        {"manifest": manifest, "commitment_salt_hex": secrets.token_hex(16)},
        headers={"X-Agent-Token": auditor},
    )
    claim_id = claim["claim"]["id"]
    tx_line("commit_claim", claim["receipts"][0])
    print(f"    claim {claim_id} on-chain #{claim['claim']['onchain_claim_id']}")

    step(6, "Defender submits a patch confined to ledger.py")
    diff = make_unified_diff(
        FIXTURE / "baseline" / "ledger.py", FIXTURE / "patched" / "ledger.py", label="ledger.py"
    )
    patch = api.post(
        f"/v1/claims/{claim_id}/patches",
        {"diff": diff, "base_commit": "1" * 40, "patched_commit": "3" * 40},
        headers={"X-Agent-Token": defender},
    )
    print(f"    patch {patch['patch']['id']} sha256={patch['patch']['diff_sha256'][:16]}…")

    step(7, "Independent verifier re-runs baseline twice + the patched revision")
    verified = api.post(
        f"/v1/claims/{claim_id}/verify", {}, headers={"Idempotency-Key": "testnet-smoke-1"}
    )
    result = verified["result"]
    print(f"    baseline exit={result['baseline']['exit_code']} patched exit={result['patched']['exit_code']}")
    print(f"    status={result['status']} approval_eligible={verified['approval_eligible']}")
    print(f"    manifest sha256={result['manifest_sha256'][:16]}…")

    step(8, "Owner moves to Review and signs approve_claim (transaction 3)")
    api.post(f"/v1/challenges/{challenge_id}/review")
    intent = api.post(
        "/v1/intents",
        {"action": "approve_claim", "payload": {"challenge_id": challenge_id, "claim_id": claim_id}},
    )
    approved = api.post(
        f"/v1/challenges/{challenge_id}/approve",
        {"message": intent["message"], "signature": wallet.sign(intent["message"])},
    )
    tx_line("approve_claim", approved["receipts"][0])
    print(f"    state={approved['bounty']['state']} winner={approved['bounty']['winner']}")

    step(9, "Owner signs request_payout (transaction 4) — PayoutPending is NOT paid")
    intent = api.post("/v1/intents", {"action": "request_payout", "payload": {"challenge_id": challenge_id}})
    payout = api.post(
        f"/v1/challenges/{challenge_id}/payout",
        {"message": intent["message"], "signature": wallet.sign(intent["message"])},
    )
    tx_line("request_payout", payout["receipts"][0])
    attempt = payout["attempt"]["attempt_id"]
    state = api.get(f"/v1/challenges/{challenge_id}")["challenge"]
    print(f"    attempt #{attempt} state={state['state']} paid={state['chain']['paid_yocto']}")

    step(10, "Waiting for the contract callback to settle the transfer")
    deadline = time.time() + args.settle_timeout
    final = state
    while time.time() < deadline:
        final = api.get(f"/v1/challenges/{challenge_id}")["challenge"]
        if final["state"] in {"Paid", "NeedsReconciliation", "Approved"}:
            break
        time.sleep(2)
    print(f"    state={final['state']} paid={final['chain']['paid_yocto']} escrow={final['chain']['escrow_balance_yocto']}")

    if final["state"] != "Paid":
        print("\nNot settled yet. Check the contract on the explorer before doing anything else.")
        print(f"  {EXPLORER}/accounts/{contract}")
        return 2

    step(11, "Exactly-once check: a second payout must be refused")
    intent = api.post("/v1/intents", {"action": "request_payout", "payload": {"challenge_id": challenge_id}})
    try:
        api.post(
            f"/v1/challenges/{challenge_id}/payout",
            {"message": intent["message"], "signature": wallet.sign(intent["message"])},
        )
        print("    ERROR: the second payout was not refused")
        return 3
    except RuntimeError as exc:
        print(f"    refused as expected: {str(exc)[:160]}")

    banner("TESTNET RESULT: verified on chain")
    print(f"challenge   : {challenge_id}")
    print(f"bounty      : #{bounty_id}")
    print(f"verification: {result['status']}")
    print(f"settlement  : {final['state']}")
    print(f"contract    : {contract}")
    print(f"explorer    : {EXPLORER}/accounts/{contract}")
    receipts = api.get(f"/v1/challenges/{challenge_id}/receipts")["receipts"]
    print("\ntransactions:")
    for receipt in receipts:
        print(f"  {receipt['kind']:<18} {receipt['receipt_id']}")
    summary = {
        "challenge_id": challenge_id,
        "bounty_id": bounty_id,
        "contract_id": contract,
        "owner": account,
        "verification_status": result["status"],
        "final_state": final["state"],
        "transactions": [{"kind": r["kind"], "tx_hash": r["receipt_id"]} for r in receipts],
    }
    out = ROOT / "evidence" / "testnet-smoke" / f"summary-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nsummary written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
