"""Create a funded NEAR testnet account, reproducibly.

    python scripts/testnet/create_account.py --name proofarena-demo-a1b2c3
    python scripts/testnet/create_account.py --name x --key-out path/to/creds.json

Uses the official helper faucet API (`POST https://helper.testnet.near.org/account`
with `newAccountId` + `newAccountPublicKey`). The private key is generated locally
and written to the standard NEAR credentials location; **it is never printed**.

This is a testnet-only convenience. It does not work for mainnet, and the helper
is rate-limited per IP, so do not loop it.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # legacy Windows consoles
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

import requests  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from proofarena.near.base58 import b58encode  # noqa: E402

FAUCET_URL = "https://helper.testnet.near.org/account"
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,58}[a-z0-9]$")


def generate_key() -> tuple[Ed25519PrivateKey, str, str]:
    """Return ``(key, public_key_ed25519, private_key_ed25519)``."""
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = key.public_key().public_bytes_raw()
    return key, "ed25519:" + b58encode(public), "ed25519:" + b58encode(seed + public)


def default_account_name() -> str:
    return "proofarena-" + secrets.token_hex(3)


def validate_name(name: str) -> str:
    if not name.endswith(".testnet"):
        name = f"{name}.testnet"
    if not NAME_PATTERN.match(name):
        raise SystemExit(
            f"invalid account id {name!r}: use 3-60 lowercase chars, digits, '-', '_' or '.', "
            "ending in .testnet"
        )
    return name


def create_account(
    account_id: str,
    public_key: str,
    *,
    session: requests.Session | None = None,
    timeout: float = 60.0,
) -> dict:
    http = session or requests.Session()
    response = http.post(
        FAUCET_URL,
        json={"newAccountId": account_id, "newAccountPublicKey": public_key},
        timeout=timeout,
    )
    if response.status_code != 200:
        raise SystemExit(
            f"faucet refused ({response.status_code}): {response.text[:300]}\n"
            "The helper is rate-limited per IP. Use your own account instead, or retry later."
        )
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text[:500]}


def credential_paths(account_id: str, key_out: str | None) -> list[Path]:
    candidates = []
    if key_out:
        candidates.append(Path(key_out).expanduser())
    else:
        candidates.append(Path.home() / ".near-credentials" / "testnet" / f"{account_id}.json")
        candidates.append(ROOT / "evidence" / "near-credentials" / f"{account_id}.json")
    return candidates


def write_credentials(account_id: str, public_key: str, private_key: str, key_out: str | None) -> list[Path]:
    payload = {
        "account_id": account_id,
        "public_key": public_key,
        "private_key": private_key,
        "warning": "TESTNET ONLY. Never commit this file, never paste the key anywhere.",
    }
    written: list[Path] = []
    for path in credential_paths(account_id, key_out):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            written.append(path)
            if not key_out and "evidence" in path.parts:
                break  # keep the workspace copy only
        except OSError as exc:
            print(f"  (could not write {path}: {type(exc).__name__})")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a funded NEAR testnet account")
    parser.add_argument("--name", default=None, help="account id (a random one is generated)")
    parser.add_argument("--key-out", default=None, help="explicit credentials path")
    parser.add_argument("--rpc", default="https://rpc.testnet.near.org")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    account_id = validate_name(args.name or default_account_name())
    print(f"generating key for {account_id}")
    _, public_key, private_key = generate_key()
    print(f"public key: {public_key}")

    result = create_account(account_id, public_key)
    print("faucet accepted")

    paths = write_credentials(account_id, public_key, private_key, args.key_out)
    if not paths:
        raise SystemExit("account created but the credentials could not be saved anywhere")

    from proofarena.near.rpc import NearRpc

    rpc = NearRpc(args.rpc)
    info = rpc.account(account_id)
    balance = int(info["amount"]) / 10**24

    if args.json:
        print(
            json.dumps(
                {
                    "account_id": account_id,
                    "public_key": public_key,
                    "balance_near": balance,
                    "credentials": [str(p) for p in paths],
                },
                indent=2,
            )
        )
    else:
        print(f"\naccount : {account_id}")
        print(f"balance : {balance} NEAR")
        print(f"key file: {paths[0]}")
        print("\nUse it:")
        print(f"  $env:NEAR_TESTNET_ACCOUNT = '{account_id}'")
        print(f"  $env:NEAR_CONTRACT_ID     = '{account_id}'")
        if "evidence" in paths[0].parts:
            print(f"  $env:NEAR_CREDENTIALS     = '{paths[0]}'   # move it to ~/.near-credentials if you prefer")
        print("\nNext: build the contract (runbook step 4), then deploy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
