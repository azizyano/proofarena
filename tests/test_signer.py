"""Wallet signer integration tests (PA-013 companion).

These exercise the real signing path the browser uses: the signer builds and signs
a canonical message, the API verifies it. A golden-byte assertion keeps the two
implementations from drifting (which is exactly the bug this test caught).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import ROOT
from proofarena.api import create_app
from wallet.signer import OwnerWallet, create_signer_app

ACCOUNT = "owner.demo.local"
DOMAIN = "proofarena.local"
NETWORK = "local-ledger"
CONTRACT = "escrow.proofarena.local"


@pytest.fixture()
def signer_app(workdir: Path):
    return create_signer_app(
        account=ACCOUNT,
        key_path=workdir / "owner.key.json",
        domain=DOMAIN,
        network=NETWORK,
        contract_id=CONTRACT,
        allowed_origins=["http://127.0.0.1:8787"],
    )


@pytest.fixture()
def signer(signer_app):
    with TestClient(signer_app) as client:
        yield client


@pytest.fixture()
def api_client(workdir: Path):
    app = create_app(
        db_path=workdir / "app.db",
        chain_db_path=workdir / "chain.db",
        artifacts_dir=workdir / "artifacts",
        fixture_root=ROOT / "fixture",
        settlement_mode="manual",
        session_secret=b"signer-test-secret",
        domain=DOMAIN,
        network=NETWORK,
        contract_id=CONTRACT,
        include_web=False,
    )
    with TestClient(app) as client:
        yield app, client


def test_signer_creates_a_key_file_without_exposing_it(signer, workdir: Path):
    status = signer.get("/status").json()
    assert status["account"] == ACCOUNT
    assert 40 <= len(status["public_key_b64"]) <= 60
    key_path = workdir / "owner.key.json"
    assert key_path.exists()
    payload = json.loads(key_path.read_text(encoding="utf-8"))
    assert "private_key_b64" in payload  # held by the signer process only
    assert "private_key_b64" not in json.dumps(status)  # never returned over HTTP


def test_registration_message_matches_server_exactly(signer, api_client):
    app, _ = api_client
    proof = signer.post("/register-proof").json()
    expected = app.state.pa.auth.registration_message(
        proof["account"], proof["public_key_b64"], proof["nonce"]
    )
    assert proof["message"] == expected, "signer and server disagree on canonical bytes"


def test_full_login_round_trip_through_signer(signer, api_client):
    _, client = api_client
    proof = signer.post("/register-proof").json()
    registered = client.post(
        "/auth/register",
        json={
            "account": proof["account"],
            "public_key_b64": proof["public_key_b64"],
            "nonce": proof["nonce"],
            "signature": proof["signature"],
        },
    )
    assert registered.status_code == 200, registered.text

    challenge = client.post("/auth/challenge", json={"account": ACCOUNT}).json()
    signed = signer.post("/sign", json={"message": challenge["message"]}).json()
    verified = client.post(
        "/auth/verify", json={"message": challenge["message"], "signature": signed["signature"]}
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["account"] == ACCOUNT


def test_signer_refuses_a_foreign_domain(signer):
    message = json.dumps(
        {
            "purpose": "login",
            "account": ACCOUNT,
            "nonce": "abc",
            "domain": "evil.example",
            "network": NETWORK,
            "contract_id": CONTRACT,
            "issued_ns": 1,
            "expires_ns": 9_999_999_999_999_999_999,
            "payload": {},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    response = signer.post("/sign", json={"message": message})
    assert response.status_code == 403
    assert response.json()["error"] == "E_REFUSED"


def test_signer_refuses_a_mainnet_network(signer):
    message = json.dumps(
        {
            "purpose": "intent",
            "account": ACCOUNT,
            "nonce": "abc",
            "domain": DOMAIN,
            "network": "mainnet",
            "contract_id": "escrow.near",
            "issued_ns": 1,
            "expires_ns": 9_999_999_999_999_999_999,
            "action": "request_payout",
            "payload": {"challenge_id": "ch_1"},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    response = signer.post("/sign", json={"message": message})
    assert response.status_code == 403
    assert "network" in response.json()["message"]


def test_signer_refuses_unknown_purpose(signer):
    message = json.dumps(
        {
            "purpose": "export_private_key",
            "account": ACCOUNT,
            "nonce": "abc",
            "domain": DOMAIN,
            "network": NETWORK,
            "contract_id": CONTRACT,
            "issued_ns": 1,
            "expires_ns": 9_999_999_999_999_999_999,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    response = signer.post("/sign", json={"message": message})
    assert response.status_code == 403
    assert "unknown purpose" in response.json()["message"]


def test_signer_refuses_disallowed_origin(signer):
    message = signer.post("/register-proof").json()["message"]
    response = signer.post(
        "/sign", json={"message": message}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    assert response.json()["error"] == "E_ORIGIN"


def test_owner_wallet_signs_and_verifies_locally():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    key = Ed25519PrivateKey.generate()
    signature = key.sign(b"proofarena")
    Ed25519PublicKey.from_public_bytes(
        key.public_key().public_bytes_raw()
    ).verify(signature, b"proofarena")
