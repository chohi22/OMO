#!/usr/bin/env python3
"""PDF Text Extractor - Extracts text content from PDF files."""

import re
import sys
import os
from collections import defaultdict

try:
    import fitz  # PyMuPDF
except ImportError:
    print("PyMuPDF not installed. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "PyMuPDF"])
    import fitz


def extract_page_text_dedup(page: fitz.Page) -> str:
    """Extract text from a page, deduplicating overlapping CJK spans.

    Some PDFs (especially Korean/CJK documents exported from word processors)
    encode text as many small overlapping spans across *different blocks*.
    The same character can appear at the end of one block and the start of the
    next block at the exact same x/y position:

        block63 char='3'  x=324~332
        block67 chars='3 이 법은'  x=324~385   ← '3' duplicated
        block72 chars='은 가능한'  x=372~431   ← '은' duplicated
        block87 chars='국기업'      x=470~510   ← '국' duplicated

    Standard get_text('text') outputs every block in full, yielding
    '33 이 법은은 가능한 한 외국국기업'.

    Fix: collect ALL characters across ALL blocks, group by visual line
    (Y coordinate), sort by x, and advance a per-line high-water mark —
    skip any character whose centre-x falls at or before that mark.
    """
    raw = page.get_text('rawdict', flags=fitz.TEXT_INHIBIT_SPACES)

    # Collect every character with its position.
    all_chars: list[tuple[float, float, float, float, str]] = []  # (y_top, x0, cx, x1, c)
    for block in raw.get('blocks', []):
        if block.get('type') != 0:
            continue
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                for ch in span.get('chars', []):
                    c = ch.get('c', '')
                    if not c:
                        continue
                    bbox = ch.get('bbox', (0, 0, 0, 0))
                    cx = (bbox[0] + bbox[2]) / 2  # centre x for overlap check
                    all_chars.append((bbox[1], bbox[0], cx, bbox[2], c))

    if not all_chars:
        return ''

    # Cluster characters into visual lines using Y-tolerance grouping.
    # Characters within Y_TOL points of each other belong to the same line.
    # This avoids the rounding boundary problem where round(100.05,1) != round(100.15,1).
    Y_TOL = 3.0
    all_chars.sort(key=lambda t: t[0])  # sort by y_top
    lines: list[list[tuple[float, float, float, str]]] = []
    current_line: list[tuple[float, float, float, str]] = []
    current_y = all_chars[0][0]
    for y_top, x0, cx, x1, c in all_chars:
        if abs(y_top - current_y) > Y_TOL:
            if current_line:
                lines.append(current_line)
            current_line = []
            current_y = y_top
        current_line.append((x0, cx, x1, c))
    if current_line:
        lines.append(current_line)

    page_parts: list[str] = []
    for line_chars in lines:
        chars = sorted(line_chars, key=lambda t: t[0])  # left-to-right
        line_str = ''
        high_x = -1.0  # rightmost right-edge seen so far on this visual line
        for x0, cx, x1, c in chars:
            if cx <= high_x:
                # Character centre is inside an already-emitted span → duplicate
                continue
            line_str += c
            if x1 > high_x:
                high_x = x1
        if line_str.strip():
            page_parts.append(line_str)

    return '\n'.join(page_parts)


def has_cjk_duplication(text: str) -> bool:
    """Detect systematic CJK character doubling from font encoding bugs."""
    if not text:
        return False
    cjk_chars = re.findall(r'[가-힣]', text)
    total_cjk = len(cjk_chars)
    if total_cjk < 10:
        return False
    particle_doubles = re.findall(r'은은|는는|이이|가가|을을|를를|에에|의의|로로|으으', text)
    if len(particle_doubles) >= 2:
        return True
    pairs = re.findall(r'([가-힣])\1', text)
    ratio = len(pairs) / total_cjk
    return len(pairs) >= 5 and ratio > 0.35


def fix_cjk_duplication(text: str) -> str:
    """Remove systematic CJK character doubling caused by font encoding bugs."""
    if not text:
        return text
    result = []
    i = 0
    while i < len(text):
        ch = text[i]
        if '\uac00' <= ch <= '\ud7a3' or '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf':
            if i + 1 < len(text) and text[i + 1] == ch:
                result.append(ch)
                i += 2
                continue
        result.append(ch)
        i += 1
    return ''.join(result)


def extract_text(pdf_path: str, output_path: str | None = None) -> str:
    """Extract text from a PDF file.

    Args:
        pdf_path: Path to the PDF file.
        output_path: Optional path to save extracted text. If None, prints to stdout.

    Returns:
        Extracted text as a string.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    all_text = []

    print(f"{'='*60}")
    print(f"PDF: {os.path.basename(pdf_path)}")
    print(f"Total pages: {total_pages}")
    print(f"{'='*60}\n")

    for page_num in range(total_pages):
        page = doc[page_num]
        text = extract_page_text_dedup(page)
        if has_cjk_duplication(text):
            text = fix_cjk_duplication(text)
        all_text.append(text)

        print(f"--- Page {page_num + 1}/{total_pages} ---")
        print(text.strip() if text.strip() else "(empty page)")
        print()

    doc.close()

    full_text = "\n".join(all_text)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(full_text)
        print(f"\n{'='*60}")
        print(f"Text saved to: {output_path}")
        print(f"{'='*60}")

    return full_text


def main():
    if len(sys.argv) < 2:
        print("Usage: python pdf_parser.py <pdf_path> [output_path]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    extract_text(pdf_path, output_path)


if __name__ == "__main__":
    main()
