# ADR-0005 — Runner isolation: what is enforced, what is not

- **Status:** accepted with declared limitations
- **Date:** 2026-11-11
- **Task:** PA-010

## Context

The kit requires untrusted-code execution in a rootless OCI container with no network, no host
mounts, resource caps and a read-only base filesystem. Docker is not installed here.

## Decision

Implement the strongest *process-level* controls that are actually testable, and state the missing
kernel-level guarantees plainly rather than implying them.

### Enforced (tested)

| Control | Mechanism | Test |
|---|---|---|
| No outbound network | generated `sitecustomize.py` replaces `socket.create_connection`, `getaddrinfo`, `gethostbyname`, `socket.connect`/`connect_ex`; each attempt is appended to `network-blocked.log` | `test_run001_network_is_blocked_inside_sandbox` (≥3 blocked attempts) |
| No host secrets | child env built from an allowlist; any key containing SECRET/PRIVATE/SEED/MNEMONIC/PASSWORD/WALLET/TOKEN/NEAR/RPC is dropped | `test_no_secret_bearing_environment_variables_are_forwarded` |
| No host working tree | sources/tests copied into a throwaway sandbox; cwd is the sandbox | `test_sandbox_does_not_copy_forbidden_artifacts` |
| Bounded runtime | wall-clock timeout with process-tree kill (`taskkill /T`) | `test_run003_runaway_job_is_killed_by_timeout` |
| Bounded output | capture truncated at `PROOFARENA_RUNNER_MAX_OUTPUT_BYTES` | `test_run007_output_is_truncated` |
| Strict patch scope | diff may only touch `ledger.py`; `git apply` must match the pinned baseline | `test_full_lifecycle_pays_once_and_only_after_owner_approval` |
| Prompt injection is data | fixture text is scanned, recorded in the manifest, never executed as policy | `test_run004_prompt_injection_is_recorded_not_obeyed` |

### NOT enforced (do not claim otherwise)

- No kernel/container boundary: a determined escape through the Python interpreter is possible.
- No memory or PID cgroup limits; only wall-clock timeouts.
- No read-only base filesystem; the sandbox directory is writable.
- No filesystem confinement: the child can read world-readable host paths.
- The network denial is in-process; a native subprocess that opens its own sockets would bypass it.

## Why this is acceptable for the MVP

The only code executed is the repository's own synthetic fixture, which is pinned, hashed, and
reviewed. The runner is the correct *shape* (isolated job spec, artifact hashing, no secrets), and
the limitations are visible in every verification manifest.

## Production requirement

Replace `run_pytest`'s subprocess with a rootless container launch (podman/docker, `--network=none`,
`--read-only`, `--memory`, `--pids-limit`, non-root UID, no docker socket, ephemeral volume). The
`RunResult` contract and artifacts already accommodate that; only `materialize`/`run_pytest` change.
