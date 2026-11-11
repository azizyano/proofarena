# NEAR testnet runbook

Everything below is run **by you**, on a machine with internet access and a funded
testnet account. It takes about 20–30 minutes the first time, most of it waiting for
Rust to build.

What you will end up with:

* a real contract on NEAR testnet,
* a running app in `near-testnet` mode where every state change is a signed transaction,
* a full demo run whose receipts are real transaction hashes with explorer links.

> **You will never paste a private key into the app, a chat, or a file in this repo.**
> The signer process reads it from the standard NEAR credentials location. If I ask
> for anything, it will be a transaction hash or an error message — never a key.

---

## 0. Checklist before you start

| Need | Check | Install |
|---|---|---|
| Internet + browser | — | — |
| Node.js (only for `near` CLI fallback) | `node --version` | already present |
| Rust + wasm target | `rustup target list --installed` | step 1 |
| Funded testnet account + key file | `preflight` will tell you | step 2 |
| Python deps | `pip install -r requirements.txt` | step 0b |

```powershell
cd proofarena
pip install -r requirements.txt
python -m pytest tests/ -q          # expect: 124 passed (no network needed)
```

If this fails, stop and send me the output. Everything above is offline.

---

## 1. Install the Rust toolchain

> Already done: the testnet account exists and the native signer has been proven against the chain
> (one real transaction executed). The only remaining blocker is compiling the wasm, which needs
> this toolchain — the build machine's link was ~15 KB/s, so it belongs here.

```powershell
winget install --id Rustlang.Rustup -e
# open a NEW terminal, then:
rustup default stable
rustup target add wasm32-unknown-unknown
cargo --version                     # expect: cargo 1.8x
rustup target list --installed      # expect: ... wasm32-unknown-unknown
```

Expected time: 5–10 minutes. `wasm-opt` is **not** required.

---

## 2. Testnet account — already created for you

An account already exists and is funded: **`proofarena-c23922.testnet`** (10 NEAR).
The key is at `evidence/near-credentials/proofarena-c23922.testnet.json` and was never printed
or committed. Details and the on-chain evidence: `docs/TESTNET_EVIDENCE.md`.

Use it as-is:

```powershell
$env:NEAR_TESTNET_ACCOUNT = "proofarena-c23922.testnet"
$env:NEAR_CONTRACT_ID     = "proofarena-c23922.testnet"
$env:NEAR_CREDENTIALS     = "evidence\near-credentials\proofarena-c23922.testnet.json"
```

Prefer your own account? Create one (the script saves the key, never prints it):

```powershell
python scripts/testnet/create_account.py --name proofarena-demo-yourname
```

`%USERPROFILE%\.near-credentials` may be unwritable in some sandboxes; the script falls back to
`evidence/near-credentials/` and tells you to export `NEAR_CREDENTIALS`.

<details>
<summary>Manual faucet route (what the script automates)</summary>

1. Open <https://helper.testnet.near.org>.
2. Create an account, e.g. `proofarena-demo-<yourname>.testnet`.
   The page shows the account id and an `ed25519:...` **private key** — copy both.
3. Save the credentials file:

```
%USERPROFILE%\.near-credentials\testnet\<your-account>.testnet.json
```

```json
{
  "account_id": "proofarena-demo-yourname.testnet",
  "public_key": "ed25519:<public key>",
  "private_key": "ed25519:<private key>"
}
```
</details>

1. Open <https://helper.testnet.near.org>.
2. Create an account, e.g. `proofarena-demo-<yourname>.testnet`.
   The page shows the account id and an `ed25519:...` **private key** — copy both.
3. Save the credentials file where the NEAR tooling expects it:

```
%USERPROFILE%\.near-credentials\testnet\<your-account>.testnet.json
```

Content:

```json
{
  "account_id": "proofarena-demo-yourname.testnet",
  "public_key": "ed25519:<the public key the faucet showed>",
  "private_key": "ed25519:<the private key the faucet showed>"
}
```

Notes:

* This file is the wallet. Do not commit it, do not paste it anywhere.
* The faucet account is both the **owner** (approves payouts) and the **contract host**.
  That is fine for testnet and keeps the steps short.
* If the faucet did not show a key, use "Import existing account" there, or run
  `near login` after installing the CLI — but the JSON file is the simplest path.

---

## 3. Point the environment at your account

Environment variables live only in **one PowerShell window**. Open PowerShell, `cd` to the repo,
then paste this block:

```powershell
$env:NEAR_TESTNET_ACCOUNT = "proofarena-c23922.testnet"
$env:NEAR_CONTRACT_ID     = $env:NEAR_TESTNET_ACCOUNT
$env:NEAR_RPC_URL         = "https://rpc.testnet.near.org"
$env:PROOFARENA_SIGNER_URL = "http://127.0.0.1:8788"
$env:NEAR_CREDENTIALS     = "evidence\near-credentials\proofarena-c23922.testnet.json"
```

Both account values must end in `.testnet`. Every script refuses anything else.

### Optional shortcut

`scripts/testnet/set_env.ps1` sets the same variables. Two notes:

1. Windows may refuse to run local `.ps1` files if the file carries a "downloaded" mark, and this
   repo's scripts are not code-signed. If you see *"is not digitally signed"*, either allow scripts
   for this window only:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
   . .\scripts\testnet\set_env.ps1
   ```

   or just paste the block above - nothing needs the script.
2. It must be **dot-sourced** (`. .\path\script.ps1`, note the leading dot) or the variables
   disappear when the script ends.

Everything else in this repo is Python, so execution policy does not affect it.

Check before going further:

```powershell
python scripts/testnet/preflight.py
```

At this stage you should see `owner balance` and `contract has code` failing — that is
correct, the contract is not deployed yet. Everything else (credentials, RPC, key
parsing) should be `PASS`. Send me this output if anything unexpected is `FAIL`.

---

## 4. Build the contract (the only step that can surprise us)

> **Prefer a guided build?** `cargo near build non-reproducible-wasm` (from
> [cargo-near](https://github.com/near/cargo-near)) builds with ABI + `wasm-opt`, and
> `cargo near --teach-me` explains each step. Install it with:
> `irm https://github.com/near/cargo-near/releases/latest/download/cargo-near-installer.ps1 | iex`.
> See `docs/DEPLOY_OPTIONS.md`. Whatever builds the wasm, the deploy step below still works -
> point it at the artifact with `--wasm`.

The contract in `contracts/escrow/src/lib.rs` has **never been compiled** — the build
environment had no Rust toolchain. Expect the compiler to find something on the first
run. That is the point of this step.

```powershell
cd contracts\escrow
cargo test            # unit tests, includes the SEC-001..010 mirror
cd ..\..
python scripts/testnet/deploy.py --check    # builds the .wasm only
```

Success looks like:

```
built ...\contracts\escrow\target\wasm32-unknown-unknown\release\proofarena_escrow.wasm (NNN KB)
```

If `cargo test`/`cargo build` fails, copy the **whole error block** to me. The likely
first fixes are small near-sdk 5.x API details (the callback promise construction and
`IterableMap` bounds). Nothing about the design changes.

---

## 5. Deploy

Dry run first — this signs locally and prints the transaction without sending it:

```powershell
python scripts/testnet/deploy.py --dry-run
```

You should see `transaction (round-trip decoded, … bytes)`, the action list
`['deploy_contract', 'function_call']`, a tx hash, and an explorer URL.

Then deploy for real:

```powershell
python scripts/testnet/deploy.py --yes
```

Expected output:

```
success at block <height>
view list_bounty_ids → []
Deployed and initialised.
  contract : proofarena-demo-yourname.testnet
  owner    : proofarena-demo-yourname.testnet
  tx       : <hash>
  explorer : https://explorer.testnet.near.org/transactions/<hash>
```

If the transaction is rejected with a deserialization/`InvalidTxError`, retry through
the official CLI (signing delegated to NEAR's own code):

```powershell
npm install -g near-cli
$env:PROOFARENA_NEAR_WALLET = "cli"
python scripts/testnet/deploy.py --yes
```

---

## 6. Run the app in testnet mode

Two terminals.

**Terminal A — the signer (owns the key):**

```powershell
$env:NEAR_TESTNET_ACCOUNT = "proofarena-demo-yourname.testnet"
$env:NEAR_CONTRACT_ID     = "proofarena-demo-yourname.testnet"
python wallet/near_signer.py
```

Expected: `[near-signer] account=… contract=… public_key=ed25519:… port=8788`

**Terminal B — the API + web UI:**

```powershell
$env:NEAR_TESTNET_ACCOUNT = "proofarena-demo-yourname.testnet"
$env:NEAR_CONTRACT_ID     = "proofarena-demo-yourname.testnet"
$env:PROOFARENA_CHAIN_MODE = "near-testnet"
python scripts/dev.py
```

Expected: `[api] http://127.0.0.1:8787 settlement=manual`

Preflight again — now everything should pass:

```powershell
python scripts/testnet/preflight.py
```

---

## 7. Run the full lifecycle on chain

```powershell
python scripts/testnet/smoke.py --reward-near 0.1
```

This drives: login (with on-chain key ownership check) → create+fund bounty →
commit claim → submit patch → independent verification → owner-signed approval →
owner-signed payout → waits for the contract's own callback → asserts a second payout
is refused. Every transaction is printed with an explorer URL.

Expected tail:

```
TESTNET RESULT: verified on chain
verification: PATCH_VERIFIED
settlement  : Paid
transactions:
  bounty_created      <hash>
  claim_committed     <hash>
  review_started      <hash>
  claim_approved      <hash>
  payout_requested    <hash>
  payout_completed    <hash>
```

`payout_completed` is the contract's own callback receipt — that is the on-chain proof
the transfer settled.

---

## 8. Do it from the browser

1. <http://127.0.0.1:8787> → **Connect owner wallet** (this asks the signer to sign a
   login message; the API verifies the key exists on your testnet account).
2. `/bounties/new` → **Create + fund** → the signer prompts, then a real `create_bounty`
   transaction is sent.
3. Issue an auditor token, run the auditor/defender scripts or the smoke test, then
   **Approve (sign)** and **Request payout (sign)**.
4. Watch `PayoutPending → Paid`; the receipts table links to the explorer.

The banner disappears in testnet mode — it only appears when the chain is the local
simulator.

---

## Costs

| Item | Approx. |
|---|---|
| Contract deploy + init | ~0.1 NEAR storage (refunded if you delete the account) |
| `create_bounty` | reward + 0.001 NEAR storage reserve |
| claim/approve/payout calls | < 0.01 NEAR gas in total |
| One full demo at `--reward-near 0.1` | ~0.11 NEAR |

A faucet account has more than enough. If you run low, top up at
<https://helper.testnet.near.org> using the same account.

---

## Troubleshooting

| Symptom | Meaning | Fix |
|---|---|---|
| `E_CHAIN_UNAVAILABLE` in the API | env not visible to that terminal | re-export `NEAR_CONTRACT_ID`, `NEAR_TESTNET_ACCOUNT`, `PROOFARENA_CHAIN_MODE` in the same terminal |
| `no NEAR credentials found` | key file not where expected | step 2; or set `$env:NEAR_CREDENTIALS` to the file path |
| `key is not an access key on …` | credentials don't match the account | re-download the key from the faucet page |
| `signer service … unreachable` | signer not running | `python wallet/near_signer.py` |
| deploy: `InvalidTxError` / deserialize | native Borsh path rejected | retry with `PROOFARENA_NEAR_WALLET=cli` (step 5) |
| `contract has code` FAIL after deploy | deploy tx never executed | check the explorer link printed in step 5 |
| `contract view works` FAIL | `init` did not run (wrong owner?) | redeploy to a fresh account, or call `new` once manually |
| payout stays `PayoutPending` | callback not executed yet / failed | wait 30s, check explorer, then use `reconcile` with evidence — never re-send payout |
| `E_REPLAY` on second payout | correct behaviour | nothing to fix |

---

## What to send me if something breaks

1. The exact command you ran.
2. The full error text (not a screenshot of one line).
3. For a transaction: the tx hash, or the explorer URL.

Never send a private key, seed phrase, or the contents of the credentials file.

---

## After it works

Record the run with `docs/DEMO_SCRIPT.md`, then I can flip the release verdict from
`READY FOR TESTNET DEMO` to `VERIFIED_TESTNET` — with your transaction hashes as the
evidence, and nothing invented.
