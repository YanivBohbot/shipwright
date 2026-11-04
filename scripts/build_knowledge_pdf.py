"""Render knowledge/specs/EXPORT-CSV-v2.md to a PDF (offline, fpdf2).

The PDF exists so that ingestion exercises the LlamaParse path. fpdf2 core fonts are latin-1
only, so text is transliterated/replaced before rendering.
"""

from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "knowledge" / "specs" / "EXPORT-CSV-v2.md"
TARGET = ROOT / "knowledge" / "specs" / "EXPORT-CSV-v2.pdf"

_TRANSLATE = str.maketrans(
    {
        "–": "-",
        "—": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
        "→": "->",
        " ": " ",
    }
)


def latin1_safe(text: str) -> str:
    """Make text encodable with the latin-1 core fonts; unknown characters become '?'."""
    return text.translate(_TRANSLATE).encode("latin-1", errors="replace").decode("latin-1")


def markdown_to_pdf(markdown: str) -> FPDF:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    in_code = False
    for raw in markdown.splitlines():
        line = latin1_safe(raw.rstrip())
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            pdf.set_font("Courier", size=9)
            pdf.multi_cell(0, 5, line or " ", new_x="LMARGIN", new_y="NEXT")
        elif line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            pdf.set_font("Helvetica", style="B", size={1: 18, 2: 14}.get(level, 12))
            pdf.ln(2)
            pdf.multi_cell(0, 8, line.lstrip("#").strip(), new_x="LMARGIN", new_y="NEXT")
        elif not line:
            pdf.ln(3)
        else:
            pdf.set_font("Helvetica", size=11)
            pdf.multi_cell(0, 6, line.replace("`", ""), new_x="LMARGIN", new_y="NEXT")
    return pdf


def main() -> None:
    pdf = markdown_to_pdf(SOURCE.read_text(encoding="utf-8"))
    pdf.output(str(TARGET))
    print(f"Wrote {TARGET}")


if __name__ == "__main__":
    main()
