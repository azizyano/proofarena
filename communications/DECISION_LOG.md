# Decision Log (append-only)

Newest last. Every entry that changes architecture, contract state machine, schema, privacy policy
or bounty trust assumptions requires an ADR reference.

## 2026-11-11 — D-001 — Build target is the executable off-chain system
- **Context:** environment inventory showed no network, no Rust, no Docker, no NEAR CLI.
- **Decision:** implement and test the full workflow locally; keep the Rust contract as reviewed
  source marked `IMPLEMENTED_UNTESTED`; refuse any fake chain artefact.
- **Alternatives:** fake receipts (rejected — violates kit rule), ship spec-only docs (rejected —
  the kit forbids a zip of documentation).
- **ADR:** 0001, 0004.

## 2026-11-11 — D-002 — Starlette instead of FastAPI
- **Decision:** build the API on Starlette + Pydantic v2 with identical route/response shapes.
- **Why:** `fastapi` is not installable offline; Starlette is FastAPI's own foundation.
- **Cost:** no generated OpenAPI document yet.
- **ADR:** 0003.

## 2026-11-11 — D-003 — Jinja2 SSR instead of Next.js
- **Decision:** server-rendered pages, no build step, vanilla JS.
- **Why:** npm registry unreachable; an unbuildable Next.js tree would be decoration.
- **Cost:** no React ecosystem; porting later is mechanical.
- **ADR:** 0002.

## 2026-11-11 — D-004 — Owner approval is a signed intent, not a session action
- **Decision:** every privileged action requires an Ed25519 signature over a canonical message that
  binds `domain`, `network`, `contract_id`, `action`, ids and a single-use nonce.
- **Why:** a session cookie proves "logged in", not "the owner approved this specific payout". This
  is the local analogue of a wallet signing a NEAR `Action`.
- **Consequence:** the API has no code path that can sign for the owner. Replays are refused because
  the nonce is consumed atomically.
- **ADR:** 0001 (interface freeze), enforced in `proofarena/auth/wallet.py`.

## 2026-11-11 — D-005 — Seeded defect is a unit-mismatch over-withdrawal
- **Decision:** the fixture compares a whole-unit request against a cent balance, so 400 units can be
  withdrawn from a 5.00-unit balance (100× over-withdrawal, negative account).
- **Why:** reachable, deterministic, economically meaningful, and fixable by a five-line patch that
  the regression suite proves does not break normal use. Matches the kit's "allowance invariant"
  suggestion.
- **Rejected:** a pure math/hash property (no reachable impact) and a crash-only defect (weak impact).

## 2026-11-11 — D-006 — Local ledger receipts are labelled, never disguised
- **Decision:** receipt ids are `local-000001…`, `network="local-ledger"`, `mode="local"`, the UI
  shows a persistent banner, `/api/meta` exposes `chain_is_simulated: true`, and
  `open_chain(mode!="local")` raises `E_CHAIN_UNAVAILABLE`.
- **Why:** the kit's strongest prohibition is presenting simulated settlement as a real transaction.

## 2026-11-11 — D-007 — Defect (bug) fixed during the build: signer/server message drift
- **Context:** live registration failed with "signature does not verify".
- **Cause:** the signer included `payload` in the registration message; the server's
  `registration_message` did not. Different bytes, valid signature, failed verification.
- **Fix:** server now builds the same explicit object; `tests/test_signer.py` asserts byte equality.
- **Lesson recorded:** integration bugs hide between two implementations of "the same" canonical
  format; add a golden-byte test across the boundary.
