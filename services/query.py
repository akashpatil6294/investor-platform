import re

_FRIENDLY_GREETING = 'Ask about revenue, profitability, cash flow, risks or growth drivers in the annual reports.'

def _is_greeting(question):
    return bool(re.fullmatch(r'\s*(hi|hello|hey|thanks|thank you|help)[!.?]*\s*', question, re.I))

def _expand_short_query(question, company, year):
    normalized = re.sub(r'[^\w\s]', '', question.lower()).strip()
    aliases = {'sales': 'revenue', 'net': 'net income', 'income': 'net income', 'profit': 'net income',
               'earnings': 'net income', 'cash': 'operating cash flow', 'assets': 'total assets',
               'liabilities': 'total liabilities', 'growth': 'growth drivers', 'drivers': 'growth drivers'}
    if len(normalized.split()) < 4:
        topic = aliases.get(normalized, normalized)
        if topic in ('revenue', 'net income', 'operating income', 'cash flow', 'operating cash flow',
                     'total assets', 'total liabilities', 'growth drivers', 'risks', 'risk'):
            return f'What were {topic} for {company or "the company"} in fiscal year {year or "the selected year"}?'
    return question
