"""Unit and currency parsing shared by detection, extraction and display.

Nothing here knows about specific companies. Reported money is stored as the
raw reported number plus the unit it was reported in; ``UNIT_MULTIPLIERS``
converts a unit to base currency units (one whole rupee / dollar / euro...).
"""

from __future__ import annotations

import re

# unit name -> multiplier to base currency units
UNIT_MULTIPLIERS = {
    "unit": 1,
    "units": 1,
    "ones": 1,
    "ten": 10,
    "hundred": 100,
    "thousand": 1_000,
    "lakhs": 100_000,
    "lakh": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "million": 1_000_000,
    "millions": 1_000_000,
    "bn": 1_000_000_000,
    "billion": 1_000_000_000,
    "billions": 1_000_000_000,
    "tn": 1_000_000_000_000,
    "trillion": 1_000_000_000_000,
}

UNIT_ALIASES = {
    "rs.": "rupee",  # handled by currency parsing below
    "mn": "million",
    "mm": "million",
    "m": "million",
    "k": "thousand",
    "cr": "crore",
    "bn": "billion",
    "b": "billion",
}

CURRENCY_ALIASES = {
    "inr": "INR",
    "rupee": "INR",
    "rupees": "INR",
    "rs": "INR",
    "₹": "INR",
    "indian rupee": "INR",
    "usd": "USD",
    "us dollar": "USD",
    "dollars": "USD",
    "$": "USD",
    "eur": "EUR",
    "euro": "EUR",
    "€": "EUR",
    "gbp": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "sterling": "GBP",
    "£": "GBP",
    "jpy": "JPY",
    "yen": "JPY",
    "¥": "JPY",
    "aud": "AUD",
    "cad": "CAD",
    "sgd": "SGD",
    "chf": "CHF",
    "cny": "CNY",
    "rmb": "CNY",
    "yuan": "CNY",
}

# Ordered by specificity: longer phrases first so "millions of INR" wins
# over "INR".

_MONEY_UNIT_RE = re.compile(
    r"(?P<cur>[₹$€£¥]|(?:Rs\.?|INR|USD|EUR|GBP|JPY|AUD|CAD|SGD|CHF|CNY)\b)?"
    r"\s*(?P<num>-?\(?\d[\d,]*(?:\.\d+)?\)?)\s*"
    r"(?P<unit>crores?|lakhs?|lacs?|billions?|trillions?|millions?|thousands?|bn|mn|k)\b\.?",
    re.IGNORECASE,
)


def normalize_unit(unit: str | None) -> str | None:
    """Map a spelling of a unit onto the canonical singular key."""
    if not unit:
        return None
    key = unit.strip().lower().rstrip(".")
    key = UNIT_ALIASES.get(key, key)
    if key in UNIT_MULTIPLIERS:
        singular = key[:-1]
        return singular if key.endswith("s") and singular in UNIT_MULTIPLIERS else key
    return None


def unit_multiplier(unit: str | None) -> float:
    """Multiplier that converts a reported value to base currency units."""
    canonical = normalize_unit(unit)
    if canonical is None:
        return 1.0
    return float(UNIT_MULTIPLIERS[canonical])


def normalize_currency(currency: str | None) -> str | None:
    if not currency:
        return None
    key = currency.strip().lower().rstrip(".")
    if key in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[key]
    upper = currency.strip().upper()
    return upper if len(upper) == 3 and upper.isalpha() else None


def currency_symbol(currency: str | None) -> str:
    return {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}.get(
        (currency or "").upper(), ""
    )


def infer_unit_from_text(text: str) -> tuple[str | None, str | None]:
    """Return (currency, unit) inferred from report boilerplate like
    'all monetary values are in Millions of INR' or
    'amounts in millions of U.S. dollars'. Generic, no company knowledge.

    The unit seen most often wins, so 'in millions' beats an isolated
    'in thousands' reference to share counts."""
    if not text:
        return None, None
    window = text[:400_000]
    lowered = window.lower()

    unit_counts: dict[str, int] = {}
    currency = None
    for match in re.finditer(
        r"(?:in|of|expressed in|rs\.?\s*in)\s+"
        r"(?:(?:rs\.?|inr|usd|eur|gbp|jpy|aud|cad|sgd|chf|cny|[₹$€£¥])\s*)?"
        r"(?P<u>crores?|lakhs?|lacs?|billions?|trillions?|millions?|thousands?)"
        r"(?:\s+of\s+(?P<c>[a-z]{3,}))?",
        lowered,
    ):
        unit = normalize_unit(match.group("u"))
        if unit:
            unit_counts[unit] = unit_counts.get(unit, 0) + 1
            if match.group("c"):
                currency = currency or normalize_currency(match.group("c"))
    unit = max(unit_counts, key=unit_counts.get) if unit_counts else None

    # Explicit "... millions of INR / dollars / rupees" wins.
    for match in re.finditer(
        r"(?:in|of)\s+(?:crores?|lakhs?|lacs?|billions?|trillions?|millions?|thousands?)"
        r"\s+of\s+([a-z ]{3,20})",
        lowered,
    ):
        currency = normalize_currency(match.group(1).strip()) or currency
        if currency:
            break
    if currency is None:
        for pattern in (r"\b(?:inr|usd|eur|gbp|jpy|aud|cad|sgd|chf|cny)\b",):
            m = re.search(pattern, lowered)
            if m:
                currency = normalize_currency(m.group(0))
                if currency:
                    break
    if currency is None and re.search(r"\b(?:rs\.?|rupees)\b", lowered):
        currency = "INR"
    if currency is None:
        symbol_candidates = []
        for char, code in (("₹", "INR"), ("€", "EUR"), ("£", "GBP"), ("¥", "JPY"), ("$", "USD")):
            count = window.count(char)
            if count:
                symbol_candidates.append((count, code))
        if symbol_candidates:
            # The symbol that actually prices the statements appears most.
            symbol_candidates.sort(reverse=True)
            currency = symbol_candidates[0][1]

    return currency, unit


def infer_period_end(text: str, fiscal_year: int | None = None) -> "date | None":
    """Infer the fiscal period end from cover-page phrasing.

    Prefers 'year ended Y' phrasing, then an explicit 'X to Y' range, then
    the most frequent YYYYMMDD XBRL context date. No company knowledge."""
    from datetime import date as _date, datetime as _dt
    from collections import Counter

    if not text:
        return None
    sample = text[:8000]
    candidates: list[_date] = []

    range_re = re.compile(
        r"(?:\d{1,2}/\d{1,2}/\d{4}|(?:January|February|March|April|May|June|July|"
        r"August|September|October|November|December)\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})"
        r"\s*(?:to|through|till|–|—|-)\s*"
        r"(\d{1,2}/\d{1,2}/\d{4}|(?:January|February|March|April|May|June|July|"
        r"August|September|October|November|December)\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})",
        re.IGNORECASE,
    )
    ended_re = re.compile(
        r"(?:fiscal\s+)?year(?:s)?\s+ended?\s+"
        r"(\d{1,2}/\d{1,2}/\d{4}|(?:January|February|March|April|May|June|July|"
        r"August|September|October|November|December)\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})",
        re.IGNORECASE,
    )

    def _parse(token: str) -> _date | None:
        token = token.strip()
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", token)
        if m:
            try:
                return _date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                return None
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", token)
        if m:
            try:
                return _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
        try:
            return _dt.strptime(token.replace(",", ""), "%B %d %Y").date()
        except ValueError:
            return None

    for match in re.finditer(r"year(?:s)?\s+ended?\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})", sample, re.I):
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                candidates.append(_dt.strptime(match.group(1), fmt).date())
                break
            except ValueError:
                pass
    for match in ended_re.finditer(sample):
        parsed = _parse(match.group(1))
        if parsed:
            candidates.append(parsed)
    if not candidates:
        for match in range_re.finditer(sample):
            parsed = _parse(match.group(1))
            if parsed:
                candidates.append(parsed)
    if not candidates:
        # XBRL context ids and file names embed dates like 20241231.
        context_dates = re.findall(r"(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])", sample)
        if context_dates:
            common = Counter("/".join(d) for d in context_dates).most_common()
            y, mo, d = common[0][0].split("/")
            candidates.append(_date(int(y), int(mo), int(d)))

    if not candidates:
        return None
    if fiscal_year is not None:
        in_year = [d for d in candidates if d.year == fiscal_year]
        if in_year:
            return max(in_year)
    return max(candidates)


def parse_reported_number(token: str | None) -> float | None:
    """Parse '204,933', '(5.57)', '-14,640.5', '₹ 2,049.3 crore' number part."""
    if token is None:
        return None
    text = str(token).strip()
    if not text:
        return None
    text = re.sub(r"^(?:[₹$€£¥]|INR|USD|EUR|GBP|Rs\.?)\s*", "", text, flags=re.I)
    text = re.sub(r"\s*(?:crores?|lakhs?|millions?|billions?|thousands?)$", "", text, flags=re.I).strip()
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    if not re.fullmatch(r"[+-]?\d[\d,]*(?:\.\d+)?", text):
        return None
    value = _to_float(text)
    return -abs(value) if negative and value is not None else value


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except (TypeError, ValueError):
        return None
