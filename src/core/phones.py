import re
from decimal import Decimal
from typing import Optional, Set

from .config import settings


def normalize_phone(phone: Optional[str]) -> str:
    if not phone:
        return ""
    raw = str(phone).strip()
    if not raw:
        return ""

    # Excel often yields numbers as "4122571528.0". Drop trailing decimal zeros.
    m = re.fullmatch(r"([0-9]+)\.0+", raw)
    if m:
        raw = m.group(1)
    elif re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+", raw):
        # Scientific notation to integer string when possible.
        try:
            dec = Decimal(raw)
            if dec == dec.to_integral_value():
                raw = str(int(dec))
        except Exception:
            pass

    return "".join(ch for ch in raw if ch.isdigit())


def format_phone_for_local_api(phone: Optional[str]) -> str:
    raw = str(phone or "").strip()
    digits = normalize_phone(raw)
    if not digits:
        return ""

    if raw.startswith("+"):
        return f"+{digits}"

    country_code = settings.local_api_country_code
    country_code = "".join(ch for ch in country_code if ch.isdigit())
    if not country_code:
        return digits

    if digits.startswith("00") and len(digits) > 2:
        return f"+{digits[2:]}"
    if digits.startswith(country_code) and len(digits) > len(country_code):
        return f"+{digits}"
    if digits.startswith("0"):
        return f"+{country_code}{digits.lstrip('0')}"
    if len(digits) == 10:
        return f"+{country_code}{digits}"

    return digits


def _phone_variants(phone: Optional[str]) -> Set[str]:
    """
    Genera variantes comparables para tolerar diferencias comunes:
    - con/sin cero inicial
    - con/sin prefijo de país (p.ej. 58)
    - últimos 10 dígitos
    """
    raw = normalize_phone(phone)
    if not raw:
        return set()

    variants: Set[str] = set()
    queue = [raw]
    while queue:
        item = queue.pop()
        if not item or item in variants:
            continue
        variants.add(item)

        no_leading = item.lstrip("0")
        if no_leading and no_leading not in variants:
            queue.append(no_leading)

        if item.startswith("58") and len(item) > 10:
            without_cc = item[2:]
            if without_cc and without_cc not in variants:
                queue.append(without_cc)

        if len(item) >= 10:
            variants.add(item[-10:])

    return variants


def phones_equivalent(a: Optional[str], b: Optional[str]) -> bool:
    va = _phone_variants(a)
    vb = _phone_variants(b)
    if not va or not vb:
        return False
    return not va.isdisjoint(vb)