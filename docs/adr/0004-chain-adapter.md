# ADR-0004 — Chain adapter: local ledger now, NEAR testnet blocked

- **Status:** accepted; testnet path explicitly BLOCKED
- **Date:** 2026-11-11
- **Tasks:** PA-004, PA-005, PA-017

## Context

The kit's primary deliverable is a NEAR testnet MVP with a real transfer. This environment has no
Rust toolchain, no `near` CLI, no `near-sandbox`, no funded testnet account and no outbound RPC
access. A real deployment is not merely inconvenient here — it is impossible.

## Decision

1. Implement the escrow state machine once, as pure Python (`proofarena/chain/engine.py`), and test
   it adversarially (41 tests: SEC-001..SEC-010, AT-001..AT-010, fuzzing).
2. Persist it through `LocalLedger`, a SQLite adapter that writes a **hash-chained receipt log**.
   Receipts are `local-000001…`, `network="local-ledger"`, and every UI surface that shows them also
   shows a "LOCAL SIMULATOR — not NEAR testnet" banner.
3. Write the Rust contract (`contracts/escrow/src/lib.rs`) against near-sdk 5.9 encoding identical
   rules, with a unit-test module. Mark it `IMPLEMENTED_UNTESTED` because it was never compiled.
4. Make the adapter boundary explicit: `open_chain(mode=...)` raises `E_CHAIN_UNAVAILABLE` for any
   mode other than `local`. Tests assert this refusal.

## Invariants the local adapter must satisfy (all tested)

- `paid + refunded <= reward` per bounty; escrow balance is derived and re-checked on every write.
- Payout and refund are mutually exclusive and terminal.
- A settlement attempt id is persisted before any transfer; callbacks must match it and be
  self-dispatched (`ctx.is_self`), which is the local analogue of `#[private]`.
- Ambiguous outcome → `NeedsReconciliation`, no automatic retry, no refund escape hatch.
- Receipt chain is tamper-evident: `verify_receipt_chain()` recomputes every link.

## What this is not

`LocalLedger` is **not** a NEAR emulator: no gas, no async receipts, no account storage costs, no
cryptographic signatures on transactions. It models *settlement semantics*, not chain mechanics.
The honest claim is "the workflow and the money rules are executed locally", never "the transfer
happened on testnet".

## Unblocking NEAR testnet (owner actions required)

1. Rust + `wasm32-unknown-unknown` toolchain, network access to crates.io.
2. `cargo test` in `contracts/escrow`, then `cargo near build` (or the wasm32 build) → `.wasm`.
3. A funded testnet account; `near deploy --initFunction new`.
4. Implement `NearTestnetAdapter` (same protocol as `LocalLedger`), map signed intents to NEAR
   `Action`s, then record funding → approval → payout receipts.
5. Replace the demo wallet key with the owner's real testnet wallet signing in a browser.

Until step 5 is executed and receipts exist, the release verdict stays at
`READY FOR TESTNET DEMO`, never `VERIFIED_TESTNET`.
