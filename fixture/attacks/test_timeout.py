"""RUN-003: a runaway job must be terminated by the wall-clock ceiling."""

from __future__ import annotations

import time


def test_runaway_cpu_loop_is_terminated():
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(0.05)
