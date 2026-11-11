"""Build and deploy the ProofArena escrow contract to NEAR testnet.

    python scripts/testnet/deploy.py --check          # build only, no transaction
    python scripts/testnet/deploy.py --dry-run        # build + print the signed tx
    python scripts/testnet/deploy.py                  # sign, broadcast, verify

Safety rails:

* refuses any account that does not end with ``.testnet``;
* refuses to broadcast unless ``--yes`` is passed (or you answer the prompt);
* never prints the private key;
* after the transaction it *re-reads the contract* and only reports success when
  the view call actually answers.

Signing is done by the local process holding the credentials file. This script is
run by the human owner; the web app never sees this key.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

try:  # make Unicode output safe on legacy Windows consoles
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - non-reconfigurable stream
    pass
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_WASM = (
    ROOT
    / "contracts"
    / "escrow"
    / "target"
    / "wasm32-unknown-unknown"
    / "release"
    / "proofarena_escrow.wasm"
)


def banner(text: str) -> None:
    print(f"\n{text}\n" + "-" * len(text))


def build_wasm(wasm: Path) -> None:
    banner("Building contract")
    if shutil.which("cargo") is None:
        raise SystemExit(
            "cargo not found. Install Rust, then:\n"
            "  rustup target add wasm32-unknown-unknown\n"
            "  python scripts/testnet/deploy.py"
        )
    env = dict(os.environ)
    env.setdefault("RUSTFLAGS", "-C link-arg=-s")
    command = [
        "cargo",
        "build",
        "--release",
        "--target",
        "wasm32-unknown-unknown",
        "--manifest-path",
        str(ROOT / "contracts" / "escrow" / "Cargo.toml"),
    ]
    print("$ " + " ".join(command))
    proc = subprocess.run(command, env=env, cwd=str(ROOT / "contracts" / "escrow"))
    if proc.returncode != 0:
        raise SystemExit(
            "cargo build failed. This is expected the first time on a new toolchain — "
            "send the compiler output to the maintainer; the contract has never been compiled here."
        )
    if not wasm.exists():
        raise SystemExit(f"build reported success but {wasm} is missing")
    print(f"built {wasm} ({wasm.stat().st_size / 1024:.0f} KB)")


def main(argv: list[str] | None = None) -> int:
    from proofarena.near.rpc import RpcError
    from proofarena.near.signing import decode_signed_transaction
    from proofarena.near.wallet import credentials_path, load_key

    parser = argparse.ArgumentParser(description="Deploy the ProofArena escrow to NEAR testnet")
    parser.add_argument("--wasm", default=str(DEFAULT_WASM))
    parser.add_argument("--account", default=os.environ.get("NEAR_TESTNET_ACCOUNT"))
    parser.add_argument("--contract", default=os.environ.get("NEAR_CONTRACT_ID") or os.environ.get("NEAR_TESTNET_ACCOUNT"))
    parser.add_argument("--owner", default=None, help="contract owner account (defaults to --account)")
    parser.add_argument("--check", action="store_true", help="build only")
    parser.add_argument("--dry-run", action="store_true", help="build and print the signed transaction")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--rpc", default=os.environ.get("NEAR_RPC_URL", "https://rpc.testnet.near.org"))
    args = parser.parse_args(argv)

    account = args.account
    contract = args.contract
    if not account:
        raise SystemExit("set NEAR_TESTNET_ACCOUNT (your funded testnet account)")
    if not contract:
        raise SystemExit("set NEAR_CONTRACT_ID (the account that will host the contract)")
    owner = args.owner or account
    for label, value in (("account", account), ("contract", contract), ("owner", owner)):
        if not value.endswith(".testnet"):
            raise SystemExit(
                f"refusing: {label}={value!r} is not a .testnet account. "
                "This build is testnet-only by design."
            )

    wasm = Path(args.wasm)
    if not wasm.exists():
        build_wasm(wasm)
    if wasm.read_bytes()[:4] != b"\x00asm":
        raise SystemExit(f"{wasm} is not a WebAssembly module")
    print(f"wasm: {wasm} ({wasm.stat().st_size / 1024:.0f} KB)")
    if args.check:
        print("build check only — no transaction built. ok")
        return 0

    credentials = credentials_path(account)
    if credentials is None:
        raise SystemExit(
            f"no credentials for {account}. Expected ~/.near-credentials/testnet/{account}.json "
            "(runbook step 3)."
        )
    key = load_key(account, path=credentials)
    from proofarena.near.rpc import NearRpc
    from proofarena.near.signing import deploy_contract_action, function_call_action, sign_actions

    rpc = NearRpc(args.rpc)
    if contract != account:
        # A different hosting account must already exist (create it with the NEAR
        # CLI or the faucet); this script deploys code, it does not create accounts.
        try:
            rpc.account(contract)
        except Exception as exc:
            raise SystemExit(
                f"contract account {contract} does not exist and this script will not create it: "
                f"{str(exc)[:120]}. Create it first, or deploy to {account} by omitting --contract."
            )
    nonce = int(rpc.view_access_key(account, key.public_key_ed25519)["nonce"]) + 1
    signed = sign_actions(
        key=key,
        signer_id=account,
        receiver_id=contract,
        nonce=nonce,
        block_hash=rpc.block_hash(),
        actions=[
            deploy_contract_action(wasm.read_bytes()),
            function_call_action("new", {"owner": owner}, gas=30_000_000_000_000, deposit=0),
        ],
    )

    decoded = decode_signed_transaction(signed.signed_borsh)
    print(f"\ntransaction (round-trip decoded, {len(signed.signed_borsh)} bytes)")
    print(f"  signer   : {decoded['signer_id']}")
    print(f"  receiver : {decoded['receiver_id']}")
    print(f"  nonce    : {decoded['nonce']}")
    print(f"  actions  : {[a['type'] for a in decoded['actions']]}")
    print(f"  tx hash  : {signed.tx_hash}")
    print(f"  explorer : {signed.explorer_url()}")

    if args.dry_run:
        print("\nDry run only — nothing was broadcast.")
        return 0

    if not args.yes:
        answer = input("\nBroadcast this deployment to NEAR TESTNET? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("aborted")
            return 1

    banner("Broadcasting")
    outcome = rpc.broadcast_commit(signed)
    if not outcome.success:
        print(f"transaction failed: {outcome.error or outcome.status}")
        print("Nothing was deployed. If the error mentions deserialization, retry with")
        print("PROOFARENA_NEAR_WALLET=cli and an installed `near` CLI.")
        return 2
    print(f"success at block {outcome.block_height}")
    for line in outcome.logs[-5:]:
        print(f"  log: {line}")

    banner("Verifying deployed contract")
    for attempt in range(1, 16):
        try:
            ids = rpc.view_call(contract, "list_bounty_ids", {})
            print(f"view list_bounty_ids → {ids}")
            print("\nDeployed and initialised.")
            print(f"  contract : {contract}")
            print(f"  owner    : {owner}")
            print(f"  tx       : {outcome.tx_hash}")
            print(f"  explorer : https://explorer.testnet.near.org/transactions/{outcome.tx_hash}")
            print("\nNext:")
            print(f"  export NEAR_CONTRACT_ID={contract}")
            print("  export PROOFARENA_CHAIN_MODE=near-testnet")
            print("  python wallet/near_signer.py")
            print("  python scripts/dev.py")
            print("  python scripts/testnet/smoke.py")
            return 0
        except RpcError as exc:
            print(f"  waiting for indexing ({attempt}/15): {str(exc)[:100]}")
            time.sleep(2)
    print("contract deployed but the view call never answered; check the explorer link above")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
