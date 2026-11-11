# QA matrix — executed results

Environment: Windows 11 (10.0.26300), Python 3.12.4, no network, no Docker, no Rust toolchain
(`docs/runtime-inventory.json`). Every row below was executed; nothing is inferred from source.

Command for the whole suite:

```
python -m pytest tests/ -q
→ 156 passed       (escrow, runner, API, web, signer, NEAR plumbing, account, build config)
```

Fresh-tree check: the committed tree was exported with `git archive HEAD` (75 files, no caches, no
working-tree state) into a new directory and re-run:

```
python -m pytest tests/ -q   →   124 passed
python scripts/e2e_demo.py   →   PATCH_VERIFIED → Paid once, 6 receipts
```

## Functional acceptance (AT)

| ID | Scenario | Result | Evidence |
|---|---|---|---|
| AT-001 | Create valid bounty, unique id | PASS | `test_at001_create_funds_bounty_and_allocates_unique_id` |
| AT-002 | Insufficient / wrong funding rejected, no state change | PASS | `test_at002_wrong_funding_is_rejected_without_state_change` (4 deposit variants) |
| AT-003 | Auditor baseline reproducer deterministic | PASS | `test_baseline_fixture_fails_deterministically`, demo step 5 |
| AT-004 | Defender patch: invariant test + regression pass | PASS | `test_patched_fixture_passes`, `test_regression_suite_passes_on_both_revisions` |
| AT-005 | Independent replay checks hashes, not pasted logs | PASS | `test_verifier_writes_private_artifacts_with_matching_digests` |
| AT-006 | Owner-signed approval changes state | PASS | `test_full_lifecycle_pays_once_and_only_after_owner_approval` |
| AT-007 | Exactly one payout: Pending → Paid | PASS | same test + demo steps 10–12 |
| AT-008 | Payout replay rejected, funds unchanged | PASS | `test_at008_cannot_pay_twice`, API 409 on second request |
| AT-009 | Refund after approval rejected | PASS | `test_at009_refund_after_approval_is_rejected`, `test_refund_after_approval_is_refused` |
| AT-010 | Refund expired no-claim bounty, terminal | PASS | `test_at010_refund_expired_bounty_is_single_and_terminal` |
| AT-011 | Invalid evidence / unknown role / bad auth rejected | PASS | `test_claim_rejects_invalid_manifest`, `test_agent_token_is_scoped_to_role_and_challenge` |
| AT-012 | No `Paid` label before transfer confirmation | PASS | lifecycle test asserts `PayoutPending` with `paid_yocto == "0"` |

## Contract adversarial (SEC)

| ID | Test | Result | Evidence |
|---|---|---|---|
| SEC-001 | Non-owner approves winner | PASS | `test_sec001_non_owner_cannot_approve` |
| SEC-002 | Non-owner refunds | PASS | `test_sec002_non_owner_cannot_refund` |
| SEC-003 | Winner from another bounty | PASS | `test_sec003_cannot_approve_claim_from_another_bounty` |
| SEC-004 | Concurrent payout requests | PASS | `test_sec004_second_payout_request_is_refused_while_pending` |
| SEC-005 | Payout transfer failure → no Paid, no auto-retry | PASS | `test_sec005_explicit_transfer_failure_does_not_pay` |
| SEC-006 | Unexpected / replaid callback | PASS | `test_sec006_out_of_order_and_foreign_callbacks_are_rejected`, `test_sec006b_callback_replay_after_success_is_rejected` |
| SEC-007 | Ambiguous callback → NeedsReconciliation, no retry | PASS | `test_sec007_ambiguous_callback_freezes_and_forbids_retry` |
| SEC-008 | Repeated refund rejected | PASS | `test_sec008_repeated_refund_is_rejected` |
| SEC-009 | Fuzz action sequences preserve invariants | PASS | `test_sec009_fuzz_random_action_sequences_preserve_invariants` (40 rounds × 30 actions) |
| SEC-010 | Cross-bounty fund isolation | PASS | `test_sec010_cross_bounty_isolation_of_funds` |

Additional kernel tests: terminal-state rejection for every action, dispute freeze, review-window
expiry, duplicate claim commitment, late claim, claim after review, reconciliation owner-only and
terminal, "never pays without approval".

## Runner / agent / privacy (RUN)

| ID | Test | Result | Evidence |
|---|---|---|---|
| RUN-001 | Outbound HTTP blocked | PASS | `test_run001_network_is_blocked_inside_sandbox` (5 attack tests pass in-sandbox, ≥3 blocked attempts logged) |
| RUN-002 | Host secrets / wallet files unavailable | PASS | `test_no_secret_bearing_environment_variables_are_forwarded`, `test_wallet_key_file_is_not_present` |
| RUN-003 | Runaway job terminated | PASS | `test_run003_runaway_job_is_killed_by_timeout` (3s ceiling, elapsed < 60s) |
| RUN-004 | Prompt injection treated as data | PASS | `test_run004_prompt_injection_is_recorded_not_obeyed`; sample recorded in manifest |
| RUN-005 | Fabricated pass superseded by rerun | PASS | verifier re-executes from the pinned fixture; auditor stdout is never trusted |
| RUN-006 | Private evidence for unrelated user → 403 + audit | PASS | `test_private_evidence_is_owner_only` |
| RUN-007 | Oversized output truncated | PASS | `test_run007_output_is_truncated` (4 KB cap) |
| RUN-008 | Malformed commitment/hash rejected | PASS | `test_claim_rejects_invalid_manifest`, `test_create_rejects_malformed_parameters` |

## Wallet / API / web

| Scenario | Result | Evidence |
|---|---|---|
| Login nonce single-use | PASS | `test_login_nonce_is_single_use` |
| Wrong signature rejected | PASS | `test_login_rejects_bad_signature` |
| Foreign domain / network rejected | PASS | `test_login_rejects_foreign_domain`, `test_login_rejects_foreign_network` |
| Intent bound to action + ids, single-use | PASS | `test_fund_requires_valid_owner_intent` |
| Unauthenticated owner action → 401 | PASS | `test_unauthenticated_owner_action_is_rejected` |
| Idempotency key prevents duplicate jobs | PASS | lifecycle test: replay returns same job, `idempotent_replay: true` |
| Signer↔server canonical bytes identical | PASS | `test_registration_message_matches_server_exactly` |
| Signer refuses foreign domain / mainnet / unknown purpose / bad origin | PASS | `tests/test_signer.py` (4 tests) |
| All pages render with simulated-chain banner | PASS | `test_pages_render_and_show_simulated_chain_banner` |
| Unknown bounty → 404 page | PASS | same test |
| Live uvicorn HTTP smoke (not TestClient) | PASS | `/health`, `/`, `/bounties`, `/bounties/new`, `/status`, `/api/meta` → 200; `/bounties/ch_nope` → 404 |
| Live signer + API registration/login/fund flow | PASS | register → login → create → sign intent → fund → receipt `local-000001` |

## NEAR testnet plumbing — offline coverage (42 tests)

`python -m pytest tests/test_near_testnet.py -q` → **42 passed**. Executed:

| Area | Assertion |
|---|---|
| base58 | known vectors (`hello world` → `StV1DL6CwTryKyV`, zero prefixes) + random round-trip + invalid chars |
| Borsh | field order and widths pinned at byte level; action tag 2; round-trip decode of signed tx; deploy action carries exact wasm bytes |
| signing | signature verifies against `sha256(unsigned)`; tx hash = b58(sha256(signed)); seed never appears in `repr`; mismatched embedded public key refused |
| RPC | `view_call` base64 decoding, error surfacing, block hash, access-key nonce, `broadcast_tx_commit` success/failure parsing, `SuccessValue` decoding |
| wallets | nonce = key nonce + 1, `InvalidNonce` retry, terminal failure raises, HTTP signer mapping, refusal handling, credentials loading/mismatch |
| mapping | contract JSON → domain `Bounty`/`Claim` incl. lowercase attempts, dispute flag → `Disputed`, claim booleans → `ClaimState` |
| factory | `open_chain("near-testnet")` refuses without config; unknown mode refused; API refuses testnet mode without `NEAR_CONTRACT_ID` |
| signer service | hides private key, refuses foreign contract, unlisted method, oversized deposit, forwards allowlisted call, refuses foreign domain when signing, audit log contains no key material |

**Not proven offline (must be proven by the owner's run):** that the chain accepts the Borsh
encoding, that `create_bounty`/`approve_claim`/`request_payout` behave as specified on-chain, and
that the contract callback settles within the polling window. A wrong encoding is rejected before
execution (no funds move); `--dry-run` prints the round-trip decode first.

## Live NEAR testnet checks (executed against `rpc.testnet.near.org`)

| Check | Result | Evidence |
|---|---|---|
| RPC reachable, `chain_id=testnet` | PASS | node `2.14.0-rc.5` |
| Faucet account creation | PASS | `proofarena-c23922.testnet`, 10 NEAR |
| Credentials parse + key matches | PASS | `ed25519:DLui9BCoqkBMwgKcTank9d43TR24ERohH2Lkw7gUUnU4` |
| Signature + Borsh accepted by the chain | PASS | tx `3E3cn3gfbJw9Lho4RgB5ntUy7QLcEieUkJ5kjwfQk22A` executed, failed with `CodeDoesNotExist` |
| Transaction hash formula | PASS (fixed) | local id == `transaction_outcome.id`; `tx` RPC returns `FINAL` |
| `tx` lookup by hash | PASS | same tx, `final_execution_status: FINAL` |
| API in `near-testnet` mode boots, banner suppressed | PASS | live `/api/meta` (`chain_is_simulated=false`), `/health` |
| Signer refuses/forwards calls correctly | PASS | live: register 200, fund 503 `CodeDoesNotExist`, audit log entries |
| Contract deployed | **PASS** | [`9bK3NTS9BvUeGPm81wek662utFnZUXRg1PBYMUJobM61`](https://explorer.testnet.near.org/transactions/9bK3NTS9BvUeGPm81wek662utFnZUXRg1PBYMUJobM61), code hash `3KcVAjtB…` |
| On-chain lifecycle: fund → claim → review → approve → payout | **PASS** | bounty #3, tx `4WzDB89V…`, `2Kpw6rLZ…`, `FpwgPgaq…`, `Dfzioifs…`, `AVwQZ4md…` |
| Payout settled exactly once, callback observed | **PASS** | `payout_completed` log inside `AVwQZ4md…`; state `Paid`, escrow 0 |
| Second payout refused on chain | **PASS** | `409 E_STATE` from the contract |
| On-chain refund after grace period | **PASS** | bounty #4, `H5eGNida…`; state `Refunded` |
| Early refund refused on chain | **PASS** | `409 E_STATE`, no transaction emitted |
| Abandoned bounty refunded after grace; escrow liability reaches 0 | **PASS** | bounty #2 → `EYyzkpCu…`; `total_escrow_yocto() → 0` |

Details: `docs/TESTNET_EVIDENCE.md`.

## Not executed (and why)

| Item | Status | Reason |
|---|---|---|
| `cargo test` on `contracts/escrow` | NOT RUN | no Rust toolchain, no crates.io access |
| NEAR Sandbox transfer tests | NOT RUN | `near-sandbox` not installed |
| NEAR testnet deploy + real payout | BLOCKED | no funded account, no RPC access, no deployed contract |
| OCI/container isolation assertions | NOT RUN | Docker not installed; process-level controls tested instead (ADR-0005) |
| Browser E2E (Playwright) | NOT RUN | no browser automation available; HTTP + template assertions used |
| Next.js build | NOT APPLICABLE | no npm registry; Jinja2 UI instead (ADR-0002) |
| Live testnet transaction | NOT RUN | no network, no funded account (runbook steps 5–7) |
| Screenshots | NOT PRODUCED | no browser/rendering tool in this environment — no fabricated images |

## Flakiness notes

- The verifier's determinism check strips pytest's timing line before comparing repeat digests;
  raw `output_sha256` still hashes the complete output.
- The flakiness *detector* is tested with an injected alternating runner, not with real randomness —
  a randomly flaky test would make the assertion flaky.
- Two full-suite runs were executed back to back to confirm stability (82 passed both times).
