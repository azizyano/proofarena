# Submission

The current, hackathon-specific package lives in **[HACKATHON_SUBMISSION.md](HACKATHON_SUBMISSION.md)**:
paste-ready description, track choice, demo-post draft, screenshot list, pre-submit checklist and the
exact steps that need the owner's own accounts (GitHub push, X post, the form).

This page keeps the long-form record of what the project can and cannot claim.

## Verified on NEAR testnet (2026-11-11)

| Item | Status |
|---|---|
| Contract deployed | `proofarena-c23922.testnet`, 230,309-byte wasm, code hash `3KcVAjtB…` |
| Full lifecycle | fund → claim → review → approve → payout, exactly once (bounty #3) |
| Refund path | expired bounties #2 and #4 refunded; early refund refused |
| Accounting | `total_escrow_yocto() → 0`; every yocto either paid to the approved winner or refunded |
| Second payout | refused by the contract (`409 E_STATE`) |
| Wallet flow | NEP-413 sign-in verified against on-chain keys; wallet-sent transactions verified against the plan |
| Tests | 176 automated tests, two consecutive green runs |

Evidence with hashes and explorer links: **[TESTNET_EVIDENCE.md](TESTNET_EVIDENCE.md)**.

## What is still not claimed

- **Not audited.** The escrow contract has had one compilation, tests written by the same author,
  and real testnet runs — not a third-party security review.
- **Testnet only.** No mainnet deployment, no real funds, no token.
- **Single-operator verifier.** Re-execution is independent of the auditor, but the operator is one
  machine, not a trustless quorum or an attestation network.
- **Contract refusals are generic wasm traps.** `panic = "abort"` drops assertion messages, so a
  refusal surfaces as `WebAssembly trap` and the API names the refused method rather than the human
  reason.
- **Browser wallet step needs a human.** The in-page popup / `signMessage` / `signAndSendTransaction`
  path cannot be exercised headlessly; the server-side verification is tested, the browser plumbing
  is not.
- **Vercel cannot host the API.** The verifier runs `pytest` in a subprocess with a filesystem; that
  needs the VPS. The static wallet UI can live on Vercel.

## What judges can reproduce

See the "Judge's path — do X, see Y" table at the top of the [README](../README.md). Steps 1–2 need
no network; steps 3–5 check live testnet state.
