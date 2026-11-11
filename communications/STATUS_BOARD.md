# ProofArena — Status Board

**Single source of truth.** Updated 2026-11-11T11:35Z by CTO/integrator.
Lifecycle: `TODO → IN_PROGRESS → IN_REVIEW → TESTED_LOCAL → VERIFIED_TESTNET → DONE`; `BLOCKED` when
an external dependency is missing.

## Environment blockers (apply to several tasks)

| Id | Blocker | Impact | Unblock action |
|---|---|---|---|
| B1 | No outbound network | cannot install npm/rust packages, cannot reach NEAR RPC | run in a networked environment |
| B2 | No Rust toolchain (`cargo` unusable, empty rustup) | contract cannot compile | `rustup toolchain install stable && rustup target add wasm32-unknown-unknown` |
| B3 | No `near` CLI / `near-sandbox` | no local chain, no deploy | install NEAR CLI + sandbox |
| B4 | No funded testnet account / owner wallet | no real transfer, no real approval signature | owner provides account + wallet |
| B5 | No Docker | runner isolation is process-level only | install Docker/Podman |
| B6 | Testnet *contract* deployment has not been executed | PA-017 stays BLOCKED until the wasm is compiled and deployed | runbook steps 4–6 |
| B7 | Build link measured at 11–19 KB/s | a 137 MB toolchain would take 2–3.5 h, so compilation must run on the owner's machine | run `cargo test` / `deploy.py` locally |

## Tasks

| ID | Pri | Owner | Status | Artifact | Evidence | Blocker |
|---|---|---|---|---|---|---|
| PA-001 | P0 | DevOps | TESTED_LOCAL | repo tree, `requirements.txt`, `pyproject.toml`, `scripts/versions.py` | `python scripts/versions.py` → `docs/runtime-inventory.json`; suite runs on clean checkout | — |
| PA-002 | P0 | CTO | DONE | `docs/adr/0001..0005`, frozen `/v1` + schemas | ADRs accepted; schemas copied verbatim from kit | — |
| PA-003 | P0 | CTO/Backend | TESTED_LOCAL | `proofarena/schemas/`, claim + verification validators | schema validation asserted in `test_claim_rejects_invalid_manifest`, `test_verifier_result_matches_frozen_schema` | — |
| PA-004 | P0 | NEAR | IMPLEMENTED_UNTESTED | `contracts/escrow/src/lib.rs` (near-sdk 5.9), same rules as engine | **not compiled** — B2; `cargo test` is runbook step 4 | B2 |
| PA-005 | P0 | NEAR/QA | TESTED_LOCAL (via reference engine) | `proofarena/chain/engine.py`, `tests/test_escrow_engine.py` | 41 tests: SEC-001..010, AT-001/002/008/009/010, fuzz | contract-level run needs B2 |
| PA-006 | P0 | Auditor | TESTED_LOCAL | `fixture/baseline/ledger.py`, `fixture/tests/test_invariant.py` | fails deterministically twice: `test_baseline_fixture_fails_deterministically` | — |
| PA-007 | P0 | Auditor | TESTED_LOCAL | claim manifest flow, `commitment_salt_hex`, commitment = H(salt‖manifest) | `test_full_lifecycle_*`, demo step 6 | — |
| PA-008 | P0 | Defender | TESTED_LOCAL | `fixture/patched/ledger.py`, `fixture/patch.diff` (sha256 `c1b82d09…`) | `test_patched_fixture_passes`, regression suite passes on both revisions | — |
| PA-009 | P0 | Verifier | TESTED_LOCAL | `proofarena/verify.py`, artifacts + manifest digest | `test_verifier_reports_patch_verified`, schema-validated result | — |
| PA-010 | P0 | Security/DevOps | TESTED_LOCAL (process-level) | `proofarena/runner.py` | RUN-001/002/003/007 tests | kernel isolation needs B5 |
| PA-011 | P0 | Backend | TESTED_LOCAL | `proofarena/api.py`, `store.py`, `auth/` | 20 API tests + live uvicorn smoke | — |
| PA-012 | P0 | Backend | TESTED_LOCAL | idempotent jobs, private evidence, audit log | idempotency replay test, RUN-006 403 test | — |
| PA-013 | P0 | Frontend | TESTED_LOCAL (+ browser wallet) | `web.py`, `templates/`, `static/`, `wallet/signer.py` | page render tests, signer integration tests, live HTTP flow; `web-vercel/` wallet app served at `/wallet/`; NEP-413 auth + wallet-tx verification tested and replayed against real testnet txs | browser interaction needs a real wallet extension |
| PA-014 | P0 | Security | IN_REVIEW | `docs/THREAT_MODEL.md`, ADR-0005 limitations | P0 findings: none open; declared residual risks | — |
| PA-015 | P0 | QA | TESTED_LOCAL | `docs/QA_MATRIX.md` | 82 passed, two consecutive runs | — |
| PA-016 | P0 | CTO/QA | TESTED_LOCAL | `scripts/e2e_demo.py` | full lifecycle run, 6 receipts, chain valid; committed tree exported with `git archive HEAD` (75 files) → 82 passed + demo complete | — |
| PA-017 | P0 | NEAR/DevOps | VERIFIED_TESTNET | `proofarena/near/` (base58, Borsh, signing, RPC, wallets), `chain/near_testnet.py`, `wallet/near_signer.py`, `scripts/testnet/{preflight,deploy,smoke}.py`, `docs/TESTNET_RUNBOOK.md` | contract deployed (`9bK3NTS9BvUeGPm81wek662utFnZUXRg1PBYMUJobM61`); full lifecycle on chain (fund → claim → approve → payout once); refund path on chain; 6+ explorer-verifiable tx hashes in `docs/TESTNET_EVIDENCE.md` | — |
| PA-018 | P0 | CEO/PM | DONE (verdict) | release verdict in README | `VERIFIED_TESTNET` — payout and refund both observed on chain; production still out of scope | — |
| PA-019 | P0 | Docs/Demo | TESTED_LOCAL | `README.md`, `docs/DEMO_SCRIPT.md`, `docs/SUBMISSION.md` | demo script executed; no screenshots (no browser) | — |
| PA-020 | P1 | Frontend | TESTED_LOCAL | responsive CSS at 1280/390, `prefers` none | CSS media queries; not visually verified in a browser | browser check pending |
| PA-021 | P1 | Backend | TESTED_LOCAL | append-only audit stream + timeline + counter metrics in `/health` | timeline tests; no secret values in logs | — |
| PA-022 | P1 | CEO/PM | TODO | customer interviews | **not started** — no fabricated traction | needs human interviews |

## Test totals

```
python -m pytest tests/ -q   →   176 passed  (escrow, runner, API, web, signer, wallet auth, NEAR plumbing, account, build config)
scripts/e2e_demo.py          →   PATCH_VERIFIED → Paid once, 6 receipts, chain valid

# fresh-tree check for the local MVP (earlier commit):
#   git archive HEAD → 82 passed + demo complete

# NEAR testnet path (offline coverage only):
python -m pytest tests/test_near_testnet.py -q   →   42 passed
python -m pytest tests/test_testnet_account.py -q →   10 passed
python scripts/testnet/preflight.py              →   reports missing account/contract (expected offline)
```

## PA-017 execution checklist (owner runs this)

| # | Step | Command | Evidence produced |
|---|---|---|---|
| 1 | Rust + wasm target | `rustup target add wasm32-unknown-unknown` | `cargo --version`, target list |
| 2 | ~~Testnet account + key~~ | done: `proofarena-c23922.testnet` + `coldlemon3275.testnet` + auditor account | account ids, funded balances |
| 3 | Env | `$env:NEAR_TESTNET_ACCOUNT`, `$env:NEAR_CONTRACT_ID` | `preflight` PASS lines |
| 4 | Compile + unit tests | `cargo test`, `python scripts/testnet/deploy.py --check` | wasm artifact + test output |
| 5 | Deploy | `python scripts/testnet/deploy.py --dry-run` then `--yes` | deploy tx `9bK3NTS9…` |
| 6 | Run app in testnet mode | `wallet/near_signer.py`, `PROOFARENA_CHAIN_MODE=near-testnet scripts/dev.py` | signer + API logs |
| 7 | On-chain lifecycle | `python scripts/testnet/smoke.py` + `refund_demo.py` | 7 tx hashes + explorer links |

## Current P0 risks (open)

1. **Contract uncompiled.** `contracts/escrow` may contain compile errors on a first build.
   Mitigation: rules are proven by the reference engine; contract is explicitly
   `IMPLEMENTED_UNTESTED`; runbook step 4 is a dedicated compile gate before deploying.
2. **No testnet receipt yet.** The testnet path is implemented and offline-tested but has never
   sent a live transaction (B6). Mitigation: nothing is faked; every local surface shows
   `local-ledger` + banner; the deploy has a dry-run and a CLI fallback.
3. **Runner is not a kernel sandbox.** A determined payload could escape process-level controls.
   Mitigation: only the pinned fixture executes; limitation stated in every manifest.
4. **Single-operator verifier.** Not trustless; disclosed in UI and docs.
5. **Prompt injection is mitigated, not solved.** Detected and recorded; a future LLM-driven
   auditor would need harder isolation.

## Handoff queue

| From → To | Artifact | State |
|---|---|---|
| NEAR → Security | `contracts/escrow/src/lib.rs` | awaiting a runnable toolchain review |
| Verifier → QA | `proofarena/verify.py` | accepted (82 tests) |
| Frontend → QA | `wallet/signer.py` + pages | accepted; visual check pending |
| Backend → Security | `proofarena/api.py` | in review (PA-014) |
