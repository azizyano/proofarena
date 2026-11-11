# Team Messages (structured, dated, facts only)

### MSG-20261111-001 — [ASSIGNMENT] [PA-001] Runtime inventory before any code
- From: CTO
- To: DevOps (CC: CEO, Security)
- UTC time: 2026-11-11T11:12:00Z
- Type: ASSIGNMENT
- Priority: P0
- Status: PROPOSED
- Dependencies: none
- Facts: zip kit extracted to `proofarena/`; workspace contains unrelated projects, so a new repo was created.
- Decision/request: produce a truthful runtime inventory including absent tools.
- Response needed from: DevOps

### MSG-20261111-002 — [BLOCKER] [PA-017] Testnet path unavailable
- From: CTO
- To: CEO, Security, human owner (CC: NEAR, DevOps)
- UTC time: 2026-11-11T11:14:00Z
- Type: BLOCKER
- Priority: P0
- Status: BLOCKED
- Dependencies: PA-004
- Facts: `cargo --version` → exit 1 (`could not create home directory`); `rustup toolchain list` → empty;
  `curl https://rpc.testnet.near.org` → TLS failure; `near`/`near-sandbox` not on PATH; `docker` not found.
- Decision/request: proceed with a labelled local ledger and an uncompiled contract; do **not** fabricate
  a testnet deployment. Owner must supply credentials/keys in a networked environment.
- Evidence: `evidence/runtime-inventory.json`
- Response needed from: human owner (accept or supply credentials)

### MSG-20261111-003 — [HANDOFF] [PA-005] Escrow rules proven in the reference engine
- From: NEAR/QA
- To: Security
- UTC time: 2026-11-11T11:24:00Z
- Type: HANDOFF
- Priority: P0
- Status: TESTED_LOCAL
- Dependencies: PA-002, PA-004
- Files: `proofarena/chain/engine.py`, `proofarena/chain/ledger.py`, `tests/test_escrow_engine.py`
- Commands: `python -m pytest tests/test_escrow_engine.py -q`
- Results: 41 passed. Covers double approval, double payout, cross-bounty claim, refund-after-approval,
  callback replay, wrong caller, explicit transfer failure, ambiguous → reconciliation, fuzz sequences.
- Security implications: the Rust contract is unverified; the engine is the authority until `cargo test` runs.
- Remaining blockers: B2 (toolchain).
- Reviewer action: confirm the state table in `docs/NEAR_ESCROW_SPEC.md` matches the implementation.

### MSG-20261111-004 — [REVIEW] [PA-010] Runner isolation claims
- From: Security
- To: CTO (CC: QA)
- UTC time: 2026-11-11T11:28:00Z
- Type: REVIEW
- Priority: P0
- Status: TESTED_LOCAL
- Facts: network denial, env allowlist, timeout kill and output cap each have a passing test.
- Finding: isolation is process-level; not a kernel boundary. `sitecustomize`-based denial can be
  bypassed by native code. Documented in ADR-0005 and in every verification manifest.
- Decision/request: keep the claim scoped to "process-level controls"; require a container before
  accepting third-party targets.
- Response needed from: CTO (accepted; recorded)

### MSG-20261111-005 — [INCIDENT-LESSON] [PA-013] Canonical message drift between signer and server
- From: CTO
- To: Frontend, Backend, QA
- UTC time: 2026-11-11T11:41:00Z
- Type: INCIDENT
- Priority: P1
- Status: TESTED_LOCAL (fixed)
- Facts: live registration failed `E_AUTH signature does not verify`; server omitted `payload` from the
  registration message while the signer included it. Fixed; golden-byte test added.
- Evidence: `tests/test_signer.py::test_registration_message_matches_server_exactly`
- Decision/request: any canonical format shared across a process boundary now needs a byte-equality test.

### MSG-20261111-006 — [DECISION] [PA-018] Release verdict
- From: CEO/PM (technical facts signed by CTO)
- To: human owner
- UTC time: 2026-11-11T11:45:00Z
- Type: DECISION
- Priority: P0
- Status: TESTED_LOCAL
- Facts: 82 tests pass twice; `scripts/e2e_demo.py` completes PATCH_VERIFIED → Paid once with 6 receipts;
  contract uncompiled; testnet not deployed.
- Decision: verdict is **READY FOR TESTNET DEMO**, not `VERIFIED_TESTNET` and not production.
- Remaining owner actions: fund a testnet account, provide wallet signing, approve the deploy.

### MSG-20261111-007 — [DECISION] [PA-017] Testnet deployment verified; stale claims corrected
- From: CTO (technical facts)
- To: CEO/PM, QA, Docs/Demo (CC: human owner)
- UTC time: 2026-11-11T22:56:00Z
- Type: DECISION
- Priority: P0
- Status: VERIFIED_TESTNET
- Dependencies: PA-017
- Files: contracts/escrow/src/lib.rs, proofarena/near/*, docs/TESTNET_EVIDENCE.md
- Facts: contract deployed at proofarena-c23922.testnet (230,309 bytes, code hash 3KcVAjtB…).
  Bounty #3: fund → claim → review → approve → payout, exactly once. Bounties #2 and #4 refunded after
  expiry. `total_escrow_yocto()` returned to 0. A second payout was refused (409 E_STATE).
- Correction: several documents still described the testnet path as BLOCKED and quoted an older test
  count (82/124). README, QA matrix, demo script, runbook, status board, UI banners and ADR notes were
  updated to the verified state; ADRs keep their historical text with an explicit update note.
- Remaining limits: `cargo test` (contract unit tests) never executed; verifier is a single operator;
  contract refusals surface as generic wasm traps because of `panic = "abort"`; browser-wallet popup
  flow needs a human with an extension.
- Decision/request: judges-facing documents now match the chain. No further claims to reconcile.
