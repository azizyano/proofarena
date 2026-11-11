# ENGINEERING HANDOFF — PA-009 / PA-010

- **From:** Independent Verifier
- **To:** QA / Security (CC: CTO)
- **Status:** TESTED_LOCAL
- **Scope:** Re-execution of the pinned failing test and the patched revision, artifact hashing,
  isolated runner controls.

## Files

- `proofarena/verify.py` — before/after runs, flakiness detection, schema-validated result, artifacts
- `proofarena/runner.py` — sandbox materialization, env allowlist, network denial, timeout, output cap
- `proofarena/patching.py` — strict `git apply` of the defender diff onto a copy of the baseline
- `tests/test_runner_verify.py`, `tests/test_signer.py`

## Commands executed

```
python -m pytest tests/test_runner_verify.py -q      → 13 passed
python -m pytest tests/ -q                           → 82 passed (twice)
```

## Results (observed, not inferred)

- Baseline, run twice in separate sandboxes: exit 1, invariant marker present, stable digests equal
  after removing pytest's timing line.
- Patched revision: exit 0, invariant marker absent, regression suite exit 0.
- Result status `PATCH_VERIFIED`; `approval_eligible: true`; `scope_valid: true`.
- Artifacts written and hashed: `baseline-1.log`, `baseline-2.log`, `patched.log`,
  `regression.log`, `diff.patch`, `manifest.json`, `summary.json`.
- RUN-001: 5 attack tests pass inside the sandbox and `network-blocked.log` records ≥3 denials.
- RUN-003: a 120s sleep loop is killed at a 3s ceiling (`timed_out: true`, `exit_code: -1`).
- RUN-007: 400 KB of output is truncated at a 4 KB cap.
- RUN-004: `SYSTEM:` injection sample found in fixture text and recorded in the manifest; scope unchanged.

## Security implications

- The verifier never trusts auditor stdout; it re-executes from the pinned fixture and hashes its own
  output. The auditor's claimed digest is stored but not used as proof.
- `git apply` is strict: a patch that does not match the pinned baseline fails the job (422) rather
  than producing a divergent tree.
- Isolation is process-level only (ADR-0005). Native code in the sandbox could bypass the Python
  socket denial; this is disclosed in every manifest's `limitations`.

## Remaining blockers

- OCI/container isolation (needs Docker).
- Independent operator: today the verifier is the same process as the demo operator. Independence is
  architectural (separate re-execution), not organizational.

## Reviewer action needed

- [ ] Re-run `python scripts/e2e_demo.py` and compare the printed digests with the ones in your run.
- [ ] Confirm no artifact path escapes `evidence/`.
- [ ] Confirm `INCONCLUSIVE` can never yield `approval_eligible: true`.
