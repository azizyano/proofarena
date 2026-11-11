# ADR-0002 — Frontend: server-rendered Jinja2 instead of Next.js

- **Status:** accepted (deviation from kit)
- **Date:** 2026-11-11
- **Task:** PA-013

## Context

The kit requires Next.js + TypeScript. npm/pnpm cannot reach a registry in this environment, so
`next`, `react` and even a single npm dependency cannot be installed. Node itself is present but
useless without packages.

## Decision

Ship the UI as server-rendered Jinja2 templates with vanilla ES modules and a hand-written
stylesheet:

- no build step, no bundler, no npm install;
- the same six routes the PRD requires (`/`, `/bounties`, `/bounties/new`, `/bounties/:id`,
  `/arena/:id`, `/evidence/:claimId`, plus `/transactions/:receiptId` and `/status`);
- responsive layout verified at 1280px and 390px widths;
- state labels rendered server-side from chain state and re-polled client-side, so
  `PayoutPending` is observable before `Paid`;
- the browser never holds a private key: it asks the local signer process for signatures.

## Consequences

- The UI is real, executable and tested (`tests/test_api.py::test_pages_render_and_show_simulated_chain_banner`).
- It is not React: no component library, no client routing, no hydration. For the hackathon demo
  the tradeoff favours a working screen over framework fidelity.
- Porting to Next.js later is mechanical: the JSON API and page routes are stable and already
  CORS-enabled.

## What would reverse this

Network access plus ~30 minutes: scaffold `web/` with `create-next-app`, port the six pages to
React Server Components, reuse `/v1` unchanged, and re-point the signer's CORS allowlist.
