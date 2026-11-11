"""Runner isolation and verifier outcome tests (PA-009, PA-010, RUN-001..RUN-007)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from conftest import ROOT
from proofarena.canonical import sha256_file, sha256_text
from proofarena.runner import (
    RunResult,
    SandboxSpec,
    materialize,
    run_pytest,
    scan_for_injection,
)
from proofarena.verify import run_verification, stable_digest

FIXTURE = ROOT / "fixture"
SCHEMAS = ROOT / "proofarena" / "schemas"
SOURCE_COMMIT = "a" * 40


def spec(tmp_path: Path, tests_dir: Path | None = None) -> SandboxSpec:
    return SandboxSpec(
        baseline_dir=FIXTURE / "baseline",
        patched_dir=FIXTURE / "patched",
        tests_dir=tests_dir or (FIXTURE / "tests"),
        work_root=tmp_path / "work",
    )


# ---------------------------------------------------------------------------
# fixture correctness
# ---------------------------------------------------------------------------
def test_baseline_fixture_fails_deterministically(workdir: Path):
    s = spec(workdir)
    runs: list[RunResult] = []
    for index in (1, 2):
        sandbox = materialize(s, "baseline", f"b{index}")
        runs.append(run_pytest(sandbox, target="tests/test_invariant.py", label=f"b{index}", timeout_s=90))
    assert [r.invariant_failed for r in runs] == [True, True], [r.stdout[:800] for r in runs]
    assert all(r.exit_code != 0 for r in runs), [(r.exit_code, r.stdout[:800]) for r in runs]
    assert runs[0].invariant_marker_seen and runs[1].invariant_marker_seen
    assert stable_digest(runs[0].stdout) == stable_digest(runs[1].stdout), "baseline must be deterministic"


def test_patched_fixture_passes(workdir: Path):
    s = spec(workdir)
    sandbox = materialize(s, "patched", "p1")
    run = run_pytest(sandbox, target="tests/test_invariant.py", label="patched", timeout_s=90)
    assert run.exit_code == 0, run.stdout
    assert run.invariant_failed is False


def test_regression_suite_passes_on_both_revisions(workdir: Path):
    s = spec(workdir)
    for variant in ("baseline", "patched"):
        sandbox = materialize(s, variant, f"r-{variant}")
        run = run_pytest(sandbox, target="tests/test_regression.py", label=f"reg-{variant}", timeout_s=90)
        assert run.exit_code == 0, f"{variant} regression failed:\n{run.stdout}"


# ---------------------------------------------------------------------------
# RUN-001 / RUN-002 — sandbox controls actually bite
# ---------------------------------------------------------------------------
def test_run001_network_is_blocked_inside_sandbox(workdir: Path):
    s = spec(workdir, FIXTURE / "attacks")
    sandbox = materialize(s, "baseline", "net")
    run = run_pytest(sandbox, target="tests/test_network_denied.py", label="network", timeout_s=90)
    assert run.exit_code == 0, run.stdout
    assert run.network_block_events >= 3, "guard did not observe denied socket attempts"
    netlog = Path(run.sandbox_dir) / "network-blocked.log"
    assert netlog.exists() and len(netlog.read_text(encoding="utf-8").splitlines()) >= 3


def test_run003_runaway_job_is_killed_by_timeout(workdir: Path):
    s = spec(workdir, FIXTURE / "attacks")
    sandbox = materialize(s, "baseline", "slow")
    run = run_pytest(sandbox, target="tests/test_timeout.py", label="timeout", timeout_s=3)
    assert run.timed_out is True
    assert run.exit_code == -1
    assert run.elapsed_s < 60


def test_run007_output_is_truncated(workdir: Path):
    s = spec(workdir, FIXTURE / "attacks")
    sandbox = materialize(s, "baseline", "flood")
    run = run_pytest(
        sandbox,
        target="tests/test_output_flood.py",
        label="flood",
        timeout_s=90,
        max_output_bytes=4096,
        extra_args=["-s"],
    )
    assert run.truncated is True
    assert len(run.stdout.encode("utf-8")) <= 4096


def test_run004_prompt_injection_is_recorded_not_obeyed(workdir: Path):
    samples = scan_for_injection(FIXTURE)
    assert any("SYSTEM:" in s for s in samples), "injection sample was not detected"
    # Detection is observation only: the fixture files are unchanged by it.
    assert (FIXTURE / "baseline" / "ledger.py").exists()


def test_sandbox_does_not_copy_forbidden_artifacts(workdir: Path):
    s = spec(workdir)
    sandbox = materialize(s, "baseline", "cp")
    copied = (sandbox / "COPIED_FILES.txt").read_text(encoding="utf-8").splitlines()
    assert "ledger.py" in copied
    assert not any(name.endswith((".env", ".key", ".pem")) for name in copied)


# ---------------------------------------------------------------------------
# verifier
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def verified(tmp_path_factory=None) -> object:
    root = ROOT / ".pytest_tmp" / "verify-module"
    root.mkdir(parents=True, exist_ok=True)
    return run_verification(
        job_id="job-verify-001",
        claim_id="claim-0001",
        source_commit=SOURCE_COMMIT,
        spec=SandboxSpec(
            baseline_dir=FIXTURE / "baseline",
            patched_dir=FIXTURE / "patched",
            tests_dir=FIXTURE / "tests",
            work_root=root / "work",
        ),
        artifacts_root=root / "artifacts",
        scope_reference="challenge-manifest.json#scope",
        timeout_s=90,
    )


def test_verifier_reports_patch_verified(verified):
    result = verified.result
    assert result["status"] == "PATCH_VERIFIED"
    assert result["baseline"]["invariant_failed"] is True
    assert result["baseline"]["exit_code"] != 0
    assert result["patched"]["invariant_failed"] is False
    assert result["patched"]["exit_code"] == 0
    assert result["scope_valid"] is True
    assert result["reviewer_kind"] == "deterministic_runner"
    assert verified.details["regression_exit_code"] == 0
    assert verified.details["deterministic_baseline"] is True


def test_verifier_result_matches_frozen_schema(verified):
    schema = json.loads((SCHEMAS / "verification_result.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(instance=verified.result, schema=schema)


def test_verifier_writes_private_artifacts_with_matching_digests(verified):
    job_dir = verified.artifacts_dir
    for name in ("manifest.json", "summary.json", "baseline-1.log", "patched.log", "diff.patch"):
        assert (job_dir / name).exists(), name
    manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["artifacts"].items():
        assert sha256_file(job_dir / name) == digest
    assert verified.result["manifest_sha256"] == sha256_file(job_dir / "manifest.json")


def test_verifier_diff_shows_the_unit_fix(verified):
    patch = (verified.artifacts_dir / "diff.patch").read_text(encoding="utf-8")
    assert "--- a/ledger.py" in patch
    assert "debit_cents > account.locked_cents" in patch


def test_verifier_marks_flaky_baseline_inconclusive(workdir: Path, monkeypatch):
    """A nondeterministic baseline must never become an approval-eligible claim.

    The alternating runner is injected here instead of relying on real randomness:
    a randomly flaky test would make this assertion itself flaky, which would be a
    poor way to test a flakiness detector.
    """
    import proofarena.verify as verify_module
    from proofarena.runner import RunResult

    counter = {"baseline": 0}

    def fake_run(sandbox, *, target, label, timeout_s=90, max_output_bytes=262144, extra_args=None):
        failed = False
        if label.startswith("baseline"):
            counter["baseline"] += 1
            failed = counter["baseline"] % 2 == 1  # fail, pass, fail, ...
        stdout = "PROOFARENA-INVARIANT:ALLOWANCE_EXCEEDED" if failed else "ok"
        return RunResult(
            label=label,
            command=["python", "-m", "pytest", target],
            exit_code=1 if failed else 0,
            elapsed_s=0.01,
            timed_out=False,
            stdout=stdout,
            stdout_sha256=sha256_text(stdout),
            truncated=False,
            invariant_failed=failed,
            invariant_marker_seen=failed,
            network_block_events=0,
            sandbox_dir=str(sandbox),
        )

    monkeypatch.setattr(verify_module, "run_pytest", fake_run)
    outcome = verify_module.run_verification(
        job_id="job-flaky-001",
        claim_id="claim-flaky",
        source_commit=SOURCE_COMMIT,
        spec=SandboxSpec(
            baseline_dir=FIXTURE / "baseline",
            patched_dir=FIXTURE / "patched",
            tests_dir=FIXTURE / "tests",
            work_root=workdir / "flaky-work",
        ),
        artifacts_root=workdir / "flaky-artifacts",
        scope_reference="challenge.json#scope",
        timeout_s=90,
        flakiness_runs=2,
    )
    assert outcome.result["status"] == "INCONCLUSIVE"
    assert outcome.details["approval_eligible"] is False
    assert outcome.details["deterministic_baseline"] is False
