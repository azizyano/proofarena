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

## Tasks

| ID | Pri | Owner | Status | Artifact | Evidence | Blocker |
|---|---|---|---|---|---|---|
| PA-001 | P0 | DevOps | TESTED_LOCAL | repo tree, `requirements.txt`, `pyproject.toml`, `scripts/versions.py` | `python scripts/versions.py` → `docs/runtime-inventory.json`; suite runs on clean checkout | — |
| PA-002 | P0 | CTO | DONE | `docs/adr/0001..0005`, frozen `/v1` + schemas | ADRs accepted; schemas copied verbatim from kit | — |
| PA-003 | P0 | CTO/Backend | TESTED_LOCAL | `proofarena/schemas/`, claim + verification validators | schema validation asserted in `test_claim_rejects_invalid_manifest`, `test_verifier_result_matches_frozen_schema` | — |
| PA-004 | P0 | NEAR | IMPLEMENTED_UNTESTED | `contracts/escrow/src/lib.rs` (near-sdk 5.9), same rules as engine | **not compiled** — B2 | B2 |
| PA-005 | P0 | NEAR/QA | TESTED_LOCAL (via reference engine) | `proofarena/chain/engine.py`, `tests/test_escrow_engine.py` | 41 tests: SEC-001..010, AT-001/002/008/009/010, fuzz | contract-level run needs B2 |
| PA-006 | P0 | Auditor | TESTED_LOCAL | `fixture/baseline/ledger.py`, `fixture/tests/test_invariant.py` | fails deterministically twice: `test_baseline_fixture_fails_deterministically` | — |
| PA-007 | P0 | Auditor | TESTED_LOCAL | claim manifest flow, `commitment_salt_hex`, commitment = H(salt‖manifest) | `test_full_lifecycle_*`, demo step 6 | — |
| PA-008 | P0 | Defender | TESTED_LOCAL | `fixture/patched/ledger.py`, `fixture/patch.diff` (sha256 `c1b82d09…`) | `test_patched_fixture_passes`, regression suite passes on both revisions | — |
| PA-009 | P0 | Verifier | TESTED_LOCAL | `proofarena/verify.py`, artifacts + manifest digest | `test_verifier_reports_patch_verified`, schema-validated result | — |
| PA-010 | P0 | Security/DevOps | TESTED_LOCAL (process-level) | `proofarena/runner.py` | RUN-001/002/003/007 tests | kernel isolation needs B5 |
| PA-011 | P0 | Backend | TESTED_LOCAL | `proofarena/api.py`, `store.py`, `auth/` | 20 API tests + live uvicorn smoke | — |
| PA-012 | P0 | Backend | TESTED_LOCAL | idempotent jobs, private evidence, audit log | idempotency replay test, RUN-006 403 test | — |
| PA-013 | P0 | Frontend | TESTED_LOCAL | `web.py`, `templates/`, `static/`, `wallet/signer.py` | page render tests, signer integration tests, live HTTP flow | screenshots need a browser |
| PA-014 | P0 | Security | IN_REVIEW | `docs/THREAT_MODEL.md`, ADR-0005 limitations | P0 findings: none open; declared residual risks | — |
| PA-015 | P0 | QA | TESTED_LOCAL | `docs/QA_MATRIX.md` | 82 passed, two consecutive runs | — |
| PA-016 | P0 | CTO/QA | TESTED_LOCAL | `scripts/e2e_demo.py` | full lifecycle run, 6 receipts, chain valid; committed tree exported with `git archive HEAD` (75 files) → 82 passed + demo complete | — |
| PA-017 | P0 | NEAR/DevOps | BLOCKED | — | no wasm, no account, no RPC | B5+B4+B2/B3 |
| PA-018 | P0 | CEO/PM | DONE (verdict) | release verdict in README | `READY FOR TESTNET DEMO`, not `VERIFIED_TESTNET` | — |
| PA-019 | P0 | Docs/Demo | TESTED_LOCAL | `README.md`, `docs/DEMO_SCRIPT.md`, `docs/SUBMISSION.md` | demo script executed; no screenshots (no browser) | — |
| PA-020 | P1 | Frontend | TESTED_LOCAL | responsive CSS at 1280/390, `prefers` none | CSS media queries; not visually verified in a browser | browser check pending |
| PA-021 | P1 | Backend | TESTED_LOCAL | append-only audit stream + timeline + counter metrics in `/health` | timeline tests; no secret values in logs | — |
| PA-022 | P1 | CEO/PM | TODO | customer interviews | **not started** — no fabricated traction | needs human interviews |

## Test totals

```
python -m pytest tests/ -q   →   82 passed   (two consecutive runs)
scripts/e2e_demo.py          →   PATCH_VERIFIED → Paid once, 6 receipts, chain valid

# fresh-tree check (no working-tree state, no caches, 75 committed files):
git archive --format=zip -o head.zip HEAD && expand into a new directory
  python -m pytest tests/ -q  →   82 passed
  python scripts/e2e_demo.py  →   PATCH_VERIFIED → Paid once
```

## Current P0 risks (open)

1. **Contract uncompiled.** `contracts/escrow` may contain compile errors. Mitigation: rules are
   proven by the reference engine; contract is explicitly `IMPLEMENTED_UNTESTED`.
2. **No testnet receipt.** The headline claim of the kit is unmet. Mitigation: nothing is faked;
   every surface shows `local-ledger` + banner.
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
