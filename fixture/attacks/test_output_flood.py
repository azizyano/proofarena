"""RUN-007: oversized output must be truncated, not buffered without limit."""

from __future__ import annotations


def test_output_flood():
    for _ in range(2000):
        print("A" * 200)
