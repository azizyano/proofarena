# NEAR testnet evidence

**Verdict: `VERIFIED_TESTNET`** — the complete ProofArena lifecycle has been executed on NEAR
testnet with real transactions, real receipts, and a real native-NEAR transfer.

| | |
|---|---|
| Network | `testnet` (`chain_id=testnet`, node `2.14.0-rc.5`) |
| Contract | `proofarena-c23922.testnet` |
| Contract owner | `coldlemon3275.testnet` (Meteor wallet account) |
| Auditor agent | `proofarena-auditor-0fbc4e.testnet` (payout recipient) |
| Recorded | 2026-11-11, 21:25–21:55 UTC |
| Evidence source | JSON-RPC `view_*`, `tx`, and explorer transaction hashes below |

Every hash in this file resolves on <https://explorer.testnet.near.org>. Nothing here is simulated;
the local-simulator path still exists for tests, but this run used the real chain.

---

## 1. Contract deployment

| Field | Value |
|---|---|
| Transaction | [`9bK3NTS9BvUeGPm81wek662utFnZUXRg1PBYMUJobM61`](https://explorer.testnet.near.org/transactions/9bK3NTS9BvUeGPm81wek662utFnZUXRg1PBYMUJobM61) |
| Signed by | `proofarena-c23922.testnet` |
| Deployed code | 230,309 bytes, valid wasm (`\0asm`) |
| Code hash | `3KcVAjtBAuCsUtJdiXLkwU84grhSziezHwWgjzZrY9Bt` |
| Init call | `new({"owner":"coldlemon3275.testnet"})` |
| Verified by | `list_bounty_ids() → []` immediately afterwards, and `total_escrow_yocto() → 0` |

## 2. Full lifecycle: fund → claim → patch → verify → approve → pay once

Run on bounty **#3**. Explorer hashes are the transaction ids returned by the signer service.

| # | Step | Transaction |
|---|---|---|
| 1 | owner funds 0.1 NEAR bounty | [`4WzDB89VMoJfQ5QGjS2zcsVR8zhySRQKQDN9MNSpg5ba`](https://explorer.testnet.near.org/transactions/4WzDB89VMoJfQ5QGjS2zcsVR8zhySRQKQDN9MNSpg5ba) |
| 2 | auditor commits the claim commitment | [`2Kpw6rLZbBV8RNAPwzq83afNHpU46KSLH8HiUC3RoSY1`](https://explorer.testnet.near.org/transactions/2Kpw6rLZbBV8RNAPwzq83afNHpU46KSLH8HiUC3RoSY1) |
| 3 | owner closes submissions (Review) | [`FpwgPgaqQEpMiXDvfZTpVxymRrSfFgrgmVDZyVy7DgfR`](https://explorer.testnet.near.org/transactions/FpwgPgaqQEpMiXDvfZTpVxymRrSfFgrgmVDZyVy7DgfR) |
| 4 | owner approves the winner | [`DfzioifsafKGibYi55Dh6Pijq6Be9XvWfR4sko2tDzQf`](https://explorer.testnet.near.org/transactions/DfzioifsafKGibYi55Dh6Pijq6Be9XvWfR4sko2tDzQf) |
| 5 | owner requests payout (one transfer) | [`AVwQZ4mdAmSmw6YiKS34FNn1mdd3YgKFX5tW5unXkRap`](https://explorer.testnet.near.org/transactions/AVwQZ4mdAmSmw6YiKS34FNn1mdd3YgKFX5tW5unXkRap) |

The last transaction's own receipt logs contain both events, which is the proof that the private
callback ran and settled:

```json
{"event":"payout_requested","bounty_id":3,"attempt_id":2,"amount_yocto":"100000000000000000000000","recipient":"proofarena-auditor-0fbc4e.testnet"}
{"event":"payout_completed","bounty_id":3,"attempt_id":2}
```

Final on-chain state (read back with `get_bounty`):

```
state                 Paid
reward_yocto          100000000000000000000000      (exactly 0.1 NEAR)
paid_yocto            100000000000000000000000      (exactly 0.1 NEAR)
refunded_yocto        0
escrow_balance_yocto  0
winner                proofarena-auditor-0fbc4e.testnet
approved_claim_id     2
attempt #2            kind=Payout status=Succeeded
```

Exactly-once check: a second `request_payout` was refused by the contract and surfaced as
`HTTP 409 E_STATE` ("contract refused request_payout"). No second transfer exists.

## 3. Refund path (no accepted claim)

Bounty **#4**, 30-second submission and review windows, no claim submitted:

| # | Step | Transaction |
|---|---|---|
| 1 | owner funds 0.02 NEAR bounty | [`FVRFPKntzXd2bs7eKcHsYwb2SV7kFk1TgnFmJkTyVZuK`](https://explorer.testnet.near.org/transactions/FVRFPKntzXd2bs7eKcHsYwb2SV7kFk1TgnFmJkTyVZuK) |
| 2 | early refund attempt (before the grace period) | refused: `409 E_STATE`, no transaction |
| 3 | owner signs `refund_expired` after expiry | [`H5eGNidaraf6akVhzggE7hNJzbaUynTGgptFVoZRe5bZ`](https://explorer.testnet.near.org/transactions/H5eGNidaraf6akVhzggE7hNJzbaUynTGgptFVoZRe5bZ) |

Final state: `Refunded`, `refunded_yocto = 20000000000000000000000`, escrow `0`.

## 3b. Cleaned-up final state (all four settlements exercised)

The interrupted run that left bounty #2 open was refunded once its grace period expired, so the
contract now has **zero outstanding liability** and both terminal paths are represented:

| Bounty | State | Paid | Refunded | Transaction |
|---|---|---|---|---|
| #1 | Paid | 0.1 NEAR | — | `6mxzbdYBpFHjgo4aJxdSjYJm1cnigJG1sJYmsgAhi4nP` |
| #2 | Refunded | — | 0.1 NEAR | [`EYyzkpCuQkxkv2U29vZr81W4SoFQKJs8mmU2UW9Fgtcd`](https://explorer.testnet.near.org/transactions/EYyzkpCuQkxkv2U29vZr81W4SoFQKJs8mmU2UW9Fgtcd) |
| #3 | Paid | 0.1 NEAR | — | `AVwQZ4mdAmSmw6YiKS34FNn1mdd3YgKFX5tW5unXkRap` |
| #4 | Refunded | — | 0.02 NEAR | `H5eGNidaraf6akVhzggE7hNJzbaUynTGgptFVoZRe5bZ` |

```
total_escrow_yocto()  ->  0
owner balance         ->  1.7918 NEAR  (0.3 spent on two rewards, the rest is gas + storage)
auditor balance       ->  10.1996 NEAR (received both 0.1 rewards)
```

Conservation, observed on the real chain: every yocto that went in either reached the approved
winner or came back to the owner. No bounty is both paid and refunded; nothing is stuck.

## 4. Independent verification (anyone can re-run this)

```powershell
. .\scripts\testnet\set_env.ps1
python scripts/testnet/preflight.py
```

Or directly:

```python
from proofarena.near.rpc import NearRpc
rpc = NearRpc("https://rpc.testnet.near.org")
c = "proofarena-c23922.testnet"
rpc.view_call(c, "list_bounty_ids", {})          # -> [1, 2, 3, 4]
rpc.view_call(c, "get_bounty", {"bounty_id": 3}) # -> state Paid, winner auditor
rpc.view_call(c, "total_escrow_yocto", {})       # -> 1e23 (bounty #2 only)
rpc.tx_status("AVwQZ4mdAmSmw6YiKS34FNn1mdd3YgKFX5tW5unXkRap", "coldlemon3275.testnet")
```

Accounting invariant, observed: `paid + refunded <= reward` per bounty, and the contract's total
escrow liability equals exactly the one bounty still open.

## 5. Bugs found by running against the real chain (all fixed)

A real network finds things a mock cannot. Each of these now has a regression test.

| Bug | Symptom | Fix |
|---|---|---|
| Missing wasm target | `error[E0463]: can't find crate for 'core'` | deploy script pre-checks `rustup target list --installed` and prints the exact command |
| `unit-testing` in `[dependencies]` | pulled `near-vm-runner`/`aws-lc-sys` into the wasm build (244 crates) | feature moved to `[dev-dependencies]` only |
| Wrong query variant | every view call failed with `PARSE_ERROR: unknown variant 'call'` | use `call_function` (pinned by test) |
| Meteor double prefix | `KeyError: ':'` parsing `ed25519:ed25519:<b58>` | `strip_key_prefix` tolerates repeated prefixes |
| Signer HTML 500 | a dropped RPC connection returned an HTML error page; API saw "non-JSON" | signer catches everything and returns JSON 502; nonce fetch retries 3× |
| Float reward | `int(0.1 * 10**24)` produced `100000000000000008388608` | `Decimal` arithmetic in the demo scripts |
| Contract refusal as 503 | a refused payout looked like "chain unavailable" | `FunctionCallError`/`ExecutionError` now map to `409 E_STATE` |
| Refund response shape | `KeyError: 'receipts'` — refund endpoint omitted receipts | refund returns the same shape as payout (test pins it) |
| Callback construction | near-sdk 4.x `Promise::new(self).method(gas)` would not compile | `Self::ext(..).with_static_gas(..)` |

## 6. Honest caveats

1. **Bounty #2 was refunded** after its grace period (see 3b); escrow liability is now `0`.
2. **Contract panics surface as generic wasm traps.** `panic = "abort"` in the release profile (a
   size optimisation) drops assertion messages, so a refusal appears as
   `WebAssembly trap: An unreachable opcode was executed` instead of the human sentence. The API
   names the refused method; a future deploy should use `env::panic_str` for user-facing refusals.
3. **Testnet only.** 10 NEAR of faucet funds, no mainnet, not audited, no dispute adjudication.
4. **The verifier is a single operator** (this machine). Independence is architectural
   (re-execution), not organisational.
5. **Two signers were used**: the owner's Meteor key for owner actions, a separate auditor key for
   `commit_claim`. Both live only in local credentials files.

## 7. Reproduce from scratch

1. `docs/TESTNET_RUNBOOK.md` steps 1–6 (toolchain, account, env, build, deploy).
2. `python wallet/near_signer.py` (owner) and a second instance for the auditor.
3. `PROOFARENA_CHAIN_MODE=near-testnet python scripts/dev.py`.
4. `python scripts/testnet/smoke.py --reward-near 0.1 --auditor-account <auditor>`.
5. `python scripts/testnet/refund_demo.py --reward-near 0.02 --window 30`.
