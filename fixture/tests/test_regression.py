"""Known-good regression suite: must pass on both baseline and patch.

If this suite ever fails on the patched revision, the fix broke something and
the claim is not eligible for payment. It therefore asserts only behaviour that
is correct *before and after* the unit fix — the exploitation check lives in
``test_invariant.py``.
"""

from __future__ import annotations

import pytest

import ledger


def test_deposit_and_withdraw_within_balance():
    book = ledger.Ledger()
    book.deposit("acct-A", 10_000)  # 100.00 units
    debited = book.withdraw("acct-A", 25)  # 25 units == 2500 cents
    assert debited == 2500
    assert book.locked_balance_cents("acct-A") == 7500
    assert book.total_withdrawn_cents() == 2500


def test_accounting_identity_holds_after_withdrawal():
    book = ledger.Ledger()
    book.deposit("acct-A", 4_000)
    book.deposit("acct-B", 6_000)
    book.withdraw("acct-A", 10)
    book.withdraw("acct-B", 20)
    assert book.total_deposited_cents() == 10_000
    assert book.total_withdrawn_cents() == 3_000
    assert book.locked_balance_cents("acct-A") == 3_000
    assert book.locked_balance_cents("acct-B") == 4_000
    assert book.total_withdrawn_cents() + (
        book.locked_balance_cents("acct-A") + book.locked_balance_cents("acct-B")
    ) == book.total_deposited_cents()


def test_unknown_account_is_rejected():
    book = ledger.Ledger()
    with pytest.raises(ledger.LedgerError):
        book.withdraw("nobody", 1)


def test_non_positive_amounts_are_rejected():
    book = ledger.Ledger()
    book.deposit("acct-A", 1_000)
    for bad in (0, -1):
        with pytest.raises(ledger.LedgerError):
            book.withdraw("acct-A", bad)
        with pytest.raises(ledger.LedgerError):
            book.deposit("acct-A", bad)


def test_non_integer_amounts_are_rejected():
    book = ledger.Ledger()
    book.deposit("acct-A", 1_000)
    for bad in (True, 1.5, "5"):
        with pytest.raises(ledger.LedgerError):
            book.withdraw("acct-A", bad)  # type: ignore[arg-type]
