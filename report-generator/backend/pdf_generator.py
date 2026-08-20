"""
PDF generation for the GenRep report wrapper.

Turns the raw GenRep agent output (markdown-ish text + collected
artifact images) into a professionally formatted, paginated PDF with
a cover page, headings, tables, code blocks and embedded charts.
"""

import re
from pathlib import Path
from typing import List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from config import config

_TITLE_COLOR = colors.HexColor("#1a1a2e")
_ACCENT_COLOR = colors.HexColor("#667eea")
_BODY_COLOR = colors.HexColor("#333333")
_LIGHT_BG = colors.HexColor("#f8f9fa")

# --- Styles ---------------------------------------------------------------
_STYLES = getSampleStyleSheet()

_STYLES.add(
    ParagraphStyle(
        "ReportH1",
        parent=_STYLES["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        textColor=_TITLE_COLOR,
        spaceAfter=12,
        spaceBefore=20,
        leading=22,
    )
)
_STYLES.add(
    ParagraphStyle(
        "ReportH2",
        parent=_STYLES["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=14,
        textColor=_ACCENT_COLOR,
        spaceAfter=10,
        spaceBefore=16,
        leading=18,
    )
)
_STYLES.add(
    ParagraphStyle(
        "ReportH3",
        parent=_STYLES["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=12,
        textColor=_TITLE_COLOR,
        spaceAfter=8,
        spaceBefore=12,
        leading=15,
    )
)
_STYLES.add(
    ParagraphStyle(
        "ReportBody",
        parent=_STYLES["BodyText"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=16,
        textColor=_BODY_COLOR,
        alignment=TA_JUSTIFY,
        spaceAfter=10,
        firstLineIndent=0,
    )
)
_STYLES.add(
    ParagraphStyle(
        "ReportBullet",
        parent=_STYLES["BodyText"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=16,
        textColor=_BODY_COLOR,
        leftIndent=20,
        bulletIndent=8,
        spaceAfter=6,
    )
)
_STYLES.add(
    ParagraphStyle(
        "ReportCover",
        fontName="Helvetica-Bold",
        fontSize=32,
        leading=40,
        textColor=_TITLE_COLOR,
        alignment=TA_CENTER,
        spaceAfter=16,
    )
)
_STYLES.add(
    ParagraphStyle(
        "ReportCoverSub",
        fontName="Helvetica",
        fontSize=13,
        leading=18,
        textColor=colors.HexColor("#666666"),
        alignment=TA_CENTER,
        spaceAfter=8,
    )
)
_STYLES.add(
    ParagraphStyle(
        "TOCHeading",
        fontName="Helvetica-Bold",
        fontSize=18,
        textColor=_TITLE_COLOR,
        spaceAfter=20,
        spaceBefore=10,
    )
)
_STYLES.add(
    ParagraphStyle(
        "TOCEntry",
        fontName="Helvetica",
        fontSize=11,
        leading=18,
        textColor=_BODY_COLOR,
        leftIndent=10,
        spaceAfter=4,
    )
)
_STYLES.add(
    ParagraphStyle(
        "TOCSubEntry",
        fontName="Helvetica",
        fontSize=10,
        leading=16,
        textColor=colors.HexColor("#555555"),
        leftIndent=30,
        spaceAfter=3,
    )
)
_STYLES.add(
    ParagraphStyle(
        "FooterNote",
        fontName="Helvetica-Oblique",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#888888"),
        alignment=TA_CENTER,
        spaceBefore=20,
    )
)


def _escape(text: str) -> str:
    """Escape XML entities for reportlab Paragraphs."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


_INLINE_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_INLINE_IMG = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_CODE_FENCE = re.compile(r"^```")
_TABLE_ROW = re.compile(r"^\s*\|")


class ReportPDFGenerator:
    """Builds the PDF report from GenRep output."""

    def __init__(self):
        self.title = config.PDF_TITLE
        self.author = config.PDF_AUTHOR
        self._page_count = 0

    # -- public ------------------------------------------------------------
    def generate(
        self,
        text: str,
        output_path: Path,
        title: Optional[str] = None,
        images: Optional[List[str]] = None,
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if title:
            self.title = title

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            rightMargin=2.2 * cm,
            leftMargin=2.2 * cm,
            topMargin=2.5 * cm,
            bottomMargin=2.5 * cm,
            title=self.title,
            author=self.author,
            subject=self.title,
        )

        story: List = []

        # Cover page (no page number)
        self._cover_page(story)
        story.append(PageBreak())

        # Table of contents (built from headings in the text)
        headings = self._extract_headings(text)
        self._build_toc_page(story, headings)
        story.append(PageBreak())

        # Body
        self._body(story, text, images or [])
        story.append(Spacer(1, 1.5 * cm))
        self._footer_note(story)

        doc.build(
            story,
            onFirstPage=self._on_first_page,
            onLaterPages=self._on_later_pages,
        )
        return output_path

    # -- page furniture ----------------------------------------------------
    def _on_first_page(self, canvas, doc):
        """Cover page - no header/footer."""
        pass

    def _on_later_pages(self, canvas, doc):
        """Header and footer for content pages."""
        canvas.saveState()

        # Header line
        canvas.setStrokeColor(_ACCENT_COLOR)
        canvas.setLineWidth(0.6)
        canvas.line(2.2 * cm, A4[1] - 1.8 * cm, A4[0] - 2.2 * cm, A4[1] - 1.8 * cm)

        # Header text
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#999999"))
        canvas.drawString(2.2 * cm, A4[1] - 1.6 * cm, self.author)
        canvas.drawRightString(A4[0] - 2.2 * cm, A4[1] - 1.6 * cm, self.title)

        # Footer line
        canvas.setStrokeColor(colors.HexColor("#dddddd"))
        canvas.line(2.2 * cm, 1.8 * cm, A4[0] - 2.2 * cm, 1.8 * cm)

        # Footer text - page number (excluding cover page)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#999999"))
        page_num = canvas.getPageNumber() - 1  # Subtract cover page
        if page_num > 0:
            canvas.drawCentredString(A4[0] / 2, 1.3 * cm, f"Page {page_num}")

        canvas.restoreState()

    def _extract_headings(self, text: str) -> List[tuple]:
        """Extract headings from markdown text for TOC."""
        headings = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("## ") and not stripped.startswith("### "):
                heading = stripped.lstrip("#").strip()
                headings.append((2, heading))
            elif stripped.startswith("### "):
                heading = stripped.lstrip("#").strip()
                headings.append((3, heading))
        return headings

    def _build_toc_page(self, story, headings: List[tuple]):
        """Build a manual table of contents page."""
        story.append(Paragraph("Table of Contents", _STYLES["TOCHeading"]))
        story.append(Spacer(1, 0.5 * cm))

        if not headings:
            story.append(Paragraph(
                "<i>No sections found in the report.</i>",
                _STYLES["TOCEntry"]
            ))
            return

        for level, heading in headings:
            style = _STYLES["TOCEntry"] if level == 2 else _STYLES["TOCSubEntry"]
            story.append(Paragraph(_escape(heading), style))

    def _cover_page(self, story):
        story.append(Spacer(1, 5 * cm))
        story.append(Paragraph(_escape(self.title), _STYLES["ReportCover"]))
        story.append(Spacer(1, 0.8 * cm))
        story.append(
            Paragraph(
                _escape("Generated by the GenRep Multi-Agent System"),
                _STYLES["ReportCoverSub"],
            )
        )
        story.append(
            Paragraph(
                _escape(
                    "Planning Agent  •  Execution Agents  •  Verification & Aggregation"
                ),
                _STYLES["ReportCoverSub"],
            )
        )
        story.append(Spacer(1, 2.5 * cm))

        import datetime

        stamp = datetime.datetime.now().strftime("%B %d, %Y")
        info = Table(
            [
                ["Generated", stamp],
                ["Engine", "GenRep (parallel tool-calling agents)"],
            ],
            colWidths=[4.5 * cm, 11.5 * cm],
        )
        info.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("TEXTCOLOR", (0, 0), (-1, -1), _BODY_COLOR),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f4fb")),
                ]
            )
        )
        story.append(info)

    def _footer_note(self, story):
        note = (
            "This report was produced autonomously by the GenRep multi-agent "
            "system. Facts and figures should be independently verified before "
            "publication."
        )
        story.append(Paragraph(_escape(note), _STYLES["FooterNote"]))

    # -- body parsing ------------------------------------------------------
    def _body(self, story, text: str, images: List[str]):
        embedded_images = self._embedding_paths(images)
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if _CODE_FENCE.match(stripped):
                block = []
                i += 1
                while i < len(lines) and not _CODE_FENCE.match(lines[i].strip()):
                    block.append(lines[i])
                    i += 1
                i += 1  # skip closing fence
                code = "\n".join(block)
                if code:
                    story.append(
                        Preformatted(
                            code[:20000],
                            ParagraphStyle(
                                "code",
                                fontName="Courier",
                                fontSize=8.5,
                                leading=12,
                                backColor=colors.HexColor("#f7f7f9"),
                                borderPadding=8,
                                textColor=_TITLE_COLOR,
                                spaceAfter=10,
                            ),
                        )
                    )
                continue

            if stripped.startswith("# ") or stripped.startswith("## ") or stripped.startswith("### "):
                level = len(stripped) - len(stripped.lstrip("#"))
                heading = stripped.lstrip("#").strip()
                # Skip "Table of Contents" heading (we built our own)
                if heading == "Table of Contents":
                    i += 1
                    continue
                story.append(Paragraph(self._links(_escape(heading)), _STYLES[f"ReportH{min(level, 3)}"]))
                i += 1
                continue

            if _TABLE_ROW.match(stripped) and stripped.count("|") >= 2:
                block = []
                while i < len(lines) and _TABLE_ROW.match(lines[i].strip()):
                    block.append(lines[i].strip())
                    i += 1
                self._table(story, block)
                continue

            if not stripped:
                i += 1
                continue

            # Images referenced inline with a real file path
            img_match = _INLINE_IMG.search(line)
            if img_match and Path(img_match.group(2)).exists():
                story.append(Spacer(1, 8))
                self._image(story, img_match.group(2))
                caption = img_match.group(1)
                if caption:
                    story.append(
                        Paragraph(_escape(caption), _STYLES["ReportCoverSub"])
                    )
                i += 1
                continue

            # Bullet / numbered list
            if re.match(r"^[-*•]\s+", stripped) or re.match(r"^\d+[.)]\s+", stripped):
                bullet = re.sub(r"^([-*•]|\d+[.)])\s+", "", stripped)
                story.append(
                    Paragraph(
                        self._links(_escape(bullet)),
                        _STYLES["ReportBullet"],
                        bulletText="•",
                    )
                )
                i += 1
                continue

            # Normal paragraph (may span lines until blank)
            para_lines = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and not _TABLE_ROW.match(lines[i].strip()) and not lines[i].lstrip().startswith("#"):
                para_lines.append(lines[i])
                i += 1
            para = " ".join(p.strip() for p in para_lines if p.strip())
            if para:
                story.append(
                    Paragraph(self._links(_escape(para)), _STYLES["ReportBody"])
                )

        # Embed any un-referenced chart images at the end
        for img in embedded_images:
            if not any(img in str(p) for p in self._last_images):
                story.append(Spacer(1, 10))
                self._image(story, img)

    # -- helpers -----------------------------------------------------------
    def _embedding_paths(self, images: List[str]) -> List[str]:
        self._last_images: List[str] = []
        for img in images:
            p = Path(img)
            if p.exists() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".svg"}:
                self._last_images.append(str(p))
        return self._last_images

    def _links(self, escaped_html: str) -> str:
        return escaped_html  # links already safe after escaping; keep simple

    def _image(self, story, path: str):
        try:
            from reportlab.platypus import Image

            img = Image(str(path))
            max_w = 16 * cm
            if img.imageWidth > max_w:
                ratio = max_w / img.imageWidth
                img.drawWidth = max_w
                img.drawHeight = img.imageHeight * ratio
            if img.drawHeight > 20 * cm:
                ratio = 20 * cm / img.drawHeight
                img.drawHeight = 20 * cm
                img.drawWidth = img.imageWidth * ratio
            story.append(img)
            story.append(Spacer(1, 8))
        except Exception as exc:  # noqa: BLE001
            print(f"[pdf_generator] Skipping image {path}: {exc}")

    def _table(self, story, rows: List[str]):
        parsed = []
        for row in rows:
            cells = [c.strip() for c in row.strip("|").split("|")]
            parsed.append(cells)
        if not parsed:
            return
        # Skip separator rows like |---|---|
        parsed = [r for r in parsed if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]
        if not parsed:
            return
        ncols = max(len(r) for r in parsed)
        parsed = [r + [""] * (ncols - len(r)) for r in parsed]

        styled = [
            [Paragraph(_escape(c), _STYLES["ReportBody"]) for c in row]
            for row in parsed
        ]
        t = Table(styled, hAlign="LEFT", repeatRows=1)
        t.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef0fb")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.append(t)
        story.append(Spacer(1, 10))


def generate_report_pdf(
    text: str,
    output_path: Path,
    title: Optional[str] = None,
    images: Optional[List[str]] = None,
) -> Path:
    return ReportPDFGenerator().generate(text, output_path, title=title, images=images)
