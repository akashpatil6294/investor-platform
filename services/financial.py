"""Deterministic financial facts. All monetary inputs are USD millions."""
import math
import re
from statistics import median

FIELDS = ('revenue', 'net_income', 'operating_income', 'cash_flow', 'total_assets', 'total_liabilities')
ALIASES = {'aapl': 'Apple', 'amzn': 'Amazon', 'msft': 'Microsoft', 'tsla': 'Tesla',
           'apple': 'Apple', 'amazon': 'Amazon', 'microsoft': 'Microsoft', 'tesla': 'Tesla'}

def canonical_company(value):
    return ALIASES.get((value or '').strip().lower(), (value or '').strip())

def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None

def money(value):
    value = number(value)
    return 'Not reported' if value is None else f'${value:,.0f}M (${value / 1000:,.1f}B)'

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

def format_metric(field, value):
    if field in FIELDS: return money(value)
    if value is None: return 'Not reported'
    return f'{value:.2f}× (derived)' if field == 'cash_conversion' else f'{value * 100:.2f}% (derived)'

def metric_value(row, field):
    return number(row.get(field)) if field in FIELDS else ratios(row).get(field)

def numeric_answer(question, companies, year, lookup):
    kind, field = intent(question)
    if not field: return None
    parts, values = [], []
    for company in companies:
        row = lookup(company, year)
        if not row:
            parts.append(f'No stored metrics for {company}, FY{year}.')
            continue
        value = metric_value(row, field)
        parts.append(f'{company} FY{year} {field.replace("_", " ")}: **{format_metric(field, value)}**.')
        if value is not None: values.append((company, value))
        if kind == 'yoy':
            prior = lookup(company, year - 1)
            old = metric_value(prior, field) if prior else None
            if old is None or old == 0 or value is None:
                parts.append(f'FY{year - 1} comparable data is missing or zero; YoY cannot be calculated.')
            else:
                change = (value - old) / abs(old) * 100
                parts.append(f'FY{year - 1}: {format_metric(field, old)}; YoY **{change:+.2f}%** (derived).')
    if len(values) == 2:
        (a, av), (b, bv) = values
        delta = money(abs(av - bv)) if field in FIELDS else (f'{abs(av-bv):.2f}×' if field == 'cash_conversion' else f'{abs(av-bv)*100:.2f} percentage points')
        parts.append(f'{a} is {delta} {"above" if av >= bv else "below"} {b} (derived).')
    return '\n\n'.join(parts) if parts else None
