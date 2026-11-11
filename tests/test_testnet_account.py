"""Tests for the testnet account bootstrap helper (offline, faucet mocked)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "testnet"))

import create_account as ca  # noqa: E402
from proofarena.near.signing import KeyPair  # noqa: E402


class FakeResponse:
    def __init__(self, status: int, payload: dict | str) -> None:
        self.status_code = status
        self._payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self) -> dict:
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post(self, url, json=None, timeout=None):  # noqa: A002
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self.response


def test_validate_name_appends_testnet_suffix():
    assert ca.validate_name("proofarena-abc123") == "proofarena-abc123.testnet"
    assert ca.validate_name("proofarena-abc123.testnet") == "proofarena-abc123.testnet"


@pytest.mark.parametrize("bad", ["UPPER.testnet", "-leading.testnet", "has space.testnet", "bad!char.testnet"])
def test_validate_name_rejects_invalid(bad: str):
    with pytest.raises(SystemExit):
        ca.validate_name(bad)


def test_generate_key_round_trips_and_public_matches_private():
    key, public_key, private_key = ca.generate_key()
    assert public_key.startswith("ed25519:")
    assert private_key.startswith("ed25519:")
    assert public_key != private_key
    pair = KeyPair.from_secret_key(private_key)
    assert pair.public_key_ed25519 == public_key


def test_create_account_posts_the_documented_payload():
    session = FakeSession(FakeResponse(200, {"receipts_outcome": []}))
    ca.create_account("proofarena-abc123.testnet", "ed25519:PUB", session=session)
    call = session.calls[0]
    assert call["url"] == ca.FAUCET_URL
    assert call["json"] == {
        "newAccountId": "proofarena-abc123.testnet",
        "newAccountPublicKey": "ed25519:PUB",
    }


def test_create_account_surfaces_faucet_refusal():
    session = FakeSession(FakeResponse(429, "rate limited"))
    with pytest.raises(SystemExit) as exc:
        ca.create_account("proofarena-abc123.testnet", "ed25519:PUB", session=session)
    assert "429" in str(exc.value)


def test_write_credentials_uses_explicit_path(workdir: Path):
    path = workdir / "creds.json"
    written = ca.write_credentials(
        "proofarena-abc123.testnet", "ed25519:PUB", "ed25519:SECRET", str(path)
    )
    assert written == [path]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["account_id"] == "proofarena-abc123.testnet"
    assert payload["private_key"] == "ed25519:SECRET"
    assert "TESTNET ONLY" in payload["warning"]


def test_main_creates_account_and_never_prints_the_private_key(
    monkeypatch, workdir: Path, capsys
):
    captured_key: dict[str, str] = {}

    real_generate = ca.generate_key

    def fake_generate():
        _, public, private = real_generate()
        captured_key["private"] = private
        return None, public, private

    monkeypatch.setattr(ca, "generate_key", fake_generate)
    monkeypatch.setattr(ca, "create_account", lambda *a, **k: {"receipts_outcome": []})

    class FakeRpc:
        def __init__(self, url: str) -> None:
            self.url = url

        def account(self, account_id: str) -> dict:
            return {"amount": str(10 * 10**24)}

    monkeypatch.setattr("proofarena.near.rpc.NearRpc", FakeRpc)

    out_path = workdir / "account.json"
    code = ca.main(["--name", "proofarena-test-01", "--key-out", str(out_path)])
    assert code == 0

    out = capsys.readouterr().out
    assert "proofarena-test-01.testnet" in out
    assert captured_key["private"] not in out, "the private key must never be printed"
    assert "10.0 NEAR" in out
    assert out_path.exists()
