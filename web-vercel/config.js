// ProofArena wallet demo — configuration.
//
// When this page is served BY the API (http://127.0.0.1:8787/wallet/), leave this
// empty and requests go to the same origin.
//
// When deployed to Vercel (static), set the public base URL of the API on your VPS:
//
//   window.PROOFARENA_API = "https://api.your-domain.example";
//
// The API enables CORS for all origins, and it holds no keys: every transaction is
// signed by the visitor's wallet.

window.PROOFARENA_API = window.PROOFARENA_API || "";
