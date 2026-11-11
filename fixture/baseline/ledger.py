"""INTENTIONALLY VULNERABLE FIXTURE — do not deploy, do not reuse.

Synthetic target for the ProofArena demo: a prepaid allowance ledger that tracks
balances in **cents** and lets a client withdraw in **whole units**.

Seeded defect (PA-006): ``withdraw`` compares the whole-unit amount directly
against the cent balance, then debits ``amount * 100`` cents. A caller can drain
up to 100x the locked balance and push the account negative.

Expected invariant: cumulative withdrawals must never exceed the funded balance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CENT = 100

INVARIANT_MARKER = "PROOFARENA-INVARIANT:ALLOWANCE_EXCEEDED"


class LedgerError(Exception):
    """Raised when a ledger precondition is violated."""


@dataclass
class Account:
    owner: str
    locked_cents: int = 0
    withdrawn_cents: int = 0
    deposited_cents: int = 0


@dataclass
class Ledger:
    accounts: dict[str, Account] = field(default_factory=dict)

    def _account(self, owner: str) -> Account:
        account = self.accounts.get(owner)
        if account is None:
            raise LedgerError(f"unknown account {owner!r}")
        return account

    def deposit(self, owner: str, amount_cents: int) -> None:
        if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
            raise LedgerError("amount must be an integer number of cents")
        if amount_cents <= 0:
            raise LedgerError("deposit must be positive")
        account = self.accounts.setdefault(owner, Account(owner=owner))
        account.locked_cents += amount_cents
        account.deposited_cents += amount_cents

    def locked_balance_cents(self, owner: str) -> int:
        return self._account(owner).locked_cents

    def total_deposited_cents(self) -> int:
        return sum(a.deposited_cents for a in self.accounts.values())

    def total_withdrawn_cents(self) -> int:
        return sum(a.withdrawn_cents for a in self.accounts.values())

    def withdraw(self, owner: str, amount_units: int) -> int:
        """Withdraw ``amount_units`` whole units (1 unit == 100 cents)."""
        if isinstance(amount_units, bool) or not isinstance(amount_units, int):
            raise LedgerError("amount must be an integer number of units")
        if amount_units <= 0:
            raise LedgerError("withdrawal must be positive")
        account = self._account(owner)

        # DEFECT: unit mismatch. `amount_units` is compared against a cent
        # balance as if it were already in cents.
        if amount_units > account.locked_cents:
            raise LedgerError("insufficient locked balance")

        debit_cents = amount_units * CENT
        account.locked_cents -= debit_cents
        account.withdrawn_cents += debit_cents
        return debit_cents
