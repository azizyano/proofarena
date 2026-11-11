"""Independent verification engine (PA-009).

The verifier does not read the auditor's stdout. It re-executes the pinned tests
itself, twice on the baseline (to detect flakiness), then once on the patched
revision plus the regression suite. Only what the runner observed is reported.

Outcome mapping (docs/AGENT_VERIFICATION_PROTOCOL.md):

====================  ==================  ===================  ==================
Status                Baseline            Patch                Approval eligible
====================  ==================  ===================  ==================
PATCH_VERIFIED        fails deterministically  fix + regression pass  yes, owner reviews
REPRODUCED            fails deterministically  patch not proven        no
REJECTED              passes              n/a                  no
INCONCLUSIVE          flaky / timeout     n/a                  no
====================  ==================  ===================  ==================
"""

from __future__ import annotations

import difflib
import json
import platform
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .canonical import sha256_file, sha256_json, sha256_text
from .config import RUNNER_TIMEOUT_S
from .patching import read_text
from .runner import (
    RunResult,
    SandboxReport,
    SandboxSpec,
    materialize,
    run_pytest,
    scan_for_injection,
)

SCHEMA_VERSION = "1.0"
FORBIDDEN_ARTIFACT_TOKENS = (".env", "id_rsa", ".pem", ".key", "wallet.json", "session.secret")


_VOLATILE_TIME = re.compile(r"in \d+(?:\.\d+)?s(?: \(\d+(?:\.\d+)?s\))?")


def stable_digest(text: str) -> str:
    """Digest of output with wall-clock timings removed.

    Determinism is about the same assertion failing, not about pytest reporting
    the same elapsed seconds. Raw ``output_sha256`` still hashes everything.
    """
    return sha256_text(_VOLATILE_TIME.sub("in <t>s", text))


def default_runner_digest() -> str:
    """Honest identifier for what executed. Not an OCI image digest."""
    return (
        f"local-sandbox:cp{sys.version_info.major}{sys.version_info.minor}"
        f"-{platform.system().lower()}-{platform.machine().lower()}-nonet"
    )


@dataclass
class VerifyOutcome:
    """`result` validates against verification_result.schema.json.

    `details` holds extra observability (patch digest, repeat digests, injection
    samples) that must not pollute the frozen schema.
    """

    result: dict[str, object]
    details: dict[str, object]
    manifest: dict[str, object]
    artifacts_dir: Path
    report: SandboxReport
    notes: list[str] = field(default_factory=list)


def _check_block(run: RunResult) -> dict[str, object]:
    return {
        "command": " ".join(run.command),
        "exit_code": int(run.exit_code),
        "invariant_failed": bool(run.invariant_failed),
        "output_sha256": run.stdout_sha256,
    }


def _diff_text(baseline_dir: Path, patched_dir: Path, filename: str = "ledger.py") -> str:
    left = read_text(baseline_dir / filename).splitlines(keepends=True)
    right = read_text(patched_dir / filename).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(left, right, fromfile=f"a/{filename}", tofile=f"b/{filename}", n=3)
    )


def run_verification(
    *,
    job_id: str,
    claim_id: str,
    source_commit: str,
    spec: SandboxSpec,
    artifacts_root: Path,
    scope_reference: str,
    runner_image_digest: str | None = None,
    timeout_s: int = RUNNER_TIMEOUT_S,
    flakiness_runs: int = 2,
) -> VerifyOutcome:
    artifacts_root.mkdir(parents=True, exist_ok=True)
    job_dir = artifacts_root / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    work_root = job_dir / "sandboxes"
    work_root.mkdir(exist_ok=True)
    effective_spec = SandboxSpec(
        baseline_dir=spec.baseline_dir,
        patched_dir=spec.patched_dir,
        tests_dir=spec.tests_dir,
        work_root=work_root,
    )

    report = SandboxReport()
    limitations: list[str] = [
        "Synthetic fixture only; no live third-party deployment was tested.",
        "Runner isolation is process-level (network denied in-process, env allowlist), "
        "not an OCI/kernel sandbox.",
        "No cryptographic attestation of execution: the verifier is a single operator.",
        "Wall-clock timeout is the only resource ceiling enforced on this platform.",
    ]

    # --- baseline: twice, to expose flakiness -----------------------------
    baseline_runs: list[RunResult] = []
    injection_samples: list[str] = []
    for index in range(1, flakiness_runs + 1):
        sandbox = materialize(effective_spec, "baseline", f"b{index}")
        if not report.copied_files:
            report.copied_files = sorted(
                (sandbox / "COPIED_FILES.txt").read_text(encoding="utf-8").splitlines()
            )
            injection_samples = scan_for_injection(sandbox)
        run = run_pytest(sandbox, target="tests/test_invariant.py", label=f"baseline-{index}", timeout_s=timeout_s)
        baseline_runs.append(run)
        report.runs.append(run)
        (job_dir / f"baseline-{index}.log").write_text(run.stdout, encoding="utf-8")

    report.prompt_injection_samples = injection_samples

    # --- patched revision -------------------------------------------------
    patched_sandbox = materialize(effective_spec, "patched", "p1")
    patched_run = run_pytest(patched_sandbox, target="tests/test_invariant.py", label="patched", timeout_s=timeout_s)
    report.runs.append(patched_run)
    (job_dir / "patched.log").write_text(patched_run.stdout, encoding="utf-8")

    regression_run = run_pytest(patched_sandbox, target="tests/test_regression.py", label="regression", timeout_s=timeout_s)
    report.runs.append(regression_run)
    (job_dir / "regression.log").write_text(regression_run.stdout, encoding="utf-8")

    # --- interpret --------------------------------------------------------
    baseline_failures = [r.invariant_failed for r in baseline_runs]
    timed_out = any(r.timed_out for r in report.runs)
    reproduced = all(baseline_failures)
    flaky = any(baseline_failures) and not all(baseline_failures)
    patch_ok = (patched_run.exit_code == 0) and not patched_run.invariant_failed and not patched_run.timed_out
    regression_ok = (
        regression_run.exit_code == 0
        and not regression_run.invariant_failed
        and not regression_run.timed_out
    )

    if timed_out:
        status = "INCONCLUSIVE"
        limitations.append("At least one run hit the wall-clock timeout; evidence is incomplete.")
    elif flaky:
        status = "INCONCLUSIVE"
        limitations.append("Baseline behaviour was not deterministic across repeats (FLAKY).")
    elif not reproduced:
        status = "REJECTED"
        limitations.append("The claimed invariant failure did not reproduce on the pinned baseline.")
    elif patch_ok and regression_ok:
        status = "PATCH_VERIFIED"
    else:
        status = "REPRODUCED"
        if not patch_ok:
            limitations.append("Patched revision did not pass the invariant test.")
        if not regression_ok:
            limitations.append("Regression suite failed on the patched revision.")

    forbidden = [
        name
        for name in report.copied_files
        if any(token in name.lower() for token in FORBIDDEN_ARTIFACT_TOKENS)
    ]
    scope_valid = source_commit_is_valid(source_commit) and not forbidden
    if forbidden:
        limitations.append(f"Forbidden artifact names present in sandbox: {forbidden}")

    # --- artifacts --------------------------------------------------------
    patch_text = _diff_text(spec.baseline_dir, spec.patched_dir)
    (job_dir / "diff.patch").write_text(patch_text, encoding="utf-8")

    baseline_ref = baseline_runs[0]
    verified_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    details = {
        "scope_reference": scope_reference,
        "baseline_repeat_digests": [stable_digest(r.stdout) for r in baseline_runs],
        "deterministic_baseline": (not flaky)
        and len({stable_digest(r.stdout) for r in baseline_runs}) == 1,
        "regression_exit_code": regression_run.exit_code,
        "patch_sha256": sha256_text(patch_text),
        "network_block_events": sum(r.network_block_events for r in report.runs),
        "prompt_injection_samples": report.prompt_injection_samples,
        "approval_eligible": status == "PATCH_VERIFIED" and scope_valid,
    }

    manifest = {
        "job_id": job_id,
        "claim_id": claim_id,
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": verified_at,
        "scope_reference": scope_reference,
        "source_commit": source_commit,
        "runner_image_digest": runner_image_digest or default_runner_digest(),
        "runner_config": {
            "timeout_s": timeout_s,
            "flakiness_runs": flakiness_runs,
            "network": "denied in-process via sitecustomize",
            "env": "allowlist only; secrets not forwarded",
            "work_root": str(work_root),
        },
        "runs": [r.to_public() for r in report.runs],
        "copied_files": report.copied_files,
        "prompt_injection_samples": report.prompt_injection_samples,
        "artifacts": {
            "baseline-1.log": sha256_file(job_dir / "baseline-1.log"),
            "baseline-2.log": sha256_file(job_dir / "baseline-2.log"),
            "patched.log": sha256_file(job_dir / "patched.log"),
            "regression.log": sha256_file(job_dir / "regression.log"),
            "diff.patch": sha256_file(job_dir / "diff.patch"),
        },
        "status": status,
        "scope_valid": scope_valid,
        "limitations": limitations,
    }
    manifest_path = job_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    manifest_digest = sha256_file(manifest_path)

    # Frozen schema shape — no extra properties, validated in tests.
    result = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "claim_id": claim_id,
        "status": status,
        "source_commit": source_commit,
        "runner_image_digest": runner_image_digest or default_runner_digest(),
        "baseline": _check_block(baseline_ref),
        "patched": _check_block(patched_run),
        "scope_valid": scope_valid,
        "limitations": limitations,
        "manifest_sha256": manifest_digest,
        "reviewer_kind": "deterministic_runner",
        "verified_at_utc": verified_at,
    }
    (job_dir / "summary.json").write_text(
        json.dumps({"result": result, "details": details}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (job_dir / "evidence_digest.txt").write_text(
        sha256_json({k: v for k, v in result.items() if k != "verified_at_utc"}) + "\n",
        encoding="utf-8",
    )

    return VerifyOutcome(
        result=result,
        details=details,
        manifest=manifest,
        artifacts_dir=job_dir,
        report=report,
        notes=limitations,
    )


def source_commit_is_valid(source_commit: str) -> bool:
    return (
        isinstance(source_commit, str)
        and len(source_commit) == 40
        and all(c in "0123456789abcdef" for c in source_commit)
    )
