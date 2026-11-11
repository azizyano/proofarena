/* ProofArena web client.
 *
 * No framework, no build step (see docs/adr/0002-frontend.md). The browser never
 * holds a private key: it asks the local wallet signer (127.0.0.1:8788) to sign a
 * canonical message, then posts the signature to the API.
 */

const META = window.PROOFARENA || {};
const SIGNER = META.wallet_url || "http://127.0.0.1:8788";

const state = {
  token: sessionStorage.getItem("pa_token") || "",
  account: sessionStorage.getItem("pa_account") || "",
};

/* ------------------------------------------------------------------ utils */
function $(sel, root = document) {
  return root.querySelector(sel);
}
function $all(sel, root = document) {
  return Array.from(root.querySelectorAll(sel));
}

function toast(message, kind = "info", ms = 6000) {
  const node = $("#toast");
  if (!node) return;
  node.textContent = message;
  node.dataset.kind = kind;
  node.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    node.hidden = true;
  }, ms);
}

function showWallet() {
  const node = $("#wallet-account");
  if (!node) return;
  node.textContent = state.account ? `wallet: ${state.account}` : "wallet: not connected";
}

async function api(path, { method = "GET", body, headers = {} } = {}) {
  const init = { method, headers: { ...headers } };
  if (body !== undefined) {
    init.headers["content-type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  if (state.token) init.headers.authorization = `Bearer ${state.token}`;
  const response = await fetch(path, init);
  const text = await response.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (response.status === 401 || (response.status === 403 && data.error === "E_AUTH")) {
    state.token = "";
    sessionStorage.removeItem("pa_token");
    showWallet();
  }
  if (!response.ok) {
    const error = new Error(data.message || data.error || `HTTP ${response.status}`);
    error.code = data.error;
    error.status = response.status;
    throw error;
  }
  return data;
}

/* -------------------------------------------------------------- wallet ops */
async function signerStatus() {
  const response = await fetch(`${SIGNER}/status`);
  if (!response.ok) throw new Error("wallet signer is not running on " + SIGNER);
  return response.json();
}

async function signMessage(message) {
  const response = await fetch(`${SIGNER}/sign`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || "wallet refused to sign");
  return data;
}

async function ensureRegistered(account, publicKeyB64) {
  try {
    await api("/auth/challenge", { method: "POST", body: { account } });
    return; // already registered
  } catch (error) {
    if (error.code !== "E_AUTH") throw error;
  }
  const proofResponse = await fetch(`${SIGNER}/register-proof`);
  const proof = await proofResponse.json();
  await api("/auth/register", {
    method: "POST",
    body: {
      account: proof.account,
      public_key_b64: proof.public_key_b64,
      nonce: proof.nonce,
      signature: proof.signature,
    },
  });
}

async function connect() {
  const status = await signerStatus();
  await ensureRegistered(status.account, status.public_key_b64);
  const challenge = await api("/auth/challenge", {
    method: "POST",
    body: { account: status.account },
  });
  const signed = await signMessage(challenge.message);
  const verified = await api("/auth/verify", {
    method: "POST",
    body: { message: challenge.message, signature: signed.signature },
  });
  state.token = verified.token;
  state.account = verified.account;
  sessionStorage.setItem("pa_token", state.token);
  sessionStorage.setItem("pa_account", state.account);
  showWallet();
  toast(`Connected ${state.account}`, "info", 3500);
  return state;
}

async function requireWallet() {
  if (!state.token) await connect();
  return state;
}

async function signedIntent(action, payload) {
  await requireWallet();
  const issued = await api("/v1/intents", { method: "POST", body: { action, payload } });
  const signed = await signMessage(issued.message);
  return { message: issued.message, signature: signed.signature };
}

function nearToYocto(value) {
  const near = Number(value);
  if (!Number.isFinite(near) || near <= 0) throw new Error("invalid NEAR amount");
  const micro = Math.round(near * 1e6);
  return (BigInt(micro) * 10n ** 18n).toString();
}

/* ------------------------------------------------------------ page: create */
async function initCreatePage() {
  const form = $("#create-form");
  if (!form) return;
  const statusNode = $("#form-status");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    statusNode.textContent = "connecting wallet…";
    try {
      await requireWallet();
      const data = new FormData(form);
      let scope;
      try {
        scope = JSON.parse(data.get("scope"));
      } catch {
        throw new Error("scope must be valid JSON");
      }
      statusNode.textContent = "creating challenge…";
      const created = await api("/v1/challenges", {
        method: "POST",
        body: {
          title: data.get("title"),
          repo_url: data.get("repo_url"),
          source_commit: data.get("source_commit"),
          scope,
          reward_yocto: nearToYocto(data.get("reward_near")),
          submission_seconds: Number(data.get("submission_seconds")),
          review_seconds: Number(data.get("review_seconds")),
        },
      });
      const challengeId = created.challenge.id;
      statusNode.textContent = "sign the funding intent in your wallet…";
      const intent = await signedIntent("fund_bounty", {
        challenge_id: challengeId,
        metadata_hash_hex: created.metadata_hash_hex,
      });
      statusNode.textContent = "funding escrow…";
      await api(`/v1/challenges/${challengeId}/fund`, { method: "POST", body: intent });
      window.location.href = `/bounties/${challengeId}`;
    } catch (error) {
      toast(error.message, "error");
      statusNode.textContent = `failed: ${error.message}`;
      button.disabled = false;
    }
  });
}

/* ------------------------------------------------------------ page: bounty */
async function initBountyPage() {
  const consoleNode = $("#owner-console");
  if (!consoleNode) return;
  const challengeId = consoleNode.dataset.challengeId;
  const tokenOut = $("#token-out");

  async function refreshState() {
    const data = await api(`/v1/challenges/${challengeId}`);
    return data.challenge;
  }

  $all("[data-action]", consoleNode).forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.action;
      button.disabled = true;
      try {
        await requireWallet();
        if (action === "refresh") {
          window.location.reload();
          return;
        }
        if (action === "issue-auditor" || action === "issue-defender") {
          const role = action === "issue-auditor" ? "auditor" : "defender";
          const agent = prompt(
            `Agent account for the ${role} token`,
            `${role}.agent.local`
          );
          if (!agent) return;
          const issued = await api(`/v1/challenges/${challengeId}/agent-token`, {
            method: "POST",
            body: { role, agent_account: agent },
          });
          tokenOut.hidden = false;
          tokenOut.textContent = `${role} token (copy now, shown once):\n${issued.token}`;
          toast(`${role} token issued`, "info");
          return;
        }
        if (action === "payout" || action === "refund") {
          const intentAction = action === "payout" ? "request_payout" : "refund_expired";
          const intent = await signedIntent(intentAction, { challenge_id: challengeId });
          const path = action === "payout" ? "payout" : "refund";
          const result = await api(`/v1/challenges/${challengeId}/${path}`, {
            method: "POST",
            body: intent,
          });
          $("#settlement-note").textContent = result.note || `state: ${result.state}`;
          toast(`${result.state} — transfer not yet confirmed`, "warn");
          pollSettlement();
          return;
        }
        if (action === "approve") {
          const claimId = button.dataset.claimId;
          const intent = await signedIntent("approve_claim", {
            challenge_id: challengeId,
            claim_id: claimId,
          });
          await api(`/v1/challenges/${challengeId}/approve`, { method: "POST", body: intent });
          toast("Claim approved by owner signature", "info");
          window.location.reload();
          return;
        }
        if (action === "dispute") {
          const current = await refreshState();
          const intent = await signedIntent("set_dispute", {
            challenge_id: challengeId,
            disputed: !current.disputed,
          });
          await api(`/v1/challenges/${challengeId}/dispute`, { method: "POST", body: intent });
          window.location.reload();
        }
      } catch (error) {
        toast(error.message, "error");
      } finally {
        button.disabled = false;
      }
    });
  });

  async function pollSettlement() {
    for (let i = 0; i < 12; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 700));
      const challenge = await refreshState();
      const badge = document.querySelector(".badge-lg");
      if (badge) {
        badge.textContent = challenge.state;
        badge.dataset.state = challenge.state;
      }
      $("#settlement-note").textContent = `state: ${challenge.state}`;
      if (challenge.state === "Paid" || challenge.state === "Refunded") {
        toast(`Settled: ${challenge.state}`, "info");
        return;
      }
    }
    $("#settlement-note").textContent =
      "Still pending. PayoutPending is not a payment; check receipts before acting.";
  }
}

/* ---------------------------------------------------------- page: evidence */
async function initEvidencePage() {
  const panel = $("#private-evidence");
  if (!panel) return;
  const button = $("#load-evidence");
  const out = $("#evidence-out");
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await requireWallet();
      const data = await api(`/v1/challenges/${panel.dataset.challengeId}/private-evidence`);
      const claim = (data.claims || []).find((item) => item.id === panel.dataset.claimId);
      out.innerHTML = "";
      if (!claim) {
        out.textContent = "No private evidence for this claim.";
        return;
      }
      const pre = document.createElement("pre");
      pre.className = "code";
      pre.textContent = JSON.stringify({ manifest: claim.manifest, diff: claim.diff }, null, 2);
      out.appendChild(pre);
      toast("Private evidence loaded (access audited)", "info", 4000);
    } catch (error) {
      toast(error.code === "E_UNAUTHORIZED" ? "Not the challenge owner — access denied" : error.message, "error");
    } finally {
      button.disabled = false;
    }
  });
}

/* ------------------------------------------------------------------- boot */
document.addEventListener("DOMContentLoaded", () => {
  showWallet();
  const connectButton = $("#connect-btn");
  if (connectButton) {
    connectButton.addEventListener("click", async () => {
      try {
        await connect();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  }
  const page = document.body.dataset.page;
  if (page === "new") initCreatePage();
  if (page === "bounty") initBountyPage();
  if (page === "evidence") initEvidencePage();
});
