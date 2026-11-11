# ADR-0001 — MVP baseline, interface freeze and pinned dependencies

- **Status:** accepted
- **Date:** 2026-11-11
- **Owners:** CTO (author), reviewed against Backend / NEAR / Security specs
- **Task:** PA-002, PA-003

## Context

The ProofArena kit specifies Next.js + FastAPI + Rust/near-sdk + PostgreSQL + OCI isolation. The
build environment was inventoried before any code was written (`docs/runtime-inventory.json`):

| Component | Available | Consequence |
|---|---|---|
| Python 3.12.4 | yes | usable |
| Node 22.22 / npm / pnpm | yes, but **no registry access** | no Next.js install |
| Rust toolchain | `rustup` shim present, **no toolchain, no crates.io** | contract source only, uncompiled |
| Docker | **not installed** | no OCI runner |
| NEAR CLI / near-sandbox | **not installed** | no local chain |
| Outbound network | **blocked** (TLS handshake fails) | no package downloads, no RPC |
| fastapi wheel | **not installed** | Starlette directly |
| starlette, pydantic v2, jinja2, jsonschema, cryptography, pytest | installed | usable |

## Decision

1. Build the *complete, executable* off-chain system on the installed stack, and keep the
   on-chain artefact honest: real contract source, uncompiled, clearly labelled.
2. Freeze the `/v1` API surface and the two JSON schemas from the kit (copied verbatim into
   `proofarena/schemas/`) before implementing anything downstream.
3. Treat the Python escrow engine as the **authoritative executable specification**; the Rust
   contract must encode identical rules and is reviewed against it line by line.
4. Pin every dependency to the versions the test evidence was produced with
   (`requirements.txt`, `pyproject.toml`).
5. No fake deployment artefacts of any kind: no invented account ids, receipt ids, explorer links
   or test output.

## Pinned versions

```
python 3.12.4 · starlette 0.41.3 · uvicorn 0.34.0 · pydantic 2.10.4 · jinja2 3.1.6
jsonschema 4.26.0 · cryptography 47.0.0 · pytest 8.3.4 · httpx 0.28.1
near-sdk 5.9.0 (declared, unresolved) · git 2.50.1.windows.1
```

## Consequences

- The demo runs on a fresh checkout with **zero network calls**.
- Three spec components are BLOCKED rather than imitated (testnet, OCI isolation, Next.js).
- Swapping in FastAPI, Next.js or the NEAR adapter is additive: the HTTP contract, the chain
  adapter protocol and the page routes were chosen to match the spec's shapes.

## Rejected alternatives

- **Vendor a fake `fastapi` shim.** Rejected: a fake dependency is the exact "empty template app"
  failure mode the kit forbids.
- **Ship an unbuildable Next.js app.** Rejected: unexecuted frontend code is decoration.
- **Present the local ledger as a sandbox chain.** Rejected: that is a fabricated receipt claim.
