"""Start the API + web application.

    python scripts/dev.py            # http://127.0.0.1:8787

The wallet signer runs separately (``python wallet/signer.py``) so the API
process never holds the owner key.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the ProofArena API and web UI")
    parser.add_argument("--host", default=os.environ.get("PROOFARENA_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PROOFARENA_PORT", "8787")))
    parser.add_argument(
        "--settlement",
        default=os.environ.get("PROOFARENA_SETTLEMENT_MODE", "delayed"),
        choices=["immediate", "delayed", "manual"],
        help="how the local ledger reports transfer finality",
    )
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)

    import uvicorn

    from proofarena.api import create_app

    app = create_app(settlement_mode=args.settlement)
    print(f"[api] http://{args.host}:{args.port}  settlement={args.settlement}", flush=True)
    print("[api] wallet signer expected at http://127.0.0.1:8788", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
