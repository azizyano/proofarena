# ADR-0006 — Native NEAR transaction signer instead of the `near` CLI

- **Status:** accepted
- **Date:** 2026-11-11
- **Tasks:** PA-017 (testnet path)
- **Supersedes part of:** ADR-0004 (which only described the local adapter)

## Context

A real testnet run needs three signed transactions plus a deploy. Two candidate
dependencies:

1. **The official `near` CLI** (npm `near-cli` legacy vs `near-cli-rs`). Two
   incompatible command surfaces; pinning either risks breaking on a version bump,
   and the CLI writes its own output format that must be parsed.
2. **A native signer** in Python: Borsh-encode the transaction, ed25519-sign it,
   broadcast over JSON-RPC.

The build environment here has no network, so neither could be exercised against
the chain. The question was which failure mode is cheaper and which dependency is
more stable.

## Decision

Implement the native signer (`proofarena/near/`) as the primary path:

- `base58.py` — alphabet encode/decode (known vectors in tests).
- `borsh.py` — encoder plus a decoder used for round-trip proof.
- `signing.py` — `KeyPair` from NEAR credentials, `sign_actions` for
  `FunctionCall` and `DeployContract`, tx-hash computation, decode helpers.
- `rpc.py` — `view_call`, `view_access_key`, `block`, `broadcast_tx_commit`, `tx`.
- `wallet.py` — `NativeWallet` (signs locally), `HttpWallet` (talks to the signer
  service), `CliWallet` (fallback, delegates signing to `near`).

Keep `CliWallet` as a one-flag fallback (`PROOFARENA_NEAR_WALLET=cli`), because if
the Borsh encoding is rejected the chain responds with an explicit
`InvalidTxError` *before executing*, and the fallback converts that dead end into a
30-second retry.

## Why this is safe to fail

A wrong encoding is rejected at transaction validation: no funds move, no state
changes. `scripts/testnet/deploy.py --dry-run` prints the round-trip-decoded
transaction first, so the field order/nonce/actions can be eyeballed before
broadcast.

## Consequences

- No npm dependency, no CLI version roulette, one Python stack.
- The exact Borsh layout cannot be proven offline; the first real transaction is
  the proof. `tests/test_near_testnet.py` pins field order, widths, action tags and
  the signature-over-`sha256(unsigned)` rule at the byte level, which catches the
  common mistakes (u32 vs u64, missing enum tag, wrong field order).
- If it is ever rejected, `CliWallet` is the documented escape hatch; nothing else
  in the architecture depends on which signer is used.

## Not covered

`AddKey`, `CreateAccount`, `Transfer`, `Stake`, `DeleteAccount` actions. The MVP
deploys to an existing faucet account and moves funds only through the contract, so
those actions are unnecessary; they are also the ones most likely to need careful
review before use.
