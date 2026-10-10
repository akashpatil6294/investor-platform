"""Deterministic financial facts, currency-aware.

All stored values are the raw reported numbers; ``currency`` and ``unit``
on the row say what they mean. USD-million rows keep the legacy $ formatting.
"""

import math
import re
from statistics import median

from ingestion.units import currency_symbol, normalize_unit, unit_multiplier

FIELDS = ('revenue', 'net_income', 'operating_income', 'cash_flow', 'total_assets', 'total_liabilities')

UNIT_LABELS = {
    'million': 'M', 'thousand': 'K', 'billion': 'B', 'trillion': 'T',
    'crore': ' crore', 'lakh': ' lakh', 'unit': '',
}


def canonical_company(value):
    """Canonical stored company name, resolved from the company_aliases table
    (see database.aliases). Unmapped or offline → the input unchanged."""
    from database.aliases import canonical_company as _db_canonical

    return _db_canonical(value)


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def money(value):
    value = number(value)
    return 'Not reported' if value is None else f'${value:,.0f}M (${value / 1000:,.1f}B)'


def format_reported(value, currency=None, unit=None):
    """Format a stored reported value with its currency and unit."""
    value = number(value)
    if value is None:
        return 'Not reported'
    unit = normalize_unit(unit)
    symbol = currency_symbol(currency)
    code = (currency or '').upper()
    prefix = symbol or (f'{code} ' if code else 'Currency unconfirmed ')
    if unit == 'million':
        label = 'M'
        return f'{prefix}{value:,.0f}{label}'
    if unit == 'billion':
        return f'{prefix}{value:,.1f}B'
    if unit == 'crore':
        return f'{prefix}{value:,.0f} crore'
    if unit == 'lakh':
        return f'{prefix}{value:,.0f} lakh'
    if unit == 'thousand':
        return f'{prefix}{value:,.0f}K'
    return f'{prefix}{value:,.0f}'


def normalized_value(row, field):
    """Value converted to base currency units (value × unit multiplier)."""
    raw = number(row.get(field))
    if raw is None:
        return None
    return raw * unit_multiplier(row.get('unit'))


def format_full(row, field):
    """The detail line: reported value plus derived equivalents when useful."""
    value = number(row.get(field))
    if value is None:
        return 'Not reported'
    currency = row.get('currency')
    unit = normalize_unit(row.get('unit'))
    reported = format_reported(value, currency, unit)
    multiplier = unit_multiplier(unit)
    detail = []
    if unit and multiplier not in (1,):
        base = value * multiplier
        detail.append(f'{currency_symbol(currency) or (currency or "")}{base:,.0f}')
    if (currency or 'USD').upper() == 'USD' and unit == 'million':
        detail.append(f'${value / 1000:,.1f}B')
    return f'{reported} ({detail[-1]})' if detail else reported


def normalize_money_text(value):
    value = re.sub(r'\\u(?:202f|00a0)', ' ', value)
    return re.sub(r'\$([\d,.]+)\s*(million|billion)\b',
                  lambda m: money(float(m[1].replace(',','')) * (1000 if m[2]=='billion' else 1)), value, flags=re.I)


def ratio(a, b):
    a, b = number(a), number(b)
    return a / b if a is not None and b not in (None, 0) else None

def ratios(row):
    return {name: ratio(row.get(a), row.get(b)) for name, a, b in (
        ('operating_margin', 'operating_income', 'revenue'),
        ('net_margin', 'net_income', 'revenue'),
        ('liabilities_to_assets', 'total_liabilities', 'total_assets'),
        ('cash_conversion', 'cash_flow', 'net_income'))}

def enrich(rows):
    result = []
    for row in rows:
        checks = {}
        for field in FIELDS:
            value = number(row.get(field))
            peers = [abs(number(r.get(field))) for r in rows if r.get('year') == row.get('year')
                     and r.get('currency') == row.get('currency') and r.get('unit') == row.get('unit')
                     and r.get('company') != row.get('company') and number(r.get(field)) not in (None, 0)]
            if value is None:
                checks[field] = 'Missing value'
            elif field in ('net_income', 'operating_income') and number(row.get('revenue')) is not None and value > number(row['revenue']):
                checks[field] = 'Income exceeds revenue'
            elif len(peers) >= 2 and abs(value) > 10 * median(peers):
                checks[field] = 'More than 10 times peer median; verify against report'
        result.append({**row, 'derived': ratios(row), 'checks': checks})
    return result


def intent(question):
    q = question.lower()
    if re.search(r'\b(why|explain|risk|drivers?|strategy|outlook)\b', q):
        return 'narrative', None
    for phrase, field in (
        ('operating margin', 'operating_margin'), ('net margin', 'net_margin'),
        ('liabilities-to-assets', 'liabilities_to_assets'), ('liabilities to assets', 'liabilities_to_assets'),
        ('cash conversion', 'cash_conversion'), ('operating income', 'operating_income'),
        ('operating profit', 'operating_income'), ('net income', 'net_income'), ('net profit', 'net_income'),
        ('cash flow', 'cash_flow'), ('assets', 'total_assets'), ('liabilities', 'total_liabilities'),
        ('revenue', 'revenue'), ('sales', 'revenue')):
        if phrase in q:
            kind = 'yoy' if re.search(r'\b(yoy|year.over.year|prior year|last year|change)\b', q) else 'numeric'
            if re.search(r'\b(vs|versus|compare|comparison)\b', q): kind = 'comparison'
            return kind, field
    return 'narrative', None


def format_metric(field, value, row=None):
    if field in FIELDS:
        if row is not None:
            return format_full(row, field)
        return money(value)
    if value is None:
        return 'Not reported'
    return f'{value:.2f}× (derived)' if field == 'cash_conversion' else f'{value * 100:.2f}% (derived)'


def metric_value(row, field):
    return number(row.get(field)) if field in FIELDS else ratios(row).get(field)


def same_currency(a, b):
    return (a or 'USD').upper() == (b or 'USD').upper()


def numeric_answer(question, companies, year, lookup):
    """Exact-period arithmetic; a missing input falls through to report research."""
    kind, field = intent(question)
    if not field or not companies:
        return None
    parts, values = [], []
    for company in companies:
        row = lookup(company, year)
        if not row or row.get('extraction_status') in ('failed', 'needs_review'):
            return None
        value = metric_value(row, field)
        if value is None:
            return None
        if field in FIELDS and (not row.get('currency') or not row.get('unit')):
            return None
        parts.append(f'{company} FY{year} {field.replace("_", " ")}: **{format_metric(field, value, row)}**.'
                     + (f' Period ended {row["period_end"]}.' if row.get('period_end') else ''))
        base = normalized_value(row, field) if field in FIELDS else value
        values.append((company, base, row))
        if kind == 'yoy':
            prior = lookup(company, year-1)
            if not prior or prior.get('currency') != row.get('currency') or prior.get('basis') != row.get('basis'):
                return None
            old = normalized_value(prior, field) if field in FIELDS else metric_value(prior, field)
            if old in (None, 0):
                return None
            parts.append(f'FY{year-1}: {format_metric(field, metric_value(prior, field), prior)}. '
                         f'Year-over-year change: {(base-old)/abs(old)*100:+.2f}% (derived).')
    if len(values) == 2:
        (a, av, ar), (b, bv, br) = values
        if ar.get('currency') != br.get('currency') or ar.get('basis') != br.get('basis'):
            parts.append('Different currencies or reporting bases; no ranking or conversion applied.')
        else:
            delta = (format_reported(abs(av-bv)/unit_multiplier(ar.get('unit')), ar.get('currency'), ar.get('unit'))
                     if field in FIELDS else f'{abs(av-bv)*100:.2f} percentage points')
            parts.append(f'{a} is {delta} {"above" if av >= bv else "below"} {b} (derived).')
    return '\n\n'.join(parts)


def resolve_row(lookup, company, year):
    return lookup(company, year), year
