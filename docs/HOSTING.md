# Hosting the demo

> **The recommended deployment is now Vercel (UI) + your VPS (API + verifier), with the visitor's
> own wallet signing every transaction. Step-by-step: `docs/DEPLOY_VERCEL_VPS.md`.**
>
> The options below remain valid but are secondary: the wallet path removes the server key entirely,
> so the "no key on the server" warning applies to the legacy signer flow only.

The contract is live on testnet. The question is how to let other people see it. There are three
honest options; pick by how much you want visitors to *do* versus *see*.

First, the constraint that shapes everything:

> The app signs with a key. The signer service (`wallet/near_signer.py`) is the only process that
> can move funds, and the API only asks it to sign **after verifying an owner-signed intent**.
> A visitor cannot produce that intent — they do not have your key. So a public deployment is
> read-only for strangers unless you add a scripted demo runner (option 2).

## Option 1 — read-only showcase (recommended, ~15 minutes)

Visitors browse the real on-chain bounties, claims, verification artifacts, receipts and explorer
links. Everything that requires the owner key returns `503 E_CHAIN_UNAVAILABLE` instead of pretending.

What you need on the host:

* Python 3.11+, `pip install -r requirements.txt`
* an application database that already contains the bounties you want to show
  (`evidence/testnet-live3/app.db` from the verified run), copied next to the app
* **no signer process** — that is the safety property

```bash
# on the server
export PROOFARENA_CHAIN_MODE=near-testnet
export NEAR_CONTRACT_ID=proofarena-c23922.testnet
export NEAR_TESTNET_ACCOUNT=coldlemon3275.testnet
export PROOFARENA_DB=/srv/proofarena/app.db
# optional: a session secret, otherwise one is generated on first start
export PROOFARENA_SESSION_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")

uvicorn proofarena.api:create_app --factory --host 127.0.0.1 --port 8787
```

Put Caddy or nginx in front for TLS:

```
demo.yourdomain.com {
    reverse_proxy 127.0.0.1:8787
}
```

Note: view calls need outbound HTTPS to `rpc.testnet.near.org`. If the host blocks egress, the pages
still render but chain reads fail — the `/health` endpoint will show it.

**Cost/risk:** a small VPS, no key on the server, no funds at risk.

## Option 2 — interactive demo for strangers (~1–2 hours of work)

Add a "Run the demo" button that makes the server execute the full lifecycle itself with a
**dedicated throwaway account** (not your Meteor wallet), a tiny reward (e.g. 0.01 NEAR), a
one-at-a-time lock and a per-IP rate limit. The server *does* hold that demo key, so:

* fund the demo account with testnet NEAR only,
* cap the reward and the number of runs per hour,
* label the run as automated,
* keep the signer bound to the demo account and the one contract (already enforced by
  `PROOFARENA_AGENT_ACCOUNT` + the method allowlist).

This is the only way a stranger can "try the app" without being handed a key. It is a product
feature, not a config flag; say the word and it can be built.

## Option 3 — local live demo (best fidelity, zero exposure)

Run it on your machine during the judging call, or record a 3–5 minute video following
`docs/DEMO_SCRIPT.md`. Judges can still verify everything independently through the explorer links
in `docs/TESTNET_EVIDENCE.md` — the chain does not need to be hosted by you.

## What not to do

* **Do not deploy the owner signer to a public host with your Meteor key.** The API never signs on
  its own, but the signer itself would then be reachable by anyone who can reach the port; keep it on
  `127.0.0.1` or behind authentication, and only on a machine you control.
* Do not expose `8788` or `8789` through the reverse proxy. Only `8787` (the API/UI) needs to be
  public, and in option 1 there is nothing behind it to sign with.
* Do not commit `evidence/` or any credentials file. `.gitignore` already excludes them.

## Sanity checks after deploying

```bash
curl -s https://demo.yourdomain.com/health | python -m json.tool
curl -s https://demo.yourdomain.com/api/meta | python -m json.tool     # chain_is_simulated must be false
curl -s https://demo.yourdomain.com/bounties | grep -c "Paid"          # seeded bounties visible
```

If `chain_is_simulated` is `true`, the deployment is still on the local simulator — check
`PROOFARENA_CHAIN_MODE`.
