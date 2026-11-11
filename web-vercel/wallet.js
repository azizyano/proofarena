// ProofArena wallet demo — browser wallet integration (no build step).
//
// Flow for every privileged action:
//   1. POST /v1/challenges/{id}/wallet-intent   -> exact transaction plan
//   2. wallet.signAndSendTransaction(plan)      -> the wallet signs AND broadcasts
//   3. POST /v1/challenges/{id}/wallet-confirm  -> server re-reads the chain
//
// The server never signs. If the wallet's transaction does not match the plan,
// confirm rejects it (see proofarena/walletflow.py).

import { setupWalletSelector } from "@near-wallet-selector/core";
import { setupMeteorWallet } from "@near-wallet-selector/meteor-wallet";
import { setupMyNearWallet } from "@near-wallet-selector/my-near-wallet";
import { setupNightly } from "@near-wallet-selector/nightly";

const API = (window.PROOFARENA_API || "").replace(/\/$/, "");
const NETWORK = "testnet";

const state = {
  selector: null,
  wallet: null,
  accountId: null,
  token: sessionStorage.getItem("pa_wallet_token") || "",
  contractId: "…",
};

const $ = (id) => document.getElementById(id);

function log(message) {
  const node = $("log");
  if (!node) return;
  node.textContent = `[${new Date().toLocaleTimeString()}] ${message}\n` + node.textContent;
}

function setBadge(id, text, kind) {
  const node = $(id);
  if (!node) return;
  node.textContent = text;
  node.className = `badge ${kind || ""}`;
}

async function api(path, { method = "GET", body, headers = {} } = {}) {
  const init = { method, headers: { ...headers } };
  if (body !== undefined) {
    init.headers["content-type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  if (state.token) init.headers.authorization = `Bearer ${state.token}`;
  const response = await fetch(`${API}${path}`, init);
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text.slice(0, 400) };
  }
  if (!response.ok) {
    const error = new Error(data.message || data.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.code = data.error;
    throw error;
  }
  return data;
}

// ---------------------------------------------------------------- base58
const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
function b58decode(text) {
  let number = 0n;
  for (const char of text) {
    const index = B58.indexOf(char);
    if (index < 0) throw new Error(`invalid base58 character ${char}`);
    number = number * 58n + BigInt(index);
  }
  let hex = number === 0n ? "" : number.toString(16);
  if (hex.length % 2) hex = `0${hex}`;
  const bytes = [];
  for (let i = 0; i < hex.length; i += 2) bytes.push(parseInt(hex.slice(i, i + 2), 16));
  let padding = 0;
  for (const char of text) {
    if (char === "1") padding += 1;
    else break;
  }
  return Uint8Array.from([...new Array(padding).fill(0), ...bytes]);
}

// ---------------------------------------------------------------- selector
async function initSelector() {
  try {
    state.selector = await setupWalletSelector({
      network: NETWORK,
      modules: [setupMeteorWallet(), setupMyNearWallet(), setupNightly()],
    });
    const accounts = state.selector.store.getState().accounts;
    log(`selector ready on ${NETWORK}; ${accounts.length} stored account(s)`);
    if (accounts.length) {
      state.accountId = accounts[0].accountId;
      setBadge("accountBadge", state.accountId, "b-OK");
      await ensureSession();
      await loadBounties();
    }
  } catch (error) {
    log(`selector failed to start: ${error.message || error}\n` +
        `If this is a CDN/import error, check that esm.sh is reachable from your browser.`);
  }
}

async function connectWith(walletId) {
  try {
    if (!state.selector) throw new Error("wallet selector not ready");
    const wallet = await state.selector.wallet(walletId);
    state.wallet = wallet;
    let accounts = [];
    if (typeof wallet.connect === "function") {
      accounts = await wallet.connect();
    } else {
      accounts = await wallet.signIn({ contractId: state.contractId });
    }
    if (!accounts || !accounts.length) {
      const stored = state.selector.store.getState().accounts;
      accounts = stored;
    }
    if (!accounts.length) throw new Error("wallet returned no accounts");
    state.accountId = accounts[0].accountId;
    sessionStorage.setItem("pa_wallet_account", state.accountId);
    setBadge("accountBadge", state.accountId, "b-OK");
    log(`connected ${state.accountId} via ${walletId}`);
    await ensureSession();
    await loadBounties();
  } catch (error) {
    log(`connect failed: ${error.message || error}`);
  }
}

// ---------------------------------------------------------------- NEP-413 auth
async function ensureSession() {
  if (state.token) return state.token;
  if (!state.wallet || !state.accountId) throw new Error("connect a wallet first");

  const nonce = await api("/auth/wallet/nonce", {
    method: "POST",
    body: { account_id: state.accountId },
  });
  const nonceBytes = b58decode(nonce.nonce);
  log("wallet: please approve the sign-in message");

  const signed = await state.wallet.signMessage({
    message: nonce.message,
    recipient: nonce.recipient,
    nonce: nonceBytes,
  });

  const verified = await api("/auth/wallet/verify", {
    method: "POST",
    body: {
      account_id: state.accountId,
      public_key: signed.publicKey,
      signature: signed.signature,
      nonce: nonce.nonce,
      message: nonce.message,
      recipient: nonce.recipient,
    },
  });
  state.token = verified.token;
  sessionStorage.setItem("pa_wallet_token", state.token);
  log(`signed in as ${verified.account}`);
  return state.token;
}

// ---------------------------------------------------------------- tx helpers
function functionCallAction(plan) {
  return {
    type: "FunctionCall",
    params: {
      methodName: plan.method,
      args: plan.args,
      gas: String(plan.gas),
      deposit: String(plan.deposit_yocto),
    },
  };
}

async function sendPlan(plan) {
  const result = await state.wallet.signAndSendTransaction({
    receiverId: plan.contract_id,
    actions: [functionCallAction(plan)],
  });
  const outcome = Array.isArray(result) ? result[0] : result;
  const hash =
    outcome?.transaction?.hash ||
    outcome?.transaction_outcome?.id ||
    outcome?.transactionHashes;
  if (!hash) throw new Error("wallet did not return a transaction hash");
  return String(hash).split(",")[0];
}

async function runAction(challengeId, action, extras = {}) {
  await ensureSession();
  const intent = await api(`/v1/challenges/${challengeId}/wallet-intent`, {
    method: "POST",
    body: { action, ...extras },
  });
  log(`${action}: approve the transaction in your wallet`);
  const txHash = await sendPlan(intent.plan);
  log(`${action}: sent ${txHash}, verifying on chain…`);
  const confirmed = await api(`/v1/challenges/${challengeId}/wallet-confirm`, {
    method: "POST",
    body: { action, tx_hash: txHash, ...extras },
  });
  log(`${action}: verified on chain — state ${confirmed.challenge?.state}`);
  return confirmed;
}

// ---------------------------------------------------------------- UI
function explorerTx(hash) {
  return `https://explorer.testnet.near.org/transactions/${hash}`;
}

function yoctoToNear(value) {
  const near = Number(value) / 1e24;
  return near.toString();
}

// Exact decimal -> yoctoNEAR (no floating point). "0.1" -> "100000000000000000000000"
function nearToYocto(text) {
  const cleaned = String(text).trim();
  if (!/^\d+(\.\d{1,24})?$/.test(cleaned)) throw new Error("invalid NEAR amount");
  const [whole, fraction = ""] = cleaned.split(".");
  return (BigInt(whole) * 10n ** 24n + BigInt((fraction + "0".repeat(24)).slice(0, 24))).toString();
}

async function createBounty() {
  const button = $("btnCreate");
  button.disabled = true;
  try {
    await ensureSession();
    const created = await api("/v1/challenges", {
      method: "POST",
      body: {
        title: $("title").value,
        repo_url: "fixture/baseline (authorized synthetic fixture)",
        source_commit: "1".repeat(40),
        scope: {
          authorization: "synthetic fixture owned by the bounty owner",
          allowed_files: ["ledger.py"],
          forbidden: ["network", "real targets"],
        },
        reward_yocto: nearToYocto($("reward").value),
        submission_seconds: Number($("submission").value),
        review_seconds: Number($("review").value),
      },
    });
    const challengeId = created.challenge.id;
    log(`challenge ${challengeId} drafted; now sign the funding transaction`);
    await runAction(challengeId, "fund_bounty");
    await loadBounties();
  } catch (error) {
    log(`create failed: ${error.message}`);
  } finally {
    button.disabled = false;
  }
}

async function loadBounties() {
  const container = $("bounties");
  try {
    const data = await api("/v1/challenges");
    if (!data.items.length) {
      container.textContent = "no bounties yet — create one above";
      return;
    }
    const rows = data.items
      .map((item) => {
        const mine = item.owner === state.accountId;
        const claimButton = mine && item.onchain_bounty_id
          ? `<button class="ghost" data-act="review" data-id="${item.id}">Close submissions</button>`
          : "";
        return `<tr>
          <td><strong>${escapeHtml(item.title)}</strong><br>
              <span class="mono small muted">${item.id}</span></td>
          <td><span class="badge b-WARN">${item.state}</span></td>
          <td class="mono">${yoctoToNear(item.reward_yocto)} NEAR</td>
          <td class="small">${escapeHtml(item.owner)}${mine ? " (you)" : ""}</td>
          <td class="small">${claimButton}
              <a class="small" href="${API}/bounties/${item.id}" target="_blank" rel="noreferrer">details →</a></td>
        </tr>`;
      })
      .join("");
    container.innerHTML = `<table>
      <thead><tr><th>Bounty</th><th>State</th><th>Reward</th><th>Owner</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="small muted">Actions for bounties you own: close submissions, approve a claim,
      payout, refund. Each one asks your wallet to sign.</p>`;
    container.querySelectorAll("button[data-act]").forEach((node) => {
      node.addEventListener("click", async () => {
        node.disabled = true;
        try {
          await runAction(node.dataset.id, node.dataset.act);
          await loadBounties();
        } catch (error) {
          log(`${node.dataset.act} failed: ${error.message}`);
        } finally {
          node.disabled = false;
        }
      });
    });
  } catch (error) {
    container.textContent = `could not load bounties: ${error.message}`;
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

// ---------------------------------------------------------------- boot
window.addEventListener("DOMContentLoaded", async () => {
  $("btnMeteor")?.addEventListener("click", () => connectWith("meteor-wallet"));
  $("btnNightly")?.addEventListener("click", () => connectWith("nightly"));
  $("btnMyNear")?.addEventListener("click", () => connectWith("my-near-wallet"));
  $("btnCreate")?.addEventListener("click", createBounty);
  $("btnRefresh")?.addEventListener("click", loadBounties);

  try {
    const meta = await api("/api/meta");
    state.contractId = meta.contract_id;
    $("contractLabel").textContent = meta.contract_id;
    setBadge("netBadge", meta.chain_mode === "near-testnet" ? "NEAR testnet" : meta.chain_mode,
             meta.chain_mode === "near-testnet" ? "b-OK" : "b-WARN");
  } catch (error) {
    log(`cannot reach the API at ${API || location.origin}: ${error.message}`);
  }
  await initSelector();
});
