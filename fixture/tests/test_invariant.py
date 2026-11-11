"""Regression test for the seeded allowance defect (PA-006).

This is the *auditor's* reproducer: it fails on the pinned baseline and passes on
the defender's patch. It must not be edited when patching the fixture — that is
the whole point of the check.
"""

from __future__ import annotations

import ledger


def test_cannot_withdraw_more_than_locked_balance():
    book = ledger.Ledger()
    book.deposit("acct-A", 500)  # 5.00 units -> 500 cents

    # 400 units is 80x the funded balance. The correct behaviour is a refusal.
    try:
        book.withdraw("acct-A", 400)
    except ledger.LedgerError:
        pass
    else:
        print(ledger.INVARIANT_MARKER)
        raise AssertionError(
            "withdrew 400 units from a 5.00 unit balance: locked balance is not enforced"
        )

    assert book.locked_balance_cents("acct-A") == 500
    assert book.total_withdrawn_cents() == 0
