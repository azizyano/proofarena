# ENGINEERING HANDOFF — PA-005 / PA-004

- **From:** NEAR Contract agent
- **To:** Independent Verifier / Security (CC: CTO, QA)
- **Status:** TESTED_LOCAL (reference engine) · IMPLEMENTED_UNTESTED (Rust contract)
- **Scope:** Escrow settlement rules: owner-only authority, single winner, exactly-once payout,
  exclusive refund, pending/reconciliation states.

## Files

- `proofarena/chain/engine.py` — pure state machine (authoritative, tested)
- `proofarena/chain/models.py` — `BountyState`, `ClaimState`, `Attempt`, `CallContext`
- `proofarena/chain/ledger.py` — SQLite adapter, hash-chained receipts, simulated finality
- `contracts/escrow/src/lib.rs` — near-sdk 5.9 contract, same rules (uncompiled)
- `contracts/escrow/Cargo.toml`
- `tests/test_escrow_engine.py`

## Commands executed

```
python -m pytest tests/test_escrow_engine.py -q
python -m pytest tests/test_api.py -q
python scripts/e2e_demo.py
```

## Results

- `test_escrow_engine.py` → **41 passed**.
- `test_api.py` → **20 passed**.
- `e2e_demo.py` → `PATCH_VERIFIED`, then `Paid` exactly once, 6 receipts, chain valid.
- `cargo test` → **not run**: `cargo --version` exits 1 (no toolchain), crates.io unreachable.

## Security implications

1. The Python engine is the executable specification; the Rust contract is a reviewed translation
   with **no executed evidence**. Any divergence is a bug in the contract.
2. `PayoutPending` is committed before the transfer promise; the callback is id-matched and
   self-dispatched only (`ctx.is_self` in Python, `#[private]` in Rust).
3. `AMBIGUOUS` never retries and never falls back to a refund.
4. Storage reserve (0.001 NEAR/bounty) is tracked separately from the reward liability; a payout can
   never consume it.
5. Exact-deposit rule (`reward + reserve`) avoids stranded surplus funds.

## Remaining blockers

- Toolchain + network to compile and run `cargo test` on the contract.
- `near-sandbox` to exercise promise callbacks and transfer failure for real.
- Funded testnet account for the deployment receipt.

## Reviewer action needed

- [ ] Diff the Rust state transitions against the Python engine (table in the engine docstring).
- [ ] Confirm no path allows a second payout or a refund after approval.
- [ ] Confirm `on_payout_complete` cannot be called by an external account.
- [ ] Confirm `NeedsReconciliation` is unreachable-until-ambiguous and escapable only via `reconcile`.
