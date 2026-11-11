"""API, authorization and end-to-end workflow tests (PA-011, PA-012, PA-015)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from starlette.testclient import TestClient

from conftest import ROOT
from proofarena.api import create_app
from proofarena.canonical import b64encode, sha256_text
from proofarena.patching import make_unified_diff
from proofarena.runner import SandboxSpec, materialize, run_pytest
from proofarena.schemas import validate_claim_manifest

FIXTURE = ROOT / "fixture"
OWNER = "owner.demo.local"
DOMAIN = "proofarena.local"
NETWORK = "local-ledger"
CONTRACT = "escrow.proofarena.local"
COMMIT = "1" * 40


class Wallet:
    def __init__(self, account: str) -> None:
        self.account = account
        self.key = Ed25519PrivateKey.generate()

    @property
    def public_key_b64(self) -> str:
        return b64encode(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    def sign(self, message: str) -> str:
        return b64encode(self.key.sign(message.encode("utf-8")))


@pytest.fixture()
def app(workdir: Path):
    return create_app(
        db_path=workdir / "app.db",
        chain_db_path=workdir / "chain.db",
        artifacts_dir=workdir / "artifacts",
        fixture_root=FIXTURE,
        settlement_mode="manual",
        session_secret=b"test-secret-do-not-use",
        domain=DOMAIN,
        network=NETWORK,
        contract_id=CONTRACT,
        include_web=True,
    )


@pytest.fixture()
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def wallet() -> Wallet:
    return Wallet(OWNER)


def register(app, client: TestClient, wallet: Wallet) -> None:
    auth = app.state.pa.auth
    nonce = "n" * 32
    message = auth.registration_message(wallet.account, wallet.public_key_b64, nonce)
    response = client.post(
        "/auth/register",
        json={
            "account": wallet.account,
            "public_key_b64": wallet.public_key_b64,
            "nonce": nonce,
            "signature": wallet.sign(message),
        },
    )
    assert response.status_code == 200, response.text


def login(app, client: TestClient, wallet: Wallet) -> dict[str, str]:
    register(app, client, wallet)
    challenge = client.post("/auth/challenge", json={"account": wallet.account})
    assert challenge.status_code == 200, challenge.text
    message = challenge.json()["message"]
    verified = client.post(
        "/auth/verify", json={"message": message, "signature": wallet.sign(message)}
    )
    assert verified.status_code == 200, verified.text
    return {"Authorization": f"Bearer {verified.json()['token']}"}


def create_and_fund(app, client, wallet, headers, *, reward_near: int = 5, submission_s: int = 600):
    created = client.post(
        "/v1/challenges",
        headers=headers,
        json={
            "title": "Allowance ledger invariant challenge",
            "repo_url": "fixture/baseline",
            "source_commit": COMMIT,
            "scope": {"allowed_files": ["ledger.py"], "forbidden": ["network"]},
            "reward_yocto": str(reward_near * 10**24),
            "submission_seconds": submission_s,
            "review_seconds": 600,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    challenge_id = body["challenge"]["id"]
    auth = app.state.pa.auth
    issued = auth.issue_intent(
        account=wallet.account,
        action="fund_bounty",
        payload={"challenge_id": challenge_id, "metadata_hash_hex": body["metadata_hash_hex"]},
    )
    funded = client.post(
        f"/v1/challenges/{challenge_id}/fund",
        headers=headers,
        json={"message": issued["message"], "signature": wallet.sign(issued["message"])},
    )
    assert funded.status_code == 201, funded.text
    return challenge_id, funded.json()


def issue_token(client, headers, challenge_id: str, role: str, account: str) -> str:
    response = client.post(
        f"/v1/challenges/{challenge_id}/agent-token",
        headers=headers,
        json={"role": role, "agent_account": account},
    )
    assert response.status_code == 201, response.text
    return response.json()["token"]


def auditor_manifest(bounty_id: int, *, stdout_sha: str, exit_code: int) -> dict:
    return {
        "schema_version": "1.0",
        "claim_id": "claim-demo-0001",
        "bounty_id": str(bounty_id),
        "source_commit": COMMIT,
        "scope_reference": "fixture/baseline#scope",
        "invariant": {
            "expected": "withdrawal may never exceed the funded balance",
            "actual": "400 units withdrawn from a 5.00 unit balance; balance goes negative",
        },
        "reproduction": {
            "command": "python -m pytest tests/test_invariant.py",
            "exit_code": exit_code,
            "stdout_sha256": stdout_sha,
        },
        "evidence_sha256": sha256_text("private-evidence-bundle"),
        "commitment_salt_hex": "ab" * 16,
        "limitations": ["Synthetic fixture only"],
        "impact_trace": "A client can over-withdraw by 100x the locked balance.",
    }


# ---------------------------------------------------------------------------
# health and meta
# ---------------------------------------------------------------------------
def test_health_reports_chain_mode_and_receipt_integrity(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["chain"]["mode"] == "local"
    assert body["chain"]["receipt_chain_valid"] is True


def test_meta_admits_chain_is_simulated(client):
    body = client.get("/api/meta").json()
    assert body["chain_is_simulated"] is True
    assert body["network_id"] == NETWORK


def test_testnet_mode_is_refused_explicitly():
    from proofarena.errors import ChainUnavailable

    with pytest.raises(ChainUnavailable):
        create_app(chain_mode="near-testnet", include_web=False)


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
def test_unauthenticated_owner_action_is_rejected(client):
    response = client.post(
        "/v1/challenges", json={"title": "x" * 5, "repo_url": "r", "source_commit": COMMIT}
    )
    assert response.status_code == 401
    assert response.json()["error"] == "E_AUTH"


def test_login_nonce_is_single_use(app, client, wallet):
    register(app, client, wallet)
    challenge = client.post("/auth/challenge", json={"account": wallet.account}).json()
    message = challenge["message"]
    signature = wallet.sign(message)
    assert client.post("/auth/verify", json={"message": message, "signature": signature}).status_code == 200
    replay = client.post("/auth/verify", json={"message": message, "signature": signature})
    assert replay.status_code == 401
    assert "already used" in replay.json()["message"]


def test_login_rejects_bad_signature(app, client, wallet):
    register(app, client, wallet)
    message = client.post("/auth/challenge", json={"account": wallet.account}).json()["message"]
    other = Wallet("attacker.local")
    response = client.post("/auth/verify", json={"message": message, "signature": other.sign(message)})
    assert response.status_code == 401


def test_login_rejects_foreign_domain(app, client, wallet):
    register(app, client, wallet)
    message = json.loads(
        client.post("/auth/challenge", json={"account": wallet.account}).json()["message"]
    )
    message["domain"] = "evil.example"
    tampered = json.dumps(message, sort_keys=True, separators=(",", ":"))
    response = client.post("/auth/verify", json={"message": tampered, "signature": wallet.sign(tampered)})
    assert response.status_code == 401
    assert "domain" in response.json()["message"]


def test_login_rejects_foreign_network(app, client, wallet):
    register(app, client, wallet)
    message = json.loads(
        client.post("/auth/challenge", json={"account": wallet.account}).json()["message"]
    )
    message["network"] = "testnet"
    tampered = json.dumps(message, sort_keys=True, separators=(",", ":"))
    response = client.post("/auth/verify", json={"message": tampered, "signature": wallet.sign(tampered)})
    assert response.status_code == 401
    assert "network" in response.json()["message"]


# ---------------------------------------------------------------------------
# challenge creation and funding
# ---------------------------------------------------------------------------
def test_create_and_fund_challenge(client, wallet, app):
    headers = login(app, client, wallet)
    challenge_id, funded = create_and_fund(app, client, wallet, headers)
    assert funded["bounty"]["state"] == "Open"
    assert funded["receipts"][0]["kind"] == "bounty_created"
    listing = client.get("/v1/challenges").json()
    assert listing["count"] == 1
    assert listing["items"][0]["reward_yocto"] == str(5 * 10**24)
    assert len(listing["items"][0]["metadata_hash_hex"]) == 64
    detail = client.get(f"/v1/challenges/{challenge_id}").json()
    assert detail["challenge"]["state"] == "Open"
    assert detail["challenge"]["chain"]["escrow_balance_yocto"] == str(5 * 10**24)


def test_create_rejects_bad_parameters(client, wallet, app):
    headers = login(app, client, wallet)
    base = {
        "title": "Valid title",
        "repo_url": "fixture/baseline",
        "source_commit": COMMIT,
        "scope": {"a": 1},
        "reward_yocto": str(10**24),
        "submission_seconds": 600,
        "review_seconds": 600,
    }
    for patch, expected in (
        ({"reward_yocto": "0"}, 422),
        ({"reward_yocto": "-1"}, 422),
        ({"source_commit": "short"}, 422),
        ({"submission_seconds": 1}, 422),
        ({"scope": {}}, 422),
        ({"title": "x"}, 422),
    ):
        response = client.post("/v1/challenges", headers=headers, json={**base, **patch})
        assert response.status_code == expected, (patch, response.text)


def test_fund_requires_valid_owner_intent(client, wallet, app):
    headers = login(app, client, wallet)
    created = client.post(
        "/v1/challenges",
        headers=headers,
        json={
            "title": "Intent test challenge",
            "repo_url": "fixture/baseline",
            "source_commit": COMMIT,
            "scope": {"a": 1},
            "reward_yocto": str(10**24),
            "submission_seconds": 600,
            "review_seconds": 600,
        },
    ).json()
    challenge_id = created["challenge"]["id"]

    # No signature at all.
    assert client.post(f"/v1/challenges/{challenge_id}/fund", headers=headers, json={}).status_code == 422

    # A signature for the wrong action.
    issued = app.state.pa.auth.issue_intent(
        account=wallet.account, action="approve_claim", payload={"challenge_id": challenge_id}
    )
    wrong = client.post(
        f"/v1/challenges/{challenge_id}/fund",
        headers=headers,
        json={"message": issued["message"], "signature": wallet.sign(issued["message"])},
    )
    assert wrong.status_code in {401, 403}

    # An intent for a different metadata hash.
    issued = app.state.pa.auth.issue_intent(
        account=wallet.account,
        action="fund_bounty",
        payload={"challenge_id": challenge_id, "metadata_hash_hex": "0" * 64},
    )
    mismatched = client.post(
        f"/v1/challenges/{challenge_id}/fund",
        headers=headers,
        json={"message": issued["message"], "signature": wallet.sign(issued["message"])},
    )
    assert mismatched.status_code == 422


def test_non_owner_cannot_fund_someone_elses_challenge(app, client, wallet):
    headers = login(app, client, wallet)
    created = client.post(
        "/v1/challenges",
        headers=headers,
        json={
            "title": "Owner isolation test",
            "repo_url": "fixture/baseline",
            "source_commit": COMMIT,
            "scope": {"a": 1},
            "reward_yocto": str(10**24),
            "submission_seconds": 600,
            "review_seconds": 600,
        },
    ).json()
    intruder = Wallet("intruder.local")
    intruder_headers = login(app, client, intruder)
    response = client.post(
        f"/v1/challenges/{created['challenge']['id']}/fund",
        headers=intruder_headers,
        json={"message": "{}", "signature": "AA=="},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# agent tokens and evidence intake
# ---------------------------------------------------------------------------
def test_agent_token_is_scoped_to_role_and_challenge(app, client, wallet):
    headers = login(app, client, wallet)
    challenge_id, _ = create_and_fund(app, client, wallet, headers)
    auditor_token = issue_token(client, headers, challenge_id, "auditor", "auditor.agent.local")

    # Auditor token cannot submit a patch (defender-only).
    response = client.post(
        "/v1/claims/cl_missing/patches",
        headers={"X-Agent-Token": auditor_token},
        json={"diff": "x", "base_commit": COMMIT, "patched_commit": COMMIT},
    )
    assert response.status_code == 404

    # Unknown token.
    assert (
        client.post(
            f"/v1/challenges/{challenge_id}/claims",
            headers={"X-Agent-Token": "pa_agent_nope"},
            json={"manifest": {}},
        ).status_code
        == 401
    )


def test_claim_rejects_invalid_manifest(app, client, wallet):
    headers = login(app, client, wallet)
    challenge_id, funded = create_and_fund(app, client, wallet, headers)
    token = issue_token(client, headers, challenge_id, "auditor", "auditor.agent.local")
    bounty_id = funded["bounty"]["bounty_id"]

    response = client.post(
        f"/v1/challenges/{challenge_id}/claims",
        headers={"X-Agent-Token": token},
        json={"manifest": {"schema_version": "1.0"}, "commitment_salt_hex": "ab" * 16},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "E_SCHEMA"

    manifest = auditor_manifest(bounty_id, stdout_sha="0" * 64, exit_code=1)
    manifest["source_commit"] = "2" * 40
    response = client.post(
        f"/v1/challenges/{challenge_id}/claims",
        headers={"X-Agent-Token": token},
        json={"manifest": manifest, "commitment_salt_hex": "ab" * 16},
    )
    assert response.status_code == 422
    assert "source_commit" in response.json()["message"]


def test_duplicate_claim_commitment_is_rejected(app, client, wallet):
    headers = login(app, client, wallet)
    challenge_id, funded = create_and_fund(app, client, wallet, headers)
    token = issue_token(client, headers, challenge_id, "auditor", "auditor.agent.local")
    manifest = auditor_manifest(funded["bounty"]["bounty_id"], stdout_sha="0" * 64, exit_code=1)

    first = client.post(
        f"/v1/challenges/{challenge_id}/claims",
        headers={"X-Agent-Token": token},
        json={"manifest": manifest, "commitment_salt_hex": "ab" * 16},
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"/v1/challenges/{challenge_id}/claims",
        headers={"X-Agent-Token": token},
        json={"manifest": manifest, "commitment_salt_hex": "ab" * 16},
    )
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# full lifecycle
# ---------------------------------------------------------------------------
def _observed_baseline(workdir: Path):
    spec = SandboxSpec(FIXTURE / "baseline", FIXTURE / "patched", FIXTURE / "tests", workdir / "obs")
    sandbox = materialize(spec, "baseline", "obs")
    return run_pytest(sandbox, target="tests/test_invariant.py", label="obs", timeout_s=90)


def test_full_lifecycle_pays_once_and_only_after_owner_approval(app, client, wallet, workdir):
    headers = login(app, client, wallet)
    challenge_id, funded = create_and_fund(app, client, wallet, headers)
    bounty_id = funded["bounty"]["bounty_id"]

    observed = _observed_baseline(workdir)
    assert observed.invariant_failed is True

    auditor_token = issue_token(client, headers, challenge_id, "auditor", "auditor.agent.local")
    manifest = auditor_manifest(
        bounty_id, stdout_sha=observed.stdout_sha256, exit_code=observed.exit_code
    )
    validate_claim_manifest(manifest)
    claim_response = client.post(
        f"/v1/challenges/{challenge_id}/claims",
        headers={"X-Agent-Token": auditor_token},
        json={"manifest": manifest, "commitment_salt_hex": "cd" * 16},
    )
    assert claim_response.status_code == 201, claim_response.text
    claim_id = claim_response.json()["claim"]["id"]

    diff = make_unified_diff(
        FIXTURE / "baseline" / "ledger.py", FIXTURE / "patched" / "ledger.py", label="ledger.py"
    )
    defender_token = issue_token(client, headers, challenge_id, "defender", "defender.agent.local")
    patch_response = client.post(
        f"/v1/claims/{claim_id}/patches",
        headers={"X-Agent-Token": defender_token},
        json={"diff": diff, "base_commit": COMMIT, "patched_commit": "3" * 40},
    )
    assert patch_response.status_code == 201, patch_response.text

    # Verification requires an idempotency key.
    assert client.post(f"/v1/claims/{claim_id}/verify", headers=headers, json={}).status_code == 422
    verified = client.post(
        f"/v1/claims/{claim_id}/verify",
        headers={**headers, "Idempotency-Key": "idem-e2e-1"},
        json={},
    )
    assert verified.status_code == 201, verified.text
    result = verified.json()["result"]
    assert result["status"] == "PATCH_VERIFIED"
    assert result["baseline"]["invariant_failed"] is True
    assert result["patched"]["invariant_failed"] is False
    assert verified.json()["approval_eligible"] is True

    replay = client.post(
        f"/v1/claims/{claim_id}/verify",
        headers={**headers, "Idempotency-Key": "idem-e2e-1"},
        json={},
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["job_id"] == verified.json()["job_id"]

    # Payout before approval must fail.
    early = client.post(f"/v1/challenges/{challenge_id}/payout", headers=headers, json={})
    assert early.status_code == 422

    # Review, then approval with a signed intent.
    assert client.post(f"/v1/challenges/{challenge_id}/review", headers=headers).status_code == 200
    approve_intent = app.state.pa.auth.issue_intent(
        account=wallet.account,
        action="approve_claim",
        payload={"challenge_id": challenge_id, "claim_id": claim_id},
    )
    approved = client.post(
        f"/v1/challenges/{challenge_id}/approve",
        headers=headers,
        json={"message": approve_intent["message"], "signature": wallet.sign(approve_intent["message"])},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["bounty"]["state"] == "Approved"

    # Payout -> PayoutPending, NOT Paid.
    payout_intent = app.state.pa.auth.issue_intent(
        account=wallet.account, action="request_payout", payload={"challenge_id": challenge_id}
    )
    payout = client.post(
        f"/v1/challenges/{challenge_id}/payout",
        headers=headers,
        json={"message": payout_intent["message"], "signature": wallet.sign(payout_intent["message"])},
    )
    assert payout.status_code == 202, payout.text
    attempt_id = payout.json()["attempt"]["attempt_id"]
    pending = client.get(f"/v1/challenges/{challenge_id}").json()
    assert pending["challenge"]["state"] == "PayoutPending"
    assert pending["challenge"]["chain"]["paid_yocto"] == "0"

    # Second payout request while pending is refused.
    dup_intent = app.state.pa.auth.issue_intent(
        account=wallet.account, action="request_payout", payload={"challenge_id": challenge_id}
    )
    duplicate = client.post(
        f"/v1/challenges/{challenge_id}/payout",
        headers=headers,
        json={"message": dup_intent["message"], "signature": wallet.sign(dup_intent["message"])},
    )
    assert duplicate.status_code == 409

    # Deliver the callback (simulated network finality).
    internal = app.state.pa.internal_token
    callback = client.post(
        f"/v1/challenges/{challenge_id}/deliver-callback",
        headers={"X-Internal-Token": internal},
        json={"attempt_id": attempt_id, "result": "success"},
    )
    assert callback.status_code == 200, callback.text
    paid = client.get(f"/v1/challenges/{challenge_id}").json()
    assert paid["challenge"]["state"] == "Paid"
    assert paid["challenge"]["chain"]["paid_yocto"] == str(5 * 10**24)
    assert paid["challenge"]["chain"]["escrow_balance_yocto"] == "0"

    # A second payout cannot exist.
    again_intent = app.state.pa.auth.issue_intent(
        account=wallet.account, action="request_payout", payload={"challenge_id": challenge_id}
    )
    again = client.post(
        f"/v1/challenges/{challenge_id}/payout",
        headers=headers,
        json={"message": again_intent["message"], "signature": wallet.sign(again_intent["message"])},
    )
    assert again.status_code == 409

    receipts = client.get(f"/v1/challenges/{challenge_id}/receipts").json()
    assert receipts["receipt_chain"]["valid"] is True
    kinds = [item["kind"] for item in receipts["receipts"]]
    assert kinds.count("payout_completed") == 1
    assert "payout_requested" in kinds

    integrity = client.get("/v1/chain/integrity").json()
    assert integrity["integrity"]["valid"] is True


def test_callback_from_outsider_is_rejected(app, client, wallet):
    headers = login(app, client, wallet)
    challenge_id, funded = create_and_fund(app, client, wallet, headers)
    response = client.post(
        f"/v1/challenges/{challenge_id}/deliver-callback",
        headers={"X-Internal-Token": "wrong"},
        json={"attempt_id": 1, "result": "success"},
    )
    assert response.status_code == 403


def test_refund_after_approval_is_refused(app, client, wallet, workdir):
    headers = login(app, client, wallet)
    challenge_id, funded = create_and_fund(app, client, wallet, headers)
    claim_id = _fast_claim(app, client, wallet, headers, challenge_id, funded["bounty"]["bounty_id"], workdir)
    client.post(f"/v1/challenges/{challenge_id}/review", headers=headers)
    intent = app.state.pa.auth.issue_intent(
        account=wallet.account,
        action="approve_claim",
        payload={"challenge_id": challenge_id, "claim_id": claim_id},
    )
    client.post(
        f"/v1/challenges/{challenge_id}/approve",
        headers=headers,
        json={"message": intent["message"], "signature": wallet.sign(intent["message"])},
    )
    refund_intent = app.state.pa.auth.issue_intent(
        account=wallet.account, action="refund_expired", payload={"challenge_id": challenge_id}
    )
    refund = client.post(
        f"/v1/challenges/{challenge_id}/refund",
        headers=headers,
        json={"message": refund_intent["message"], "signature": wallet.sign(refund_intent["message"])},
    )
    assert refund.status_code == 409
    assert client.get(f"/v1/challenges/{challenge_id}").json()["challenge"]["state"] == "Approved"


def test_private_evidence_is_owner_only(app, client, wallet, workdir):
    headers = login(app, client, wallet)
    challenge_id, funded = create_and_fund(app, client, wallet, headers)
    claim_id = _fast_claim(app, client, wallet, headers, challenge_id, funded["bounty"]["bounty_id"], workdir)

    attacker = Wallet("curious.local")
    attacker_headers = login(app, client, attacker)
    denied = client.get(f"/v1/challenges/{challenge_id}/private-evidence", headers=attacker_headers)
    assert denied.status_code == 403
    allowed = client.get(f"/v1/challenges/{challenge_id}/private-evidence", headers=headers)
    assert allowed.status_code == 200
    claims = allowed.json()["claims"]
    assert claims[0]["id"] == claim_id
    assert "manifest" in claims[0]

    # The public timeline must not contain the private manifest.
    timeline = client.get(f"/v1/challenges/{challenge_id}/timeline").json()
    serialized = json.dumps(timeline)
    assert "withdrawal may never exceed" not in serialized


def _fast_claim(app, client, wallet, headers, challenge_id, bounty_id, workdir) -> str:
    """Submit a claim + patch without running the verifier."""
    observed = _observed_baseline(workdir)
    token = issue_token(client, headers, challenge_id, "auditor", "auditor.agent.local")
    manifest = auditor_manifest(
        bounty_id, stdout_sha=observed.stdout_sha256, exit_code=observed.exit_code
    )
    response = client.post(
        f"/v1/challenges/{challenge_id}/claims",
        headers={"X-Agent-Token": token},
        json={"manifest": manifest, "commitment_salt_hex": "ef" * 16},
    )
    assert response.status_code == 201, response.text
    claim_id = response.json()["claim"]["id"]
    diff = make_unified_diff(
        FIXTURE / "baseline" / "ledger.py", FIXTURE / "patched" / "ledger.py", label="ledger.py"
    )
    defender = issue_token(client, headers, challenge_id, "defender", "defender.agent.local")
    patch = client.post(
        f"/v1/claims/{claim_id}/patches",
        headers={"X-Agent-Token": defender},
        json={"diff": diff, "base_commit": COMMIT, "patched_commit": "3" * 40},
    )
    assert patch.status_code == 201, patch.text
    return claim_id


# ---------------------------------------------------------------------------
# web pages
# ---------------------------------------------------------------------------
def test_pages_render_and_show_simulated_chain_banner(app, client, wallet):
    headers = login(app, client, wallet)
    challenge_id, funded = create_and_fund(app, client, wallet, headers)
    for path in ("/", "/bounties", "/bounties/new", f"/bounties/{challenge_id}", f"/arena/{challenge_id}", "/status"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code)
        assert "LOCAL SIMULATOR" in response.text or path == "/status"
    assert client.get("/bounties/ch_missing").status_code == 404


def test_refund_response_includes_receipts(app, client, wallet, workdir, monkeypatch):
    """The refund endpoint must mirror the payout endpoint's response shape."""
    headers = login(app, client, wallet)
    challenge_id, funded = create_and_fund(app, client, wallet, headers)

    canned = {
        "attempt": {"attempt_id": 42, "kind": "refund", "status": "pending"},
        "state": "RefundPending",
        "receipts": [{"receipt_id": "local-000042", "kind": "refund_requested"}],
    }
    monkeypatch.setattr(app.state.pa.ledger, "refund_expired", lambda **kwargs: canned)
    monkeypatch.setattr(app.state.pa.ledger, "get_bounty", lambda bid: _bounty_stub())

    intent = app.state.pa.auth.issue_intent(
        account=wallet.account, action="refund_expired", payload={"challenge_id": challenge_id}
    )
    response = client.post(
        f"/v1/challenges/{challenge_id}/refund",
        headers=headers,
        json={"message": intent["message"], "signature": wallet.sign(intent["message"])},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["receipts"][0]["receipt_id"] == "local-000042"
    assert body["attempt"]["attempt_id"] == 42
    assert "RefundPending" in body["note"]


def _bounty_stub():
    from proofarena.chain.models import Bounty, BountyState

    return Bounty(
        bounty_id=1,
        owner=OWNER,
        title="stub",
        reward_yocto=10**23,
        metadata_hash_hex="a" * 64,
        deadline_ns=1,
        review_deadline_ns=2,
        created_ns=0,
        state=BountyState.REFUND_PENDING,
        escrow_balance_yocto=10**23,
    )
