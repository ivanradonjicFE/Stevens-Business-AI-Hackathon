"""Render an alert Markdown file (from run.py) as a PDF.

    python to_pdf.py out/alert-20260925-1420.md      # -> out/alert-20260925-1420.pdf
    python to_pdf.py                                  # latest alert in out/
"""
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

INK, MUTED, RULE, HEAD_BG = colors.HexColor("#1b1f24"), colors.HexColor("#5b6470"), colors.HexColor("#d5dae0"), colors.HexColor("#f1f3f5")
LEVEL_COLOR = {"WARNING": "#c62828", "WATCH": "#d97706", "ADVISORY": "#5b6470"}
NEG, POS = "#c62828", "#2e7d32"

ss = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("t", parent=ss["Title"], fontSize=17, leading=21, textColor=INK, alignment=TA_LEFT, spaceAfter=6),
    "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12.5, leading=16, textColor=INK, spaceBefore=14, spaceAfter=4),
    "h3": ParagraphStyle("h3", parent=ss["Heading4"], fontSize=9.5, leading=12, textColor=INK, spaceBefore=8, spaceAfter=3),
    "body": ParagraphStyle("b", parent=ss["BodyText"], fontSize=8.8, leading=12, textColor=INK),
    "meta": ParagraphStyle("m", parent=ss["BodyText"], fontSize=8.2, leading=11, textColor=MUTED),
    "bullet": ParagraphStyle("bl", parent=ss["BodyText"], fontSize=8.5, leading=11.5, leftIndent=10, bulletIndent=0, textColor=INK),
    "audit": ParagraphStyle("a", parent=ss["BodyText"], fontSize=7.6, leading=10, leftIndent=10, bulletIndent=0, textColor=MUTED),
    "cell": ParagraphStyle("c", parent=ss["BodyText"], fontSize=7.6, leading=9.6, textColor=INK),
    "cellh": ParagraphStyle("ch", parent=ss["BodyText"], fontSize=7.6, leading=9.6, textColor=INK, fontName="Helvetica-Bold"),
}


def inline(text: str, color_pct: bool = False) -> str:
    t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(https?://[^\s|)]+)", lambda m: f'<link href="{m[1]}" color="#1f5fbf">{m[1] if len(m[1]) < 70 else m[1][:67] + "..."}</link>', t)
    if color_pct:
        t = re.sub(r"([+-]\d+\.\d%)", lambda m: f'<font color="{NEG if m[1][0] == "-" else POS}">{m[1]}</font>', t)
    return t


def table(rows: list[list[str]], width: float) -> Table:
    ncol = len(rows[0])
    # Precedent tables have a long last column; reaction tables are label + numbers
    widths = {7: [1.0, 0.82, 0.46, 0.5, 0.52, 2.15, 1.95], 6: [1.35, 0.72, 0.55, 0.6, 0.62, 3.26]}.get(ncol, [2.0, 1.9, 1.3, 1.3])
    scale = width / sum(widths)
    data = [[Paragraph(inline(c, color_pct=r > 0).replace(" • ", "<br/>• "), S["cellh" if r == 0 else "cell"])
             for c in row] for r, row in enumerate(rows)]
    t = Table(data, colWidths=[w * scale for w in widths], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def build(md_path: Path) -> Path:
    pdf_path = md_path.with_suffix(".pdf")
    doc = SimpleDocTemplate(str(pdf_path), pagesize=letter, leftMargin=0.7 * inch, rightMargin=0.7 * inch,
                            topMargin=0.65 * inch, bottomMargin=0.65 * inch, title=md_path.stem)
    width = doc.width
    story, tbl, in_audit = [], [], False

    def flush_table():
        if tbl:
            story.append(table([r for r in tbl if not set("".join(r)) <= set("-: ")], width))
            story.append(Spacer(1, 4))
            tbl.clear()

    for raw in md_path.read_text().splitlines():
        line = raw.rstrip()
        if line.startswith("|"):
            tbl.append([c.strip() for c in line.strip("|").split("|")])
            continue
        flush_table()
        if not line:
            continue
        if line.startswith("<details>"):
            in_audit = True
            story.append(Paragraph("How we scored this", S["h3"]))
        elif line.startswith("</details>"):
            in_audit = False
        elif line.startswith("# "):
            story += [Paragraph(inline(line[2:]), S["title"]), HRFlowable(width="100%", color=RULE, thickness=0.8)]
        elif line.startswith("## "):
            head = inline(line[3:])
            m = re.match(r"(\d+\.) \[(\w+)\] (.*)", line[3:])
            if m:
                c = LEVEL_COLOR.get(m[2], "#5b6470")
                head = f'{m[1]} <font color="{c}">[{m[2]}]</font> {inline(m[3])}'
            story.append(KeepTogether([Spacer(1, 4), HRFlowable(width="100%", color=RULE, thickness=0.5),
                                       Paragraph(head, S["h2"])]))
        elif line == "---":
            story += [Spacer(1, 8), HRFlowable(width="100%", color=RULE, thickness=0.5), Spacer(1, 4)]
        elif line.startswith("- "):
            story.append(Paragraph(inline(line[2:]), S["audit" if in_audit else "bullet"], bulletText="•"))
        elif re.fullmatch(r"\*\*[^*]+\*\*( \(.*\))?", line):  # section label like **What's exposed**
            story.append(Paragraph(inline(line), S["h3"]))
        elif line.startswith("**Source:**") or line.startswith("**Relevance"):
            story.append(Paragraph(inline(line), S["meta"]))
        else:
            story.append(Paragraph(inline(line, color_pct=False), S["body"]))
    flush_table()

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc_.leftMargin, 0.4 * inch, "Semiconductor supply-chain alert - automated early-warning signal, not investment or underwriting advice")
        canvas.drawRightString(letter[0] - doc_.rightMargin, 0.4 * inch, f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return pdf_path


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else max(Path(__file__).parent.joinpath("out").glob("alert-*.md"))
    print(build(src))
