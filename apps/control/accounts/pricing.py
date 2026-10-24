"""Prices and trial rules (1 credit = ₹1). Every value is configuration; prices sit ~1.3× measured unit cost
(apps/control/rates.py): a call minute costs us ~₹3.3, a new document's SMS alone ~₹7."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


@dataclass  # changed in place by a publish from the admin console (apps/control/agent_config.py)
class Pricing:
    minute: int = field(default_factory=lambda: _int("PRICE_MINUTE", 4))
    doc_create: int = field(default_factory=lambda: _int("PRICE_DOC", 10))
    doc_create_big: int = field(default_factory=lambda: _int("PRICE_BIG_DOC", 60))  # ~2x a Hermes run's cost
    doc_edit: int = field(default_factory=lambda: _int("PRICE_EDIT", 2))
    signup_bonus: int = field(default_factory=lambda: _int("SIGNUP_BONUS", 2000))
    trial_calls: int = field(default_factory=lambda: _int("TRIAL_CALLS", 3))
    linked_numbers: int = field(default_factory=lambda: _int("LINKED_NUMBERS", 3))  # besides the sign-in number
    trial_max_call_s: int = field(default_factory=lambda: _int("TRIAL_MAX_CALL_S", 300))
    trial_docs_per_call: int = field(default_factory=lambda: _int("TRIAL_DOCS_PER_CALL", 2))
    trial_edits_per_doc: int = field(default_factory=lambda: _int("TRIAL_EDITS_PER_DOC", 3))
    counted_call_s: int = field(default_factory=lambda: _int("TRIAL_COUNTED_CALL_S", 20))
    max_call_s: int = field(default_factory=lambda: _int("MAX_CALL_S", 1800))
    # Fair use of a number published on the website: every connected call counts (short dial-and-drop ones too).
    trial_calls_per_day: int = field(default_factory=lambda: _int("TRIAL_CALLS_PER_DAY", 3))
    trial_minutes_per_day: int = field(default_factory=lambda: _int("TRIAL_MINUTES_PER_DAY", 10))
    calls_per_day: int = field(default_factory=lambda: _int("CALLS_PER_DAY", 20))       # signed-up numbers
    minutes_per_day: int = field(default_factory=lambda: _int("MINUTES_PER_DAY", 120))
    burst_calls: int = field(default_factory=lambda: _int("BURST_CALLS", 3))            # at most this many calls
    burst_window_s: int = field(default_factory=lambda: _int("BURST_WINDOW_S", 1800))   # ... in any 30 minutes
    trial_minutes_budget: int = field(default_factory=lambda: _int("TRIAL_MINUTES_BUDGET", 300))  # all trials, a day
    refuse_withheld: bool = field(default_factory=lambda: os.environ.get("REFUSE_WITHHELD", "1") == "1")
    # the owner's own test phones: never limited (comma-separated, e.g. +919988776655)
    unlimited_numbers: tuple = field(default_factory=lambda: tuple(
        n.strip() for n in os.environ.get("UNLIMITED_NUMBERS", "").split(",") if n.strip()))

    def docs_estimate(self, balance: int, call_minutes: int = 3) -> int:
        """'≈ N documents' for the console: each with a short call of `call_minutes`."""
        per = self.doc_create + call_minutes * self.minute
        return max(0, balance // per)

    def price_for(self, kind: str) -> int:
        """Credits for a ledger kind of document work (undo is free)."""
        return {"doc_create": self.doc_create, "doc_create_big": self.doc_create_big,
                "doc_edit": self.doc_edit}.get(kind, 0)


PRICING = Pricing()
