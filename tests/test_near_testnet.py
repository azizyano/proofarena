"""NEAR testnet plumbing tests: base58, Borsh, signing, RPC, wallets, signer guards.

Everything runs offline against fakes. The one thing these tests cannot prove is
that the chain accepts the Borsh encoding — that is what
``scripts/testnet/deploy.py --dry-run`` and the first real transaction are for.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from conftest import ROOT
from proofarena.chain.ledger import open_chain
from proofarena.chain.near_testnet import (
    NearTestnetAdapter,
    _map_bounty,
    _map_claim,
    _translate_contract_error,
)
from proofarena.chain.models import BountyState, ClaimState
from proofarena.errors import ChainUnavailable
from proofarena.near.base58 import b58decode, b58encode
from proofarena.near.rpc import NearRpc, RpcError, TxOutcome
from proofarena.near.signing import (
    KeyPair,
    deploy_contract_action,
    decode_signed_transaction,
    function_call_action,
    public_key_b64_to_ed25519,
    sign_actions,
    sign_transaction,
    verify_signature,
)
from proofarena.near.wallet import (
    HttpWallet,
    NativeWallet,
    WalletError,
    load_key,
    load_wallet,
    success_value,
)

BLOCK_HASH = bytes(range(32))
ACCOUNT = "owner.testnet"
CONTRACT = "escrow.owner.testnet"


@pytest.fixture()
def key() -> KeyPair:
    return KeyPair.from_seed(bytes(range(32)))


# ---------------------------------------------------------------------------
# base58
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,encoded",
    [
        (b"", ""),
        (b"\x00", "1"),
        (b"\x00\x00", "11"),
        (b"hello world", "StV1DL6CwTryKyV"),
        (b"abc", "ZiCa"),
    ],
)
def test_base58_known_vectors(raw: bytes, encoded: str):
    assert b58encode(raw) == encoded
    assert b58decode(encoded) == raw


def test_base58_round_trip_random_bytes():
    for size in (1, 5, 32, 64):
        raw = bytes((i * 37 + size) % 256 for i in range(size))
        assert b58decode(b58encode(raw)) == raw


def test_base58_rejects_invalid_characters():
    from proofarena.near.base58 import Base58Error

    with pytest.raises(Base58Error):
        b58decode("0OIl")


# ---------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------
def test_key_from_near_secret_key_and_public_key_match():
    seed = bytes(range(32))
    derived = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    secret = "ed25519:" + b58encode(seed + derived)
    pair = KeyPair.from_secret_key(secret)
    assert pair.public_key == derived
    assert pair.public_key_ed25519.startswith("ed25519:")
    assert "seed" not in repr(pair) or "<redacted>" in repr(pair)
    assert b58encode(seed) not in repr(pair)


def test_key_accepts_double_prefixed_meteor_export():
    """Meteor exports ed25519:ed25519:<base58>; near-cli uses one prefix."""
    from proofarena.near.signing import strip_key_prefix

    seed = bytes(range(32))
    derived = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    body = b58encode(seed + derived)
    single = KeyPair.from_secret_key("ed25519:" + body)
    double = KeyPair.from_secret_key("ed25519:ed25519:" + body)
    assert single.public_key == double.public_key == derived
    assert strip_key_prefix("ed25519:ed25519:" + body) == body
    assert strip_key_prefix("ed25519:" + body) == body
    with pytest.raises(ValueError):
        strip_key_prefix("ed25519:abc:def")


def test_key_refuses_mismatched_public_key():
    seed = bytes(range(32))
    bogus_public = bytes(reversed(range(32)))
    secret = "ed25519:" + b58encode(seed + bogus_public)
    with pytest.raises(ValueError):
        KeyPair.from_secret_key(secret)


def test_public_key_b64_converts_to_ed25519_string(key: KeyPair):
    b64 = base64.b64encode(key.public_key).decode("ascii")
    assert public_key_b64_to_ed25519(b64) == key.public_key_ed25519


# ---------------------------------------------------------------------------
# transaction encoding
# ---------------------------------------------------------------------------
def test_signed_transaction_round_trips_and_verifies(key: KeyPair):
    signed = sign_transaction(
        key=key,
        signer_id=ACCOUNT,
        receiver_id=CONTRACT,
        nonce=7,
        block_hash=BLOCK_HASH,
        method_name="create_bounty",
        args={"title": "demo", "reward_yocto": "100"},
        gas=100_000_000_000_000,
        deposit=101_000_000_000_000_000_000_000,
    )
    decoded = decode_signed_transaction(signed.signed_borsh)
    assert decoded["signer_id"] == ACCOUNT
    assert decoded["receiver_id"] == CONTRACT
    assert decoded["nonce"] == 7
    assert decoded["block_hash"] == BLOCK_HASH
    assert len(decoded["actions"]) == 1
    action = decoded["actions"][0]
    assert action["type"] == "function_call"
    assert action["method_name"] == "create_bounty"
    assert action["deposit"] == 101_000_000_000_000_000_000_000
    assert json.loads(action["args"]) == {"title": "demo", "reward_yocto": "100"}
    # signature covers sha256(unsigned borsh)
    verify_signature(
        key.public_key, hashlib.sha256(signed.unsigned_borsh).digest(), b58decode(signed.signature_b58)
    )
    # NEAR hashes the *unsigned* transaction; confirmed against testnet.
    assert signed.tx_hash == b58encode(hashlib.sha256(signed.unsigned_borsh).digest())
    assert signed.payload["actions"][0]["FunctionCall"]["args"] == base64.b64encode(
        action["args"]
    ).decode("ascii")


def test_borsh_layout_pins_field_order_and_sizes(key: KeyPair):
    """A byte-level check that catches accidental field reordering/width changes."""
    signed = sign_transaction(
        key=key,
        signer_id="a",
        receiver_id="b",
        nonce=1,
        block_hash=BLOCK_HASH,
        method_name="m",
        args={},
        gas=1,
        deposit=0,
    )
    raw = signed.unsigned_borsh
    # signer_id "a": u32 len (1) + 'a'
    assert raw[:4] == (1).to_bytes(4, "little")
    assert raw[4:5] == b"a"
    # public key: tag 0 + 32 bytes
    assert raw[5] == 0
    assert raw[6:38] == key.public_key
    # nonce u64
    assert raw[38:46] == (1).to_bytes(8, "little")
    # receiver "b"
    assert raw[46:50] == (1).to_bytes(4, "little")
    assert raw[50:51] == b"b"
    # block hash 32 bytes
    assert raw[51:83] == BLOCK_HASH
    # one action
    assert raw[83:87] == (1).to_bytes(4, "little")
    assert raw[87] == 2  # FunctionCall tag


def test_deploy_action_encodes_code_and_init_call(key: KeyPair):
    wasm = b"\x00asm\x01\x00\x00\x00" + b"\xaa" * 16
    signed = sign_actions(
        key=key,
        signer_id=ACCOUNT,
        receiver_id=CONTRACT,
        nonce=3,
        block_hash=BLOCK_HASH,
        actions=[
            deploy_contract_action(wasm),
            function_call_action("new", {"owner": ACCOUNT}, gas=30_000_000_000_000, deposit=0),
        ],
    )
    decoded = decode_signed_transaction(signed.signed_borsh)
    assert [a["type"] for a in decoded["actions"]] == ["deploy_contract", "function_call"]
    assert decoded["actions"][0]["code"] == wasm
    assert decoded["actions"][1]["method_name"] == "new"


def test_signing_rejects_invalid_inputs(key: KeyPair):
    with pytest.raises(ValueError):
        sign_transaction(
            key=key,
            signer_id=ACCOUNT,
            receiver_id=CONTRACT,
            nonce=1,
            block_hash=b"short",
            method_name="m",
            args={},
            gas=1,
            deposit=0,
        )
    with pytest.raises(ValueError):
        sign_actions(
            key=key,
            signer_id=ACCOUNT,
            receiver_id=CONTRACT,
            nonce=1,
            block_hash=BLOCK_HASH,
            actions=[],
        )


# ---------------------------------------------------------------------------
# RPC
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, json=None, timeout=None):  # noqa: A002 - mirror requests API
        self.calls.append(json)
        return FakeResponse(self.responses.pop(0))


def test_rpc_view_call_decodes_base64_json():
    payload = b'{"bounty_id":1,"state":"Open"}'
    session = FakeSession(
        [{"jsonrpc": "2.0", "id": "x", "result": {"result": list(payload), "logs": []}}]
    )
    rpc = NearRpc("https://example.invalid", session=session)
    assert rpc.view_call(CONTRACT, "get_bounty", {"bounty_id": 1}) == {
        "bounty_id": 1,
        "state": "Open",
    }
    sent = session.calls[0]
    # `call` is rejected by current nearcore; `call_function` is the valid variant.
    assert sent["params"]["request_type"] == "call_function", "regression: invalid query variant"
    assert sent["params"]["method_name"] == "get_bounty"
    assert json.loads(base64.b64decode(sent["params"]["args_base64"])) == {"bounty_id": 1}


def test_rpc_surfaces_errors():
    session = FakeSession(
        [{"jsonrpc": "2.0", "id": "x", "error": {"message": "Method not found", "code": -32601}}]
    )
    rpc = NearRpc("https://example.invalid", session=session)
    with pytest.raises(RpcError) as exc:
        rpc.view_call(CONTRACT, "nope", {})
    assert "Method not found" in str(exc.value)


def test_rpc_block_hash_and_access_key():
    block = b58encode(bytes(range(32)))
    session = FakeSession(
        [
            {"result": {"header": {"hash": block}}},
            {"result": {"nonce": 42, "permission": "FullAccess"}},
        ]
    )
    rpc = NearRpc("https://example.invalid", session=session)
    assert rpc.block_hash() == bytes(range(32))
    assert rpc.view_access_key(ACCOUNT, "ed25519:abc")["nonce"] == 42


def test_rpc_missing_access_key_raises():
    session = FakeSession([{"result": {"block_height": 1, "error": "does not exist"}}])
    rpc = NearRpc("https://example.invalid", session=session)
    with pytest.raises(RpcError) as exc:
        rpc.view_access_key(ACCOUNT, "ed25519:abc")
    assert exc.value.code == "E_KEY_NOT_AUTHORISED"


def test_broadcast_commit_parses_success_and_failure(key: KeyPair):
    signed = sign_transaction(
        key=key, signer_id=ACCOUNT, receiver_id=CONTRACT, nonce=1, block_hash=BLOCK_HASH,
        method_name="m", args={}, gas=1, deposit=0,
    )
    ok_session = FakeSession(
        [
            {
                "result": {
                    "status": {"SuccessValue": base64.b64encode(b"7").decode("ascii")},
                    "transaction_outcome": {"block_hash": "abc"},
                    "receipts_outcome": [{"outcome": {"logs": ["log line"]}}],
                    "block_height": 123,
                }
            }
        ]
    )
    outcome = NearRpc("https://example.invalid", session=ok_session).broadcast_commit(signed)
    assert outcome.success is True
    assert outcome.logs == ["log line"]
    assert success_value(outcome) == 7

    bad_session = FakeSession(
        [{"result": {"status": {"Failure": {"ActionError": {"kind": "InsufficientBalance"}}}}}]
    )
    bad = NearRpc("https://example.invalid", session=bad_session).broadcast_commit(signed)
    assert bad.success is False
    assert "InsufficientBalance" in (bad.error or "")


# ---------------------------------------------------------------------------
# wallets
# ---------------------------------------------------------------------------
class StubRpc:
    def __init__(self, nonces: list[int] | None = None) -> None:
        self.nonces = list(nonces or [10, 11, 12, 13])
        self.broadcast: list = []
        self.outcomes: list[TxOutcome] = []

    def view_access_key(self, account_id, public_key):
        return {"nonce": self.nonces.pop(0)}

    def block_hash(self, finality="final"):
        return BLOCK_HASH

    def broadcast_commit(self, signed):
        self.broadcast.append(signed)
        if self.outcomes:
            return self.outcomes.pop(0)
        return TxOutcome(tx_hash=signed.tx_hash, success=True, status={"SuccessValue": ""})

    def view_call(self, contract_id, method_name, args=None):
        return []

    def account(self, account_id):
        return {"amount": str(6 * 10**24)}


def test_native_wallet_builds_increments_nonce(key: KeyPair, monkeypatch):
    monkeypatch.setenv("NEAR_CONTRACT_ID", CONTRACT)
    rpc = StubRpc()
    wallet = NativeWallet(account_id=ACCOUNT, key=key, rpc=rpc)
    outcome = wallet.call("mark_review", {"bounty_id": 1})
    assert outcome.success is True
    decoded = decode_signed_transaction(rpc.broadcast[0].signed_borsh)
    assert decoded["nonce"] == 11  # 10 + 1
    assert decoded["actions"][0]["method_name"] == "mark_review"


def test_native_wallet_retries_on_invalid_nonce(key: KeyPair, monkeypatch):
    monkeypatch.setenv("NEAR_CONTRACT_ID", CONTRACT)
    rpc = StubRpc()
    rpc.outcomes = [
        TxOutcome(tx_hash="a", success=False, error="InvalidNonce"),
        TxOutcome(tx_hash="b", success=True, status={"SuccessValue": ""}),
    ]
    wallet = NativeWallet(account_id=ACCOUNT, key=key, rpc=rpc)
    outcome = wallet.call("mark_review", {"bounty_id": 1})
    assert outcome.success is True and outcome.tx_hash == "b"


def test_native_wallet_raises_on_terminal_failure(key: KeyPair, monkeypatch):
    monkeypatch.setenv("NEAR_CONTRACT_ID", CONTRACT)
    rpc = StubRpc()
    rpc.outcomes = [TxOutcome(tx_hash="a", success=False, error="InsufficientBalance")]
    wallet = NativeWallet(account_id=ACCOUNT, key=key, rpc=rpc)
    with pytest.raises(WalletError) as exc:
        wallet.call("approve_claim", {"bounty_id": 1, "claim_id": 2})
    assert "InsufficientBalance" in str(exc.value)


def test_http_wallet_posts_to_signer_and_maps_response(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"tx_hash": "HASH", "success": True, "status": {}, "logs": ["l"]}

    def fake_post(url, json=None, timeout=None):  # noqa: A002
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr("proofarena.near.wallet.requests.post", fake_post)
    wallet = HttpWallet(
        account_id=ACCOUNT,
        rpc=StubRpc(),
        signer_url="http://signer.local:8788",
        contract_id_value=CONTRACT,
    )
    outcome = wallet.call("request_payout", {"bounty_id": 3}, deposit_yocto=0)
    assert outcome.tx_hash == "HASH"
    assert captured["url"].endswith("/call")
    assert captured["json"]["contract_id"] == CONTRACT
    assert captured["json"]["method"] == "request_payout"


def test_http_wallet_refusal_is_an_error(monkeypatch):
    class FakeResponse:
        status_code = 403

        def json(self):
            return {"message": "method not allowed"}

    monkeypatch.setattr("proofarena.near.wallet.requests.post", lambda *a, **k: FakeResponse())
    wallet = HttpWallet(
        account_id=ACCOUNT, rpc=StubRpc(), signer_url="http://x", contract_id_value=CONTRACT
    )
    with pytest.raises(WalletError) as exc:
        wallet.call("withdraw_all_funds", {})
    assert "refused" in str(exc.value)


def test_load_key_reads_credentials_file(workdir: Path):
    seed = bytes(range(32))
    derived = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    path = workdir / f"{ACCOUNT}.json"
    path.write_text(
        json.dumps(
            {
                "account_id": ACCOUNT,
                "public_key": "ed25519:" + b58encode(derived),
                "private_key": "ed25519:" + b58encode(seed + derived),
            }
        ),
        encoding="utf-8",
    )
    key = load_key(ACCOUNT, path=path)
    assert key.public_key == derived


def test_load_key_reports_missing_credentials(workdir: Path):
    with pytest.raises(WalletError) as exc:
        load_key("nobody.testnet", path=None)
    assert "near-credentials" in str(exc.value)


def test_load_wallet_http_mode_needs_no_key(monkeypatch):
    monkeypatch.setenv("NEAR_TESTNET_ACCOUNT", ACCOUNT)
    monkeypatch.setenv("NEAR_CONTRACT_ID", CONTRACT)
    wallet = load_wallet(mode="http")
    assert isinstance(wallet, HttpWallet)
    assert wallet.account_id == ACCOUNT


def test_load_wallet_native_mode_requires_credentials(monkeypatch):
    monkeypatch.setenv("NEAR_TESTNET_ACCOUNT", "nobody.testnet")
    monkeypatch.setenv("NEAR_CONTRACT_ID", CONTRACT)
    monkeypatch.setenv("NEAR_CREDENTIALS", "/nonexistent/creds.json")
    with pytest.raises(WalletError):
        load_wallet(mode="native")


# ---------------------------------------------------------------------------
# contract JSON mapping
# ---------------------------------------------------------------------------
CONTRACT_BOUNTY = {
    "bounty_id": 4,
    "owner": ACCOUNT,
    "title": "demo",
    "reward_yocto": "5000000000000000000000000",
    "metadata_hash_hex": "a" * 64,
    "deadline_ns": 1_700_000_000_000_000_000,
    "review_deadline_ns": 1_700_000_100_000_000_000,
    "created_ns": 1_699_999_000_000_000_000,
    "state": "PayoutPending",
    "approved_claim_id": 2,
    "winner": "auditor.agent.local",
    "paid_yocto": "0",
    "refunded_yocto": "0",
    "escrow_balance_yocto": "5000000000000000000000000",
    "storage_reserve_yocto": "1000000000000000000000",
    "pending_attempt_id": 9,
    "last_attempt_id": 9,
    "disputed": False,
    "claims": [1, 2],
    "attempts": [
        {
            "attempt_id": 9,
            "kind": "Payout",
            "status": "Pending",
            "created_ns": 1_700_000_050_000_000_000,
            "state_before": "Approved",
            "resolved_ns": None,
        }
    ],
}


def test_contract_bounty_maps_to_domain_object():
    bounty = _map_bounty(CONTRACT_BOUNTY)
    assert bounty.state is BountyState.PAYOUT_PENDING
    assert bounty.reward_yocto == 5 * 10**24
    assert bounty.pending_attempt_id == 9
    attempt = bounty.attempt(9)
    assert attempt is not None
    assert attempt.kind.value == "payout"
    assert attempt.status.value == "pending"
    assert bounty.effective_state is BountyState.PAYOUT_PENDING


def test_contract_bounty_dispute_flag_maps_to_effective_state():
    raw = dict(CONTRACT_BOUNTY, state="Approved", disputed=True, pending_attempt_id=None, attempts=[])
    bounty = _map_bounty(raw)
    assert bounty.state is BountyState.APPROVED
    assert bounty.effective_state is BountyState.DISPUTED


def test_contract_claim_maps_booleans_to_claim_state():
    base = {
        "claim_id": 2,
        "bounty_id": 4,
        "submitter": "auditor.agent.local",
        "evidence_commitment_hex": "b" * 64,
        "manifest_sha256_hex": "c" * 64,
        "submitted_ns": 1,
    }
    assert _map_claim(dict(base, approved=False, paid=False)).state is ClaimState.SUBMITTED
    assert _map_claim(dict(base, approved=True, paid=False)).state is ClaimState.APPROVED
    assert _map_claim(dict(base, approved=True, paid=True)).state is ClaimState.PAID


# ---------------------------------------------------------------------------
# factory behaviour
# ---------------------------------------------------------------------------
def test_open_chain_testnet_without_config_raises(monkeypatch, workdir: Path):
    monkeypatch.delenv("NEAR_TESTNET_ACCOUNT", raising=False)
    monkeypatch.delenv("NEAR_CONTRACT_ID", raising=False)
    with pytest.raises(ChainUnavailable) as exc:
        open_chain(workdir / "x.db", contract_id="", network="near-testnet", mode="near-testnet")
    assert "NEAR_TESTNET_ACCOUNT" in str(exc.value)


def test_open_chain_rejects_unknown_mode(workdir: Path):
    with pytest.raises(ChainUnavailable):
        open_chain(workdir / "x.db", contract_id="c", network="n", mode="mainnet")


def test_api_refuses_testnet_mode_without_contract(monkeypatch, workdir: Path):
    from proofarena.api import create_app

    monkeypatch.delenv("NEAR_CONTRACT_ID", raising=False)
    with pytest.raises(ChainUnavailable):
        create_app(
            db_path=workdir / "a.db",
            chain_db_path=workdir / "c.db",
            fixture_root=ROOT / "fixture",
            chain_mode="near-testnet",
            contract_id=None,
            include_web=False,
        )


# ---------------------------------------------------------------------------
# signer service guards
# ---------------------------------------------------------------------------
@pytest.fixture()
def signer_client(monkeypatch, workdir: Path, key: KeyPair):
    from wallet import near_signer

    monkeypatch.setattr(near_signer, "load_key", lambda *a, **k: key)
    state = near_signer.SignerState(
        account_id=ACCOUNT,
        contract_id=CONTRACT,
        key_path=None,
        rpc_url="https://example.invalid",
        domain="proofarena.local",
        network="near-testnet",
        audit_path=workdir / "audit.jsonl",
    )
    calls: list[tuple[str, dict]] = []

    def fake_call(method, args, *, deposit_yocto=0, gas=0):
        calls.append((method, {"deposit": deposit_yocto, "gas": gas, "args": args}))
        return TxOutcome(tx_hash="TXHASH123", success=True, status={}, logs=["ok"])

    state.wallet.call = fake_call  # type: ignore[method-assign]
    app = near_signer.create_signer_app(state, allowed_origins=["http://127.0.0.1:8787"])
    with TestClient(app) as client:
        client.calls = calls  # type: ignore[attr-defined]
        client.signer_state = state  # type: ignore[attr-defined]
        yield client


def test_signer_status_hides_private_key(signer_client):
    body = signer_client.get("/status").json()
    assert body["account"] == ACCOUNT
    assert body["public_key"].startswith("ed25519:")
    serialized = json.dumps(body)
    assert "private" not in serialized.lower()
    assert "seed" not in serialized.lower()


def test_signer_refuses_foreign_contract(signer_client):
    response = signer_client.post(
        "/call", json={"contract_id": "evil.testnet", "method": "request_payout", "args": {}}
    )
    assert response.status_code == 403


def test_signer_refuses_unlisted_method(signer_client):
    response = signer_client.post(
        "/call", json={"contract_id": CONTRACT, "method": "delete_account", "args": {}}
    )
    assert response.status_code == 403


def test_signer_refuses_oversized_deposit(signer_client):
    response = signer_client.post(
        "/call",
        json={
            "contract_id": CONTRACT,
            "method": "create_bounty",
            "args": {},
            "deposit_yocto": str(10**30),
        },
    )
    assert response.status_code == 422
    assert response.json()["error"] == "E_LIMIT"


def test_signer_forwards_an_allowlisted_call(signer_client):
    response = signer_client.post(
        "/call",
        json={"contract_id": CONTRACT, "method": "request_payout", "args": {"bounty_id": 1}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tx_hash"] == "TXHASH123"
    assert body["explorer_url"].endswith("TXHASH123")
    assert signer_client.calls[0][0] == "request_payout"


def test_signer_signs_login_and_refuses_foreign_domain(signer_client):
    good = {
        "purpose": "login",
        "account": ACCOUNT,
        "nonce": "n",
        "domain": "proofarena.local",
        "network": "near-testnet",
        "contract_id": CONTRACT,
        "issued_ns": 1,
        "expires_ns": 9_999_999_999_999_999_999,
    }
    message = json.dumps(good, sort_keys=True, separators=(",", ":"))
    response = signer_client.post("/sign", json={"message": message})
    assert response.status_code == 200
    assert len(response.json()["signature"]) > 40

    bad = dict(good, domain="evil.example")
    tampered = json.dumps(bad, sort_keys=True, separators=(",", ":"))
    assert signer_client.post("/sign", json={"message": tampered}).status_code == 403


def test_signer_audit_log_records_calls_without_secrets(signer_client, workdir: Path):
    signer_client.post(
        "/call",
        json={"contract_id": CONTRACT, "method": "request_payout", "args": {"bounty_id": 1}},
    )
    text = (workdir / "audit.jsonl").read_text(encoding="utf-8")
    assert "sent" in text and "TXHASH123" in text
    assert "ed25519:" not in text


# ---------------------------------------------------------------------------
# adapter behaviour with a stubbed wallet
# ---------------------------------------------------------------------------
def _adapter(workdir: Path, monkeypatch, wallet) -> NearTestnetAdapter:
    import proofarena.chain.near_testnet as module

    monkeypatch.setattr(module, "load_wallet", lambda **kwargs: wallet)
    return NearTestnetAdapter(
        network="near-testnet", contract_id=CONTRACT, txlog_path=workdir / "txlog.sqlite3"
    )


def test_adapter_create_bounty_records_real_tx_hash(workdir: Path, monkeypatch):
    class Wallet:
        account_id = ACCOUNT
        rpc = None

        def call(self, method, args, *, deposit_yocto=0, gas=0):
            return TxOutcome(tx_hash="TXCREATE", success=True, status={"SuccessValue": "Nw=="}, logs=[])

    rpc_views = {"list_bounty_ids": [7], "get_bounty": CONTRACT_BOUNTY}

    adapter = _adapter(workdir, monkeypatch, Wallet())
    adapter.rpc = type(
        "R",
        (),
        {
            "view_call": staticmethod(lambda c, m, a=None: rpc_views[m]),
            "call_view_optional": staticmethod(lambda c, m, a=None: rpc_views.get(m)),
            "tx_status": staticmethod(lambda h, s: {"status": {}}),
            "url": "https://rpc.testnet.near.org",
        },
    )()
    result = adapter.create_bounty(
        signer=ACCOUNT,
        title="demo",
        reward_yocto=5 * 10**24,
        metadata_hash_hex="a" * 64,
        deadline_ns=1,
        review_deadline_ns=2,
        attached_yocto=5 * 10**24 + 10**21,
    )
    assert result["receipts"][0]["receipt_id"] == "TXCREATE"
    assert result["receipts"][0]["explorer_url"].endswith("TXCREATE")
    assert result["receipts"][0]["simulated"] is False
    assert adapter.receipts(bounty_id=7)[0]["receipt_id"] == "TXCREATE"


# ---------------------------------------------------------------------------
# agent signer routing (claims must be signed by the auditor, not the owner)
# ---------------------------------------------------------------------------
class StubAgentWallet:
    account_id = "auditor.testnet"

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.calls: list[tuple[str, dict]] = []

    def call(self, method, args, *, deposit_yocto=0, gas=0):
        self.calls.append((method, args))
        # commit_claim returns the new claim id
        return TxOutcome(
            tx_hash="TXCLAIM",
            success=True,
            status={"SuccessValue": base64.b64encode(b"5").decode("ascii")},
            logs=[],
        )


def test_claims_route_through_the_agent_signer(workdir: Path, monkeypatch):
    import proofarena.near.wallet as wallet_module

    monkeypatch.setenv("PROOFARENA_AGENT_SIGNER_URL", "http://127.0.0.1:8789")
    monkeypatch.setenv("PROOFARENA_AGENT_ACCOUNT", "auditor.testnet")
    monkeypatch.setattr(wallet_module, "HttpWallet", StubAgentWallet)

    class OwnerWallet:
        account_id = ACCOUNT
        rpc = None

        def call(self, *a, **k):
            raise AssertionError("owner wallet must not sign claims")

    adapter = _adapter(workdir, monkeypatch, OwnerWallet())
    adapter.rpc = type(
        "R",
        (),
        {
            "view_call": staticmethod(lambda c, m, a=None: {"claim_id": 5, "bounty_id": 9, "submitter": "auditor.testnet", "evidence_commitment_hex": "b" * 64, "approved": False, "paid": False, "submitted_ns": 1}),
            "call_view_optional": staticmethod(
                lambda c, m, a=None: {
                    "claim_id": 5,
                    "bounty_id": 9,
                    "submitter": "auditor.testnet",
                    "evidence_commitment_hex": "b" * 64,
                    "approved": False,
                    "paid": False,
                    "submitted_ns": 1,
                }
                if m == "get_claim"
                else []
            ),
            "tx_status": staticmethod(lambda h, s: {"status": {}}),
            "url": "https://rpc.testnet.near.org",
        },
    )()
    result = adapter.commit_claim(
        signer="auditor.testnet", bounty_id=9, evidence_commitment_hex="b" * 64
    )
    assert result["receipts"][0]["receipt_id"] == "TXCLAIM"
    assert adapter.agent_wallet.calls[0][0] == "commit_claim"
    assert adapter.agent_account == "auditor.testnet"


def test_claim_signer_mismatch_is_refused(workdir: Path, monkeypatch):
    import proofarena.near.wallet as wallet_module

    monkeypatch.setenv("PROOFARENA_AGENT_SIGNER_URL", "http://127.0.0.1:8789")
    monkeypatch.setenv("PROOFARENA_AGENT_ACCOUNT", "auditor.testnet")
    monkeypatch.setattr(wallet_module, "HttpWallet", StubAgentWallet)

    class OwnerWallet:
        account_id = ACCOUNT
        rpc = None

    adapter = _adapter(workdir, monkeypatch, OwnerWallet())
    with pytest.raises(ChainUnavailable) as exc:
        adapter.commit_claim(signer="someone.else", bounty_id=1, evidence_commitment_hex="c" * 64)
    assert "does not match the configured agent account" in str(exc.value)


def test_agent_signer_requires_an_account(workdir: Path, monkeypatch):
    import proofarena.near.wallet as wallet_module

    monkeypatch.setenv("PROOFARENA_AGENT_SIGNER_URL", "http://127.0.0.1:8789")
    monkeypatch.delenv("PROOFARENA_AGENT_ACCOUNT", raising=False)
    monkeypatch.setattr(wallet_module, "HttpWallet", StubAgentWallet)

    class OwnerWallet:
        account_id = ACCOUNT
        rpc = None

    with pytest.raises(ChainUnavailable):
        _adapter(workdir, monkeypatch, OwnerWallet())


# ---------------------------------------------------------------------------
# error translation: a contract refusal is the caller's problem (409), not an
# unavailable chain (503)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "message",
    [
        '{"ActionError": {"kind": {"FunctionCallError": {"ExecutionError": "Smart contract panicked: bounty is terminal; cannot request payout"}}}}',
        "Smart contract panicked: a settlement attempt is already pending for this bounty",
        "FunctionCallError: ExecutionError",
    ],
)
def test_contract_refusal_maps_to_state_error(message):
    from proofarena.errors import StateError

    error = _translate_contract_error("request_payout", message)
    assert isinstance(error, StateError)
    assert error.code == "E_STATE"
    assert "request_payout" in error.message


def test_transport_failure_maps_to_chain_unavailable():
    from proofarena.errors import ChainUnavailable

    error = _translate_contract_error("request_payout", "RPC request failed: connection reset")
    assert isinstance(error, ChainUnavailable)


def test_second_payout_message_is_preserved():
    message = 'Smart contract panicked: a settlement attempt is already pending for this bounty'
    error = _translate_contract_error("request_payout", message)
    assert "already pending" in error.message


# ---------------------------------------------------------------------------
# the signer must never answer with a non-JSON 500 when the RPC is flaky
# ---------------------------------------------------------------------------
def test_signer_returns_json_when_the_wallet_explodes(signer_client):
    """A dropped RPC connection must come back as JSON 502, never an HTML 500."""

    def boom(*args, **kwargs):
        raise RuntimeError("RemoteDisconnected: connection aborted")

    signer_client.signer_state.wallet.call = boom
    response = signer_client.post(
        "/call", json={"contract_id": CONTRACT, "method": "request_payout", "args": {}}
    )
    assert response.status_code == 502
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "E_TX"
    assert "RemoteDisconnected" in body["message"]
    assert "ed25519:" not in response.text
