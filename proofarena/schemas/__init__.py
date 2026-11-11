"""Frozen JSON schemas (PA-003) copied verbatim from the ProofArena kit.

The schemas are the interface contract between the auditor/defender agents and
the verifier. They are loaded from disk rather than duplicated in code so that
the validator can never drift from the published schema file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ..errors import SchemaError

SCHEMA_DIR = Path(__file__).resolve().parent
CLAIM_SCHEMA = SCHEMA_DIR / "claim_manifest.schema.json"
VERIFICATION_SCHEMA = SCHEMA_DIR / "verification_result.schema.json"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def claim_manifest_schema() -> dict[str, Any]:
    return load(CLAIM_SCHEMA)


def verification_result_schema() -> dict[str, Any]:
    return load(VERIFICATION_SCHEMA)


def _validate(document: Any, schema: dict[str, Any], label: str) -> None:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.path) or "<root>"
        raise SchemaError(f"{label} invalid at {location}: {first.message}")


def validate_claim_manifest(document: Any) -> None:
    _validate(document, claim_manifest_schema(), "claim manifest")


def validate_verification_result(document: Any) -> None:
    _validate(document, verification_result_schema(), "verification result")


__all__ = [
    "CLAIM_SCHEMA",
    "SCHEMA_DIR",
    "VERIFICATION_SCHEMA",
    "claim_manifest_schema",
    "validate_claim_manifest",
    "validate_verification_result",
    "verification_result_schema",
]
