"""Local wallet signer — the stand-in for a browser wallet extension.

Runs as its own process on ``PROOFARENA_WALLET_PORT`` (default 8788) and holds
the demo owner's Ed25519 key in ``wallet/owner.key.json`` (git-ignored). The API
never receives the key, only signatures.

Safety rails:

* only signs messages whose ``domain``/``network``/``contract_id`` match the
  configured demo environment,
* refuses unknown purposes,
* requires an allowed ``Origin`` for browser calls,
* never signs a mainnet or unknown-contract message.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from proofarena.canonical import b64decode, b64encode  # noqa: E402


class OwnerWallet:
    def __init__(self, key_path: Path, account: str) -> None:
        self.key_path = key_path
        self.account = account
        self._key = self._load_or_create()

    def _load_or_create(self) -> Ed25519PrivateKey:
        if self.key_path.exists():
            raw = json.loads(self.key_path.read_text(encoding="utf-8"))
            return Ed25519PrivateKey.from_private_bytes(b64decode(raw["private_key_b64"]))
        key = Ed25519PrivateKey.generate()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.write_text(
            json.dumps(
                {
                    "account": self.account,
                    "private_key_b64": b64encode(
                        key.private_bytes(
                            encoding=Encoding.Raw,
                            format=PrivateFormat.Raw,
                            encryption_algorithm=NoEncryption(),
                        )
                    ),
                    "warning": "LOCAL DEMO KEY ONLY. Never use for real funds.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    @property
    def public_key_b64(self) -> str:
        return b64encode(self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    def sign(self, message: str) -> str:
        return b64encode(self._key.sign(message.encode("utf-8")))


def create_signer_app(
    *,
    account: str,
    key_path: Path,
    domain: str,
    network: str,
    contract_id: str,
    allowed_origins: list[str],
) -> Starlette:
    wallet = OwnerWallet(key_path, account)

    def _allowed(request: Request) -> bool:
        origin = request.headers.get("origin")
        return origin is None or origin in allowed_origins

    async def status(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "account": wallet.account,
                "public_key_b64": wallet.public_key_b64,
                "domain": domain,
                "network": network,
                "contract_id": contract_id,
                "key_path": str(key_path),
                "warning": "Local demo signer. No real funds. Testnet/mainnet not configured.",
            }
        )

    async def sign(request: Request) -> JSONResponse:
        if not _allowed(request):
            return JSONResponse({"error": "E_ORIGIN", "message": "origin not allowed"}, 403)
        try:
            body: dict[str, Any] = await request.json()
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
            ("domain", domain),
            ("network", network),
            ("contract_id", contract_id),
        ):
            if parsed.get(field) != expected:
                return JSONResponse(
                    {
                        "error": "E_REFUSED",
                        "message": f"refusing message with {field}={parsed.get(field)!r}, expected {expected!r}",
                    },
                    403,
                )
        return JSONResponse(
            {
                "account": wallet.account,
                "public_key_b64": wallet.public_key_b64,
                "message": message,
                "signature": wallet.sign(message),
            }
        )

    async def register_proof(request: Request) -> JSONResponse:
        """Sign a proof-of-possession message for key registration."""
        if not _allowed(request):
            return JSONResponse({"error": "E_ORIGIN", "message": "origin not allowed"}, 403)
        nonce = secrets.token_hex(16)
        message = json.dumps(
            {
                "purpose": "register",
                "account": wallet.account,
                "nonce": nonce,
                "domain": domain,
                "network": network,
                "contract_id": contract_id,
                "issued_ns": 0,
                "expires_ns": 0,
                "payload": {"public_key_b64": wallet.public_key_b64},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return JSONResponse(
            {
                "account": wallet.account,
                "public_key_b64": wallet.public_key_b64,
                "nonce": nonce,
                "message": message,
                "signature": wallet.sign(message),
            }
        )

    app = Starlette(
        routes=[
            Route("/status", status, methods=["GET"]),
            Route("/sign", sign, methods=["POST"]),
            Route("/register-proof", register_proof, methods=["POST"]),
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
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ProofArena local owner wallet signer")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PROOFARENA_WALLET_PORT", "8788")))
    parser.add_argument("--account", default=os.environ.get("PROOFARENA_DEMO_OWNER", "owner.demo.local"))
    parser.add_argument(
        "--key", default=os.environ.get("PROOFARENA_WALLET_KEY", str(ROOT / "wallet" / "owner.key.json"))
    )
    parser.add_argument("--domain", default=os.environ.get("PROOFARENA_AUTH_DOMAIN", "proofarena.local"))
    parser.add_argument("--network", default=os.environ.get("PROOFARENA_NETWORK", "local-ledger"))
    parser.add_argument(
        "--contract", default=os.environ.get("PROOFARENA_CONTRACT_ID", "escrow.proofarena.local")
    )
    args = parser.parse_args(argv)

    import uvicorn

    app = create_signer_app(
        account=args.account,
        key_path=Path(args.key),
        domain=args.domain,
        network=args.network,
        contract_id=args.contract,
        allowed_origins=[
            "http://127.0.0.1:8787",
            "http://localhost:8787",
            f"http://127.0.0.1:{args.port}",
        ],
    )
    print(f"[wallet] account={args.account} port={args.port} key={args.key}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
