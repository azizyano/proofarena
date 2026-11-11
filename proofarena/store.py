"""Off-chain persistence: the application read model.

The database is a *mirror*, never the source of financial truth. Settlement
status shown to users is derived from chain receipts; this store keeps the
challenge metadata, private evidence manifests, verification job results and the
append-only audit stream.

Amounts are stored as decimal strings in yoctoNEAR — never floats.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_nonces (
    nonce TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    domain TEXT NOT NULL,
    purpose TEXT NOT NULL,
    message TEXT NOT NULL,
    expires_ns INTEGER NOT NULL,
    consumed_ns INTEGER,
    created_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS user_keys (
    account TEXT PRIMARY KEY,
    public_key_b64 TEXT NOT NULL,
    created_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_tokens (
    token_hash TEXT PRIMARY KEY,
    challenge_id TEXT NOT NULL,
    role TEXT NOT NULL,
    agent_account TEXT NOT NULL,
    expires_ns INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS challenges (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    title TEXT NOT NULL,
    repo_url TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    metadata_hash_hex TEXT NOT NULL,
    reward_yocto TEXT NOT NULL,
    deadline_ns INTEGER NOT NULL,
    review_deadline_ns INTEGER NOT NULL,
    network TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    onchain_bounty_id INTEGER,
    state TEXT NOT NULL,
    created_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    challenge_id TEXT NOT NULL,
    onchain_claim_id INTEGER,
    submitter TEXT NOT NULL,
    commitment_hex TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    created_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS patches (
    id TEXT PRIMARY KEY,
    challenge_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    defender TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    patched_commit TEXT NOT NULL,
    diff_sha256 TEXT NOT NULL,
    diff_text TEXT NOT NULL,
    status TEXT NOT NULL,
    created_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_jobs (
    id TEXT PRIMARY KEY,
    challenge_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    result_json TEXT,
    details_json TEXT,
    artifacts_dir TEXT,
    error TEXT,
    created_ns INTEGER NOT NULL,
    finished_ns INTEGER
);

CREATE TABLE IF NOT EXISTS chain_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    network TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    bounty_id INTEGER,
    payload_json TEXT NOT NULL,
    indexed_ns INTEGER NOT NULL,
    UNIQUE (network, receipt_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    correlation_id TEXT,
    created_ns INTEGER NOT NULL
);
"""


def now_ns() -> int:
    return time.time_ns()


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    # ------------------------------------------------------------------
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    # ------------------------------------------------------------------
    # auth
    # ------------------------------------------------------------------
    def put_nonce(
        self, *, nonce: str, account: str, domain: str, purpose: str, message: str, expires_ns: int
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO auth_nonces (nonce, account, domain, purpose, message, expires_ns,"
                " created_ns) VALUES (?,?,?,?,?,?,?)",
                (nonce, account, domain, purpose, message, expires_ns, now_ns()),
            )

    def consume_nonce(self, nonce: str, *, purpose: str, domain: str, now: int) -> sqlite3.Row | None:
        """Single-use consumption. Returns the row only on the first success."""
        with self.tx() as conn:
            row = conn.execute(
                "SELECT * FROM auth_nonces WHERE nonce = ? AND purpose = ? AND domain = ?",
                (nonce, purpose, domain),
            ).fetchone()
            if row is None or row["consumed_ns"] is not None or row["expires_ns"] < now:
                return None
            updated = conn.execute(
                "UPDATE auth_nonces SET consumed_ns = ? WHERE nonce = ? AND consumed_ns IS NULL",
                (now, nonce),
            ).rowcount
            return row if updated == 1 else None

    def register_key(self, account: str, public_key_b64: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO user_keys (account, public_key_b64, created_ns) VALUES (?,?,?)"
                " ON CONFLICT(account) DO UPDATE SET public_key_b64 = excluded.public_key_b64",
                (account, public_key_b64, now_ns()),
            )

    def get_key(self, account: str) -> str | None:
        row = self.one("SELECT public_key_b64 FROM user_keys WHERE account = ?", (account,))
        return row["public_key_b64"] if row else None

    def add_agent_token(
        self, token_hash: str, challenge_id: str, role: str, expires_ns: int, agent_account: str
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO agent_tokens (token_hash, challenge_id, role, agent_account,"
                " expires_ns, created_ns) VALUES (?,?,?,?,?,?)",
                (token_hash, challenge_id, role, agent_account, expires_ns, now_ns()),
            )

    def get_agent_token(self, token_hash: str) -> sqlite3.Row | None:
        return self.one(
            "SELECT * FROM agent_tokens WHERE token_hash = ? AND revoked = 0", (token_hash,)
        )

    def revoke_agent_tokens(self, challenge_id: str) -> int:
        with self.tx() as conn:
            return conn.execute(
                "UPDATE agent_tokens SET revoked = 1 WHERE challenge_id = ?", (challenge_id,)
            ).rowcount

    # ------------------------------------------------------------------
    # challenges / claims / patches
    # ------------------------------------------------------------------
    def insert_challenge(self, row: dict[str, Any]) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO challenges (id, owner, title, repo_url, source_commit, scope_json,"
                " metadata_hash_hex, reward_yocto, deadline_ns, review_deadline_ns, network,"
                " contract_id, onchain_bounty_id, state, created_ns)"
                " VALUES (:id,:owner,:title,:repo_url,:source_commit,:scope_json,"
                ":metadata_hash_hex,:reward_yocto,:deadline_ns,:review_deadline_ns,:network,"
                ":contract_id,:onchain_bounty_id,:state,:created_ns)",
                row,
            )

    def update_challenge(self, challenge_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self.tx() as conn:
            conn.execute(
                f"UPDATE challenges SET {assignments} WHERE id = ?",
                (*fields.values(), challenge_id),
            )

    def get_challenge(self, challenge_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM challenges WHERE id = ?", (challenge_id,))

    def list_challenges(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.all("SELECT * FROM challenges ORDER BY created_ns DESC LIMIT ?", (limit,))

    def insert_claim(self, row: dict[str, Any]) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO claims (id, challenge_id, onchain_claim_id, submitter, commitment_hex,"
                " manifest_json, manifest_sha256, status, created_ns)"
                " VALUES (:id,:challenge_id,:onchain_claim_id,:submitter,:commitment_hex,"
                ":manifest_json,:manifest_sha256,:status,:created_ns)",
                row,
            )

    def get_claim(self, claim_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM claims WHERE id = ?", (claim_id,))

    def claim_by_commitment(self, challenge_id: str, commitment: str) -> sqlite3.Row | None:
        return self.one(
            "SELECT * FROM claims WHERE challenge_id = ? AND commitment_hex = ?",
            (challenge_id, commitment),
        )

    def list_claims(self, challenge_id: str) -> list[sqlite3.Row]:
        return self.all(
            "SELECT * FROM claims WHERE challenge_id = ? ORDER BY created_ns", (challenge_id,)
        )

    def update_claim_status(self, claim_id: str, status: str) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE claims SET status = ? WHERE id = ?", (status, claim_id))

    def insert_patch(self, row: dict[str, Any]) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO patches (id, challenge_id, claim_id, defender, base_commit,"
                " patched_commit, diff_sha256, diff_text, status, created_ns)"
                " VALUES (:id,:challenge_id,:claim_id,:defender,:base_commit,:patched_commit,"
                ":diff_sha256,:diff_text,:status,:created_ns)",
                row,
            )

    def get_patch_for_claim(self, claim_id: str) -> sqlite3.Row | None:
        return self.one(
            "SELECT * FROM patches WHERE claim_id = ? ORDER BY created_ns DESC LIMIT 1", (claim_id,)
        )

    def list_patches(self, challenge_id: str) -> list[sqlite3.Row]:
        return self.all(
            "SELECT * FROM patches WHERE challenge_id = ? ORDER BY created_ns", (challenge_id,)
        )

    # ------------------------------------------------------------------
    # verification jobs
    # ------------------------------------------------------------------
    def insert_job(self, row: dict[str, Any]) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO verification_jobs (id, challenge_id, claim_id, status,"
                " idempotency_key, created_ns) VALUES (:id,:challenge_id,:claim_id,:status,"
                ":idempotency_key,:created_ns)",
                row,
            )

    def job_by_idempotency(self, key: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM verification_jobs WHERE idempotency_key = ?", (key,))

    def finish_job(
        self,
        job_id: str,
        *,
        status: str,
        result: dict[str, Any] | None,
        details: dict[str, Any] | None,
        artifacts_dir: str | None,
        error: str | None = None,
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE verification_jobs SET status = ?, result_json = ?, details_json = ?,"
                " artifacts_dir = ?, error = ?, finished_ns = ? WHERE id = ?",
                (
                    status,
                    json.dumps(result) if result else None,
                    json.dumps(details) if details else None,
                    artifacts_dir,
                    error,
                    now_ns(),
                    job_id,
                ),
            )

    def get_job(self, job_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM verification_jobs WHERE id = ?", (job_id,))

    def list_jobs(self, challenge_id: str) -> list[sqlite3.Row]:
        return self.all(
            "SELECT * FROM verification_jobs WHERE challenge_id = ? ORDER BY created_ns",
            (challenge_id,),
        )

    # ------------------------------------------------------------------
    # chain mirror + audit
    # ------------------------------------------------------------------
    def index_receipt(self, receipt: dict[str, Any], network: str) -> bool:
        """Idempotent chain-event ingestion. False means it was already known."""
        with self.tx() as conn:
            inserted = conn.execute(
                "INSERT OR IGNORE INTO chain_events (network, receipt_id, kind, bounty_id,"
                " payload_json, indexed_ns) VALUES (?,?,?,?,?,?)",
                (
                    network,
                    receipt["receipt_id"],
                    receipt.get("kind", "unknown"),
                    receipt.get("bounty_id"),
                    json.dumps(receipt, sort_keys=True),
                    now_ns(),
                ),
            ).rowcount
            return inserted == 1

    def chain_events(self, bounty_id: int | None = None, limit: int = 200) -> list[sqlite3.Row]:
        if bounty_id is None:
            return self.all("SELECT * FROM chain_events ORDER BY id DESC LIMIT ?", (limit,))
        return self.all(
            "SELECT * FROM chain_events WHERE bounty_id = ? ORDER BY id DESC LIMIT ?",
            (bounty_id, limit),
        )

    def audit(
        self,
        *,
        actor: str,
        action: str,
        entity: str,
        entity_id: str,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO audit_events (actor, action, entity, entity_id, payload_json,"
                " correlation_id, created_ns) VALUES (?,?,?,?,?,?,?)",
                (
                    actor,
                    action,
                    entity,
                    entity_id,
                    json.dumps(payload or {}, sort_keys=True),
                    correlation_id,
                    now_ns(),
                ),
            )

    def audit_events(self, entity_id: str | None = None, limit: int = 300) -> list[sqlite3.Row]:
        if entity_id is None:
            return self.all("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,))
        return self.all(
            "SELECT * FROM audit_events WHERE entity_id = ? ORDER BY id ASC LIMIT ?",
            (entity_id, limit),
        )
