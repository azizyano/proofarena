# Deployment options — including "is there a Remix for NEAR?"

Short answer: **no, not for native NEAR Rust contracts.** Remix compiles Solidity
*in the browser*; a NEAR contract is Rust compiled to WebAssembly, which needs a real
Rust toolchain, so nothing in a browser can build it. What exists instead:

| Option | What it actually is | Best for | Needs local Rust? |
|---|---|---|---|
| **`scripts/testnet/deploy.py`** (this repo) | Scripted build + sign + broadcast + verify; dry-run first | repeatable deploys, CI, no surprises | yes (to build) |
| **`cargo near deploy`** ([cargo-near](https://github.com/near/cargo-near)) | Interactive menu: builds with ABI + `wasm-opt`, then prompts you through deploy | the closest thing to a "click deploy" experience | yes (to build) |
| **GitHub Codespaces** (`near/cargo-near-new-project-template`, or the `.devcontainer` in this repo) | Browser VS Code with Rust + wasm target preinstalled | when you don't want to install anything | **no** — runs in the cloud |
| **`near-cli-rs`** (`near contract deploy …`) | Interactive CLI deploy, signs with your keychain | deploying an already-built wasm | no (for deploy only) |
| **Remix** | Solidity IDE for EVM | — | not applicable to native NEAR |
| **Aurora** (EVM on NEAR) | Runs Solidity/EVM contracts *on NEAR's EVM engine*, Remix-compatible | EVM teams wanting NEAR settlement | not applicable — different contract model |

So if you want the Remix *feel*, `cargo near` is the nearest thing, and Codespaces removes
the install step entirely. But the deploy still ends up being a signed transaction from a
terminal or a browser wallet — there is no drag-and-drop contract deploy on NEAR.

## Note on PowerShell script execution

Every command here is either Python or Cargo, so the Windows execution policy never enters the
picture - only the optional `set_env.ps1` helper is a PowerShell script. If Windows refuses it
("is not digitally signed"), paste the `$env:` lines instead, or run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force` for that window.

## Option 1 — this repo's scripted deploy (recommended)

Deterministic, dry-run first, verifies the contract answers a view call afterwards.

```powershell
. .\scripts\testnet\set_env.ps1
cargo test --manifest-path contracts/escrow/Cargo.toml
python scripts/testnet/deploy.py --dry-run
python scripts/testnet/deploy.py --yes
```

Pros: one path, no prompts to misread, works with the funded account already created.
Cons: plain output, no ABI generation.

## Option 2 — `cargo near` (guided, Remix-ish)

```powershell
irm https://github.com/near/cargo-near/releases/latest/download/cargo-near-installer.ps1 | iex
cd contracts\escrow
cargo near build non-reproducible-wasm        # adds ABI + wasm-opt (smaller wasm = cheaper deploy)
cargo near deploy build-non-reproducible-wasm # interactive: pick account, network, sign
```

`cargo near --teach-me` explains every step it takes. `cargo near create-dev-account`
creates a testnet account for you (you already have one, so this is optional).

Two caveats:

* `cargo near deploy`'s prompts decide whether it calls an init function. Our contract needs
  `new` with `{"owner": "<account>"}`. If it does not offer init arguments, deploy with this
  repo's script instead (Option 1) — or deploy the wasm it built:
  `python scripts/testnet/deploy.py --wasm contracts/escrow/target/near/proofarena_escrow.wasm --yes`.
* The `reproducible-wasm` variants need Docker. You don't have Docker; use the
  `non-reproducible-wasm` variants.

## Option 3 — Codespaces (no local install)

Commit the included `.devcontainer/` configuration, open the repo on GitHub →
**Code → Codespaces → Create codespace**. The container has Rust, the wasm target, Python and
`cargo-near` preinstalled, so the only thing you bring is the credentials file (paste it into the
container, never into git).

Useful when Rust installation is the obstacle. Caveat: the testnet key would then live in a
cloud VM, so use a throwaway testnet account for that path.

## Using a browser wallet (Meteor, Nightly, MyNearWallet)

Browser wallets sign **inside their own UI**. They cannot sign for a Python script, cannot deploy
a contract, and cannot be the signer for this server-side flow. Two ways to include one:

* **Export the account's private key once** and point `NEAR_CREDENTIALS` at it (Layout A in the
  runbook). The wallet still works normally afterwards; both just share the key.
* **Wallet Selector** (`@near-wallet-selector/*`): the browser signs each action and the server
  never holds a key. This is the "proper" dapp architecture and the right long-term answer, but it
  needs a JS build (React or ESM bundling) and browser testing, so it is out of scope for this
  server-rendered UI.

## What not to expect

* No browser IDE compiles Rust to NEAR wasm. Any tutorial claiming so is either using a
  prebuilt wasm or a hosted backend.
* Remix + NEAR = Aurora (EVM), a different deployment target: same chain family, different
  contract format. This repo's escrow is native Rust and cannot run on Aurora unchanged.
