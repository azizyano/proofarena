# ADR-0003 — Backend on Starlette instead of FastAPI

- **Status:** accepted (deviation from kit)
- **Date:** 2026-11-11
- **Task:** PA-011

## Context

`fastapi` is not installed and cannot be downloaded. `starlette` 0.41.3 and `pydantic` 2.10.4 —
FastAPI's two core dependencies — are present.

## Decision

Build the API directly on Starlette with Pydantic v2 models, keeping the exact request/response
shapes and route names from `docs/DATA_MODEL_AND_API.md`. No compatibility shim is created and no
module is named `fastapi`.

Request validation lives in explicit helpers (`_parse_challenge_body`, `_body`, schema validators)
rather than decorators. Errors are uniform: `{"error": "E_CODE", "message": "..."}` with mapped
HTTP statuses (401/402/403/404/409/422/429/503).

## Consequences

- Everything runs today: `uvicorn` + Starlette is the same ASGI server stack FastAPI uses.
- No automatic OpenAPI document is generated yet. This is the main missing FastAPI feature; when
  the dependency is installable, adding `fastapi` and decorating the existing handlers is
  incremental because the handlers already return responses and do their own validation.
- Tests assert the HTTP contract, not the framework, so a later swap does not invalidate them.

## Not done deliberately

Route-level dependency injection, `response_model` serialization, and `TestClient` lifespan
shortcuts were reimplemented by hand rather than emulated. Keeping the code plain Starlette makes
the swap honest instead of hiding a second framework.
