"""The facts a complete document of a common kind states, in the order a careful clerk asks for them.

The intake (intake.py) names the kind of document the caller wants and fills in what they have said; every fact of its
form is then asked on the call before writing, unless the caller asks to leave it out. The writer's own judgement of
what a document needs varies from run to run: on the user's leave request the intake asked for the caller's designation
1 time in 3 and the writer never left a place for it, so the letter went out signed without one ("caller should get
ready made complete doc not partial, until caller ask to leave it blank"). Documents of any other kind are worked out by
the intake as before.
"""
from __future__ import annotations

import re
from typing import Dict, List, NamedTuple


class Form(NamedTuple):
    kind: str
    what: str                   # when it applies, for the intake
    facts: List[tuple]          # (label, how BolDoc asks for it)


FORMS: Dict[str, Form] = {f.kind: f for f in [
    Form("leave_office", "a leave application to an employer", [
        ("recipient", "Who should it go to, for example the HR Manager or your manager?"),
        ("from date", "From which date?"), ("to date", "To which date?"),
        ("reason", "What is the leave for?"),
        ("your name", "What is your name?"), ("your designation", "What is your designation?"),
        ("organisation or department", "Which organisation or department are you in?")]),
    Form("leave_school", "a leave application to a school or college, for a student", [
        ("recipient", "Who should it go to, for example the Principal or the class teacher?"),
        ("student's name", "What is the student's name?"),
        ("class and section", "Which class and section, or course and year?"),
        ("from date", "From which date?"), ("to date", "To which date?"),
        ("reason", "What is the leave for?"),
        ("signed by", "Whose name should it be signed with, the parent's or the student's?")]),
    Form("bank_request", "a request to a bank: closing an account, changing an address or mobile number, a "
         "statement, a cheque book, KYC", [
        ("bank and branch", "Which bank and branch?"),
        ("account holder's name", "What is the account holder's name?"),
        ("account number", "What is the account number?"),
        ("request", "What exactly should the bank do?")]),
    Form("complaint", "a complaint that reports something wrong (a fault, a nuisance, a theft, poor service) to an "
         "office that should act on it: a municipality, the police, a housing society, a company", [
        ("recipient", "Who should the complaint go to?"),
        ("what happened", "What exactly is the problem?"),
        ("where", "Where is it, the address or area?"),
        ("since when", "Since when, or on which date?"),
        ("your name", "What is your name?"), ("your address or phone", "Your address or phone number for a reply?")]),
    Form("rent_receipt", "a receipt for rent paid", [
        ("landlord's name", "What is the landlord's name?"), ("tenant's name", "What is the tenant's name?"),
        ("property address", "What is the property's address?"), ("amount", "How much rent?"),
        ("period", "For which month or period?"), ("payment date and mode", "When and how was it paid?")]),
    Form("invoice", "an invoice or bill for goods or services", [
        ("seller", "Whose invoice is it, the business name and address?"),
        ("buyer", "Who is it for, the buyer's name?"),
        ("items", "What items or services, with quantity and rate?"),
        ("tax", "Is GST charged? If so, the rate and GSTIN.")]),
    Form("resignation", "a resignation letter", [
        ("recipient", "Who should it go to?"), ("your name", "What is your name?"),
        ("your designation", "What is your designation?"), ("organisation", "Which organisation?"),
        ("last working day", "What will be your last working day?")]),
]}


_STOP = {"the", "your", "caller", "s", "of", "or", "and", "a", "an", "for", "my"}


def _key(label: str) -> set:
    """The words that tell a fact apart; "name" only when there's nothing else ("your name", "caller's name")."""
    words = {w for w in re.findall(r"[a-z]+", label.lower()) if w not in _STOP}
    return (words - {"name"}) or words


def same_fact(a: str, b: str) -> bool:
    """Two labels for one fact ("your designation" / "caller's designation", "from date" / "leave start date")."""
    ka, kb = _key(a), _key(b)
    if not ka or not kb:
        return a.strip().lower() == b.strip().lower()
    start, end = {"from", "start", "starting"}, {"to", "end", "till", "until", "ending", "last"}
    if {"date", "day"} & ka and {"date", "day"} & kb and (start | end) & (ka | kb):  # the two ends of a period
        return bool((start & ka and start & kb) or (end & ka and end & kb))
    return bool(ka & kb) and (ka <= kb or kb <= ka)


def kinds_block() -> str:
    """The forms, as the intake reads them."""
    return "\n".join(f'- "{f.kind}": {f.what}. Its facts: ' + "; ".join(label for label, _ in f.facts) + "."
                     for f in FORMS.values())
