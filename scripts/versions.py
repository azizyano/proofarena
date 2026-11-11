"""Print the exact runtime inventory used for PA-001 evidence."""

from __future__ import annotations

import importlib
import json
import platform
import shutil
import subprocess
import sys

try:  # make Unicode output safe on legacy Windows consoles
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - non-reconfigurable stream
    pass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _cmd(argv: list[str]) -> str:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable ({exc.__class__.__name__})"
    if proc.returncode != 0:
        return f"unavailable (exit {proc.returncode})"
    return (proc.stdout or proc.stderr).strip().splitlines()[0]


def _module(name: str) -> str:
    try:
        importlib.import_module(name)
    except Exception:
        return "not installed"
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return "installed (version metadata unavailable)"


def main() -> int:
    inventory = {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "node": _cmd(["node", "--version"]),
        "npm": _cmd(["npm", "--version"]),
        "pnpm": _cmd(["pnpm", "--version"]),
        "git": _cmd(["git", "--version"]),
        "cargo": _cmd(["cargo", "--version"]),
        "rustc": _cmd(["rustc", "--version"]),
        "near_cli": shutil.which("near") or "not installed",
        "near_sandbox": shutil.which("near-sandbox") or "not installed",
        "docker": shutil.which("docker") or "not installed",
        "packages": {
            name: _module(name)
            for name in (
                "starlette",
                "pydantic",
                "uvicorn",
                "jinja2",
                "jsonschema",
                "cryptography",
                "pytest",
                "httpx",
            )
        },
        "fastapi": _module("fastapi"),
    }
    print(json.dumps(inventory, indent=2, sort_keys=True))
    out = ROOT / "evidence" / "runtime-inventory.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nwritten: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
