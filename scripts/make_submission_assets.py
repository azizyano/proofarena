"""Generate the submission images from real data (architecture + on-chain proof card).

    python scripts/make_submission_assets.py

Both images are drawn from facts already in the repository — the transaction hashes
are the ones in docs/TESTNET_EVIDENCE.md, not invented. Re-run after a new run to
refresh the proof card.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets"
BG = "#0b0f14"
PANEL = "#121a23"
LINE = "#2b3a4a"
TEXT = "#e6edf5"
MUTED = "#8fa3b8"
ACCENT = "#61d0a4"
BLUE = "#7cc4ff"
WARN = "#ffcf6b"


def _box(ax, x, y, w, h, title, lines, accent=LINE):
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0.012,rounding_size=0.02",
            linewidth=1.4, edgecolor=accent, facecolor=PANEL, zorder=2,
        )
    )
    ax.text(x + w / 2, y + h - 0.05, title, ha="center", va="top", color=TEXT,
            fontsize=11.5, fontweight="bold", zorder=3)
    for index, line in enumerate(lines[:3]):
        ax.text(x + 0.018, y + h - 0.105 - index * 0.052, line, ha="left", va="top",
                color=MUTED, fontsize=8.5, zorder=3)


def _arrow(ax, start, end, color=LINE, style="-|>", rad=0.0):
    ax.add_patch(
        FancyArrowPatch(
            start, end, arrowstyle=style, mutation_scale=13,
            linewidth=1.3, color=color, connectionstyle=f"arc3,rad={rad}", zorder=1,
        )
    )


def architecture() -> Path:
    fig, ax = plt.subplots(figsize=(13.2, 6.6), dpi=150)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(0.5, 0.975, "ProofArena - verification is the economic boundary",
            ha="center", va="top", color=TEXT, fontsize=17, fontweight="bold")
    ax.text(0.5, 0.933,
            "agents do the work  ·  a human wallet releases the money  ·  the contract pays exactly once",
            ha="center", va="top", color=MUTED, fontsize=10)

    top_y, bottom_y, h = 0.575, 0.185, 0.255
    col_a, col_b, col_c = 0.03, 0.365, 0.72
    wide, narrow = 0.30, 0.25

    _box(ax, col_a, top_y, wide, h, "Owner / visitor browser",
         ["Meteor · Nightly · MyNearWallet",
          "NEP-413 sign-in",
          "signs & broadcasts to NEAR",
          "holds the only key in the system"], accent=BLUE)
    _box(ax, col_b, top_y, wide, h, "API   (VPS, holds no keys)",
         ["hands out transaction plans",
          "re-reads the chain, then records",
          "mirror never overrides chain"], accent=ACCENT)
    _box(ax, col_c, top_y, narrow, h, "NEAR testnet escrow",
         ["near-sdk 5.9 · Rust",
          "one winner · owner-only",
          "PayoutPending -> Paid"], accent=WARN)

    _box(ax, col_a, bottom_y, wide, h, "UI",
         ["wallet demo (Vercel, static)",
          "dashboard: PayoutPending != Paid",
          "explorer link per receipt"], accent=BLUE)
    _box(ax, col_b, bottom_y, wide, h, "Independent verifier",
         ["isolated pytest sandbox",
          "baseline x2 -> patch -> regression",
          "artifacts hashed + manifest"], accent=ACCENT)
    _box(ax, col_c, bottom_y, narrow, h, "On-chain evidence",
         ["proofarena-c23922.testnet",
          "2 paid · 2 refunded",
          "escrow liability 0"], accent=WARN)

    # browser <-> api
    _arrow(ax, (col_a + wide, top_y + 0.17), (col_b, top_y + 0.17), BLUE)
    _arrow(ax, (col_b, top_y + 0.09), (col_a + wide, top_y + 0.09), MUTED)
    # api -> escrow (reads) and escrow -> api (state)
    _arrow(ax, (col_b + wide, top_y + 0.17), (col_c, top_y + 0.17), ACCENT)
    _arrow(ax, (col_c, top_y + 0.09), (col_b + wide, top_y + 0.09), MUTED)
    # ui -> verifier -> evidence
    _arrow(ax, (col_a + wide, bottom_y + 0.13), (col_b, bottom_y + 0.13), BLUE)
    _arrow(ax, (col_b + wide, bottom_y + 0.13), (col_c, bottom_y + 0.13), ACCENT)
    # verifier feeds the api (results), api feeds the ui
    _arrow(ax, (col_b + 0.15, bottom_y + h), (col_b + 0.15, top_y), ACCENT)
    _arrow(ax, (col_a + 0.15, top_y), (col_a + 0.15, bottom_y + h), MUTED)
    ax.text(col_a, 0.115,
            "Trust boundaries:  the wallet authorises · the contract settles · the verifier re-executes "
            "· the API only reports what the chain says",
            ha="left", va="center", color=TEXT, fontsize=9.4)
    ax.text(col_a, 0.062,
            "The wallet sends transactions straight to NEAR; the API only verifies them. Agents never hold keys. "
            "A submitted transfer is not a payment - ambiguous ones freeze into NeedsReconciliation.",
            ha="left", va="center", color=MUTED, fontsize=8.6)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "architecture.png"
    fig.savefig(path, facecolor=BG, bbox_inches="tight", pad_inches=0.22)
    plt.close(fig)
    return path


BOUNTIES = [
    ("#1", "Paid", "0.1 NEAR", "—", "6mxzbdYBpFHjgo4aJxdSjYJm1cnigJG1sJYmsgAhi4nP"),
    ("#2", "Refunded", "—", "0.1 NEAR", "EYyzkpCuQkxkv2U29vZr81W4SoFQKJs8mmU2UW9Fgtcd"),
    ("#3", "Paid", "0.1 NEAR", "—", "AVwQZ4mdAmSmw6YiKS34FNn1mdd3YgKFX5tW5unXkRap"),
    ("#4", "Refunded", "—", "0.02 NEAR", "H5eGNidaraf6akVhzggE7hNJzbaUynTGgptFVoZRe5bZ"),
]


def proof_card() -> Path:
    fig, ax = plt.subplots(figsize=(12.4, 5.6), dpi=150)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(0.04, 0.93, "ProofArena on NEAR testnet — real settlement, not a mock",
            ha="left", va="top", color=TEXT, fontsize=16, fontweight="bold")
    ax.text(0.04, 0.855,
            "contract proofarena-c23922.testnet  ·  owner coldlemon3275.testnet  ·  "
            "auditor proofarena-auditor-0fbc4e.testnet",
            ha="left", va="top", color=MUTED, fontsize=9)

    headers = ["bounty", "state", "paid", "refunded", "transaction"]
    xs = [0.04, 0.13, 0.26, 0.36, 0.50]
    for x, header in zip(xs, headers):
        ax.text(x, 0.775, header.upper(), ha="left", va="center", color=MUTED, fontsize=8.4)

    for row, (bounty, chain_state, paid, refunded, tx) in enumerate(BOUNTIES):
        y = 0.705 - row * 0.085
        ax.add_patch(
            FancyBboxPatch((0.035, y - 0.036), 0.93, 0.072,
                           boxstyle="round,pad=0.004,rounding_size=0.006",
                           facecolor=PANEL if row % 2 == 0 else "#0f1620",
                           edgecolor=LINE, linewidth=0.8)
        )
        colour = ACCENT if chain_state == "Paid" else BLUE
        ax.text(xs[0], y, bounty, ha="left", va="center", color=TEXT, fontsize=10, fontweight="bold")
        ax.text(xs[1], y, chain_state, ha="left", va="center", color=colour, fontsize=9.5)
        ax.text(xs[2], y, paid, ha="left", va="center", color=TEXT, fontsize=9)
        ax.text(xs[3], y, refunded, ha="left", va="center", color=TEXT, fontsize=9)
        ax.text(xs[4], y, tx[:30] + "…", ha="left", va="center", color=MUTED, fontsize=8.6,
                family="monospace")

    ax.text(0.04, 0.285,
            "total_escrow_yocto() → 0        every yocto either reached the approved winner or came "
            "back to the owner",
            ha="left", va="center", color=TEXT, fontsize=10)
    ax.text(0.04, 0.225,
            "176 automated tests  ·  second payout refused by the contract (409)  ·  early refund "
            "refused  ·  receipt chain verifiable",
            ha="left", va="center", color=MUTED, fontsize=9)
    ax.text(0.04, 0.155,
            textwrap.fill(
                "Full hashes, explorer links and the exact reproduction commands: "
                "docs/TESTNET_EVIDENCE.md",
                width=110,
            ),
            ha="left", va="center", color=MUTED, fontsize=8.6)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "onchain-proof.png"
    fig.savefig(path, facecolor=BG, bbox_inches="tight", pad_inches=0.22)
    plt.close(fig)
    return path


def main() -> int:
    for path in (architecture(), proof_card()):
        print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
