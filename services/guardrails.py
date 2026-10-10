"""Chat guardrails: pre-checks before any retrieval or model call, and
post-checks on every model answer before it reaches the user.

Pre-checks (deterministic, no model call):
  - scope classification: companies, filings, financial statements, KPIs,
    risks, strategy, finance-term definitions, and questions about this app
    are in scope; everything else gets a short polite decline;
  - prompt-injection handling: user text is data; pure injection attempts are
    declined without a model call, embedded attempts are stripped;
  - no personalised investment advice: buy/sell/price-target questions get an
    explanation and an offer of the factual data instead.

Post-checks:
  - every financial number in a RAG answer must appear in the supplied
    metrics or excerpts (formatting-normalised); unverifiable sentences are
    removed, and an answer left empty becomes the honest could-not-verify;
  - answers must not name a different company than the one asked about;
  - web claims need at least one http(s) source URL;
  - contact details are redacted unless the user asked for a company's
    public investor contact;
  - HTML/script content is stripped and answers are length-capped.
"""

from __future__ import annotations

import os
import re

# ------------------------------------------------------------------
# Pre-check: scope classification
# ------------------------------------------------------------------

_IN_SCOPE_RE = re.compile(
    r"\b(revenue|sales|turnover|income|profit|loss|earnings|margin|assets?|"
    r"liabilit(?:y|ies)|equity|cash ?flow|balance ?sheet|income statement|"
    r"statement of (profit|financial|operations|cash)|fiscal|financial year|"
    r"fiscal year|annual report|10-?k|annual|report|filing|filings|auditor|"
    r"audit|kpi|metrics?|dividend|debt|borrowing|capex|ebitda|ebit|"
    r"crores?|lakhs?|millions?|billions?|rupees?|dollars?|currency|inr|usd|eur|gbp|"
    r"risks?|risk factors?|growth drivers?|drivers?|strategy|outlook|guidance|"
    r"market share|competition|competitors?|segment|subsidiar(?:y|ies)|"
    r"year over year|yoy|compare|comparison|vs\.?|versus|trend|"
    r"operating (income|profit|margin|cash)|net (income|profit|margin)|gross margin|"
    r"investor relations?|research and development|annual general meeting|"
    r"chairman|ceo|cfo|statutory|unqualified|deferred|provision|"
    r"companies?|company|firm|business|industry|sector)\b",
    re.IGNORECASE,
)

_APP_USAGE_RE = re.compile(
    r"\b(upload|ingest|dashboard|chat|app|application|this (tool|site|platform)|"
    r"how (do|can) i|where (do|can) i|which (compan|year|report)|what (compan|year|report)|"
    r"help|export|csv|dark mode|theme|delete|replace|re-?extract)\b",
    re.IGNORECASE,
)

_OFF_TOPIC_RE = re.compile(
    r"\b(python|javascript|java\b|c\+\+|sql\b|code|coding|program|debug|stack ?trace|"
    r"api key|homework|essay|poem|story|joke|song|lyrics|recipe|cook|weather|"
    r"election|president|prime minister|parliament|political|party\b|vote|"
    r"symptom|disease|medicine|medication|doctor|diagnos|treatment|"
    r"lawyer|lawsuit|sue\b|legal advice|draft a contract|"
    r"movie|film|netflix|game|gaming|football|cricket match|travel|flight|hotel)\b",
    re.IGNORECASE,
)

_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|namaste|greetings|yo)\b[\s!.,]*$",
    re.IGNORECASE,
)

_DECLINE = (
    "I'm a financial research assistant, so I can only help with companies, "
    "annual reports, financial statements and the numbers in them. "
    "Try something like \u201cWhat was Apple's operating margin in FY2024?\u201d or "
    "\u201cWhat risks does Flipkart identify in its latest report?\u201d"
)

_ADVICE_PHRASES = re.compile(
    r"\b(should i (buy|sell|hold|invest)|buy|sell or hold|buy or sell|"
    r"(buy|sell|hold) (recommendation|rating|call)|price target|target price|"
    r"will (?:the |my )?\w+ (?:stock|shares?|share price|price) (go|going|rise|fall|drop|recover|grow)|"
    r"will (?:the )?(?:stock|share|share price|price|it) (go|going|rise|fall|grow|drop)|"
    r"stock (go|going) (up|down)|share (go|going) (up|down)|"
    r"invest in|investment (advice|idea|opportunity)|good investment|"
    r"worth (investing|buying)|undervalued|overvalued|bullish|bearish|strong buy)\b",
    re.IGNORECASE,
)
_ADVICE_DECLINE = (
    "I report what companies disclosed \u2014 I don't give buy/sell/hold "
    "recommendations or price targets, and I can't predict share prices. "
    "I can show you the reported figures instead: revenue, margins, "
    "cash flow, assets and liabilities, or the risks and growth drivers "
    "named in the annual report. Which would help?"
)

_INJECTION_RE = re.compile(
    r"(ignore (all|any|the|your|previous|prior|above)|disregard (all|any|the|your|previous|prior)|"
    r"forget (all|any|your|the) (previous|prior|above)|"
    r"(reveal|show|print|repeat|give me) (me )?(your|the) (system prompt|instructions|prompt|"
    r"secret|api key|keys|password|credentials|environment|env var)|"
    r"you are now|from now on you are|pretend to be|role-?play as|act as (a|an|my)|"
    r"developer mode|jailbreak|dan mode|do anything now|override (your|the) (rules|instructions)|"
    r"new (system )?prompt|updated instructions)",
    re.IGNORECASE,
)


class PreCheck:
    """Outcome of the pre-check: allow, decline (with text), or sanitise."""

    def __init__(self, action: str, reply: str | None = None, clean_question: str | None = None):
        self.action = action  # 'allow' | 'decline' | 'advice_refusal' | 'sanitize'
        self.reply = reply
        self.clean_question = clean_question


def _looks_like_followup(question: str, history: list) -> bool:
    if not history:
        return False
    words = len(question.split())
    return words <= 4 or bool(re.match(r"^(and|what about|how about|why|it|that|tell me more)\b", question, re.I))


def pre_check(question: str, history: list | None = None) -> PreCheck:
    """Run the deterministic pre-checks. Never calls a model."""
    text = (question or "").strip()
    if not text:
        return PreCheck("decline", "Please ask a question about an annual report or a stored figure.")

    if _GREETING_RE.match(text):
        return PreCheck("allow", clean_question=text)

    if _ADVICE_PHRASES.search(text):
        # Requests for recommendations get the refusal regardless of other
        # finance nouns; educational ratio questions never match this list.
        return PreCheck("advice_refusal", _ADVICE_DECLINE)

    if _IN_SCOPE_RE.search(text) or _APP_USAGE_RE.search(text):
        clean, injected = sanitize_user_text(text)
        if injected and not _IN_SCOPE_RE.search(clean) and not _APP_USAGE_RE.search(clean):
            return PreCheck("decline", _DECLINE)
        return PreCheck("sanitize" if injected else "allow", clean_question=clean)

    if _OFF_TOPIC_RE.search(text):
        return PreCheck("decline", _DECLINE)

    if _INJECTION_RE.search(text):
        return PreCheck("decline", _DECLINE)

    # Ambiguous, no signals either way: allow short follow-ups to an existing
    # conversation; decline everything else rather than risk a made-up answer.
    if _looks_like_followup(text, history or []):
        clean, injected = sanitize_user_text(text)
        if injected:
            return PreCheck("decline", _DECLINE)
        return PreCheck("allow", clean_question=clean)
    if re.search(r"\?", text) and len(text.split()) <= 6:
        return PreCheck("decline", _DECLINE)
    return PreCheck("decline", _DECLINE)


def sanitize_user_text(text: str) -> tuple[str, bool]:
    """Neutralise injection attempts in a kept question.

    Sentences whose purpose is to override the assistant are removed; the
    rest of the question survives. Returns (clean_text, was_modified)."""
    if not _INJECTION_RE.search(text):
        return text, False
    sentences = re.split(r"(?<=[.!?\n])\s+", text)
    kept = [s for s in sentences if not _INJECTION_RE.search(s)]
    if not kept:
        return "", True
    return " ".join(kept), True


# ------------------------------------------------------------------
# Post-checks on answers
# ------------------------------------------------------------------

_NUMBER_TOKEN_RE = re.compile(r"-?\(?\d[\d,]*(?:\.\d+)?\)?")
_UNIT_SUFFIX_RE = re.compile(r"\s*(crores?|lakhs?|lacs?|millions?|billions?|thousands?|bn|mn|k)\.?$", re.IGNORECASE)
_CURRENCY_PREFIX_RE = re.compile(r"^[₹$€£¥]|^(?:inr|usd|eur|gbp|jpy|rs\.?)\s*", re.IGNORECASE)
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_CITATION_RE = re.compile(r"^\[?\d+\]?$")

# Words that legitimately introduce a comparative (prior-year) figure.
_COMPARATIVE_RE = re.compile(r"\b(from|vs\.?|versus|compared|prior|previous|earlier|up from|down from|against)\b", re.IGNORECASE)

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d\-() ]{8,}\d)\b")
_HTML_RE = re.compile(r"<[^>]+>|<script[\s\S]*?</script>|javascript:", re.IGNORECASE)

MAX_ANSWER_CHARS = 8000


def _normalise_number(token: str) -> list[float]:
    """All plausible numeric values of a printed token.

    '12,400' → [12400]; '(5.57)' → [-5.57]; '₹2,049.3 crore' handled by the
    caller stripping the suffix first; returns both the bare value and the
    value with the common lakh/crore/million expansions when a unit follows."""
    text = token.strip()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = _CURRENCY_PREFIX_RE.sub("", text).replace(",", "").strip()
    try:
        value = float(text)
    except ValueError:
        return []
    return [-value] if negative else [value]


def _reference_numbers(sources: list[dict], metric_values: list) -> set[float]:
    """Every number the answer is allowed to use: metric values (reported and
    base-unit forms), and all numbers in the supplied excerpts."""
    allowed: set[float] = set()

    def add(token: str) -> None:
        for value in _normalise_number(token):
            allowed.add(round(value, 4))
            allowed.add(round(abs(value), 4))

    for value in metric_values:
        if value is None:
            continue
        add(str(value))

    for source in sources or []:
        for key in ("snippet", "content", "quote", "answer"):
            text = source.get(key)
            if not text:
                continue
            cleaned = _UNIT_SUFFIX_RE.sub(" ", text)
            for match in _NUMBER_TOKEN_RE.finditer(cleaned):
                add(match.group(0))
    return allowed


def app_usage_only(question: str) -> bool:
    """A question purely about using the app (no finance nouns) — canned help."""
    return bool(_APP_USAGE_RE.search(question)) and not bool(_IN_SCOPE_RE.search(question))


def _sentence_numbers(sentence: str) -> list[str]:
    tokens = []
    for match in _NUMBER_TOKEN_RE.finditer(sentence):
        token = match.group(0)
        if _YEAR_RE.match(token) or _CITATION_RE.match(token):
            continue
        bare = re.sub(r"[^\d.\-]", "", token.replace(",", ""))
        try:
            value = float(bare)
        except ValueError:
            continue
        # Small plain integers are prose ("3 risks", "page 12"); financial
        # figures are >= 1,000, formatted with separators, or decimal.
        if value < 1000 and "," not in token and "." not in token:
            continue
        tokens.append(token)
    return tokens


def verify_numbers(answer: str, sources: list[dict], metric_values: list) -> tuple[str, list[str]]:
    """Remove sentences with numbers that cannot be matched to the evidence."""
    allowed = _reference_numbers(sources, metric_values)
    removed = []
    kept_sentences = []
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\u2018\u201c(\[])", answer):
        numbers = _sentence_numbers(sentence)
        if not numbers:
            kept_sentences.append(sentence)
            continue
        comparative = bool(_COMPARATIVE_RE.search(sentence))
        verified = True
        for token in numbers:
            candidates = _normalise_number(token)
            if not candidates:
                continue
            if not any(round(abs(c), 4) in allowed or round(c, 4) in allowed for c in candidates):
                if comparative and any(abs(c) in allowed for c in candidates):
                    continue
                verified = False
                break
        if verified:
            kept_sentences.append(sentence)
        else:
            removed.append(sentence.strip()[:120])
    result = " ".join(s.strip() for s in kept_sentences if s.strip())
    return result, removed


def check_company_scope(answer: str, asked_company: str | None, known_companies: list[str]) -> tuple[str, str | None]:
    """Drop sentences that attribute figures to a different company."""
    if not asked_company:
        return answer, None
    asked = asked_company.casefold()
    wrong = [c for c in known_companies
             if c.casefold() != asked and re.search(r"(?<!\w)" + re.escape(c) + r"(?!\w)", answer, re.I)]
    if not wrong:
        return answer, None
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", answer):
        if any(re.search(r"(?<!\w)" + re.escape(c) + r"(?!\w)", sentence, re.I) for c in wrong):
            continue
        kept.append(sentence)
    result = " ".join(k for k in kept if k.strip())
    return result, wrong[0]


def scrub_contacts(answer: str, allow_investor_contact: bool) -> str:
    if allow_investor_contact:
        return answer
    answer = _EMAIL_RE.sub("[redacted email]", answer)
    return _PHONE_RE.sub("[redacted number]", answer)


def strip_and_cap(answer: str) -> str:
    answer = _HTML_RE.sub(" ", answer)
    answer = re.sub(r"\s{3,}", "  ", answer).strip()
    if len(answer) > MAX_ANSWER_CHARS:
        answer = answer[:MAX_ANSWER_CHARS].rsplit(" ", 1)[0] + " \u2026"
    return answer


def enforce_web_sources(answer: str, sources: list[dict]) -> tuple[bool, str | None]:
    """A web-tier answer must have at least one http(s) source with a URL."""
    urls = [s.get("url") for s in (sources or []) if s.get("url")]
    if not any(isinstance(u, str) and u.startswith(("http://", "https://")) for u in urls):
        return False, "The web result had no verifiable source link, so it was dropped."
    return True, None
