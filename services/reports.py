"""One explicit identity manifest for known reports; years are fiscal years."""
from pathlib import Path
from services.financial import canonical_company

ROOT = Path(__file__).resolve().parents[1]
REPORTS = {
    '2024_Apple.pdf': ('Apple', 2024),
    '2024_Microsoft.pdf': ('Microsoft', 2024),
    '2024_Tesla.pdf': ('Tesla', 2024),
    '2024_Amazon.pdf': ('Amazon', 2024),
    '2025_AMZN.pdf': ('Amazon', 2025),
}

def identity(filename):
    if filename in REPORTS: return REPORTS[filename]
    stem = Path(filename).stem
    year, company = stem.split('_', 1)
    return canonical_company(company), int(year)

def source_path(name):
    # No arbitrary paths or symlinks outside the report directories.
    if Path(name).name != name or '/' in name or '\\' in name: return None
    for directory in ('raw_pdfs', 'markdown'):
        base = (ROOT / 'data' / directory).resolve()
        candidate = (base / name).resolve()
        if candidate.parent == base and candidate.is_file() and candidate.suffix.lower() in ('.pdf','.md'):
            return candidate
    return None
