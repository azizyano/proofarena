# Hackathon submission — NEAR HACKATHON: Build Beyond Boundaries

Deadline: **2026-11-14 23:59 UTC**. Judging 2026-11-16 → 18.
Form: <https://nearhackathon.com/#apply>. One submission per team, **cannot be edited after sending**.

What the form asks for, and where each answer is prepared below:

| Field | Ready? |
|---|---|
| Project name | ✅ ProofArena |
| Description (20–2,000 chars) | ✅ two versions below (short + long) |
| Team lead, email, team size | ⚠️ you fill in |
| Preferred track | ✅ recommended: **AI Security & Defense** (alternatives listed) |
| NEAR account for prize payout | ⚠️ you fill in (e.g. `coldlemon3275.testnet`) |
| Public repository URL | ⚠️ you must push (commands below) |
| Demo post on X | ⚠️ you must post (draft + shot list below) |

Three actions are yours because they need your accounts: **push to GitHub → post on X → submit the
form.** Everything else is prepared.

---

## 1. Description for the form

Two finished versions, both inside the 20-2,000 character limit. Paste one; nothing else needed.

| Version | Characters | Use when |
|---|---|---|
| A - full | 1620 | recommended (default choice) |
| B - short | 884 | if you want it terser |

### Version A - full (1620 characters)

```text
AI can now propose a vulnerability and a patch in seconds. But a proposal is not evidence, and a convincing patch is not proof the exploit is gone. Security bounties still pay for plausible prose.

ProofArena makes verification the thing that unlocks the money. An owner funds a bounty on NEAR for an explicitly authorized fixture. An auditor agent submits a claim: a reproducible failing test, with only a salted commitment going on-chain and the exploit detail kept private. A defender submits a patch that may touch nothing outside the authorized file. An independent verifier re-executes the baseline twice, applies the patch, re-runs the failing test plus a regression suite, and hashes every artifact. Then, and only then, the owner signs approval from their own wallet.

A near-sdk escrow contract pays exactly once. State moves through PayoutPending, a private callback, and NeedsReconciliation when a transfer is ambiguous - no automatic retries. Paid and Refunded are terminal, and the contract refuses a second payout or a refund after approval.

NEAR is the settlement layer and the trust boundary, not decoration. The escrow holds native NEAR, checks predecessor identity for owner-only approvals, and pushes funds with promise callbacks. Agents hold no keys: browser wallets sign every transaction through NEP-413 sign-in, and the server verifies what actually landed on chain instead of trusting the client.

It runs: deployed at proofarena-c23922.testnet, one bounty verified and paid exactly once, expired bounties refunded, escrow liability back to zero, 176 automated tests. Testnet only, not audited.
```

### Version B - short (884 characters)

```text
AI agents can now propose vulnerabilities and patches in seconds, but neither is evidence. ProofArena makes verified evidence the thing that unlocks payment: an owner funds a bounty for an authorized fixture, an auditor agent submits a reproducible failing test, a defender patches, an independent verifier re-runs baseline and patch in a sandbox, and only then does the owner's wallet approve.

NEAR is the settlement layer: a near-sdk escrow holds native NEAR, enforces one winner and owner-only approval, and pays exactly once with pending, callback and reconciliation states that forbid a second payout or a refund after approval. Agents never hold keys; wallets sign with NEP-413 and the server verifies the transaction on chain.

Deployed on testnet at proofarena-c23922.testnet: verified, paid once, refunds settled, escrow liability zero, 176 tests. Testnet only, not audited.
```

Both state the problem, what was built, how NEAR is used, the real testnet evidence, and the limits
(testnet only, not audited). Do not add "audited", "trustless" or mainnet claims.

## 2. Track

Recommended: **AI Security & Defense** — "make AI-controlled money safer with spending limits, wallet
safeguards…". This project is exactly that: agent-generated security work, human-gated settlement,
strict escrow invariants.

Acceptable alternatives if you prefer: **Autonomous Business** (agents run the work, humans keep the
purse), **AI Swarms** (auditor/defender/verifier roles), or **Open Innovation**.

## 3. Demo post on X (draft)

The rules require a public post with a video or screenshots, mentioning that it was built for NEAR
HACKATHON.

> **ProofArena — verifiable AI security work, paid once by a human** 🧩
>
> AI finds a bug and writes a patch in seconds. Neither is evidence. So we made verification the
> thing that unlocks money:
>
> owner funds → auditor submits a failing test → independent verifier re-runs it → defender patches →
> verifier re-runs → **owner's wallet approves → escrow pays exactly once**
>
> Live on NEAR testnet today (real receipts, 0.1 NEAR):
> • fund `4WzDB89V…` • approve `Dfzioifs…` • payout `AVwQZ4md…`
> • a second payout: refused. a refund after expiry: settled.
>
> Agents never hold keys. The contract enforces one winner + terminal states. Browser wallets sign
> with NEP-413; the server has no key.
>
> 176 tests. Rust + near-sdk escrow, FastAPI-style API, wallet-signed UI.
>
> Built for @NEAR_hckthn #NEARHackathon
>
> repo: <your GitHub URL>

Keep the four short hashes as they are (they are real, see `docs/TESTNET_EVIDENCE.md`); full hashes
can be in a reply.

### Screenshots to take (the app is running locally)

Start it (`python scripts/dev.py` and the signer), then capture:

1. **Wallet page** — `http://127.0.0.1:8787/wallet/` with a wallet connected (shows "NEAR testnet"
   badge and the account).
2. **Bounty detail** — `/bounties/<id>` for bounty #3: `Paid`, winner, receipt rows with explorer
   links.
3. **Explorer** — one transaction page in the browser (e.g. the payout) showing success + logs
   `payout_requested` / `payout_completed`.
4. **Verification artifacts** — the job page or `summary.json` with `PATCH_VERIFIED`, baseline exit 1,
   patched exit 0.

A 30–60 s screen recording of "connect → create+fund → approve → payout → explorer" is stronger than
stills if you have time; `docs/DEMO_SCRIPT.md` has the beats.

## 4. Push the repository (public)

Status: **already public** at <https://github.com/azizyano/proofarena> (default branch `master`).
To publish the latest commits and the release tag:

```powershell
cd C:\Users\Yano\OneDrive\Documents\deepseek-harness\default-workspace\proofarena
git push origin master
git push origin v0.1.0-hackathon
```

Before any push, confirm nothing sensitive is tracked (expect no output):

```powershell
git ls-files | Select-String -Pattern "credential|\.env$|key\.json|evidence/"
```

`evidence/` (databases, generated keys, artifacts) is git-ignored, and the history has been audited:
no private key, token or seed appears in any commit. See `SECURITY.md` for where the demo keys
actually live.

Then set the repo description to the short pitch and add topics:
`near`, `near-protocol`, `ai-agents`, `escrow`, `security`, `rust`, `near-sdk`, `hackathon`.

## 5. Submit the form

1. Open <https://nearhackathon.com/#apply>.
2. Paste the short or long description.
3. Track: **AI Security & Defense**.
4. Prize account: your `*.testnet` (or mainnet) address.
5. Repo URL: the GitHub link. Demo post: the X link.
6. **Check every field before sending — it cannot be edited.**

## 6. After submitting

Submission is a snapshot of links, not of code: the repo keeps changing and judges review on
Nov 16–18. So the right order is *submit as soon as the repo and post exist*, then keep improving:

- [ ] Finish the browser-wallet verification you were doing (create + fund from Meteor).
- [ ] Optional: `env::panic_str` for readable on-chain refusals (currently generic wasm traps).
- [ ] Optional: "Run the agents" button so a stranger reaches `Paid` without running scripts.
- [ ] Keep `docs/TESTNET_EVIDENCE.md` updated with any new transaction hashes.

Do **not** rewrite history or force-push after submission — judges may have the repo cached.
