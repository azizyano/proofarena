"""Guards on the contract build configuration.

Two regressions this pins:

* ``unit-testing`` must not be enabled for the normal dependency. It pulls
  near-vm-runner/aws-lc-sys into the wasm build, which bloats it and can fail.
* the deploy script must detect a missing ``wasm32`` target and say so, instead of
  letting the user read ``error[E0463]: can't find crate for `core```.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CARGO = ROOT / "contracts" / "escrow" / "Cargo.toml"

sys.path.insert(0, str(ROOT / "scripts" / "testnet"))
import deploy  # noqa: E402


def load_manifest() -> dict:
    return tomllib.loads(CARGO.read_text(encoding="utf-8"))


def _near_sdk_spec(section: dict) -> dict:
    spec = section["near-sdk"]
    return spec if isinstance(spec, dict) else {"version": spec}


def test_contract_builds_as_cdylib():
    manifest = load_manifest()
    assert "cdylib" in manifest["lib"]["crate-type"]


def test_unit_testing_is_not_enabled_for_the_wasm_build():
    manifest = load_manifest()
    deps = _near_sdk_spec(manifest["dependencies"])
    features = deps.get("features", [])
    assert "unit-testing" not in features, (
        "unit-testing in [dependencies] pulls near-vm-runner/aws-lc-sys into the wasm build"
    )


def test_unit_testing_is_enabled_for_tests():
    manifest = load_manifest()
    dev = _near_sdk_spec(manifest["dev-dependencies"])
    assert "unit-testing" in dev.get("features", [])


def test_release_profile_is_wasm_friendly():
    manifest = load_manifest()
    release = manifest["profile"]["release"]
    assert release["panic"] == "abort"
    assert release["overflow-checks"] is True
    assert release["lto"] is True


@pytest.mark.parametrize(
    "targets,expected",
    [
        (["wasm32-unknown-unknown"], True),
        (["x86_64-pc-windows-msvc", "wasm32-unknown-unknown"], True),
        (["x86_64-pc-windows-msvc"], False),
        ([], False),
        (["wasm32-wasip1"], False),
    ],
)
def test_wasm_target_detection(targets, expected):
    assert deploy.wasm_target_installed(targets) is expected


def test_target_hint_contains_the_exact_command():
    assert "rustup target add wasm32-unknown-unknown" in deploy.TARGET_HINT


def test_default_wasm_path_matches_cargo_output():
    path = deploy.DEFAULT_WASM
    assert path.name == "proofarena_escrow.wasm"
    assert "wasm32-unknown-unknown" in path.parts
    assert "release" in path.parts
