"""NEAR testnet preflight: prove every prerequisite before spending time or funds.

    python scripts/testnet/preflight.py

Exit code 0 = every required check passed. Each check prints PASS / WARN / FAIL
and what to do about it. Nothing here signs or sends a transaction.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

try:  # make Unicode output safe on legacy Windows consoles
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - non-reconfigurable stream
    pass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_NEAR = 6 * 10**24  # 5 NEAR reward + storage + fees for one demo run


class Check:
    def __init__(self, name: str, status: str, detail: str, hint: str = "") -> None:
        self.name = name
        self.status = status
        self.detail = detail
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        return {"check": self.name, "status": self.status, "detail": self.detail, "hint": self.hint}

    def render(self) -> str:
        marker = {"PASS": "OK", "WARN": "!!", "FAIL": "XX"}.get(self.status, "?")
        line = f"  [{marker:<2}] {self.name:<34} {self.status:<4} {self.detail}"
        if self.hint and self.status != "PASS":
            line += f"\n        → {self.hint}"
        return line


def check_python() -> Check:
    missing = []
    for module in ("requests", "cryptography"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        return Check("python dependencies", "FAIL", f"missing: {', '.join(missing)}", "pip install -r requirements.txt")
    return Check("python dependencies", "PASS", "requests, cryptography importable")


def check_env() -> list[Check]:
    checks = []
    account = os.environ.get("NEAR_TESTNET_ACCOUNT", "")
    contract = os.environ.get("NEAR_CONTRACT_ID", "")
    rpc = os.environ.get("NEAR_RPC_URL", "https://rpc.testnet.near.org")
    checks.append(
        Check(
            "NEAR_TESTNET_ACCOUNT",
            "PASS" if account else "FAIL",
            account or "not set",
            "export NEAR_TESTNET_ACCOUNT=yourname.testnet",
        )
    )
    checks.append(
        Check(
            "NEAR_CONTRACT_ID",
            "PASS" if contract else "FAIL",
            contract or "not set (expected after deploy)",
            "run scripts/testnet/deploy.py, then export NEAR_CONTRACT_ID",
        )
    )
    checks.append(Check("NEAR_RPC_URL", "PASS", rpc))
    for value, label in ((account, "NEAR_TESTNET_ACCOUNT"), (contract, "NEAR_CONTRACT_ID")):
        if value and not value.endswith(".testnet"):
            checks.append(
                Check(f"{label} is testnet", "FAIL", f"{value} does not end with .testnet", "never point this at mainnet")
            )
    return checks


def check_credentials(account: str) -> Check:
    from proofarena.near.wallet import WalletError, credentials_path, load_key

    path = credentials_path(account)
    if path is None:
        return Check(
            f"credentials for {account}",
            "FAIL",
            "not found",
            f"save the key as ~/.near-credentials/testnet/{account}.json (runbook step 3)",
        )
    try:
        key = load_key(account, path=path)
    except WalletError as exc:
        return Check("credentials parse", "FAIL", str(exc)[:120], "re-download the key from the faucet page")
    except Exception as exc:  # corrupted key, wrong format
        return Check("credentials parse", "FAIL", f"{type(exc).__name__}: {exc}"[:120], "re-download the key")
    return Check("credentials parse", "PASS", f"ed25519 public key {key.public_key_ed25519}")


def check_rpc(rpc_url: str) -> tuple[Check, Any]:
    from proofarena.near.rpc import NearRpc, RpcError

    rpc = NearRpc(rpc_url)
    try:
        status = rpc.status()
    except RpcError as exc:
        return (
            Check("RPC reachable", "FAIL", str(exc)[:140], "check network access / NEAR_RPC_URL"),
            None,
        )
    chain_id = status.get("chain_id", "?")
    version = (status.get("version") or {}).get("version", "?")
    status_word = "PASS" if chain_id == "testnet" else "FAIL"
    return (
        Check("RPC reachable", status_word, f"chain_id={chain_id} node={version}", "must be testnet"),
        rpc,
    )


def check_account(rpc: Any, account: str) -> Check:
    from proofarena.near.rpc import RpcError

    try:
        info = rpc.account(account)
    except RpcError as exc:
        return Check(
            f"account {account}",
            "FAIL",
            str(exc)[:120],
            "create it at https://helper.testnet.near.org (runbook step 2)",
        )
    amount = int(info["amount"])
    near = amount / 10**24
    status = "PASS" if amount >= REQUIRED_NEAR else "WARN"
    hint = "" if amount >= REQUIRED_NEAR else "top up at the faucet; 5 NEAR demo reward needs ~6 NEAR"
    return Check("owner balance", status, f"{near:.3f} NEAR", hint)


def check_contract(rpc: Any, contract: str, reachable: bool) -> list[Check]:
    from proofarena.near.rpc import RpcError

    if not contract:
        return [Check("contract deployed", "FAIL", "NEAR_CONTRACT_ID not set", "deploy first")]
    checks: list[Check] = []
    try:
        info = rpc.account(contract)
    except RpcError as exc:
        checks.append(
            Check(
                "contract account exists",
                "FAIL",
                str(exc)[:120],
                "deploy the contract to this account (scripts/testnet/deploy.py)",
            )
        )
        return checks
    code_hash = info.get("code_hash", "")
    deployed = bool(code_hash) and code_hash != "11111111111111111111111111111111"
    checks.append(
        Check(
            "contract has code",
            "PASS" if deployed else "FAIL",
            f"code_hash={code_hash[:16]}…" if deployed else "account exists but has no contract code",
            "deploy the wasm",
        )
    )
    if deployed and reachable:
        try:
            ids = rpc.view_call(contract, "list_bounty_ids", {})
            checks.append(Check("contract view works", "PASS", f"list_bounty_ids → {ids}"))
        except RpcError as exc:
            checks.append(
                Check(
                    "contract view works",
                    "FAIL",
                    str(exc)[:140],
                    "the deployed contract may not match this source, or init failed",
                )
            )
    return checks


def check_wasm(path: Path) -> Check:
    if not path.exists():
        return Check(
            "wasm artifact",
            "WARN",
            f"not built ({path.relative_to(ROOT) if path.is_relative_to(ROOT) else path})",
            "python scripts/testnet/deploy.py builds it (needs Rust + wasm32 target)",
        )
    raw = path.read_bytes()[:4]
    if raw != b"\x00asm":
        return Check("wasm artifact", "FAIL", "file does not start with \\0asm", "rebuild the contract")
    size_kb = path.stat().st_size / 1024
    return Check("wasm artifact", "PASS", f"{size_kb:.0f} KB, valid magic")


def check_signer(url: str) -> Check:
    import requests

    try:
        response = requests.get(f"{url.rstrip('/')}/status", timeout=5)
    except requests.RequestException:
        return Check(
            "signer service",
            "WARN",
            f"not running at {url}",
            "start it: python wallet/near_signer.py (needed by the web app)",
        )
    if response.status_code != 200:
        return Check("signer service", "WARN", f"HTTP {response.status_code}", "check signer logs")
    body = response.json()
    return Check(
        "signer service",
        "PASS",
        f"{body.get('account')} → {body.get('contract_id')}",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ProofArena NEAR testnet preflight")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--rpc", default=os.environ.get("NEAR_RPC_URL", "https://rpc.testnet.near.org"))
    parser.add_argument(
        "--wasm",
        default=str(ROOT / "contracts" / "escrow" / "target" / "wasm32-unknown-unknown" / "release" / "proofarena_escrow.wasm"),
    )
    parser.add_argument("--signer-url", default=os.environ.get("PROOFARENA_SIGNER_URL", "http://127.0.0.1:8788"))
    args = parser.parse_args(argv)

    account = os.environ.get("NEAR_TESTNET_ACCOUNT", "")
    contract = os.environ.get("NEAR_CONTRACT_ID", "")

    checks: list[Check] = [check_python()]
    checks.extend(check_env())
    if account:
        checks.append(check_credentials(account))

    rpc_check, rpc = check_rpc(args.rpc)
    checks.append(rpc_check)
    if rpc is not None and account:
        checks.append(check_account(rpc, account))
        checks.extend(check_contract(rpc, contract, reachable=rpc_check.status == "PASS"))

    checks.append(check_wasm(Path(args.wasm)))
    checks.append(check_signer(args.signer_url))

    failures = [c for c in checks if c.status == "FAIL"]
    warnings = [c for c in checks if c.status == "WARN"]

    if args.json:
        print(json.dumps({"checks": [c.to_dict() for c in checks], "ok": not failures}, indent=2))
    else:
        print("\nProofArena - NEAR testnet preflight\n" + "=" * 72)
        for check in checks:
            print(check.render())
        print("=" * 72)
        print(f"{len(checks)} checks · {len(failures)} failed · {len(warnings)} warnings")
        if failures:
            print("\nNot ready. Fix the FAIL lines above (hints included), then re-run.")
        else:
            print("\nReady. Next: python scripts/testnet/deploy.py  (or the runbook steps).")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
