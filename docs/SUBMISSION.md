# Hackathon submission checklist (truthful)

> **Verify the actual rules and cutoff on the organizer's own page before submitting.** This file
> records what the project can and cannot claim; it is not a statement of eligibility (NEARHunt
> states a November 1–14, 2026 window — confirm it yourself).

## What can be submitted today

| Required artefact | Status | Where |
|---|---|---|
| Working source tree | yes | repository root |
| Exact install/test/run commands | yes | `README.md` |
| Contract source (Rust / near-sdk) | yes, **uncompiled** | `contracts/escrow/src/lib.rs` |
| Contract test suite | written, **not executed** | `#[cfg(test)] mod tests` in the same file |
| Backend API | yes, tested | `proofarena/api.py` |
| Frontend | yes, server-rendered (not Next.js) | `proofarena/web.py`, `templates/` |
| Reproducible fixture + verifier runner | yes, tested | `fixture/`, `proofarena/runner.py` |
| `.env.example` with no real secrets | yes | `.env.example` |
| Status board with per-task evidence | yes | `communications/STATUS_BOARD.md` |
| Handoffs, ADRs, threat model, QA matrix | yes | `communications/`, `docs/` |
| Local end-to-end run | yes | `scripts/e2e_demo.py` |
| Release verdict | `READY FOR TESTNET DEMO` | README |

## What cannot be claimed

- ❌ NEAR testnet deployment — **BLOCKED** (no toolchain, no funded account, no RPC).
- ❌ A real on-chain payout transaction or explorer link.
- ❌ Contract unit/Sandbox test results.
- ❌ Browser E2E test or screenshots (no browser automation available; none were fabricated).
- ❌ Next.js frontend or FastAPI backend (documented deviations: ADR-0002, ADR-0003).
- ❌ "Audited", "trustless", "production-ready".

## Submission text skeleton (fill only what is true)

> **ProofArena** — verifiable AI security work with owner-approved NEAR escrow.
> An owner funds a bounty for an authorized fixture; an auditor submits a reproducible failing test;
> an independent verifier re-runs it; a defender patches; the verifier re-runs before/after; the owner
> signs the approval with their wallet; escrow settles exactly once.
> This build runs the complete workflow locally (82 tests + end-to-end demo) against a clearly
> labelled ledger simulator. The near-sdk escrow contract is written and reviewed but not yet compiled
> or deployed; testnet settlement is the next milestone and is reported as BLOCKED, not as done.

## Post-submission TODO (in priority order)

1. Install Rust + wasm32 target; run `cargo test` on `contracts/escrow`; fix whatever the compiler
   finds; publish the wasm hash.
2. Deploy to testnet with the owner's funded account; record funding, approval and payout receipts.
3. Implement `NearTestnetAdapter` behind the existing `ChainAdapter` protocol.
4. Replace the demo signer with a real browser wallet (NEAR wallet selector).
5. Add Docker-based runner isolation (ADR-0005) and re-run RUN-001..RUN-003 in a container.
6. Optional: port the UI to Next.js and the API to FastAPI (ADRs 0002/0003 already describe the swap).
7. Record a 3–5 minute demo following `docs/DEMO_SCRIPT.md`.
