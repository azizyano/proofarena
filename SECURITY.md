# Security policy

## Scope of this repository

ProofArena is a **testnet research prototype**. The escrow contract has never been audited and must
not custody real funds. A deployed instance exists on NEAR testnet only
(`proofarena-c23922.testnet`); there is no mainnet configuration, and none is planned in this
iteration.

## No secrets in the repository

The repository is public. Nothing tracked in git contains a private key, seed phrase, RPC
credential, or API token. This is enforced three ways:

1. `.gitignore` excludes `evidence/` (runtime data and credentials), `wallet/*.json`, `.env*`
   (except the placeholder `.env.example`), `*.sqlite3`, `.pytest_tmp/`, and common key file names.
2. `.env.example` contains **placeholders only** — no real values, no keys.
3. CI/test fixtures generate throwaway keys at runtime; they are never committed.

If you think you have found a secret in the history, treat it as compromised: report it privately
(first line below) and rotate the key on testnet.

### Where the demo keys actually live

| Key | Location | Purpose |
|---|---|---|
| Owner (`coldlemon3275.testnet`) | `%USERPROFILE%\.near-credentials\testnet\` — **outside** this repo | signs owner actions in the demo |
| Contract host (`proofarena-c23922.testnet`) | `evidence/near-credentials/` inside the workspace, git-ignored | can upgrade or delete the deployed contract account |
| Auditor (`proofarena-auditor-0fbc4e.testnet`) | `evidence/near-credentials/`, git-ignored | signs `commit_claim` in the demo |
| Local demo signer | `wallet/owner.key.json`, git-ignored, regenerated on demand | local-mode login only; deleted from this checkout |

**Recommendation:** move every `*.json` credential out of the repository into
`%USERPROFILE%\.near-credentials\testnet\`. Ignored is not the same as safe when the repository is
public and the folder syncs to cloud storage.

All three are **testnet-only** accounts funded by the faucet. If you fork this repository to run the
demo, generate your own accounts (`python scripts/testnet/create_account.py`) — do not reuse keys
from someone else's run, and never place a key inside the repository directory.

## Reporting a vulnerability

This is a hackathon prototype with no production users. If you still want to report something:

- open a GitHub security advisory on this repository (private), or
- contact the repository owner directly.

Please include the exact revision, a reproduction, and the observable impact. Reports about the
**on-chain escrow invariants** (double payout, refund-after-approval, callback replay, cross-bounty
accounting) are the most valuable; `docs/THREAT_MODEL.md` lists what is and is not defended.

## Known limitations (not vulnerabilities to report)

- The verifier is a single operator: independent re-execution, not a trustless quorum.
- Contract assertion messages are lost because the release build uses `panic = "abort"`, so a
  refusal surfaces as a generic wasm trap.
- The runner denies network access in-process; it is not a kernel/container sandbox.
- Everything is testnet, and the dispute path is a manual owner decision.
