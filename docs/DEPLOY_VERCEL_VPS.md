# Deploying: Vercel (free link) + your VPS (API + verifier)

This is the deployment the project actually needs. Two pieces, because they have different
requirements:

| Piece | Where | Why |
|---|---|---|
| Wallet demo / UI | **Vercel** (static) | free HTTPS URL, global CDN, no secrets |
| API + verifier | **VPS** (your SSH box) | the verifier runs `pytest` in a subprocess with a real filesystem; serverless cannot |

Crucially, **neither piece needs an owner key**. The visitor's wallet signs and broadcasts every
transaction; the API only verifies what landed on chain (see `proofarena/walletflow.py`).

```
visitor browser ── wallet (Meteor/Nightly/MyNearWallet) ──► NEAR testnet
      │                                                        ▲
      │ fetch (CORS)                                           │ tx confirmed
      ▼                                                        │
Vercel static page ──────► VPS: FastAPI/Starlette ──── RPC ────┘
                                   │
                                   └─ isolated verifier (pytest subprocess)
```

---

## 1. API on the VPS

Assumes Ubuntu-ish with Python 3.11+, `git`, and a domain or at least an IP.

```bash
sudo apt update && sudo apt install -y python3.12-venv caddy git
sudo adduser --system --group proofarena
sudo -u proofarena -H bash -c '
  cd ~ && git clone <your-repo-url> app && cd app
  python3 -m venv .venv && . .venv/bin/activate
  pip install -r requirements.txt
'
```

Environment file `/etc/proofarena.env` (no secrets needed — there is no key):

```ini
PROOFARENA_CHAIN_MODE=near-testnet
NEAR_CONTRACT_ID=proofarena-c23922.testnet
NEAR_TESTNET_ACCOUNT=coldlemon3275.testnet
PROOFARENA_RPC_TIMEOUT_S=25
PROOFARENA_DB=/var/lib/proofarena/app.db
PROOFARENA_DATA_DIR=/var/lib/proofarena
```

`/etc/systemd/system/proofarena.service`:

```ini
[Unit]
Description=ProofArena API
After=network.target

[Service]
User=proofarena
WorkingDirectory=/home/proofarena/app
EnvironmentFile=/etc/proofarena.env
ExecStart=/home/proofarena/app/.venv/bin/uvicorn proofarena.api:create_app --factory --host 127.0.0.1 --port 8787
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/var/lib/proofarena

[Install]
WantedBy=multi-user.target
```

```bash
sudo mkdir -p /var/lib/proofarena && sudo chown proofarena: /var/lib/proofarena
sudo systemctl daemon-reload && sudo systemctl enable --now proofarena
curl -s localhost:8787/health | python3 -m json.tool
```

Caddy (`/etc/caddy/Caddyfile`) gives you HTTPS automatically:

```
api.your-domain.example {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8787
}
```

```bash
sudo systemctl reload caddy
curl -s https://api.your-domain.example/api/meta | python3 -m json.tool
```

Sanity checks before moving on:

* `chain_mode` must be `near-testnet`
* `chain_is_simulated` must be `false`
* `/wallet/` must return the demo page

The verifier writes artifacts under `PROOFARENA_DATA_DIR`; keep that directory backed up if you care
about the evidence trail.

## 2. Vercel (static wallet demo)

The `web-vercel/` folder is a build-free static site. Set the API URL first:

```js
// web-vercel/config.js
window.PROOFARENA_API = "https://api.your-domain.example";
```

Deploy one of two ways.

**Dashboard:** push the repo to GitHub → Vercel → *Add New Project* → import the repo →
**Root Directory** `web-vercel` → Framework presets **Other** → Build command empty →
Output directory empty → Deploy.

**CLI:**

```bash
cd web-vercel
npx vercel deploy --prod
```

You get `https://<project>.vercel.app`. Open it, connect a testnet wallet, create a bounty.

CORS is already permissive in the API (`allow_origins=["*"]`), so the cross-origin calls work. If you
want to lock it down, edit the middleware in `proofarena/api.py` and restrict to your Vercel domain.

## 3. What a visitor can do

| Step | Signed by | Server role |
|---|---|---|
| Connect wallet, sign in | wallet (NEP-413 message) | verifies signature against the account's on-chain access keys, issues a session |
| Create + fund a bounty | **wallet** (`create_bounty`, deposit) | returns the plan, then verifies the tx on chain |
| Close submissions | **wallet** (`mark_review`) | same |
| Approve / payout / refund / dispute | **wallet** | same |
| Claim, patch, verification (agents) | platform demo agent token | executes the sandbox job |

Nothing is trusted because a browser said so: `wallet-confirm` re-reads the transaction, checks the
signer, receiver, method, arguments and deposit against the plan it handed out, and rejects failures.

## 4. Common problems

| Symptom | Cause | Fix |
|---|---|---|
| browser console: CORS error | API not reachable, or a proxy strips headers | `curl -i https://api…/api/meta` and check `access-control-allow-origin` |
| `selector failed to start` | esm.sh blocked by a network policy | allow `esm.sh`, or vendor the bundles |
| wallet opens but nothing happens | wrong network in the wallet (must be **testnet**) | switch network in the extension |
| `signer refused` on confirm | the tx used a different account than the session | reconnect; only create bounties from the connected account |
| `transaction is not visible on chain yet` | confirm called too early | retry after ~2 s; the page does this automatically once the wallet returns |
| `/wallet/` 404 on the VPS | the `web-vercel/` folder was not deployed | `git pull` on the VPS and restart the service |
