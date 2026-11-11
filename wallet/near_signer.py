"""NEAR testnet signer service — the testnet counterpart of ``wallet/signer.py``.

Runs on ``127.0.0.1:8788``, owns the owner's NEAR key (read from the standard
credentials file), and is the **only** process that signs anything. The API talks
to it over HTTP; the browser talks to it for login/intent signatures.

Endpoints
---------
``GET  /status``          account, public key, contract, network (never the key)
``POST /sign``            sign a canonical login/intent message
``GET  /register-proof``  build and sign a key-registration proof
``POST /call``            submit an allowlisted contract call

Guards on ``/call``: contract must match, method must be in the allowlist,
deposit must be under the cap, gas under the protocol max, rate limited per
minute, and every call is written to an audit log (method, deposit, tx hash — no
key material, no arguments).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from proofarena.near.rpc import NearRpc, RpcError  # noqa: E402
from proofarena.near.wallet import (  # noqa: E402
    DEFAULT_RPC,
    DEFAULT_GAS,
    NativeWallet,
    WalletError,
    load_key,
)

CALLABLE_METHODS = frozenset(
    {
        "create_bounty",
        "commit_claim",
        "mark_review",
        "approve_claim",
        "request_payout",
        "refund_expired",
        "reconcile",
        "set_dispute",
    }
)
MAX_DEPOSIT_YOCTO = 150 * 10**24  # hard cap per call, regardless of configuration
MAX_SIGNED_MESSAGES_PER_MINUTE = 30
MAX_CALLS_PER_MINUTE = 10


class SignerState:
    def __init__(
        self,
        *,
        account_id: str,
        contract_id: str,
        key_path: Path | None,
        rpc_url: str,
        domain: str,
        network: str,
        audit_path: Path,
        allow_full: bool = False,
    ) -> None:
        self.account_id = account_id
        self.contract_id = contract_id
        self.domain = domain
        self.network = network
        self.audit_path = audit_path
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.rpc = NearRpc(rpc_url or DEFAULT_RPC)
        self.key = load_key(account_id, path=key_path)
        self.wallet = NativeWallet(account_id=account_id, key=self.key, rpc=self.rpc)
        self.allow_full = allow_full
        self._sign_times: list[float] = []
        self._call_times: list[float] = []

    def rate_limit(self, bucket: str, limit: int) -> None:
        now = time.monotonic()
        times = self._sign_times if bucket == "sign" else self._call_times
        times[:] = [t for t in times if now - t < 60]
        if len(times) >= limit:
            raise WalletError(f"signer rate limit reached ({limit}/minute)")
        times.append(now)

    def audit(self, record: dict[str, Any]) -> None:
        record = {"ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **record}
        try:
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            pass


def create_signer_app(
    state: SignerState,
    *,
    allowed_origins: list[str],
) -> Starlette:
    def _origin_ok(request: Request) -> bool:
        origin = request.headers.get("origin")
        return origin is None or origin in allowed_origins

    async def status(request: Request) -> JSONResponse:
        try:
            balance = state.wallet.balance_yocto()
        except Exception as exc:  # RPC down should not hide the signer
            balance = None
            balance_note = str(exc)[:200]
        else:
            balance_note = None
        return JSONResponse(
            {
                "account": state.account_id,
                "public_key": state.key.public_key_ed25519,
                "contract_id": state.contract_id,
                "network": state.network,
                "domain": state.domain,
                "rpc": state.rpc.url,
                "balance_yocto": str(balance) if balance is not None else None,
                "balance_error": balance_note,
                "callable_methods": sorted(CALLABLE_METHODS),
                "max_deposit_yocto": str(MAX_DEPOSIT_YOCTO),
                "warning": "TESTNET ONLY. Local signer; no key material is returned.",
            }
        )

    async def register_proof(request: Request) -> JSONResponse:
        if not _origin_ok(request):
            return JSONResponse({"error": "E_ORIGIN", "message": "origin not allowed"}, 403)
        nonce = secrets.token_hex(16)
        message = json.dumps(
            {
                "purpose": "register",
                "account": state.account_id,
                "nonce": nonce,
                "domain": state.domain,
                "network": state.network,
                "contract_id": state.contract_id,
                "issued_ns": 0,
                "expires_ns": 0,
                "payload": {"public_key_b64": _public_b64(state)},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return JSONResponse(
            {
                "account": state.account_id,
                "public_key_b64": _public_b64(state),
                "public_key": state.key.public_key_ed25519,
                "nonce": nonce,
                "message": message,
                "signature": _sign_b64(state, message),
            }
        )

    async def sign_message(request: Request) -> JSONResponse:
        if not _origin_ok(request):
            return JSONResponse({"error": "E_ORIGIN", "message": "origin not allowed"}, 403)
        try:
            state.rate_limit("sign", MAX_SIGNED_MESSAGES_PER_MINUTE)
        except WalletError as exc:
            return JSONResponse({"error": "E_RATE", "message": str(exc)}, 429)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "E_PARAM", "message": "invalid JSON"}, 422)
        message = body.get("message")
        if not isinstance(message, str):
            return JSONResponse({"error": "E_PARAM", "message": "message is required"}, 422)
        try:
            parsed = json.loads(message)
        except json.JSONDecodeError:
            return JSONResponse({"error": "E_PARAM", "message": "message is not JSON"}, 422)
        purpose = parsed.get("purpose")
        if purpose not in {"login", "intent", "register"}:
            return JSONResponse(
                {"error": "E_REFUSED", "message": f"refusing unknown purpose {purpose!r}"}, 403
            )
        for field, expected in (
            ("domain", state.domain),
            ("network", state.network),
            ("contract_id", state.contract_id),
        ):
            if parsed.get(field) != expected:
                return JSONResponse(
                    {
                        "error": "E_REFUSED",
                        "message": f"refusing message with {field}={parsed.get(field)!r}",
                    },
                    403,
                )
        if parsed.get("account") != state.account_id:
            return JSONResponse(
                {"error": "E_REFUSED", "message": "refusing to sign for another account"}, 403
            )
        state.audit({"kind": "signed", "purpose": purpose, "action": parsed.get("action")})
        return JSONResponse(
            {
                "account": state.account_id,
                "public_key_b64": _public_b64(state),
                "public_key": state.key.public_key_ed25519,
                "message": message,
                "signature": _sign_b64(state, message),
            }
        )

    async def call(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "E_PARAM", "message": "invalid JSON"}, 422)

        contract_id = body.get("contract_id")
        method = body.get("method")
        args = body.get("args") or {}
        if contract_id != state.contract_id:
            state.audit({"kind": "refused", "reason": "contract", "method": method})
            return JSONResponse(
                {"error": "E_REFUSED", "message": f"contract must be {state.contract_id!r}"}, 403
            )
        if method not in CALLABLE_METHODS and not state.allow_full:
            state.audit({"kind": "refused", "reason": "method", "method": method})
            return JSONResponse(
                {"error": "E_REFUSED", "message": f"method {method!r} is not allowed"}, 403
            )
        if not isinstance(args, dict):
            return JSONResponse({"error": "E_PARAM", "message": "args must be an object"}, 422)
        try:
            deposit = int(body.get("deposit_yocto", 0))
            gas = int(body.get("gas", DEFAULT_GAS))
        except (TypeError, ValueError):
            return JSONResponse({"error": "E_PARAM", "message": "deposit/gas must be integers"}, 422)
        if deposit < 0 or deposit > MAX_DEPOSIT_YOCTO:
            return JSONResponse(
                {"error": "E_LIMIT", "message": f"deposit must be 0..{MAX_DEPOSIT_YOCTO}"}, 422
            )
        try:
            state.rate_limit("call", MAX_CALLS_PER_MINUTE)
        except WalletError as exc:
            return JSONResponse({"error": "E_RATE", "message": str(exc)}, 429)

        try:
            outcome = state.wallet.call(method, args, deposit_yocto=deposit, gas=gas)
        except WalletError as exc:
            state.audit({"kind": "failed", "method": method, "deposit_yocto": str(deposit), "error": str(exc)[:200]})
            return JSONResponse(
                {"error": "E_TX", "message": str(exc), "success": False}, 502
            )
        state.audit(
            {
                "kind": "sent",
                "method": method,
                "deposit_yocto": str(deposit),
                "tx_hash": outcome.tx_hash,
            }
        )
        return JSONResponse(
            {
                "tx_hash": outcome.tx_hash,
                "success": outcome.success,
                "status": outcome.status,
                "logs": outcome.logs,
                "error": outcome.error,
                "explorer_url": f"https://explorer.testnet.near.org/transactions/{outcome.tx_hash}",
            }
        )

    return Starlette(
        routes=[
            Route("/status", status, methods=["GET"]),
            Route("/sign", sign_message, methods=["POST"]),
            Route("/register-proof", register_proof, methods=["GET", "POST"]),
            Route("/call", call, methods=["POST"]),
        ],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=allowed_origins,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["*"],
            )
        ],
    )


def _public_b64(state: SignerState) -> str:
    import base64

    return base64.b64encode(state.wallet.key.public_key).decode("ascii")


def _sign_b64(state: SignerState, message: str) -> str:
    import base64

    return base64.b64encode(state.wallet.key.sign(message.encode("utf-8"))).decode("ascii")


def build_state(args: argparse.Namespace) -> SignerState:
    account = args.account or os.environ.get("NEAR_TESTNET_ACCOUNT")
    contract = args.contract or os.environ.get("NEAR_CONTRACT_ID")
    if not account or not contract:
        raise SystemExit(
            "NEAR_TESTNET_ACCOUNT and NEAR_CONTRACT_ID must be set "
            "(or pass --account/--contract). See docs/TESTNET_RUNBOOK.md."
        )
    return SignerState(
        account_id=account,
        contract_id=contract,
        key_path=Path(args.key) if args.key else None,
        rpc_url=args.rpc,
        domain=args.domain,
        network=args.network,
        audit_path=Path(args.audit),
        allow_full=args.allow_full,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ProofArena NEAR testnet signer")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PROOFARENA_WALLET_PORT", "8788")))
    parser.add_argument("--account", default=os.environ.get("NEAR_TESTNET_ACCOUNT"))
    parser.add_argument("--contract", default=os.environ.get("NEAR_CONTRACT_ID"))
    parser.add_argument("--key", default=os.environ.get("NEAR_CREDENTIALS"))
    parser.add_argument("--rpc", default=os.environ.get("NEAR_RPC_URL", DEFAULT_RPC))
    parser.add_argument("--domain", default=os.environ.get("PROOFARENA_AUTH_DOMAIN", "proofarena.local"))
    parser.add_argument("--network", default="near-testnet")
    parser.add_argument(
        "--audit",
        default=str(ROOT / "evidence" / "near-testnet" / "signer-audit.jsonl"),
    )
    parser.add_argument("--allow-full", action="store_true", help="do not restrict method names")
    args = parser.parse_args(argv)

    import uvicorn

    state = build_state(args)
    app = create_signer_app(
        state,
        allowed_origins=[
            "http://127.0.0.1:8787",
            "http://localhost:8787",
            f"http://127.0.0.1:{args.port}",
        ],
    )
    print(
        f"[near-signer] account={state.account_id} contract={state.contract_id} "
        f"public_key={state.key.public_key_ed25519} port={args.port}",
        flush=True,
    )
    print("[near-signer] the private key stays in this process; no key is ever returned", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
