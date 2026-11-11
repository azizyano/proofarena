"""HTTP API (PA-011 / PA-012).

Framework note (ADR-0003): FastAPI is not installable in this offline
environment, so the service is built directly on Starlette — the same ASGI
foundation FastAPI wraps — with Pydantic v2 models for validation. Request and
response shapes match ``docs/DATA_MODEL_AND_API.md``; swapping in FastAPI later
is additive, not a redesign.

Invariants enforced here:

* No endpoint signs with an owner key. Privileged actions require a *signed
  intent* produced by the wallet and are single-use.
* The API never reports ``Paid`` from its own database; settlement labels come
  from the chain mirror.
* Verification results are produced by re-executing tests, then validated
  against the frozen JSON schema before storage.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import config as cfg
from .auth import RateLimiter, WalletAuth, issue_session, new_agent_token, verify_session
from .canonical import canonical_bytes, sha256_hex, sha256_json, sha256_text
from .chain import Attempt, Bounty, LocalLedger, SettlementResult, open_chain
from .errors import (
    AuthError,
    ChainUnavailable,
    NotFound,
    ParamError,
    ProofArenaError,
    SchemaError,
    StateError,
    Unauthorized,
)
from .patching import PatchError, apply_unified_diff, diff_touches_only
from .near.base58 import b58decode, b58encode
from .runner import SandboxSpec
from .schemas import validate_claim_manifest, validate_verification_result
from .store import Store
from .verify import run_verification
from .walletflow import GAS_100_TGAS, TxPlan, verify_nep413_signature, verify_wallet_tx

MAX_BODY_BYTES = 2 * 1024 * 1024
TERMINAL_STATE_LABELS = {"Paid", "Refunded"}
SETTLING_LABELS = {"PayoutPending", "RefundPending"}
ALLOWED_CLAIM_STATUSES = {"Submitted", "Approved", "Paid", "Rejected"}


class AppContext:
    """Everything the handlers need, created once per process."""

    def __init__(
        self,
        *,
        store: Store,
        ledger: LocalLedger,
        auth: WalletAuth,
        artifacts_dir: Path,
        fixture_root: Path,
        settlement_mode: str,
        internal_token: str,
        session_secret: bytes,
        chain_mode: str,
    ) -> None:
        self.store = store
        self.ledger = ledger
        self.auth = auth
        self.artifacts_dir = artifacts_dir
        self.fixture_root = fixture_root
        self.settlement_mode = settlement_mode
        self.internal_token = internal_token
        self.session_secret = session_secret
        self.chain_mode = chain_mode
        self.rate_limiter = RateLimiter(limit=60, window_s=60)
        self._lock = threading.RLock()
        # Chain reads are expensive on a slow link; the list view is served from
        # the mirror, and detail reads are cached briefly.
        self._bounty_cache: dict[int, tuple[float, Bounty | None]] = {}
        self._bounty_cache_ttl_s = 5.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _now_ns() -> int:
    return time.time_ns()


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / 10**9, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def _ctx(request: Request) -> AppContext:
    return request.app.state.pa


def api(fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)
    async def wrapper(request: Request) -> Response:
        try:
            result = await fn(request)
            return result
        except ProofArenaError as exc:
            return _json(exc.to_dict(), status=exc.http_status)
        except json.JSONDecodeError:
            return _json({"error": "E_PARAM", "message": "request body is not valid JSON"}, 422)

    return wrapper


async def _body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise ParamError(f"request body exceeds {MAX_BODY_BYTES} bytes")
    if not raw:
        raise ParamError("request body is required")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ParamError("request body must be a JSON object")
    return parsed


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise AuthError("missing bearer session token")
    return header[7:].strip()


def _session_account(request: Request) -> str:
    return verify_session(_bearer(request), secret=_ctx(request).session_secret)


def _require_owner(request: Request, challenge_id: str) -> tuple[str, Any]:
    account = _session_account(request)
    row = _ctx(request).store.get_challenge(challenge_id)
    if row is None:
        raise NotFound(f"challenge {challenge_id} not found")
    if row["owner"] != account:
        raise Unauthorized("only the challenge owner may perform this action")
    return account, row


def _agent(request: Request, *, challenge_id: str, role: str) -> dict[str, Any]:
    token = request.headers.get("x-agent-token", "")
    if not token:
        raise AuthError("missing X-Agent-Token header")
    token_hash = sha256_text(token)
    row = _ctx(request).store.get_agent_token(token_hash)
    if row is None:
        raise AuthError("agent token is unknown or revoked")
    if row["challenge_id"] != challenge_id:
        raise Unauthorized("agent token is scoped to a different challenge")
    if row["role"] != role:
        raise Unauthorized(f"agent token role is {row['role']!r}, expected {role!r}")
    if row["expires_ns"] < _now_ns():
        raise AuthError("agent token has expired")
    return {"account": row["agent_account"], "role": role, "challenge_id": challenge_id}


def _chain_bounty(ctx: AppContext, challenge_row: Any, *, use_cache: bool = True) -> Bounty | None:
    bounty_id = challenge_row["onchain_bounty_id"]
    if bounty_id is None:
        return None
    key = int(bounty_id)
    now = time.monotonic()
    if use_cache:
        cached = ctx._bounty_cache.get(key)
        if cached and now - cached[0] < ctx._bounty_cache_ttl_s:
            return cached[1]
    try:
        bounty = ctx.ledger.get_bounty(key)
    except (NotFound, ProofArenaError):
        bounty = None
    ctx._bounty_cache[key] = (now, bounty)
    return bounty


def _challenge_view(ctx: AppContext, row: Any, *, light: bool = False) -> dict[str, Any]:
    # `light` reads only the mirror: a list of N bounties must not cost N RPC calls.
    bounty = None if light else _chain_bounty(ctx, row)
    claims = ctx.store.list_claims(row["id"])
    patches = ctx.store.list_patches(row["id"])
    jobs = ctx.store.list_jobs(row["id"])
    state = bounty.effective_state.value if bounty else row["state"]
    return {
        "id": row["id"],
        "owner": row["owner"],
        "title": row["title"],
        "repo_url": row["repo_url"],
        "source_commit": row["source_commit"],
        "scope": json.loads(row["scope_json"]),
        "metadata_hash_hex": row["metadata_hash_hex"],
        "reward_yocto": row["reward_yocto"],
        "deadline_ns": row["deadline_ns"],
        "review_deadline_ns": row["review_deadline_ns"],
        "deadline_utc": _iso(row["deadline_ns"]),
        "review_deadline_utc": _iso(row["review_deadline_ns"]),
        "created_utc": _iso(row["created_ns"]),
        "network": row["network"],
        "contract_id": row["contract_id"],
        "onchain_bounty_id": row["onchain_bounty_id"],
        "state": state,
        "chain_is_simulated": ctx.chain_mode == "local",
        "claim_count": len(claims),
        "patch_count": len(patches),
        "jobs": [
            {
                "id": job["id"],
                "status": job["status"],
                "claim_id": job["claim_id"],
                "result_status": _job_status(job),
                "created_utc": _iso(job["created_ns"]),
            }
            for job in jobs
        ],
        "chain": bounty.public() if bounty else None,
    }


def _job_status(job: Any) -> str | None:
    if not job["result_json"]:
        return None
    return json.loads(job["result_json"]).get("status")


def _claim_view(ctx: AppContext, row: Any, *, include_private: bool = False) -> dict[str, Any]:
    patch = ctx.store.get_patch_for_claim(row["id"])
    view: dict[str, Any] = {
        "id": row["id"],
        "challenge_id": row["challenge_id"],
        "onchain_claim_id": row["onchain_claim_id"],
        "submitter": row["submitter"],
        "commitment_hex": row["commitment_hex"],
        "manifest_sha256": row["manifest_sha256"],
        "status": row["status"],
        "created_utc": _iso(row["created_ns"]),
        "patch": (
            {
                "id": patch["id"],
                "defender": patch["defender"],
                "base_commit": patch["base_commit"],
                "patched_commit": patch["patched_commit"],
                "diff_sha256": patch["diff_sha256"],
                "status": patch["status"],
            }
            if patch
            else None
        ),
    }
    if include_private:
        view["manifest"] = json.loads(row["manifest_json"])
        view["diff"] = patch["diff_text"] if patch else None
    return view


def _sync_challenge(ctx: AppContext, challenge_id: str, bounty: Bounty | None) -> None:
    if bounty is None:
        return
    ctx.store.update_challenge(challenge_id, state=bounty.effective_state.value)
    ctx._bounty_cache[int(bounty.bounty_id)] = (time.monotonic(), bounty)


def _index_receipts(ctx: AppContext, receipts: list[dict[str, Any]]) -> None:
    for receipt in receipts:
        ctx.store.index_receipt(receipt, network=ctx.ledger.network)


def _settle_async(ctx: AppContext, challenge_id: str, attempt_id: int, delay_s: float = 1.5) -> None:
    """Simulate async network finality so PayoutPending is observable."""

    def deliver() -> None:
        try:
            row = ctx.store.get_challenge(challenge_id)
            if row is None:
                return
            result = ctx.ledger.deliver_callback(
                bounty_id=int(row["onchain_bounty_id"]),
                attempt_id=attempt_id,
                result=SettlementResult.SUCCESS,
                note="local ledger transfer completed",
            )
            _index_receipts(ctx, result["receipts"])
            _sync_challenge(ctx, challenge_id, Bounty.from_dict(result["bounty"]))
            ctx.store.audit(
                actor="chain.local-ledger",
                action="settlement_callback_delivered",
                entity="challenge",
                entity_id=challenge_id,
                payload={"attempt_id": attempt_id, "state": result["bounty"]["state"]},
            )
        except ProofArenaError:
            # A failed callback is exactly the ambiguity the product must surface;
            # it is never retried automatically.
            ctx.store.audit(
                actor="chain.local-ledger",
                action="settlement_callback_failed",
                entity="challenge",
                entity_id=challenge_id,
                payload={"attempt_id": attempt_id},
            )

    if ctx.settlement_mode == "immediate":
        deliver()
        return
    if ctx.settlement_mode == "delayed":
        timer = threading.Timer(delay_s, deliver)
        timer.daemon = True
        timer.start()


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------
@api
async def health(request: Request) -> Response:
    ctx = _ctx(request)
    integrity = ctx.ledger.verify_receipt_chain()
    return _json(
        {
            "status": "ok" if integrity["valid"] else "degraded",
            "version": __import__("proofarena").__version__,
            "chain": {
                "mode": ctx.chain_mode,
                "network": ctx.ledger.network,
                "contract_id": ctx.ledger.contract_id,
                "receipt_chain_valid": integrity["valid"],
                "receipts": integrity["checked"],
            },
            "database": "ok",
        }
    )


@api
async def meta(request: Request) -> Response:
    ctx = _ctx(request)
    return _json(
        {
            "version": __import__("proofarena").__version__,
            "chain_mode": ctx.chain_mode,
            "chain_is_simulated": ctx.chain_mode == "local",
            "network_id": ctx.ledger.network,
            "contract_id": ctx.ledger.contract_id,
            "auth_domain": ctx.auth.domain,
            "settlement_mode": ctx.settlement_mode,
            "limits": {
                "max_reward_yocto": str(cfg.MAX_REWARD_YOCTO),
                "storage_fee_yocto": str(cfg.STORAGE_FEE_YOCTO),
                "runner_timeout_s": cfg.RUNNER_TIMEOUT_S,
                "max_body_bytes": MAX_BODY_BYTES,
            },
        }
    )


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------
@api
async def auth_register(request: Request) -> Response:
    ctx = _ctx(request)
    body = await _body(request)
    for field in ("account", "public_key_b64", "nonce", "signature"):
        if not isinstance(body.get(field), str) or not body[field]:
            raise ParamError(f"{field} is required")
    message = ctx.auth.register_key(
        account=body["account"],
        public_key_b64=body["public_key_b64"],
        nonce=body["nonce"],
        signature_b64=body["signature"],
    )
    return _json({"account": body["account"], "registered": True, "message": message})


@api
async def auth_challenge(request: Request) -> Response:
    ctx = _ctx(request)
    client = request.client.host if request.client else "unknown"
    ctx.rate_limiter.check(client)
    body = await _body(request)
    account = body.get("account")
    if not isinstance(account, str) or not account:
        raise ParamError("account is required")
    issued = ctx.auth.issue_login(account)
    return _json(
        {
            "account": account,
            "message": issued["message"],
            "expires_ns": issued["expires_ns"],
            "expires_utc": _iso(issued["expires_ns"]),
        }
    )


@api
async def auth_verify(request: Request) -> Response:
    ctx = _ctx(request)
    body = await _body(request)
    message, signature = body.get("message"), body.get("signature")
    if not isinstance(message, str) or not isinstance(signature, str):
        raise ParamError("message and signature are required")
    account = ctx.auth.verify_login(message=message, signature_b64=signature)
    token = issue_session(account=account, secret=ctx.session_secret)
    return _json({"token": token, "account": account, "token_type": "bearer"})


@api
async def auth_me(request: Request) -> Response:
    account = _session_account(request)
    return _json({"account": account})


# ---------------------------------------------------------------------------
# intents
# ---------------------------------------------------------------------------
@api
async def create_intent(request: Request) -> Response:
    ctx = _ctx(request)
    account = _session_account(request)
    body = await _body(request)
    action = body.get("action")
    payload = body.get("payload")
    if not isinstance(action, str) or not isinstance(payload, dict):
        raise ParamError("action and payload are required")
    issued = ctx.auth.issue_intent(account=account, action=action, payload=payload)
    return _json(
        {
            "action": action,
            "message": issued["message"],
            "expires_ns": issued["expires_ns"],
            "expires_utc": _iso(issued["expires_ns"]),
        }
    )


def _signed_intent(request: Request, body: dict[str, Any], action: str) -> tuple[str, dict[str, Any]]:
    message, signature = body.get("message"), body.get("signature")
    if not isinstance(message, str) or not isinstance(signature, str):
        raise ParamError("a signed message and signature are required for this action")
    return _ctx(request).auth.verify_intent(message=message, signature_b64=signature, action=action)


# ---------------------------------------------------------------------------
# challenges
# ---------------------------------------------------------------------------
def _parse_challenge_body(body: dict[str, Any]) -> dict[str, Any]:
    title = body.get("title")
    repo_url = body.get("repo_url")
    source_commit = body.get("source_commit")
    scope = body.get("scope")
    reward = body.get("reward_yocto")
    submission_s = body.get("submission_seconds")
    review_s = body.get("review_seconds")

    if not isinstance(title, str) or not (3 <= len(title.strip()) <= 120):
        raise ParamError("title must be 3..120 characters")
    if not isinstance(repo_url, str) or not (3 <= len(repo_url) <= 300):
        raise ParamError("repo_url must be 3..300 characters")
    if not isinstance(source_commit, str) or len(source_commit) != 40 or any(
        c not in "0123456789abcdef" for c in source_commit
    ):
        raise ParamError("source_commit must be a 40-character lowercase git sha")
    if not isinstance(scope, dict) or not scope:
        raise ParamError("scope must be a non-empty object")
    scope_blob = canonical_bytes(scope)
    if len(scope_blob) > 16_384:
        raise ParamError("scope exceeds 16 KB")
    try:
        reward_int = int(reward)
    except (TypeError, ValueError) as exc:
        raise ParamError("reward_yocto must be an integer string") from exc
    if reward_int <= 0 or reward_int > cfg.MAX_REWARD_YOCTO:
        raise ParamError("reward_yocto is outside the allowed range")
    if not isinstance(submission_s, int) or not (30 <= submission_s <= 7 * 86_400):
        raise ParamError("submission_seconds must be 30..604800")
    if not isinstance(review_s, int) or not (30 <= review_s <= 14 * 86_400):
        raise ParamError("review_seconds must be 30..1209600")
    return {
        "title": title.strip(),
        "repo_url": repo_url.strip(),
        "source_commit": source_commit,
        "scope": scope,
        "reward_yocto": reward_int,
        "submission_s": submission_s,
        "review_s": review_s,
    }


@api
async def create_challenge(request: Request) -> Response:
    ctx = _ctx(request)
    account = _session_account(request)
    body = await _body(request)
    parsed = _parse_challenge_body(body)

    challenge_id = "ch_" + secrets.token_hex(6)
    now = _now_ns()
    deadline_ns = now + parsed["submission_s"] * 10**9
    review_deadline_ns = deadline_ns + parsed["review_s"] * 10**9
    metadata = {
        "schema_version": "1.0",
        "challenge_id": challenge_id,
        "title": parsed["title"],
        "repo_url": parsed["repo_url"],
        "source_commit": parsed["source_commit"],
        "scope": parsed["scope"],
        "reward_yocto": str(parsed["reward_yocto"]),
        "network": ctx.ledger.network,
        "contract_id": ctx.ledger.contract_id,
        "owner": account,
        "deadline_ns": deadline_ns,
        "review_deadline_ns": review_deadline_ns,
    }
    metadata_hash = sha256_json(metadata)
    ctx.store.insert_challenge(
        {
            "id": challenge_id,
            "owner": account,
            "title": parsed["title"],
            "repo_url": parsed["repo_url"],
            "source_commit": parsed["source_commit"],
            "scope_json": json.dumps(parsed["scope"], sort_keys=True),
            "metadata_hash_hex": metadata_hash,
            "reward_yocto": str(parsed["reward_yocto"]),
            "deadline_ns": deadline_ns,
            "review_deadline_ns": review_deadline_ns,
            "network": ctx.ledger.network,
            "contract_id": ctx.ledger.contract_id,
            "onchain_bounty_id": None,
            "state": "Draft",
            "created_ns": now,
        }
    )
    ctx.store.audit(
        actor=account,
        action="challenge_drafted",
        entity="challenge",
        entity_id=challenge_id,
        payload={"metadata_hash_hex": metadata_hash, "reward_yocto": str(parsed["reward_yocto"])},
    )
    row = ctx.store.get_challenge(challenge_id)
    return _json(
        {
            "challenge": _challenge_view(ctx, row),
            "metadata": metadata,
            "metadata_hash_hex": metadata_hash,
            "next": "sign a fund_bounty intent and POST /fund",
        },
        201,
    )


@api
async def fund_challenge(request: Request) -> Response:
    ctx = _ctx(request)
    challenge_id = request.path_params["challenge_id"]
    account, row = _require_owner(request, challenge_id)
    body = await _body(request)
    signer, payload = _signed_intent(request, body, "fund_bounty")
    if signer != account:
        raise Unauthorized("signed intent account does not match the session")
    if payload.get("challenge_id") != challenge_id:
        raise ParamError("intent payload challenge_id does not match the request")
    if payload.get("metadata_hash_hex") != row["metadata_hash_hex"]:
        raise ParamError("intent metadata hash does not match the stored challenge")
    if row["onchain_bounty_id"] is not None:
        raise StateError("challenge is already funded")

    reward = int(row["reward_yocto"])
    result = ctx.ledger.create_bounty(
        signer=account,
        title=row["title"],
        reward_yocto=reward,
        metadata_hash_hex=row["metadata_hash_hex"],
        deadline_ns=row["deadline_ns"],
        review_deadline_ns=row["review_deadline_ns"],
        attached_yocto=reward + cfg.STORAGE_FEE_YOCTO,
    )
    bounty = Bounty.from_dict(result["bounty"])
    ctx.store.update_challenge(
        challenge_id, onchain_bounty_id=bounty.bounty_id, state=bounty.effective_state.value
    )
    _index_receipts(ctx, result["receipts"])
    ctx.store.audit(
        actor=account,
        action="challenge_funded",
        entity="challenge",
        entity_id=challenge_id,
        payload={"bounty_id": bounty.bounty_id, "reward_yocto": str(reward)},
    )
    return _json(
        {
            "challenge": _challenge_view(ctx, ctx.store.get_challenge(challenge_id)),
            "bounty": bounty.to_dict(),
            "receipts": result["receipts"],
        },
        201,
    )


@api
async def list_challenges(request: Request) -> Response:
    ctx = _ctx(request)
    rows = ctx.store.list_challenges()
    return _json(
        {"items": [_challenge_view(ctx, row, light=True) for row in rows], "count": len(rows)}
    )


@api
async def get_challenge(request: Request) -> Response:
    ctx = _ctx(request)
    row = ctx.store.get_challenge(request.path_params["challenge_id"])
    if row is None:
        raise NotFound("challenge not found")
    claims = ctx.store.list_claims(row["id"])
    jobs = ctx.store.list_jobs(row["id"])
    return _json(
        {
            "challenge": _challenge_view(ctx, row),
            "claims": [_claim_view(ctx, claim) for claim in claims],
            "jobs": [
                {
                    "id": job["id"],
                    "status": job["status"],
                    "claim_id": job["claim_id"],
                    "result_status": _job_status(job),
                    "error": job["error"],
                    "created_utc": _iso(job["created_ns"]),
                }
                for job in jobs
            ],
        }
    )


@api
async def issue_agent_token(request: Request) -> Response:
    ctx = _ctx(request)
    challenge_id = request.path_params["challenge_id"]
    account, row = _require_owner(request, challenge_id)
    if row["onchain_bounty_id"] is None:
        raise StateError("fund the challenge before issuing agent tokens")
    body = await _body(request)
    role = body.get("role")
    agent_account = body.get("agent_account")
    if role not in {"auditor", "defender"}:
        raise ParamError("role must be 'auditor' or 'defender'")
    if not isinstance(agent_account, str) or not (3 <= len(agent_account) <= 128):
        raise ParamError("agent_account must be 3..128 characters")
    token, token_hash = new_agent_token()
    expires = _now_ns() + 24 * 3600 * 10**9
    ctx.store.add_agent_token(token_hash, challenge_id, role, expires, agent_account)
    ctx.store.audit(
        actor=account,
        action="agent_token_issued",
        entity="challenge",
        entity_id=challenge_id,
        payload={"role": role, "agent_account": agent_account},
    )
    return _json(
        {
            "token": token,
            "role": role,
            "agent_account": agent_account,
            "challenge_id": challenge_id,
            "expires_ns": expires,
            "expires_utc": _iso(expires),
            "note": "returned once; only its hash is stored",
        },
        201,
    )


# ---------------------------------------------------------------------------
# claims and patches
# ---------------------------------------------------------------------------
@api
async def submit_claim(request: Request) -> Response:
    ctx = _ctx(request)
    challenge_id = request.path_params["challenge_id"]
    agent = _agent(request, challenge_id=challenge_id, role="auditor")
    row = ctx.store.get_challenge(challenge_id)
    if row is None:
        raise NotFound("challenge not found")
    if row["onchain_bounty_id"] is None:
        raise StateError("challenge is not funded")

    body = await _body(request)
    manifest = body.get("manifest")
    salt_hex = body.get("commitment_salt_hex")
    if not isinstance(manifest, dict):
        raise ParamError("manifest object is required")
    validate_claim_manifest(manifest)
    if not isinstance(salt_hex, str) or not (32 <= len(salt_hex) <= 128):
        raise ParamError("commitment_salt_hex must be 32..128 hex characters")

    if manifest["bounty_id"] != str(row["onchain_bounty_id"]):
        raise ParamError("manifest bounty_id does not match the funded bounty")
    if manifest["source_commit"] != row["source_commit"]:
        raise ParamError("manifest source_commit does not match the pinned challenge commit")

    commitment = sha256_hex(
        f"{salt_hex}:".encode("utf-8") + canonical_bytes(manifest)
    )
    existing = ctx.store.claim_by_commitment(challenge_id, commitment)
    if existing is not None:
        raise StateError("an identical claim was already submitted (duplicate commitment)")
    if len(ctx.store.list_claims(challenge_id)) >= 32:
        raise StateError("claim limit reached for this challenge")

    chain_result = ctx.ledger.commit_claim(
        signer=agent["account"],
        bounty_id=int(row["onchain_bounty_id"]),
        evidence_commitment_hex=commitment,
        manifest_sha256=sha256_json(manifest),
    )
    onchain_claim_id = chain_result["claim"]["claim_id"]
    claim_id = "cl_" + secrets.token_hex(6)
    ctx.store.insert_claim(
        {
            "id": claim_id,
            "challenge_id": challenge_id,
            "onchain_claim_id": onchain_claim_id,
            "submitter": agent["account"],
            "commitment_hex": commitment,
            "manifest_json": json.dumps(manifest, sort_keys=True),
            "manifest_sha256": sha256_json(manifest),
            "status": "Submitted",
            "created_ns": _now_ns(),
        }
    )
    _index_receipts(ctx, chain_result["receipts"])
    ctx.store.audit(
        actor=agent["account"],
        action="claim_submitted",
        entity="claim",
        entity_id=claim_id,
        payload={"commitment_hex": commitment, "manifest_sha256": sha256_json(manifest)},
    )
    return _json(
        {
            "claim": _claim_view(ctx, ctx.store.get_claim(claim_id)),
            "receipts": chain_result["receipts"],
        },
        201,
    )


@api
async def submit_patch(request: Request) -> Response:
    ctx = _ctx(request)
    claim = ctx.store.get_claim(request.path_params["claim_id"])
    if claim is None:
        raise NotFound("claim not found")
    challenge_id = claim["challenge_id"]
    agent = _agent(request, challenge_id=challenge_id, role="defender")
    row = ctx.store.get_challenge(challenge_id)
    if row is None:
        raise NotFound("challenge not found")
    if ctx.store.get_patch_for_claim(claim["id"]) is not None:
        raise StateError("a patch is already registered for this claim")

    body = await _body(request)
    diff = body.get("diff")
    base_commit = body.get("base_commit")
    patched_commit = body.get("patched_commit")
    if not isinstance(diff, str) or not diff.strip():
        raise ParamError("diff is required")
    if not isinstance(base_commit, str) or base_commit != row["source_commit"]:
        raise ParamError("base_commit must equal the pinned challenge commit")
    if not isinstance(patched_commit, str) or len(patched_commit) != 40:
        raise ParamError("patched_commit must be a 40-character commit id")

    out_of_scope = diff_touches_only(diff, {"ledger.py"})
    if out_of_scope:
        raise ParamError(f"patch touches files outside the authorized scope: {out_of_scope}")

    patch_id = "pt_" + secrets.token_hex(6)
    diff_sha = sha256_text(diff)
    ctx.store.insert_patch(
        {
            "id": patch_id,
            "challenge_id": challenge_id,
            "claim_id": claim["id"],
            "defender": agent["account"],
            "base_commit": base_commit,
            "patched_commit": patched_commit,
            "diff_sha256": diff_sha,
            "diff_text": diff,
            "status": "Submitted",
            "created_ns": _now_ns(),
        }
    )
    ctx.store.audit(
        actor=agent["account"],
        action="patch_submitted",
        entity="claim",
        entity_id=claim["id"],
        payload={"patch_id": patch_id, "diff_sha256": diff_sha},
    )
    return _json(
        {"patch": {"id": patch_id, "diff_sha256": diff_sha, "status": "Submitted"}}, 201
    )


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------
@api
async def verify_claim(request: Request) -> Response:
    ctx = _ctx(request)
    claim = ctx.store.get_claim(request.path_params["claim_id"])
    if claim is None:
        raise NotFound("claim not found")
    challenge_id = claim["challenge_id"]
    account, row = _require_owner(request, challenge_id)

    idempotency_key = request.headers.get("idempotency-key", "").strip()
    if not idempotency_key:
        raise ParamError("Idempotency-Key header is required for verification requests")
    if len(idempotency_key) > 128:
        raise ParamError("Idempotency-Key is too long")

    existing = ctx.store.job_by_idempotency(idempotency_key)
    if existing is not None:
        return _json(_job_payload(ctx, existing, replay=True))

    patch = ctx.store.get_patch_for_claim(claim["id"])
    if patch is None:
        raise StateError("the claim has no registered patch to verify")

    job_id = "job_" + secrets.token_hex(6)
    ctx.store.insert_job(
        {
            "id": job_id,
            "challenge_id": challenge_id,
            "claim_id": claim["id"],
            "status": "Running",
            "idempotency_key": idempotency_key,
            "created_ns": _now_ns(),
        }
    )
    try:
        outcome = _run_job(ctx, job_id=job_id, challenge_id=challenge_id, claim=claim, patch=patch)
    except (PatchError, ProofArenaError) as exc:
        ctx.store.finish_job(
            job_id, status="Failed", result=None, details=None, artifacts_dir=None, error=str(exc)
        )
        ctx.store.audit(
            actor="verifier",
            action="verification_failed",
            entity="claim",
            entity_id=claim["id"],
            payload={"job_id": job_id, "error": str(exc)},
        )
        return _json({"job_id": job_id, "status": "Failed", "error": str(exc)}, 422)

    validate_verification_result(outcome.result)
    ctx.store.finish_job(
        job_id,
        status="Succeeded",
        result=outcome.result,
        details=outcome.details,
        artifacts_dir=str(outcome.artifacts_dir),
    )
    ctx.store.update_claim_status(claim["id"], str(outcome.result["status"]))
    ctx.store.audit(
        actor="verifier",
        action="verification_completed",
        entity="claim",
        entity_id=claim["id"],
        payload={
            "job_id": job_id,
            "status": outcome.result["status"],
            "manifest_sha256": outcome.result["manifest_sha256"],
        },
    )
    return _json(_job_payload(ctx, ctx.store.get_job(job_id), replay=False), 201)


def _run_job(ctx: AppContext, *, job_id: str, challenge_id: str, claim: Any, patch: Any):
    job_dir = ctx.artifacts_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    applied_dir = job_dir / "patched-source"
    apply_unified_diff(
        source_dir=ctx.fixture_root / "baseline",
        diff_text=patch["diff_text"],
        target_dir=applied_dir,
    )
    spec = SandboxSpec(
        baseline_dir=ctx.fixture_root / "baseline",
        patched_dir=applied_dir,
        tests_dir=ctx.fixture_root / "tests",
        work_root=job_dir / "sandboxes",
    )
    return run_verification(
        job_id=job_id,
        claim_id=claim["id"],
        source_commit=ctx.store.get_challenge(challenge_id)["source_commit"],
        spec=spec,
        artifacts_root=ctx.artifacts_dir,
        scope_reference=f"{challenge_id}#scope",
        timeout_s=cfg.RUNNER_TIMEOUT_S,
    )


def _job_payload(ctx: AppContext, job: Any, *, replay: bool) -> dict[str, Any]:
    result = json.loads(job["result_json"]) if job["result_json"] else None
    details = json.loads(job["details_json"]) if job["details_json"] else None
    return {
        "job_id": job["id"],
        "challenge_id": job["challenge_id"],
        "claim_id": job["claim_id"],
        "status": job["status"],
        "result": result,
        "details": details,
        "artifacts_dir": job["artifacts_dir"],
        "error": job["error"],
        "idempotent_replay": replay,
        "approval_eligible": bool(details and details.get("approval_eligible")),
        "chain_is_simulated": ctx.chain_mode == "local",
    }


@api
async def get_verification(request: Request) -> Response:
    ctx = _ctx(request)
    job = ctx.store.get_job(request.path_params["job_id"])
    if job is None:
        raise NotFound("verification job not found")
    row = ctx.store.get_challenge(job["challenge_id"])
    account = _session_account(request)
    if row is None or row["owner"] != account:
        raise Unauthorized("only the challenge owner may read verification results")
    return _json(_job_payload(ctx, job, replay=False))


@api
async def get_job_logs(request: Request) -> Response:
    ctx = _ctx(request)
    job = ctx.store.get_job(request.path_params["job_id"])
    if job is None:
        raise NotFound("verification job not found")
    row = ctx.store.get_challenge(job["challenge_id"])
    account = _session_account(request)
    if row is None or row["owner"] != account:
        raise Unauthorized("only the challenge owner may read raw runner logs")
    name = request.query_params.get("file", "patched.log")
    if name not in {"baseline-1.log", "baseline-2.log", "patched.log", "regression.log", "manifest.json", "diff.patch"}:
        raise ParamError("unknown artifact name")
    if not job["artifacts_dir"]:
        raise NotFound("job has no artifacts")
    path = Path(job["artifacts_dir"]) / name
    if not path.exists():
        raise NotFound(f"artifact {name} not found")
    data = path.read_text(encoding="utf-8", errors="replace")
    return PlainTextResponse(data[:200_000])


# ---------------------------------------------------------------------------
# owner approvals and settlement
# ---------------------------------------------------------------------------
def _require_claim_for_bounty(ctx: AppContext, challenge_row: Any, claim_id: str) -> Any:
    claim = ctx.store.get_claim(claim_id)
    if claim is None:
        raise NotFound("claim not found")
    if claim["challenge_id"] != challenge_row["id"]:
        raise StateError("claim does not belong to this challenge")
    if claim["onchain_claim_id"] is None:
        raise StateError("claim has no on-chain id")
    return claim


@api
async def start_review(request: Request) -> Response:
    """Owner-only transition to Review (closes submissions before the deadline).

    No funds move, so a session is sufficient; it is audited and owner-gated.
    """
    ctx = _ctx(request)
    challenge_id = request.path_params["challenge_id"]
    account, row = _require_owner(request, challenge_id)
    if row["onchain_bounty_id"] is None:
        raise StateError("challenge is not funded")
    result = ctx.ledger.mark_review(
        signer=account, bounty_id=int(row["onchain_bounty_id"])
    )
    bounty = Bounty.from_dict(result["bounty"])
    _index_receipts(ctx, result["receipts"])
    _sync_challenge(ctx, challenge_id, bounty)
    ctx.store.audit(
        actor=account,
        action="review_started",
        entity="challenge",
        entity_id=challenge_id,
        payload={"state": bounty.state.value},
    )
    return _json(
        {
            "challenge": _challenge_view(ctx, ctx.store.get_challenge(challenge_id)),
            "bounty": bounty.to_dict(),
            "receipts": result["receipts"],
        }
    )


@api
async def approve_claim(request: Request) -> Response:
    ctx = _ctx(request)
    challenge_id = request.path_params["challenge_id"]
    account, row = _require_owner(request, challenge_id)
    body = await _body(request)
    signer, payload = _signed_intent(request, body, "approve_claim")
    if signer != account:
        raise Unauthorized("signed intent account does not match the session")
    if payload.get("challenge_id") != challenge_id:
        raise ParamError("intent payload challenge_id does not match the request")
    claim_id = payload.get("claim_id")
    if not isinstance(claim_id, str):
        raise ParamError("intent payload must include claim_id")
    claim = _require_claim_for_bounty(ctx, row, claim_id)

    result = ctx.ledger.approve_claim(
        signer=account,
        bounty_id=int(row["onchain_bounty_id"]),
        claim_id=int(claim["onchain_claim_id"]),
    )
    bounty = Bounty.from_dict(result["bounty"])
    _index_receipts(ctx, result["receipts"])
    _sync_challenge(ctx, challenge_id, bounty)
    ctx.store.update_claim_status(claim["id"], "Approved")
    ctx.store.audit(
        actor=account,
        action="claim_approved",
        entity="challenge",
        entity_id=challenge_id,
        payload={"claim_id": claim["id"], "bounty_id": bounty.bounty_id, "state": bounty.state.value},
    )
    return _json(
        {
            "challenge": _challenge_view(ctx, ctx.store.get_challenge(challenge_id)),
            "bounty": bounty.to_dict(),
            "receipts": result["receipts"],
        }
    )


@api
async def request_payout(request: Request) -> Response:
    ctx = _ctx(request)
    challenge_id = request.path_params["challenge_id"]
    account, row = _require_owner(request, challenge_id)
    body = await _body(request)
    signer, payload = _signed_intent(request, body, "request_payout")
    if signer != account:
        raise Unauthorized("signed intent account does not match the session")
    if payload.get("challenge_id") != challenge_id:
        raise ParamError("intent payload challenge_id does not match the request")

    result = ctx.ledger.request_payout(
        signer=account, bounty_id=int(row["onchain_bounty_id"])
    )
    attempt = result["attempt"]
    _index_receipts(ctx, result["receipts"])
    bounty = ctx.ledger.get_bounty(int(row["onchain_bounty_id"]))
    _sync_challenge(ctx, challenge_id, bounty)
    ctx.store.audit(
        actor=account,
        action="payout_requested",
        entity="challenge",
        entity_id=challenge_id,
        payload={"attempt_id": attempt["attempt_id"], "state": bounty.state.value},
    )
    _settle_async(ctx, challenge_id, int(attempt["attempt_id"]))
    return _json(
        {
            "attempt": attempt,
            "state": bounty.effective_state.value,
            "note": "PayoutPending — a submitted transfer is not a completed payment",
            "receipts": result["receipts"],
        },
        202,
    )


@api
async def refund_challenge(request: Request) -> Response:
    ctx = _ctx(request)
    challenge_id = request.path_params["challenge_id"]
    account, row = _require_owner(request, challenge_id)
    body = await _body(request)
    signer, payload = _signed_intent(request, body, "refund_expired")
    if signer != account:
        raise Unauthorized("signed intent account does not match the session")
    if payload.get("challenge_id") != challenge_id:
        raise ParamError("intent payload challenge_id does not match the request")

    result = ctx.ledger.refund_expired(signer=account, bounty_id=int(row["onchain_bounty_id"]))
    _index_receipts(ctx, result["receipts"])
    bounty = ctx.ledger.get_bounty(int(row["onchain_bounty_id"]))
    _sync_challenge(ctx, challenge_id, bounty)
    ctx.store.audit(
        actor=account,
        action="refund_requested",
        entity="challenge",
        entity_id=challenge_id,
        payload={"attempt_id": result["attempt"]["attempt_id"]},
    )
    _settle_async(ctx, challenge_id, int(result["attempt"]["attempt_id"]))
    return _json(
        {
            "attempt": result["attempt"],
            "state": bounty.effective_state.value,
            "receipts": result["receipts"],
            "note": "RefundPending — the refund is not final until the callback settles",
        },
        202,
    )


@api
async def reconcile_challenge(request: Request) -> Response:
    ctx = _ctx(request)
    challenge_id = request.path_params["challenge_id"]
    account, row = _require_owner(request, challenge_id)
    body = await _body(request)
    signer, payload = _signed_intent(request, body, "reconcile")
    if signer != account:
        raise Unauthorized("signed intent account does not match the session")
    if payload.get("challenge_id") != challenge_id:
        raise ParamError("intent payload challenge_id does not match the request")
    resolution = payload.get("resolution")
    evidence = payload.get("evidence_sha256")
    note = payload.get("note")
    if resolution not in {"paid", "refunded"}:
        raise ParamError("resolution must be 'paid' or 'refunded'")
    if not isinstance(evidence, str) or not isinstance(note, str):
        raise ParamError("evidence_sha256 and note are required")

    result = ctx.ledger.reconcile(
        signer=account,
        bounty_id=int(row["onchain_bounty_id"]),
        resolution=resolution,
        evidence_sha256=evidence,
        note=note,
    )
    bounty = Bounty.from_dict(result["bounty"])
    _index_receipts(ctx, result["receipts"])
    _sync_challenge(ctx, challenge_id, bounty)
    ctx.store.audit(
        actor=account,
        action="reconciled",
        entity="challenge",
        entity_id=challenge_id,
        payload={"resolution": resolution, "state": bounty.state.value},
    )
    return _json({"bounty": bounty.to_dict(), "receipts": result["receipts"]})


@api
async def set_dispute(request: Request) -> Response:
    ctx = _ctx(request)
    challenge_id = request.path_params["challenge_id"]
    account, row = _require_owner(request, challenge_id)
    body = await _body(request)
    signer, payload = _signed_intent(request, body, "set_dispute")
    if signer != account:
        raise Unauthorized("signed intent account does not match the session")
    disputed = payload.get("disputed")
    if not isinstance(disputed, bool):
        raise ParamError("disputed must be a boolean")

    result = ctx.ledger.set_dispute(
        signer=account, bounty_id=int(row["onchain_bounty_id"]), disputed=disputed
    )
    bounty = Bounty.from_dict(result["bounty"])
    _index_receipts(ctx, result["receipts"])
    _sync_challenge(ctx, challenge_id, bounty)
    return _json({"bounty": bounty.to_dict(), "receipts": result["receipts"]})


@api
async def deliver_callback(request: Request) -> Response:
    """Internal settlement callback delivery (simulated network finality)."""
    ctx = _ctx(request)
    token = request.headers.get("x-internal-token", "")
    if not secrets.compare_digest(token, ctx.internal_token):
        raise Unauthorized("internal token required")
    challenge_id = request.path_params["challenge_id"]
    row = ctx.store.get_challenge(challenge_id)
    if row is None or row["onchain_bounty_id"] is None:
        raise NotFound("funded challenge not found")
    body = await _body(request)
    attempt_id = body.get("attempt_id")
    result_raw = body.get("result", "success")
    if not isinstance(attempt_id, int):
        raise ParamError("attempt_id must be an integer")
    try:
        result = SettlementResult(result_raw)
    except ValueError as exc:
        raise ParamError("result must be success, failure or ambiguous") from exc

    outcome = ctx.ledger.deliver_callback(
        bounty_id=int(row["onchain_bounty_id"]), attempt_id=attempt_id, result=result
    )
    bounty = Bounty.from_dict(outcome["bounty"])
    _index_receipts(ctx, outcome["receipts"])
    _sync_challenge(ctx, challenge_id, bounty)
    return _json({"bounty": bounty.to_dict(), "receipts": outcome["receipts"]})


# ---------------------------------------------------------------------------
# evidence, timeline and receipts
# ---------------------------------------------------------------------------
def _safe_audit(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "actor": row["actor"],
        "action": row["action"],
        "entity": row["entity"],
        "entity_id": row["entity_id"],
        "payload": json.loads(row["payload_json"]),
        "created_utc": _iso(row["created_ns"]),
    }


@api
async def timeline(request: Request) -> Response:
    ctx = _ctx(request)
    row = ctx.store.get_challenge(request.path_params["challenge_id"])
    if row is None:
        raise NotFound("challenge not found")
    events: list[dict[str, Any]] = []
    for item in ctx.store.audit_events(row["id"]):
        events.append({"kind": "app", **_safe_audit(item)})
    if row["onchain_bounty_id"] is not None:
        for receipt in ctx.ledger.receipts(bounty_id=int(row["onchain_bounty_id"])):
            events.append(
                {
                    "kind": "chain",
                    "receipt_id": receipt["receipt_id"],
                    "event": receipt["kind"],
                    "bounty_id": receipt["bounty_id"],
                    "attempt_id": receipt.get("attempt_id"),
                    "hash": receipt["hash"],
                    "prev_hash": receipt["prev_hash"],
                    "payload": receipt.get("data", {}),
                    "created_utc": _iso(receipt["ts_ns"]),
                    "network": receipt["network"],
                    "simulated": receipt["network"] == "local-ledger",
                }
            )
    return _json(
        {
            "challenge_id": row["id"],
            "state": row["state"],
            "events": events,
            "note": "chain events from the local ledger are simulated, not testnet",
        }
    )


@api
async def private_evidence(request: Request) -> Response:
    ctx = _ctx(request)
    challenge_id = request.path_params["challenge_id"]
    account, row = _require_owner(request, challenge_id)
    claims = ctx.store.list_claims(challenge_id)
    jobs = ctx.store.list_jobs(challenge_id)
    ctx.store.audit(
        actor=account,
        action="private_evidence_read",
        entity="challenge",
        entity_id=challenge_id,
        payload={"claims": len(claims)},
    )
    return _json(
        {
            "challenge_id": challenge_id,
            "claims": [_claim_view(ctx, claim, include_private=True) for claim in claims],
            "jobs": [
                {
                    "id": job["id"],
                    "status": job["status"],
                    "artifacts_dir": job["artifacts_dir"],
                    "result": json.loads(job["result_json"]) if job["result_json"] else None,
                    "details": json.loads(job["details_json"]) if job["details_json"] else None,
                }
                for job in jobs
            ],
            "warning": "Private evidence. Do not publish without coordinated disclosure.",
        }
    )


@api
async def challenge_receipts(request: Request) -> Response:
    ctx = _ctx(request)
    row = ctx.store.get_challenge(request.path_params["challenge_id"])
    if row is None:
        raise NotFound("challenge not found")
    if row["onchain_bounty_id"] is None:
        return _json({"receipts": [], "note": "challenge is not funded"})
    receipts = ctx.ledger.receipts(bounty_id=int(row["onchain_bounty_id"]))
    return _json(
        {
            "receipts": receipts,
            "receipt_chain": ctx.ledger.verify_receipt_chain(),
            "simulated": True,
        }
    )


@api
async def chain_integrity(request: Request) -> Response:
    ctx = _ctx(request)
    return _json(
        {
            "integrity": ctx.ledger.verify_receipt_chain(),
            "network": ctx.ledger.network,
            "mode": ctx.chain_mode,
        }
    )


@api
async def chain_receipts(request: Request) -> Response:
    ctx = _ctx(request)
    limit = min(int(request.query_params.get("limit", 100)), 500)
    return _json({"receipts": ctx.ledger.receipts(limit=limit), "network": ctx.ledger.network})



# ---------------------------------------------------------------------------
# browser-wallet flow: no key on the server
# ---------------------------------------------------------------------------
WALLET_ACTION_METHODS = {
    "fund_bounty": "create_bounty",
    "review": "mark_review",
    "approve_claim": "approve_claim",
    "request_payout": "request_payout",
    "refund_expired": "refund_expired",
    "set_dispute": "set_dispute",
    "reconcile": "reconcile",
}


def _require_wallet_chain(ctx: AppContext) -> Any:
    """Wallet signing only makes sense on a real chain."""
    if not hasattr(ctx.ledger, "record_wallet_tx"):
        raise StateError(
            "wallet signing requires NEAR testnet mode (PROOFARENA_CHAIN_MODE=near-testnet)"
        )
    return ctx.ledger


def _tx_plan(ctx: AppContext, row: Any, action: str, extras: dict[str, Any]) -> TxPlan:
    ledger = _require_wallet_chain(ctx)
    contract = ledger.contract_id
    bounty_id = row["onchain_bounty_id"]

    if action == "fund_bounty":
        if bounty_id is not None:
            raise StateError("challenge is already funded")
        reward = int(row["reward_yocto"])
        args = {
            "title": row["title"],
            "metadata_hash_hex": row["metadata_hash_hex"],
            "reward_yocto": str(reward),
            "deadline_ns": int(row["deadline_ns"]),
            "review_deadline_ns": int(row["review_deadline_ns"]),
        }
        return TxPlan(action, contract, "create_bounty", args, reward + cfg.STORAGE_FEE_YOCTO, GAS_100_TGAS)

    if bounty_id is None:
        raise StateError("fund the challenge before this action")

    if action == "review":
        return TxPlan(action, contract, "mark_review", {"bounty_id": int(bounty_id)}, 0, GAS_100_TGAS)

    if action == "approve_claim":
        claim = _require_claim_for_bounty(ctx, row, str(extras.get("claim_id") or ""))
        return TxPlan(
            action,
            contract,
            "approve_claim",
            {"bounty_id": int(bounty_id), "claim_id": int(claim["onchain_claim_id"])},
            0,
            GAS_100_TGAS,
        )

    if action == "request_payout":
        return TxPlan(action, contract, "request_payout", {"bounty_id": int(bounty_id)}, 0, GAS_100_TGAS)

    if action == "refund_expired":
        return TxPlan(action, contract, "refund_expired", {"bounty_id": int(bounty_id)}, 0, GAS_100_TGAS)

    if action == "set_dispute":
        disputed = extras.get("disputed")
        if not isinstance(disputed, bool):
            raise ParamError("disputed must be a boolean")
        return TxPlan(
            action,
            contract,
            "set_dispute",
            {"bounty_id": int(bounty_id), "disputed": disputed},
            0,
            GAS_100_TGAS,
        )

    if action == "reconcile":
        resolution = str(extras.get("resolution") or "")
        evidence = str(extras.get("evidence_sha256") or "")
        note = str(extras.get("note") or "")
        if resolution not in {"paid", "refunded"}:
            raise ParamError("resolution must be 'paid' or 'refunded'")
        if not evidence or not note:
            raise ParamError("evidence_sha256 and note are required")
        return TxPlan(
            action,
            contract,
            "reconcile",
            {
                "bounty_id": int(bounty_id),
                "resolution": resolution,
                "evidence_sha256": evidence,
                "note": note,
            },
            0,
            GAS_100_TGAS,
        )

    raise ParamError(f"unknown wallet action {action!r}")


@api
async def wallet_nonce(request: Request) -> Response:
    """Step 1 of wallet login: hand the browser a nonce to sign (NEP-413)."""
    ctx = _ctx(request)
    body = await _body(request)
    account = body.get("account_id")
    if not isinstance(account, str) or not account:
        raise ParamError("account_id is required")
    nonce = secrets.token_bytes(32)
    nonce_b58 = b58encode(nonce)
    message = f"Sign in to ProofArena as {account}"
    recipient = ctx.auth.domain
    issued = _now_ns()
    expires = issued + 5 * 60 * 10**9
    ctx.store.put_nonce(
        nonce=nonce_b58,
        account=account,
        domain=recipient,
        purpose="wallet_login",
        message=message,
        expires_ns=expires,
    )
    return _json(
        {
            "account_id": account,
            "nonce": nonce_b58,
            "message": message,
            "recipient": recipient,
            "expires_ns": expires,
            "sign_as": "NEP-413",
        }
    )


@api
async def wallet_verify(request: Request) -> Response:
    """Step 2: verify the wallet's signMessage result against its on-chain keys."""
    ctx = _ctx(request)
    body = await _body(request)
    account = str(body.get("account_id") or "")
    public_key = str(body.get("public_key") or "")
    signature = str(body.get("signature") or "")
    nonce_b58 = str(body.get("nonce") or "")
    message = str(body.get("message") or "")
    recipient = str(body.get("recipient") or ctx.auth.domain)
    if not all([account, public_key, signature, nonce_b58, message]):
        raise ParamError("account_id, public_key, signature, nonce and message are required")

    ledger = _require_wallet_chain(ctx)
    try:
        nonce = b58decode(nonce_b58)
    except Exception as exc:
        raise ParamError("nonce is not valid base58") from exc

    verify_nep413_signature(
        public_key_ed25519=public_key,
        signature_b64=signature,
        message=message,
        nonce=nonce,
        recipient=recipient,
    )

    # The key must actually belong to the account right now.
    try:
        keys = ledger.rpc.view_access_key_list(account)
    except Exception as exc:
        raise AuthError(f"cannot read access keys for {account!r}: {str(exc)[:160]}") from exc
    if public_key not in {item.get("public_key") for item in keys}:
        raise AuthError(f"{public_key} is not an access key on {account!r}")

    consumed = ctx.store.consume_nonce(
        nonce_b58, purpose="wallet_login", domain=recipient, now=_now_ns()
    )
    if consumed is None:
        raise AuthError("nonce is expired, unknown or already used")

    token = issue_session(account=account, secret=ctx.session_secret)
    ctx.store.audit(
        actor=account,
        action="wallet_login_nep413",
        entity="user",
        entity_id=account,
        payload={"public_key": public_key},
    )
    return _json({"token": token, "account": account, "token_type": "bearer"})


@api
async def wallet_intent(request: Request) -> Response:
    """Return the exact transaction the browser wallet should sign and send."""
    ctx = _ctx(request)
    challenge_id = request.path_params["challenge_id"]
    account, row = _require_owner(request, challenge_id)
    body = await _body(request)
    action = str(body.get("action") or "")
    plan = _tx_plan(ctx, row, action, body)
    return _json(
        {
            "plan": plan.to_public(),
            "signer": account,
            "note": "sign and send this with your wallet, then POST the tx hash to /wallet-confirm",
        }
    )


@api
async def wallet_confirm(request: Request) -> Response:
    """Verify the wallet's transaction on chain, then update the mirror."""
    ctx = _ctx(request)
    challenge_id = request.path_params["challenge_id"]
    account, row = _require_owner(request, challenge_id)
    body = await _body(request)
    action = str(body.get("action") or "")
    tx_hash = str(body.get("tx_hash") or "")
    plan = _tx_plan(ctx, row, action, body)
    ledger = _require_wallet_chain(ctx)

    result = verify_wallet_tx(
        ledger.rpc,
        tx_hash=tx_hash,
        contract_id=ledger.contract_id,
        signer_account=account,
        plan=plan,
    )
    logs: list[str] = []
    for item in result.get("receipts_outcome", []) or []:
        logs.extend(item.get("outcome", {}).get("logs", []) or [])

    bounty = None
    bounty_id = row["onchain_bounty_id"]
    if action == "fund_bounty":
        # create_bounty allocates the id on chain; read it back rather than guessing.
        ids = ledger.rpc.call_view_optional(ledger.contract_id, "list_bounty_ids", {}) or []
        bounty_id = max((int(i) for i in ids), default=None)
        if bounty_id is not None:
            ctx.store.update_challenge(challenge_id, onchain_bounty_id=bounty_id)
            row = ctx.store.get_challenge(challenge_id)
    if bounty_id is not None:
        try:
            bounty = ledger.get_bounty(int(bounty_id))
        except ProofArenaError:
            bounty = None
    if bounty is not None:
        _sync_challenge(ctx, challenge_id, bounty)

    receipt = ledger.record_wallet_tx(
        kind={
            "fund_bounty": "bounty_created",
            "review": "review_started",
            "approve_claim": "claim_approved",
            "request_payout": "payout_requested",
            "refund_expired": "refund_requested",
            "set_dispute": "dispute_changed",
            "reconcile": "reconciled",
        }.get(action, "wallet_tx"),
        tx_hash=tx_hash,
        bounty_id=int(bounty_id) if bounty_id is not None else None,
        logs=logs,
    )
    ctx.store.audit(
        actor=account,
        action=f"wallet_tx_{action}",
        entity="challenge",
        entity_id=challenge_id,
        payload={"tx_hash": tx_hash, "method": plan.method},
    )
    return _json(
        {
            "tx_hash": tx_hash,
            "receipt": receipt,
            "challenge": _challenge_view(ctx, ctx.store.get_challenge(challenge_id)),
            "bounty": bounty.to_dict() if bounty else None,
            "verified": True,
        }
    )


def routes() -> list[Route]:
    return [
        Route("/health", health, methods=["GET"]),
        Route("/api/meta", meta, methods=["GET"]),
        Route("/auth/register", auth_register, methods=["POST"]),
        Route("/auth/challenge", auth_challenge, methods=["POST"]),
        Route("/auth/verify", auth_verify, methods=["POST"]),
        Route("/auth/me", auth_me, methods=["GET"]),
        Route("/auth/wallet/nonce", wallet_nonce, methods=["POST"]),
        Route("/auth/wallet/verify", wallet_verify, methods=["POST"]),
        Route(
            "/v1/challenges/{challenge_id}/wallet-intent", wallet_intent, methods=["POST"]
        ),
        Route(
            "/v1/challenges/{challenge_id}/wallet-confirm", wallet_confirm, methods=["POST"]
        ),
        Route("/v1/intents", create_intent, methods=["POST"]),
        Route("/v1/challenges", create_challenge, methods=["POST"]),
        Route("/v1/challenges", list_challenges, methods=["GET"]),
        Route("/v1/challenges/{challenge_id}", get_challenge, methods=["GET"]),
        Route("/v1/challenges/{challenge_id}/fund", fund_challenge, methods=["POST"]),
        Route(
            "/v1/challenges/{challenge_id}/agent-token", issue_agent_token, methods=["POST"]
        ),
        Route("/v1/challenges/{challenge_id}/claims", submit_claim, methods=["POST"]),
        Route("/v1/challenges/{challenge_id}/timeline", timeline, methods=["GET"]),
        Route(
            "/v1/challenges/{challenge_id}/private-evidence",
            private_evidence,
            methods=["GET"],
        ),
        Route(
            "/v1/challenges/{challenge_id}/receipts", challenge_receipts, methods=["GET"]
        ),
        Route("/v1/challenges/{challenge_id}/approve", approve_claim, methods=["POST"]),
        Route("/v1/challenges/{challenge_id}/review", start_review, methods=["POST"]),        Route("/v1/challenges/{challenge_id}/payout", request_payout, methods=["POST"]),
        Route("/v1/challenges/{challenge_id}/refund", refund_challenge, methods=["POST"]),
        Route(
            "/v1/challenges/{challenge_id}/reconcile", reconcile_challenge, methods=["POST"]
        ),
        Route("/v1/challenges/{challenge_id}/dispute", set_dispute, methods=["POST"]),
        Route(
            "/v1/challenges/{challenge_id}/deliver-callback",
            deliver_callback,
            methods=["POST"],
        ),
        Route("/v1/claims/{claim_id}/patches", submit_patch, methods=["POST"]),
        Route("/v1/claims/{claim_id}/verify", verify_claim, methods=["POST"]),
        Route("/v1/verification/{job_id}", get_verification, methods=["GET"]),
        Route("/v1/verification/{job_id}/logs", get_job_logs, methods=["GET"]),
        Route("/v1/chain/integrity", chain_integrity, methods=["GET"]),
        Route("/v1/chain/receipts", chain_receipts, methods=["GET"]),
    ]


def create_app(
    *,
    db_path: Path | None = None,
    chain_db_path: Path | None = None,
    artifacts_dir: Path | None = None,
    fixture_root: Path | None = None,
    settlement_mode: str | None = None,
    internal_token: str | None = None,
    session_secret: bytes | None = None,
    domain: str | None = None,
    network: str | None = None,
    contract_id: str | None = None,
    chain_mode: str | None = None,
    include_web: bool = True,
) -> Starlette:
    cfg.ensure_dirs()
    db = Path(db_path) if db_path else cfg.DB_PATH
    chain_db = Path(chain_db_path) if chain_db_path else db.with_name("chain.sqlite3")
    artifacts = Path(artifacts_dir) if artifacts_dir else cfg.ARTIFACT_DIR
    fixtures = Path(fixture_root) if fixture_root else cfg.ROOT / "fixture"
    domain = domain or cfg.AUTH_DOMAIN
    mode = (chain_mode or cfg.CHAIN_MODE).strip().lower()
    if mode in {"near-testnet", "near", "testnet"}:
        mode = "near-testnet"
        network = network or "near-testnet"
        contract_id = contract_id or cfg.NEAR_CONTRACT_ID
        if not contract_id:
            raise ChainUnavailable(
                "NEAR_CONTRACT_ID is not set. Deploy the contract first "
                "(docs/TESTNET_RUNBOOK.md), then export NEAR_CONTRACT_ID."
            )
        # On testnet the contract dispatches its own settlement callback, so no
        # local timer is used; the UI polls the chain state instead.
        settlement_mode = settlement_mode or "manual"
    else:
        mode = "local"
        network = network or cfg.NETWORK_ID
        contract_id = contract_id or cfg.CONTRACT_ID
        settlement_mode = settlement_mode or "delayed"

    store = Store(db)
    ledger = open_chain(chain_db, contract_id=contract_id, network=network, mode=mode)
    auth = WalletAuth(store=store, domain=domain, network=network, contract_id=contract_id)
    if mode == "near-testnet":
        # Bind API identity to real on-chain key ownership: the key being
        # registered must be an access key on the account. Without this, anyone
        # could register a key for "someone-else.testnet".
        def _check_onchain_key(account: str, public_key_ed25519: str) -> None:
            try:
                ledger.rpc.view_access_key(account, public_key_ed25519)
            except Exception as exc:
                raise AuthError(
                    f"key is not an access key on {account!r} (on-chain ownership check failed): "
                    f"{str(exc)[:160]}"
                ) from exc

        auth.onchain_key_checker = _check_onchain_key
    context = AppContext(
        store=store,
        ledger=ledger,
        auth=auth,
        artifacts_dir=artifacts,
        fixture_root=fixtures,
        settlement_mode=settlement_mode,
        internal_token=internal_token or secrets.token_urlsafe(24),
        session_secret=session_secret or cfg.session_secret(),
        chain_mode=mode,
    )

    all_routes = routes()
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["*"],
        )
    ]
    app = Starlette(routes=all_routes, middleware=middleware)
    app.state.pa = context

    if include_web:
        from .web import add_web_routes

        add_web_routes(app)

    return app
