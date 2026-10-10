"""PDF -> markdown conversion that always preserves page numbers.

Each PDF page becomes its own ``pymupdf4llm`` call and pages are joined with
``<!-- PAGE:n -->`` markers so every extracted value can cite a real page.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf4llm

PAGE_MARKER_RE = re.compile(r"<!--\s*PAGE:(\d+)\s*-->")
# Printed page labels such as "_287_" seen in regulatory filings.
PRINTED_PAGE_RE = re.compile(r"^_([0-9]{1,4})_\s*$", re.MULTILINE)


class PDFToMarkdownConverter:
    """Convert PDF documents to Markdown with per-page markers."""

    def convert_pdf(self, pdf_path: str, output_dir: str, ocr_fn=None, progress=None) -> str:
        pdf_file = Path(pdf_path)
        if not pdf_file.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        pages_text = self.convert_pages(str(pdf_file), ocr_fn=ocr_fn, progress=progress)
        markdown_content = "\n\n".join(
            f"<!-- PAGE:{number} -->\n\n{text.strip()}"
            for number, text in pages_text
        )
        markdown_file = output_path / f"{pdf_file.stem}.md"
        markdown_file.write_text(markdown_content, encoding="utf-8")
        return str(markdown_file)

    def convert_pages(self, pdf_path: str, ocr_fn=None, progress=None) -> list[tuple[int, str]]:
        """Return [(page_number, markdown_text), ...] with 1-based pages.

        ``ocr_fn(page) -> str`` is called for pages whose native text layer is
        empty or negligible (scanned documents)."""
        import fitz

        pdf_file = Path(pdf_path)
        results: list[tuple[int, str]] = []
        with fitz.open(str(pdf_file)) as doc:
            # Parse batches once; per-page calls repeatedly scan document metadata.
            for start in range(0, len(doc), 10):
                batch = pymupdf4llm.to_markdown(doc, pages=list(range(start, min(start+10, len(doc)))),
                                                page_chunks=True, use_ocr=False)
                for offset, chunk in enumerate(batch):
                    index = start + offset
                    page = doc[index]
                    text = chunk.get("text", "")
                    if len(page.get_text().strip()) < 200 and ocr_fn is not None:
                        if progress: progress(f"OCR page {index+1}/{len(doc)} (low text density)")
                        text = ocr_fn(page)
                    results.append((index+1, text or ""))
                if progress: progress(f"Converted {min(start+10,len(doc))}/{len(doc)} pages")
        return results

    def convert_directory(self, input_dir: str, output_dir: str) -> list[str]:
        input_path = Path(input_dir)
        markdown_files = []
        for pdf_file in input_path.glob("*.pdf"):
            markdown_files.append(
                self.convert_pdf(pdf_path=str(pdf_file), output_dir=output_dir)
            )
        return markdown_files


def split_markdown_pages(markdown: str) -> list[tuple[int, str]]:
    """Split markdown into (page_number, text).

    Uses ``<!-- PAGE:n -->`` markers when present. Older files are split on
    printed page labels (``_12_``) as a fallback; a file with neither is one
    unnamed page (page numbers stay unknown rather than invented)."""
    if PAGE_MARKER_RE.search(markdown):
        parts = PAGE_MARKER_RE.split(markdown)
        pages = []
        for i in range(1, len(parts), 2):
            number = int(parts[i])
            text = parts[i + 1] if i + 1 < len(parts) else ""
            if text.strip():
                pages.append((number, text.strip()))
        return pages

    labels = list(PRINTED_PAGE_RE.finditer(markdown))
    if labels:
        pages = []
        start = 0
        for match in labels:
            pages.append((int(match.group(1)), markdown[start:match.start()].strip()))
            start = match.end()
        tail = markdown[start:].strip()
        if tail:
            pages.append((pages[-1][0] + 1 if pages else 1, tail))
        pages = [(n, t) for n, t in pages if t]
        return pages

    return [(1, markdown.strip())] if markdown.strip() else []
