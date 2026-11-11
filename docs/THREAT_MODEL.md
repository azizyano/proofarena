# Threat model — implemented controls and residual risk

Condensed from `docs/SECURITY_AND_TRUST.md` (kit) and mapped to what this build actually enforces.
Anything marked **not enforced** is not claimed anywhere else in the repository.

## Assets

Escrow funds (testnet/local only) · owner authorization · private vulnerability evidence · runner
host · audit log and settlement state integrity · wallet key material.

## Adversaries

Malicious bounty participant · compromised agent · prompt injection in repository text · fabricated
verifier output · replaying settlement calls · leaked credentials · owner error.

## Controls

| # | Threat | Control in this build | Enforced? | Evidence |
|---|---|---|---|---|
| 1 | Agent approves its own payout | No agent role can call an owner action; approval needs an owner-signed intent binding action+ids+nonce | yes | `test_sec001_non_owner_cannot_approve`, `test_fund_requires_valid_owner_intent` |
| 2 | Session cookie mistaken for approval | Privileged endpoints require a signed intent, not just a bearer token | yes | lifecycle test |
| 3 | Intent replay across bounties or actions | Nonce consumed atomically; action and ids inside the signed payload | yes | `test_fund_requires_valid_owner_intent`, `test_login_nonce_is_single_use` |
| 4 | Double payout | One pending attempt pointer; second request `E_REPLAY`; terminal states refuse all settlement | yes | `test_sec004`, `test_at008`, `test_terminal_states_reject_every_settlement_action` |
| 5 | Refund after approval / payout after refund | Explicit state guards; refund requires no approved claim | yes | `test_at009`, `test_sec005` |
| 6 | Cross-bounty fund confusion | Per-bounty `escrow_balance` derived from `reward - paid - refunded`, re-checked on every write | yes | `test_sec010`, fuzz invariants |
| 7 | Forged settlement callback | Callback requires self-dispatch; must match the outstanding attempt id; replays refused | yes | `test_sec006`, `test_sec006b` |
| 8 | Missing/ambiguous callback treated as failure | `AMBIGUOUS` → `NeedsReconciliation`; retries and refunds frozen; owner-signed `reconcile` with evidence digest | yes | `test_sec007`, `test_sec007b` |
| 9 | Untrusted code reaches network | In-process socket denial + attempt log | partial | `test_run001`; not a kernel boundary |
| 10 | Untrusted code reads secrets | Env allowlist drops SECRET/PRIVATE/SEED/MNEMONIC/PASSWORD/WALLET/TOKEN/NEAR/RPC keys; sandbox copy only | partial | `test_no_secret_bearing_environment_variables_are_forwarded`; filesystem not confined |
| 11 | Runaway job exhausts host | Wall-clock timeout + process-tree kill; output cap; patch size cap; claim/attempt limits | yes (time/output) | `test_run003`, `test_run007`; no memory/PID caps |
| 12 | Patch touches unauthorized files | Diff inspection allows only `ledger.py`; `git apply` must match pinned baseline | yes | lifecycle test, `diff_touches_only` |
| 13 | Prompt injection in repo text | Text treated as data; instruction-like lines detected and recorded in the manifest; no scope change | partial | `test_run004`; an LLM auditor would need stronger isolation |
| 14 | Fabricated test output | Verifier re-executes; auditor stdout stored but never trusted; artifacts hashed; manifest digest in result | yes | `test_verifier_*`, `test_verifier_writes_private_artifacts_with_matching_digests` |
| 15 | Private evidence leak | Only commitments on "chain"; private endpoints owner-only and audited; public timeline excludes manifests | yes | `test_private_evidence_is_owner_only` |
| 16 | Receipt tampering | Hash-chained receipts recomputed by `/v1/chain/integrity` | yes (local) | `test_health_reports_chain_mode_and_receipt_integrity` |
| 17 | Fake testnet claim | `open_chain(mode!="local")` raises; UI banner; `chain_is_simulated`; receipts `local-*` | yes | `test_testnet_mode_is_refused_explicitly` |
| 18 | Wallet key exfiltration via API | API never receives or stores a private key; signer returns only signatures and a public key | yes | `test_signer_creates_a_key_file_without_exposing_it` |
| 19 | Signer signs arbitrary messages | Signer refuses unknown purpose, foreign domain/network/contract, and disallowed Origin | yes | `tests/test_signer.py` (4 tests) |
| 20 | Duplicate job on retry | `Idempotency-Key` required; unique index; replay returns the same job | yes | lifecycle test |

## Not enforced (explicit)

- No kernel/container isolation; no cgroup memory or PID limits; no read-only root filesystem.
- No filesystem confinement: the sandboxed child can read world-readable host paths.
- No trustless verification: one operator re-executes; no attestation or quorum.
- No on-chain dispute adjudication; the owner keeps veto power (MVP by design).
- No protection against a malicious owner refusing to pay, or paying the "wrong" acceptable claim.
- No Sybil resistance, no reputation, no anti-spam deposit beyond per-bounty claim limits.
- No mainnet. `chain_mode` accepts `local` only.

## P0 findings

None open. Two were found and fixed during the build:

1. **Registration-message drift** between the signer and the API (signature never verified in the live
   path). Fixed; golden-byte regression test added (`DECISION_LOG` D-007).
2. **Network guard broke `ssl`** by replacing `socket.socket` with a function, which crashed the run
   before any test executed. Fixed by patching egress methods only; documented in the runner.

## Release-blocking conditions (any of these ⇒ BLOCKED)

Wallet authorization bypass · possible double payout/refund · unsandboxed execution of a live target ·
public exploit disclosure · misleading payout state or fabricated verifier evidence · unsafe handling
of ambiguous asynchronous transfers.

None are present in the current build, with the caveats listed under "Not enforced".
