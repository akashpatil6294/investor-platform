"""Fixture PDFs for ingestion tests: a small valid annual report ("Northwind
Ltd", INR crore, fiscal year ending 2026-03-31) with native text, plus helpers
for encrypted and scanned variants."""

from __future__ import annotations

from pathlib import Path

STATEMENT_ROWS = [
    ("Particulars", "2026", "2025"),
    ("INCOME STATEMENT", "", ""),
    ("Revenue from operations", "12,400", "10,850"),
    ("Other income", "180", "150"),
    ("Total income", "12,580", "11,000"),
    ("Total expenses", "11,010", "9,780"),
    ("Finance costs", "310", "280"),
    ("Profit for the year", "1,250", "1,020"),
    ("BALANCE SHEET", "", ""),
    ("Total assets", "18,300", "16,900"),
    ("Total equity", "6,850", "6,300"),
    ("Total liabilities", "11,450", "10,600"),
    ("CASH FLOW STATEMENT", "", ""),
    ("Net cash flows from operating activities", "2,150", "1,880"),
    ("Net cash flows from investing activities", "(900)", "(760)"),
]


def build_northwind_pdf(path: Path) -> Path:
    """Two pages of native text: cover + consolidated statements."""
    import fitz

    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_textbox(fitz.Rect(50, 60, 545, 780), (
        "NORTHWIND LIMITED\n"
        "Annual Report 2025-26\n\n"
        "Registered office: 12 Harbour Road, Mumbai 400001, India\n"
        "Corporate Identity Number: U99999MH2026PLC000001\n\n"
        "For the financial year ended March 31, 2026\n\n"
        "All amounts are in Rs. Crore unless stated otherwise.\n"
        "This cover page and the notice of the annual general meeting are not "
        "part of the financial statements. The audit committee reviewed the "
        "draft financial statements in its meeting held in May 2026 and "
        "recommended them to the board for approval."
    ), fontsize=11)

    p2 = doc.new_page()
    p2.insert_text((50, 60), "Consolidated Financial Statements", fontsize=13)
    p2.insert_text((50, 80), "Year ended March 31, 2026, with comparatives for March 31, 2025", fontsize=9)
    y = 110
    for label, c1, c2 in STATEMENT_ROWS:
        p2.insert_text((50, y), label, fontsize=9)
        if c1:
            p2.insert_text((330, y), c1, fontsize=9)
            p2.insert_text((440, y), c2, fontsize=9)
        y += 16
    p2.insert_textbox(fitz.Rect(50, y + 10, 545, 780), (
        "Notes to the accounts form an integral part of the consolidated "
        "financial statements and should be read together with them. The "
        "consolidated results include the figures of the subsidiary and the "
        "associate accounted for under the equity method."
    ), fontsize=9)

    doc.save(str(path))
    doc.close()
    return path


def build_encrypted_pdf(path: Path) -> Path:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(50, 60, 545, 700), "Secret annual report contents " * 20, fontsize=11)
    doc.save(str(path), encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="ownerpw", user_pw="userpw")
    doc.close()
    return path


def build_scanned_pdf(path: Path) -> Path:
    """A page whose text layer is far below the OCR threshold."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 100), "scanned page 1", fontsize=9)
    doc.save(str(path))
    doc.close()
    return path
