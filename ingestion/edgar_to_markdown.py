"""
Fetch a company's latest 10-K from SEC EDGAR (HTML) and
convert it to Markdown.

EDGAR filings are always HTML, never image-based, so this
works for any public company regardless of how their own
IR page renders PDFs.

Usage:
    python edgar_to_markdown.py <CIK> <year> <CompanyName>
Example:
    python edgar_to_markdown.py 0001018724 2024 Amazon
"""

import sys
import re
import json
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import html2text


# SEC requires a User-Agent with contact info
USER_AGENT = "InvestorPlatform/1.0 (contact@example.com)"

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "data" / "markdown"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

EDGAR_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_BASE = "https://www.sec.gov"


def fetch_submissions(cik: str) -> dict:
    cik_padded = cik.zfill(10)
    url = EDGAR_SUBMISSIONS.format(cik=cik_padded)
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.json()


def find_10k(submissions: dict, year: int) -> dict | None:
    """Find the most recent 10-K whose report period is in `year`."""
    recent = submissions.get("filings", {}).get("recent", {})

    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])

    matches = []
    for i, form in enumerate(forms):
        if form != "10-K":
            continue
        report = report_dates[i] if i < len(report_dates) else ""
        if report.startswith(str(year)):
            matches.append({
                "accession": accessions[i].replace("-", ""),
                "primary": primary_docs[i],
                "filed": filing_dates[i],
                "report": report,
            })

    if not matches:
        return None
    matches.sort(key=lambda m: m["filed"], reverse=True)
    return matches[0]


def download_filing_html(cik: str, filing: dict) -> str:
    cik_stripped = cik.lstrip("0") or cik
    url = (
        f"{EDGAR_BASE}/Archives/edgar/data/{cik_stripped}/"
        f"{filing['accession']}/{filing['primary']}"
    )
    print(f"Fetching {url}")
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    return r.text


def html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    # Drop scripts, styles, hidden inputs
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    h = html2text.HTML2Text()
    h.ignore_links = True
    h.ignore_images = True
    h.body_width = 0  # no wrapping

    md = h.handle(str(soup))
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)

    cik, year, company = sys.argv[1], int(sys.argv[2]), sys.argv[3]

    print(f"CIK       : {cik}")
    print(f"Year      : {year}")
    print(f"Company   : {company}")

    subs = fetch_submissions(cik)
    filing = find_10k(subs, year)

    if not filing:
        print(f"No 10-K filing found for {year}")
        sys.exit(1)

    print(f"Filing    : {filing['accession']} (filed {filing['filed']})")

    html = download_filing_html(cik, filing)
    print(f"HTML size : {len(html):,} chars")

    md = html_to_markdown(html)
    print(f"Markdown  : {len(md):,} chars")

    if len(md) < 50000:
        print("WARNING: markdown is smaller than expected. Inspect output.")

    out = OUTPUT_DIR / f"{year}_{company}.md"
    out.write_text(md, encoding="utf-8")
    print(f"Saved to  : {out}")


if __name__ == "__main__":
    main()