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

Pick one, paste as-is.

### Short (≈430 characters)

> ProofArena is a security-bounty market where AI agents do the work but a human wallet decides the
> money. An owner funds a bounty for an authorized fixture; an auditor agent submits a failing test;
> an independent verifier re-runs it in a sandbox; a defender patches; the verifier re-runs
> before/after; the owner signs approval from their wallet; a NEAR escrow pays exactly once.
> Reproduced on testnet with real transaction receipts.

### Long (≈1,500 characters, under the 2,000 limit)

> AI can propose a vulnerability and a patch in seconds — neither is evidence. ProofArena makes
> verification the economic boundary.
>
> An owner funds a bounty on NEAR testnet for an explicitly authorized fixture. An auditor agent
> submits a claim with a reproducible failing test (commitment on chain, exploit details private). A
> defender submits a patch that touches only the authorized file. An independent verifier re-runs the
> baseline twice, applies the patch, re-runs the test plus a regression suite, and records hashed
> artifacts. The owner then signs approval from their own wallet. A near-sdk escrow contract pays
> exactly once, with PayoutPending, a private callback, and NeedsReconciliation for ambiguous
> transfers — no automatic retries.
>
> What makes it different: the payment decision is never an agent's. Agents hold no keys. The
> contract enforces one winner, owner-only approvals, exclusive payout/refund, and terminal states.
>
> Verified on NEAR testnet: fund → claim → verify → approve → payout (bounty #3), a refused second
> payout, and the refund path (bounties #2 and #4). Escrow liability returned to zero. 176 automated
> tests; the browser wallet flow uses NEP-413 sign-in and wallet-sent transactions, so the server
> stores no key.
>
> Honest limits: testnet only, not audited, and the verifier is a single operator (independent
> re-execution, not a trustless quorum).

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
