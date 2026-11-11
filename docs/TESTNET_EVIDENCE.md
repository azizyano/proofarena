# NEAR testnet evidence (what is real right now)

Recorded 2026-11-11 from this workspace, against **NEAR testnet** (`chain_id=testnet`,
node `2.14.0-rc.5`). Everything here is verifiable with the RPC commands at the bottom.
Nothing in this file is simulated.

## 1. A funded testnet account exists

| Field | Value |
|---|---|
| Account | `proofarena-c23922.testnet` |
| Balance | 10 NEAR (faucet) |
| Contract code | none yet (`code_hash = 111…111`), i.e. nothing deployed |
| Public key | `ed25519:DLui9BCoqkBMwgKcTank9d43TR24ERohH2Lkw7gUUnU4` |
| Key file | `proofarena/evidence/near-credentials/proofarena-c23922.testnet.json` (git-ignored) |
| How it was created | `POST https://helper.testnet.near.org/account` with `newAccountId` + `newAccountPublicKey` |

The key file could not be written to `%USERPROFILE%\.near-credentials` (sandbox ACL), so it
lives inside the workspace. Export `NEAR_CREDENTIALS` to that path, or move the file to the
standard location yourself.

## 2. The native signer is proven against the real chain

This is the important one. The contract does not exist yet, so the test transaction
*executes and fails* — which is exactly the evidence we need: the chain accepted the
signature and the Borsh encoding, then failed at execution for a different reason.

| Field | Value |
|---|---|
| Transaction | [`3E3cn3gfbJw9Lho4RgB5ntUy7QLcEieUkJ5kjwfQk22A`](https://explorer.testnet.near.org/transactions/3E3cn3gfbJw9Lho4RgB5ntUy7QLcEieUkJ5kjwfQk22A) |
| Method called | `get_bounty` on an account with no contract code |
| Final status | `Failure → ActionError → FunctionCallError → CompilationError → CodeDoesNotExist` |
| Meaning | transaction **validated** (signature + Borsh correct), **executed**, then failed for the expected reason |
| RPC lookup | `tx` → `final_execution_status: FINAL` |

If the encoding or signature had been wrong, the node would have returned
`InvalidTxError` and the transaction would never have executed. It did execute, so the
signing path in `proofarena/near/` is correct.

### A hash bug this caught

The first version hashed the **signed** transaction bytes for the receipt id. The chain's
`transaction_outcome.id` is `sha256(unsigned transaction)` instead. Symptom: a valid
transaction whose local hash the node had never heard of (`UNKNOWN_TRANSACTION` on lookup).
Fixed in `signing.sign_actions`, and `rpc._parse_commit` now prefers the node's own id.
`tests/test_near_testnet.py` pins the corrected formula.

## 3. What is still missing

| Item | Status | Why |
|---|---|---|
| Compiled `proofarena_escrow.wasm` | **BLOCKED here** | no Rust toolchain; downloads run at 11–19 KB/s (≈2–3.5 h for 137 MB) |
| `cargo test` on the contract | **BLOCKED here** | same |
| Contract deployed | **BLOCKED** | needs the wasm |
| On-chain lifecycle receipts | **BLOCKED** | needs the contract |

So the account and the signer are real; the contract deployment is your three commands.

## 3b. API ↔ signer ↔ chain wiring (live, executed)

With `wallet/near_signer.py` and `PROOFARENA_CHAIN_MODE=near-testnet scripts/dev.py` running:

| Step | Result |
|---|---|
| `GET /api/meta` | `chain_mode=near-testnet`, `chain_is_simulated=false`, contract `proofarena-c23922.testnet` |
| `GET /health` | `status=ok` |
| landing page | simulator banner correctly **absent** in testnet mode |
| `POST /auth/register` | **200** — the on-chain ownership check (`view_access_key`) accepted the NEAR key |
| `POST /auth/challenge` → `/sign` → `/auth/verify` | **200**, session for `proofarena-c23922.testnet` |
| `POST /v1/challenges` | **201**, manifest + digest created |
| `POST /v1/intents` (fund_bounty) → sign → `/fund` | **503 `E_CHAIN_UNAVAILABLE`** with `CodeDoesNotExist` — a real transaction was signed by the signer, broadcast, and rejected because no contract is deployed |

Signer audit log (real, no key material):

```json
{"kind":"signed","purpose":"login"}
{"kind":"signed","purpose":"intent","action":"fund_bounty"}
{"kind":"failed","method":"create_bounty","deposit_yocto":"101000000000000000000000",
 "error":"... CompilationError CodeDoesNotExist ..."}
```

So the entire application path is wired to testnet. The only missing piece is the contract bytecode.

## 4. Verify it yourself

```powershell
# account exists, balance, no contract code
python -c "from proofarena.near.rpc import NearRpc; import json; r=NearRpc('https://rpc.testnet.near.org'); print(json.dumps(r.account('proofarena-c23922.testnet'), indent=2))"

# the evidence transaction is retrievable and final
python -c "from proofarena.near.rpc import NearRpc; import json; r=NearRpc('https://rpc.testnet.near.org'); print(json.dumps(r.tx_status('3E3cn3gfbJw9Lho4RgB5ntUy7QLcEieUkJ5kjwfQk22A','proofarena-c23922.testnet'), indent=2)[:800])"

# or run the preflight, which checks all of the above and prints PASS/FAIL
$env:NEAR_TESTNET_ACCOUNT = "proofarena-c23922.testnet"
$env:NEAR_CONTRACT_ID     = "proofarena-c23922.testnet"
$env:NEAR_CREDENTIALS     = "evidence\near-credentials\proofarena-c23922.testnet.json"
python scripts/testnet/preflight.py
```

Expected right now: credentials PASS, RPC PASS, balance PASS, **contract has code FAIL**
(nothing deployed), wasm WARN, signer WARN (not running yet).

## 5. Exact next steps

See `docs/TESTNET_RUNBOOK.md` steps 4–8. Short form:

```powershell
rustup default stable
rustup target add wasm32-unknown-unknown
cargo test --manifest-path contracts/escrow/Cargo.toml        # first compile — report errors
python scripts/testnet/deploy.py --check
python scripts/testnet/deploy.py --dry-run
python scripts/testnet/deploy.py --yes
```

Then start `wallet/near_signer.py` + `PROOFARENA_CHAIN_MODE=near-testnet scripts/dev.py` and run
`python scripts/testnet/smoke.py --reward-near 0.1`. When that prints six transaction hashes, the
release verdict can move to `VERIFIED_TESTNET`.
