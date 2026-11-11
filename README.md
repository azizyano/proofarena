# ProofArena

**Verifiable AI security work with owner-approved NEAR escrow.**

An owner funds a bounty for an explicitly authorized codebase or synthetic fixture. An auditor
agent submits a reproducible failing test. An independent verifier re-runs it. A defender
submits a patch. The verifier re-runs before/after. The owner signs the approval with their own
wallet. Escrow settles exactly once.

This repository is the working MVP, built to the ProofArena kit spec. It is deliberately narrow:
one bounty, one winner, native token, testnet-only.

---

## Release verdict

| Verdict | Status |
|---|---|
| Local end-to-end workflow | **TESTED_LOCAL** — 74 automated tests + `scripts/e2e_demo.py` |
| Escrow rules (payout/refund/approval) | **TESTED_LOCAL** via the executable reference engine |
| Rust NEAR contract | **IMPLEMENTED_UNTESTED** — no Rust toolchain in the build environment |
| NEAR testnet deployment | **BLOCKED** — no funded account, no deployed contract, no RPC access |
| Overall | **READY FOR TESTNET DEMO** after the testnet steps below; **not** production |

Read `communications/STATUS_BOARD.md` for per-task evidence and `docs/QA_MATRIX.md` for the
executed test matrix. Nothing in this repo is mainnet-ready and no real funds are involved.

---

## The one honest caveat, stated first

This build environment had **no outbound network, no Rust toolchain, no Docker, no NEAR CLI and
no funded testnet account**. Three planned components are therefore marked BLOCKED instead of
faked:

1. **NEAR testnet contract.** `contracts/escrow/src/lib.rs` is complete near-sdk 5.9 source with a
   unit-test module, but it has not been compiled or deployed. It is **not** claimed as tested.
2. **Chain adapter.** Settlement runs against a local ledger that implements the *same* state
   machine as the contract (`proofarena/chain/engine.py`). Every receipt is prefixed `local-`,
   labelled `local-ledger`, and the UI shows a persistent banner. No receipt is presented as a
   testnet transaction.
3. **Next.js / FastAPI.** Neither could be installed offline. The API is Starlette + Pydantic v2
   (FastAPI's own foundation) and the UI is server-rendered Jinja2 with no build step. Routes,
   states and API shapes match the spec.

Everything else is real and executable: wallet-grade Ed25519 signatures over canonical intents,
the verification runner that actually executes tests in a sandbox, patch application, hash-chained
receipts, and the HTTP surface.

---

## Quick start (no network required)

```bash
# Python 3.11+ with the pinned dependencies
pip install -r requirements.txt

# 1. Verify everything (74 tests: escrow invariants, runner isolation, API, web)
python -m pytest tests/ -q

# 2. Drive the entire lifecycle and print real receipts
python scripts/e2e_demo.py

# 3. Run the app and the wallet signer (two terminals, or & on POSIX)
python scripts/dev.py            # http://127.0.0.1:8787
python wallet/signer.py          # http://127.0.0.1:8788
```

Then open <http://127.0.0.1:8787>, click **Connect owner wallet**, and create a bounty. The signer
holds the demo key; the API process never sees it.

Generated state lands in `evidence/` (git-ignored). The demo prints its run directory, which
contains `app.db`, `chain.db`, per-job artifacts and `summary.json`.

### The demo flow

```
[1]  owner registers an Ed25519 key and signs a login challenge
[2]  owner creates the challenge manifest      -> metadata_hash (SHA-256, canonical JSON)
[3]  owner signs fund_bounty                   -> escrow funded, state Open
[4]  owner issues scoped agent tokens          -> auditor / defender, one challenge each
[5]  auditor runs the pinned fixture           -> exit 1, invariant marker observed
[6]  auditor commits the claim commitment      -> only the digest goes on chain
[7]  defender submits a patch (ledger.py only)  -> scope enforced by diff inspection
[8]  verifier re-runs baseline twice + patched  -> PATCH_VERIFIED, artifacts hashed
[9]  owner signs approve_claim                  -> Approved
[10] owner signs request_payout                 -> PayoutPending  (NOT paid)
[11] duplicate payout request                   -> refused (E_REPLAY)
[12] settlement callback delivered              -> Paid, once
[13] receipt chain verified                     -> 6 receipts, chain valid
```

---

## Layout

```
proofarena/
  proofarena/
    chain/engine.py        authoritative escrow state machine (mirrors the Rust contract)
    chain/ledger.py        SQLite local ledger + hash-chained receipts (labelled simulated)
    chain/models.py        bounty/claim/attempt domain types
    runner.py              isolated pytest runner (network denied, env allowlist, timeouts)
    verify.py              independent before/after verification, artifacts, manifest
    patching.py            strict `git apply` of the defender diff onto the pinned baseline
    auth/wallet.py         Ed25519 login + single-use signed intents
    auth/sessions.py       HMAC session tokens, rate limiting
    store.py               off-chain read model (SQLite): challenges, claims, jobs, audit
    api.py                 HTTP API (Starlette + Pydantic v2)
    web.py + templates/    server-rendered UI, 6 routes, responsive
  contracts/escrow/        Rust near-sdk 5.9 contract + unit tests (UNCOMPILED — see verdict)
  fixture/                 intentionally vulnerable ledger, tests, prompt-injection sample
  wallet/signer.py         local wallet signer process (stands in for a browser wallet)
  scripts/                 versions, dev server, patch builder, end-to-end demo
  tests/                   74 tests
  docs/                    ADRs, QA matrix, threat model
  communications/          status board, decision log, handoffs
```

---

## API (frozen `/v1` surface)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | public | liveness + receipt-chain integrity |
| GET | `/api/meta` | public | chain mode, limits, `chain_is_simulated` |
| POST | `/auth/register` | signed proof | register an Ed25519 wallet key |
| POST | `/auth/challenge` | rate-limited | issue a login nonce |
| POST | `/auth/verify` | signed nonce | exchange a signature for a session token |
| POST | `/v1/intents` | session | issue a canonical message to sign |
| POST | `/v1/challenges` | session | create the off-chain manifest + digest |
| POST | `/v1/challenges/{id}/fund` | session + `fund_bounty` intent | fund escrow |
| POST | `/v1/challenges/{id}/agent-token` | owner session | issue chained auditor/defender tokens |
| POST | `/v1/challenges/{id}/claims` | auditor token | submit claim manifest (private) |
| POST | `/v1/claims/{id}/patches` | defender token | submit a scoped patch |
| POST | `/v1/claims/{id}/verify` | owner session + `Idempotency-Key` | run independent verification |
| GET | `/v1/verification/{job}` | owner session | sanitized result |
| GET | `/v1/verification/{job}/logs` | owner session | raw private runner logs |
| POST | `/v1/challenges/{id}/review` | owner session | close submissions |
| POST | `/v1/challenges/{id}/approve` | session + `approve_claim` intent | approve the winner |
| POST | `/v1/challenges/{id}/payout` | session + `request_payout` intent | initiate exactly-once payout |
| POST | `/v1/challenges/{id}/refund` | session + `refund_expired` intent | refund an expired bounty |
| POST | `/v1/challenges/{id}/reconcile` | session + `reconcile` intent | resolve a stuck transfer |
| GET | `/v1/challenges/{id}/timeline` | public sanitized | append-only events |
| GET | `/v1/challenges/{id}/private-evidence` | owner session | manifests, diffs, results (audited) |
| GET | `/v1/chain/integrity` | public | recompute the receipt hash chain |

Privileged actions require an owner-signed **intent** that binds domain, network, contract id,
action, ids and a single-use nonce. The API has no code path that signs with an owner key.

---

## What the escrow enforces

- Owner-only `approve`, `request_payout`, `refund`, `reconcile`, `set_dispute`.
- One approved winner; claims from another bounty are rejected.
- `PayoutPending` / `RefundPending` are distinct from `Paid` / `Refunded`.
- A settlement attempt id is written **before** the transfer promise; callbacks are private and
  id-matched.
- An ambiguous callback freezes into `NeedsReconciliation`; retries require an owner-signed
  reconciliation with an evidence digest. There is no automatic retry path.
- `Paid` and `Refunded` are terminal; `paid + refunded <= reward` per bounty, escrow balance is
  recomputed on every mutation.
- Duplicate claim commitments and duplicate payout requests are refused.
- Conservation and terminality are fuzzed with randomized action sequences.

## Verification semantics

| Verifier outcome | Baseline | Patch | Payable after owner review? |
|---|---|---|---|
| `PATCH_VERIFIED` | fails deterministically (run twice) | invariant test + regression suite pass | yes |
| `REPRODUCED` | fails deterministically | patch not proven | no |
| `REJECTED` | does not fail | n/a | no |
| `INCONCLUSIVE` | flaky, timeout, or scope invalid | n/a | no |

The verifier never reads the auditor's stdout as proof: it re-executes the pinned test itself,
hashes every artifact, and stores the manifest digest in the result.

---

## Enabling NEAR testnet (blocked today)

Do this in an environment with a network and a funded testnet account:

```bash
rustup target add wasm32-unknown-unknown
cd contracts/escrow && cargo test && cargo near build      # or cargo build --target wasm32-unknown-unknown --release
near deploy <contract.testnet> <wasm> --initFunction new --initArgs '{"owner":"<owner.testnet>"}'
```

Then:

1. Implement `NearTestnetAdapter` against the same `ChainAdapter` protocol as `LocalLedger`
   (`proofarena/chain/ledger.py`). The `open_chain(mode="near-testnet")` factory already refuses
   to pretend.
2. Replace signed intents with real wallet-signed `Action`s (the payload shapes are already bound
   to `bounty_id` / `claim_id` / `contract_id` / `network`).
3. Keep the callback path: `on_payout_complete` must remain `#[private]` and id-checked.
4. Re-run `python -m pytest tests/ -q` plus the contract suite, then record a real funding,
   approval and payout receipt. Only then change the verdict to `VERIFIED_TESTNET`.

Do **not** point this build at mainnet. `PROOFARENA_CHAIN_MODE` accepts `local` only.

---

## Security notes

- The demo wallet key lives in `wallet/owner.key.json` (git-ignored) and is generated locally.
  It is a demo key. Never fund it.
- The runner denies outbound sockets in-process, allowlists the child environment, caps output and
  kills runaway jobs. It is **not** an OCI/kernel sandbox — see
  `docs/adr/0005-runner-isolation.md` for exactly what is and is not enforced.
- Reports are private: only commitments go "on chain"; the public timeline and timeline API never
  include manifest content.
- Prompt injection in fixture text is treated as data: it is detected, recorded in the manifest and
  cannot alter scope (RUN-004).

## License

No license file yet — add one before any public submission.
