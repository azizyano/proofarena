# Demo storyboard (for a 3–5 minute recording)

**Read this first:** the chain is the local simulator. Say so on camera. Never describe a `local-`
receipt as a testnet transaction.

## Pre-flight (off camera)

```bash
pip install -r requirements.txt
python -m pytest tests/ -q                 # expect: 176 passed
python scripts/e2e_demo.py                 # expect: PATCH_VERIFIED → Paid once
```

Terminal A: `python scripts/dev.py` → <http://127.0.0.1:8787>
Terminal B: `python wallet/signer.py`

## Beat 1 — the problem (25s)

Show the landing page. One line: *"AI can propose a vulnerability and a patch in seconds. Neither is
evidence. This marketplace pays only for reproduced work, and only when the owner signs."*
Point at the banner: **CHAIN MODE: LOCAL SIMULATOR — not testnet.**

## Beat 2 — owner funds a bounty (40s)

1. **Connect owner wallet** (top right) → account appears.
2. `/bounties/new` → keep defaults (5 NEAR, 600s window) → **Create + fund**.
3. Wallet prompt signs `fund_bounty`; page lands on the bounty.
   Say: *"The API never signs. It asked the wallet for one signature over a message binding this
   challenge, this amount and a single-use nonce."*
4. Point at state `Open` and receipt `local-000001 bounty_created`.

## Beat 3 — auditor finds a real defect (45s)

In a terminal (or show the demo output):

```bash
python scripts/e2e_demo.py   # steps [5]–[6]
```

Show: `exit code 1`, `invariant_failed=True`, the assertion text, the claim commitment.
Say: *"Only a hash of the manifest goes on chain. The reproducer stays private."*

## Beat 4 — defender patches, verifier re-runs (50s)

Demo steps `[7]`–`[8]`. Highlight:

- patch touches only `ledger.py` (scope enforced),
- baseline run **twice** → deterministic,
- patched run exit 0, regression suite exit 0,
- status `PATCH_VERIFIED`, `approval_eligible: true`,
- artifacts hashed in `jobs/<id>/manifest.json`.

Say: *"The verifier did not read the auditor's logs. It re-ran the test itself."*

## Beat 5 — owner approves, escrow pays once (50s)

Demo steps `[9]`–`[12]`, or in the UI: **Approve (sign)** → **Request payout (sign)**.

- State goes `Approved → PayoutPending`. **Stop here.** Say: *"This is not a payment yet."*
- Deliver settlement → `Paid`. `paid = 5 NEAR`, escrow `0`.
- Try a second payout → `409 E_REPLAY`.

## Beat 6 — audit trail and honesty (30s)

Open `/status` and a `/transactions/local-000001` page: hash chain valid, receipt commits to its
predecessor. Then read the release verdict out loud:

> "176 tests pass locally. The escrow contract is deployed on NEAR testnet and has paid out and
> refunded for real — the transaction hashes are in docs/TESTNET_EVIDENCE.md. This recording runs the
> local simulator so it works without a network, which is why these receipts say `local-ledger`."

## Failure paths worth showing (optional, 20s)

- `python -m pytest tests/test_runner_verify.py -q -k "network or timeout"` → sandbox denies network,
  kills runaway jobs.
- Fixture README contains a `SYSTEM:` prompt-injection line; the verifier records it and scope does
  not change (`RUN-004`).

## Do not do

- Do not claim a testnet explorer link, contract id or transaction hash.
- Do not say "audited", "trustless", "production ready" or "provably secure".
- Do not show a screenshot of a payment that did not run in this session.
