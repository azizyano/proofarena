"""Runtime configuration.

Every value has a safe local default so that ``pytest`` and the demo server run
on a clean checkout without a ``.env`` file. Secrets are never printed and never
logged; ``PROOFARENA_SESSION_SECRET`` may be supplied by the environment or is
generated once into a git-ignored file.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = Path(os.environ.get("PROOFARENA_DATA_DIR") or (ROOT / "evidence" / "local")).resolve()
ARTIFACT_DIR = DATA_DIR / "artifacts"
DB_PATH = Path(os.environ.get("PROOFARENA_DB") or (DATA_DIR / "proofarena.db"))
SECRET_FILE = DATA_DIR / "session.secret"

#: Chain mode. ``local`` runs the SQLite simulator; ``near-testnet`` submits real
#: transactions with the owner's key. Both adapters are implemented; testnet just
#: needs a deployed contract plus credentials (docs/TESTNET_RUNBOOK.md).
CHAIN_MODE = os.environ.get("PROOFARENA_CHAIN_MODE", "local").strip().lower()

#: NEAR testnet settings (only read when CHAIN_MODE is near-testnet).
NEAR_RPC_URL = os.environ.get("NEAR_RPC_URL", "https://rpc.testnet.near.org")
NEAR_TESTNET_ACCOUNT = os.environ.get("NEAR_TESTNET_ACCOUNT", "")
NEAR_CONTRACT_ID = os.environ.get("NEAR_CONTRACT_ID", "")
NEAR_SIGNER_MODE = os.environ.get("PROOFARENA_NEAR_SIGNER", "native").strip().lower()
NEAR_SETTLE_TIMEOUT_S = float(os.environ.get("PROOFARENA_NEAR_SETTLE_TIMEOUT_S", "30"))
NEAR_EXPLORER_BASE = os.environ.get(
    "NEAR_EXPLORER_BASE", "https://explorer.testnet.near.org/transactions"
)

#: Values that are *labels* in local mode. They are deliberately not realistic
#: testnet account IDs: no fabricated mainnet/testnet identity is claimed.
NETWORK_ID = os.environ.get("PROOFARENA_NETWORK", "local-ledger")
CONTRACT_ID = os.environ.get("PROOFARENA_CONTRACT_ID", "escrow.proofarena.local")
AUTH_DOMAIN = os.environ.get("PROOFARENA_AUTH_DOMAIN", "proofarena.local")
AUTH_TTL_S = int(os.environ.get("PROOFARENA_AUTH_TTL_S", "300"))

API_HOST = os.environ.get("PROOFARENA_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("PROOFARENA_PORT", "8787"))
WALLET_PORT = int(os.environ.get("PROOFARENA_WALLET_PORT", "8788"))

#: Runner limits for the isolated verification job (see runner.py).
RUNNER_TIMEOUT_S = int(os.environ.get("PROOFARENA_RUNNER_TIMEOUT_S", "90"))
RUNNER_MAX_OUTPUT_BYTES = int(os.environ.get("PROOFARENA_RUNNER_MAX_OUTPUT_BYTES", "262144"))
RUNNER_MAX_ARTIFACT_BYTES = int(os.environ.get("PROOFARENA_RUNNER_MAX_ARTIFACT_BYTES", "1048576"))

#: Hard ceiling for a single bounty, in yoctoNEAR (100 NEAR).
MAX_REWARD_YOCTO = 100_000_000_000_000_000_000_000_000
#: Storage reserve retained per bounty record, in yoctoNEAR (0.001 NEAR).
STORAGE_FEE_YOCTO = 1_000_000_000_000_000_000_000
YOCTO_PER_NEAR = 10**24

DEMO_OWNER_ACCOUNT = os.environ.get("PROOFARENA_DEMO_OWNER", "owner.demo.local")


def session_secret() -> bytes:
    """Return the HMAC key for session tokens, creating a local one if needed."""
    env = os.environ.get("PROOFARENA_SESSION_SECRET")
    if env:
        return env.encode("utf-8")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SECRET_FILE.exists():
        raw = SECRET_FILE.read_text(encoding="utf-8").strip()
        if raw:
            return raw.encode("utf-8")
    raw = secrets.token_urlsafe(48)
    SECRET_FILE.write_text(raw + "\n", encoding="utf-8")
    try:  # best effort on POSIX; harmless on Windows
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass
    return raw.encode("utf-8")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def is_local_chain() -> bool:
    return CHAIN_MODE == "local"
