# Wallet demo — static site for Vercel

A single-page app that lets **anyone with a NEAR testnet wallet** drive the contract: their wallet
signs and broadcasts every transaction, and the API only verifies what landed on chain.

No build step: plain HTML + ES modules, wallet-selector loaded from esm.sh. Vercel serves it as
static files.

## What it does

1. Connect Meteor / Nightly / MyNearWallet (testnet).
2. Sign a NEP-413 message → the API checks the key against the account's on-chain access keys and
   issues a session. No server key exists.
3. **Create + fund a bounty**: the API returns the exact `create_bounty` plan; the wallet signs and
   sends it; the API re-reads the chain and only then records it.
4. Close submissions, approve, payout, refund — same intent → wallet → confirm loop.

## Deploy to Vercel

```bash
cd web-vercel
npx vercel deploy --prod        # or: push the repo and import it in the Vercel dashboard
```

Vercel settings:

| Setting | Value |
|---|---|
| Framework preset | **Other** |
| Root directory | `web-vercel` |
| Build command | *(none)* |
| Output directory | *(none / `.`)* |

Then point the page at your API. Edit `config.js` **before** deploying:

```js
window.PROOFARENA_API = "https://api.your-domain.example";
```

That API runs on your VPS (see `docs/HOSTING.md`); Vercel cannot host it because the verifier runs
`pytest` in a subprocess and needs a real filesystem. Cross-origin calls work: the API sets
permissive CORS headers.

Alternative without editing the file: open the deployed page and run
`localStorage.setItem("pa_api","https://api…")`? Not implemented — `config.js` is the intended knob.

## Try it before deploying

The API serves this same folder at <http://127.0.0.1:8787/wallet/> (same origin, so `config.js`
stays empty).

## Limitations (honest)

* The browser flow needs a real wallet extension; it cannot be exercised by automated tests.
  Everything on the server side (NEP-413 verification, transaction matching) **is** tested, including
  a replay of real testnet transactions (`scripts/testnet/verify_wallet_txs.py`).
* The agent steps (claim / patch / verification) still run server-side with scoped agent tokens and
  a platform demo agent account; the demo page links to the full dashboard for those.
* Testnet only. There is no mainnet configuration.
