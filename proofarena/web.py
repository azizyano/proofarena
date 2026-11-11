"""Server-rendered pages (PA-013).

Next.js is not installable offline (ADR-0002), so the UI is server-rendered
Jinja2 with progressive enhancement. Routes and state labels match the PRD:

``/`` landing, ``/bounties`` list, ``/bounties/new`` create+fund,
``/bounties/:id`` owner review, ``/arena/:id`` agent progress,
``/evidence/:claimId`` permissioned report, ``/transactions/:receiptId`` trace.

Authenticated data is fetched by the browser with a bearer session token; the
server-side pages never embed a token or the owner key.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from . import __version__, config as cfg
from .api import AppContext, _challenge_view, _claim_view, _ctx, _iso, _job_status

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def _fmt_near(yocto: str | int) -> str:
    try:
        value = int(yocto)
    except (TypeError, ValueError):
        return "—"
    whole, frac = divmod(value, cfg.YOCTO_PER_NEAR)
    if frac == 0:
        return f"{whole} NEAR"
    return f"{whole}.{str(frac).rjust(24, '0')[:4].rstrip('0') or '0'} NEAR"


templates.env.filters["fmt_near"] = _fmt_near


def _common(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    return {
        "meta": {
            "version": __version__,
            "chain_mode": ctx.chain_mode,
            "network": ctx.ledger.network,
            "contract_id": ctx.ledger.contract_id,
            "settlement_mode": ctx.settlement_mode,
            "chain_is_simulated": ctx.chain_mode == "local",
            "explorer_base": cfg.NEAR_EXPLORER_BASE,
            "wallet_url": f"http://127.0.0.1:{cfg.WALLET_PORT}",
        },
        "nav": [
            ("/", "Overview"),
            ("/bounties", "Bounties"),
            ("/bounties/new", "Create"),
            ("/status", "Status"),
        ],
    }


async def index(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    rows = ctx.store.list_challenges(limit=6)
    cards = [_challenge_view(ctx, row, light=True) for row in rows]
    return templates.TemplateResponse(
        request,
        "index.html",
        {**_common(request), "challenges": cards, "count": len(rows)},
    )


async def bounty_list(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    rows = ctx.store.list_challenges(limit=100)
    cards = [_challenge_view(ctx, row, light=True) for row in rows]
    return templates.TemplateResponse(
        request, "bounties.html", {**_common(request), "challenges": cards}
    )


async def bounty_new(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "new.html", _common(request))


async def bounty_detail(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    row = ctx.store.get_challenge(request.path_params["challenge_id"])
    if row is None:
        return templates.TemplateResponse(
            request, "not_found.html", {**_common(request), "what": "bounty"}, status_code=404
        )
    claims = [_claim_view(ctx, claim) for claim in ctx.store.list_claims(row["id"])]
    jobs = []
    for job in ctx.store.list_jobs(row["id"]):
        jobs.append(
            {
                "id": job["id"],
                "status": job["status"],
                "claim_id": job["claim_id"],
                "result_status": _job_status(job),
                "error": job["error"],
                "created_utc": _iso(job["created_ns"]),
            }
        )
    receipts = []
    if row["onchain_bounty_id"] is not None:
        receipts = ctx.ledger.receipts(bounty_id=int(row["onchain_bounty_id"]))
    return templates.TemplateResponse(
        request,
        "bounty_detail.html",
        {
            **_common(request),
            "challenge": _challenge_view(ctx, row),
            "claims": claims,
            "jobs": jobs,
            "receipts": receipts,
            "owner": row["owner"],
        },
    )


async def arena(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    row = ctx.store.get_challenge(request.path_params["challenge_id"])
    if row is None:
        return templates.TemplateResponse(
            request, "not_found.html", {**_common(request), "what": "arena"}, status_code=404
        )
    events: list[dict[str, Any]] = []
    for item in ctx.store.audit_events(row["id"]):
        events.append(
            {
                "kind": "app",
                "action": item["action"],
                "actor": item["actor"],
                "created_utc": _iso(item["created_ns"]),
                "payload": json.loads(item["payload_json"]),
            }
        )
    if row["onchain_bounty_id"] is not None:
        for receipt in ctx.ledger.receipts(bounty_id=int(row["onchain_bounty_id"])):
            events.append(
                {
                    "kind": "chain",
                    "action": receipt["kind"],
                    "actor": receipt["network"],
                    "created_utc": _iso(receipt["ts_ns"]),
                    "payload": receipt.get("data", {}),
                    "receipt_id": receipt["receipt_id"],
                    "hash": receipt["hash"],
                }
            )
    return templates.TemplateResponse(
        request,
        "arena.html",
        {
            **_common(request),
            "challenge": _challenge_view(ctx, row),
            "events": events,
        },
    )


async def evidence(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    claim = ctx.store.get_claim(request.path_params["claim_id"])
    if claim is None:
        return templates.TemplateResponse(
            request, "not_found.html", {**_common(request), "what": "claim"}, status_code=404
        )
    challenge = ctx.store.get_challenge(claim["challenge_id"])
    public_claim = _claim_view(ctx, claim)
    return templates.TemplateResponse(
        request,
        "evidence.html",
        {
            **_common(request),
            "claim": public_claim,
            "challenge": _challenge_view(ctx, challenge),
            "claim_id": claim["id"],
        },
    )


async def transaction(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    receipt_id = request.path_params["receipt_id"]
    try:
        receipt = ctx.ledger.receipt(receipt_id)
    except Exception:
        return templates.TemplateResponse(
            request, "not_found.html", {**_common(request), "what": "receipt"}, status_code=404
        )
    bounty = None
    if receipt.get("bounty_id"):
        try:
            bounty = ctx.ledger.get_bounty(int(receipt["bounty_id"]))
        except Exception:
            bounty = None
    integrity = ctx.ledger.verify_receipt_chain()
    return templates.TemplateResponse(
        request,
        "transaction.html",
        {
            **_common(request),
            "receipt": receipt,
            "receipt_json": json.dumps(receipt, indent=2, sort_keys=True),
            "bounty": bounty,
            "integrity": integrity,
        },
    )


async def status_page(request: Request) -> HTMLResponse:
    ctx = _ctx(request)
    integrity = ctx.ledger.verify_receipt_chain()
    return templates.TemplateResponse(
        request,
        "status.html",
        {
            **_common(request),
            "integrity": integrity,
            "backend": "STARLETTE (FastAPI unavailable offline)",
            "rows": [
                ("App version", __version__),
                ("Chain mode", ctx.chain_mode),
                ("Explorer", cfg.NEAR_EXPLORER_BASE if ctx.chain_mode != "local" else "n/a (local simulator)"),
                ("Network label", ctx.ledger.network),
                ("Contract label", ctx.ledger.contract_id),
                ("Settlement mode", ctx.settlement_mode),
                ("Receipts", str(integrity["checked"])),
                ("Receipt chain", "valid" if integrity["valid"] else "BROKEN"),
                ("Wallet signer", f"http://127.0.0.1:{cfg.WALLET_PORT}"),
            ],
        },
    )


async def api_health(request: Request) -> JSONResponse:
    ctx = _ctx(request)
    return JSONResponse({"status": "ok", "chain": ctx.ledger.verify_receipt_chain()})


def add_web_routes(app: Any) -> None:
    app.routes.extend(
        [
            Route("/", index, methods=["GET"]),
            Route("/bounties", bounty_list, methods=["GET"]),
            Route("/bounties/new", bounty_new, methods=["GET"]),
            Route("/bounties/{challenge_id}", bounty_detail, methods=["GET"]),
            Route("/arena/{challenge_id}", arena, methods=["GET"]),
            Route("/evidence/{claim_id}", evidence, methods=["GET"]),
            Route("/transactions/{receipt_id}", transaction, methods=["GET"]),
            Route("/status", status_page, methods=["GET"]),
            Mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static"),
        ]
    )
