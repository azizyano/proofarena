"""Build ``fixture/patch.diff`` from the pinned baseline/patched fixture pair.

The defender agent submits this exact diff. It is regenerated rather than
hand-written so the patch can never drift from the fixture it is supposed to
repair.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from proofarena.canonical import sha256_text  # noqa: E402
from proofarena.patching import make_unified_diff  # noqa: E402


def main() -> int:
    diff = make_unified_diff(
        ROOT / "fixture" / "baseline" / "ledger.py",
        ROOT / "fixture" / "patched" / "ledger.py",
        label="ledger.py",
    )
    target = ROOT / "fixture" / "patch.diff"
    target.write_text(diff, encoding="utf-8")
    print(f"wrote {target}")
    print(f"sha256 {sha256_text(diff)}")
    print(f"bytes  {len(diff.encode('utf-8'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
