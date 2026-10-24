"""Phone numbers as BolDoc sees them: Indian mobiles (SMS-capable) vs everything else."""
from __future__ import annotations

from typing import Optional

import phonenumbers


def indian_mobile(raw: Optional[str]) -> Optional[str]:
    """'919988776655' / '+91 99887 76655' -> '+919988776655' if it is an Indian mobile number, else None."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit()).lstrip("0")  # 0-prefixed national format
    if not digits:
        return None
    try:
        n = phonenumbers.parse(digits if len(digits) == 10 else "+" + digits, "IN")
    except phonenumbers.NumberParseException:
        return None
    if n.country_code != 91 or not phonenumbers.is_valid_number(n):
        return None
    if phonenumbers.number_type(n) not in (phonenumbers.PhoneNumberType.MOBILE,
                                           phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE):
        return None
    return phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164)


def masked(number: Optional[str]) -> str:
    """'+919988776655' -> '…6655' (for logs: never a whole number)."""
    return "…" + number[-4:] if number and len(number) >= 4 else "none"
