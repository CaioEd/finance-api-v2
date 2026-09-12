"""Geração de PDF: único módulo que importa reportlab.

Recebe um `Document` de texto já formatado e devolve bytes. Renderiza duas vezes para o rodapé
saber o total de páginas: a subclasse de `Canvas` que evitaria isso esbarra no
`disallow_subclassing_any` do mypy, porque o reportlab não publica tipos.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

logger = logging.getLogger(__name__)

# --------------------------------------------------------------- o documento


class Align(StrEnum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"


class Tone(StrEnum):
    """Cor de um número em destaque. Quem decide é quem calculou, não o renderizador."""

    NEUTRAL = "neutral"
    POSITIVE = "positive"
    NEGATIVE = "negative"


@dataclass(frozen=True, slots=True)
class Column:
    header: str
    ratio: float = 1.0
    align: Align = Align.LEFT
    wrap: bool = False


@dataclass(frozen=True, slots=True)
class TableBlock:
    columns: Sequence[Column]
    rows: Sequence[Sequence[str]]


@dataclass(frozen=True, slots=True)
class SummaryItem:
    label: str
    value: str
    tone: Tone = Tone.NEUTRAL


@dataclass(frozen=True, slots=True)
class SummaryBlock:
    items: Sequence[SummaryItem]


@dataclass(frozen=True, slots=True)
class NoteBlock:
    text: str


type Block = TableBlock | SummaryBlock | NoteBlock


@dataclass(frozen=True, slots=True)
class Brand:
    """Marca do topo da folha. Logo ausente ou ilegível deixa só o nome."""

    name: str
    logo_path: Path | None = None


@dataclass(frozen=True, slots=True)
class Document:
    title: str
    subtitle: str
    generated_at: str
    footer: str
    brand: Brand
    filters: Sequence[tuple[str, str]] = ()
    blocks: Sequence[Block] = field(default=())


# ------------------------------------------------------------------ aparência

PAGE_SIZE = A4

SIDE_MARGIN = 18 * mm
TOP_MARGIN = 32 * mm  # inclui a faixa do cabeçalho, pintada fora do frame
BOTTOM_MARGIN = 18 * mm

LOGO_HEIGHT = 12 * mm
LOGO_MAX_WIDTH = 55 * mm
BRAND_SIZE = 13
BRAND_GAP = 8
CAP_RATIO = 0.72  # altura de caixa alta da Helvetica, para centrar o nome na logo

SANS = "Helvetica"
SANS_BOLD = "Helvetica-Bold"

INK = HexColor("#1F2933")
MUTED = HexColor("#6B7280")
RULE = HexColor("#D8DEE6")
ZEBRA = HexColor("#F4F6F8")
ACCENT = HexColor("#0F766E")
NEGATIVE = HexColor("#B42318")

TONE_COLORS = {
    Tone.NEUTRAL: INK,
    Tone.POSITIVE: ACCENT,
    Tone.NEGATIVE: NEGATIVE,
}

TITLE_STYLE = ParagraphStyle(
    "titulo", fontName=SANS_BOLD, fontSize=17, leading=21, textColor=INK, spaceAfter=2
)
SUBTITLE_STYLE = ParagraphStyle(
    "subtitulo", fontName=SANS, fontSize=10.5, leading=14, textColor=MUTED
)
FILTER_STYLE = ParagraphStyle("filtro", fontName=SANS, fontSize=8.5, leading=12, textColor=MUTED)
NOTE_STYLE = ParagraphStyle("nota", fontName=SANS, fontSize=9.5, leading=14, textColor=MUTED)
CELL_STYLE = ParagraphStyle("celula", fontName=SANS, fontSize=8.5, leading=11, textColor=INK)

# Célula com `wrap` vira Paragraph, e aí quem alinha é o estilo dela, não o ALIGN da tabela.
CELL_STYLES = {
    Align.LEFT: CELL_STYLE,
    Align.RIGHT: ParagraphStyle("celula-direita", parent=CELL_STYLE, alignment=2),
}

BLOCK_GAP = 6 * mm


# ----------------------------------------------------------------- renderização


def render_pdf(document: Document) -> bytes:
    """A primeira passada só conta as páginas, para o rodapé da segunda dizer "de N"."""
    logo = _load_logo(document.brand)
    _, pages = _build(document, logo=logo, total_pages=None)
    content, _ = _build(document, logo=logo, total_pages=pages)
    return content


def _build(document: Document, *, logo: _Logo | None, total_pages: int | None) -> tuple[bytes, int]:
    buffer = BytesIO()
    template = SimpleDocTemplate(
        buffer,
        pagesize=PAGE_SIZE,
        leftMargin=SIDE_MARGIN,
        rightMargin=SIDE_MARGIN,
        topMargin=TOP_MARGIN,
        bottomMargin=BOTTOM_MARGIN,
        title=document.title,
        subject=document.subtitle,
        author=document.brand.name,
    )

    def paint(canvas: Any, frame: Any) -> None:
        _paint_frame(canvas, frame, document=document, logo=logo, total_pages=total_pages)

    template.build(_story(document, template.width), onFirstPage=paint, onLaterPages=paint)
    return buffer.getvalue(), int(template.page)


def _story(document: Document, width: float) -> list[Any]:
    """Título e filtros só na primeira página; o que se repete é pintado no canvas."""
    story: list[Any] = [
        Paragraph(escape(document.title), TITLE_STYLE),
        Paragraph(escape(document.subtitle), SUBTITLE_STYLE),
    ]
    if document.filters:
        story.append(Spacer(0, 3 * mm))
        story.extend(
            Paragraph(f"<b>{escape(label)}:</b> {escape(value)}", FILTER_STYLE)
            for label, value in document.filters
        )

    for block in document.blocks:
        story.append(Spacer(0, BLOCK_GAP))
        story.append(_render_block(block, width))
    return story


def _render_block(block: Block, width: float) -> Any:
    if isinstance(block, TableBlock):
        return _table(block, width)
    if isinstance(block, SummaryBlock):
        return _summary(block, width)
    return Paragraph(escape(block.text), NOTE_STYLE)


def _table(block: TableBlock, width: float) -> Any:
    widths = _column_widths(block.columns, width)
    data: list[list[Any]] = [[column.header for column in block.columns]]
    data.extend(
        [_cell(column, value) for column, value in zip(block.columns, row, strict=True)]
        for row in block.rows
    )

    style = TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, 0), SANS_BOLD),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("BACKGROUND", (0, 0), (-1, 0), INK),
            ("FONTNAME", (0, 1), (-1, -1), SANS),
            ("FONTSIZE", (0, 1), (-1, -1), 8.5),
            ("TEXTCOLOR", (0, 1), (-1, -1), INK),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, ZEBRA]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, RULE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]
    )
    for index, column in enumerate(block.columns):
        style.add("ALIGN", (index, 0), (index, -1), column.align.value)

    return LongTable(data, colWidths=widths, repeatRows=1, style=style, hAlign="LEFT")


def _summary(block: SummaryBlock, width: float) -> Any:
    labels = [item.label for item in block.items]
    values = [item.value for item in block.items]
    columns = max(len(block.items), 1)
    widths = [width / columns] * columns

    style = TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, 0), SANS),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
            ("FONTNAME", (0, 1), (-1, 1), SANS_BOLD),
            ("FONTSIZE", (0, 1), (-1, 1), 14),
            ("BACKGROUND", (0, 0), (-1, -1), ZEBRA),
            ("BOX", (0, 0), (-1, -1), 0.4, RULE),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, white),
            ("TOPPADDING", (0, 0), (-1, 0), 7),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]
    )
    for index, item in enumerate(block.items):
        style.add("TEXTCOLOR", (index, 1), (index, 1), TONE_COLORS[item.tone])

    return Table([labels, values], colWidths=widths, style=style, hAlign="LEFT")


def _column_widths(columns: Sequence[Column], width: float) -> list[float]:
    total = sum(column.ratio for column in columns) or 1.0
    return [width * column.ratio / total for column in columns]


def _cell(column: Column, value: str) -> Any:
    if not column.wrap:
        return value
    return Paragraph(escape(value), CELL_STYLES[column.align])


# ------------------------------------------------------- faixa, logo e rodapé


@dataclass(frozen=True, slots=True)
class _Logo:
    image: Any
    width: float
    height: float


def _load_logo(brand: Brand) -> _Logo | None:
    """Qualquer falha ao ler a imagem vira aviso no log e faixa sem logo."""
    if brand.logo_path is None:
        return None
    try:
        image = ImageReader(str(brand.logo_path))
        width, height = image.getSize()
    except Exception:
        logger.warning(
            "logo de relatório ilegível em %s; o cabeçalho sai com o nome da aplicação",
            brand.logo_path,
        )
        return None

    scale = LOGO_HEIGHT / height if height else 1.0
    drawn_width = min(width * scale, LOGO_MAX_WIDTH)
    return _Logo(image=image, width=drawn_width, height=LOGO_HEIGHT)


def _paint_frame(
    canvas: Any,
    frame: Any,
    *,
    document: Document,
    logo: _Logo | None,
    total_pages: int | None,
) -> None:
    canvas.saveState()
    page_width, page_height = frame.pagesize
    left = frame.leftMargin
    right = page_width - frame.rightMargin
    band_baseline = page_height - 17 * mm

    _paint_brand(canvas, logo=logo, brand=document.brand, x=left, baseline=band_baseline)

    canvas.setFont(SANS, 8)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(right, band_baseline + 4, document.generated_at)
    if canvas.getPageNumber() > 1:
        canvas.drawRightString(right, band_baseline - 7, document.title)

    rule = page_height - TOP_MARGIN + 6 * mm
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.8)
    canvas.line(left, rule, right, rule)

    footer = BOTTOM_MARGIN - 8 * mm
    canvas.setLineWidth(0.4)
    canvas.line(left, footer + 5 * mm, right, footer + 5 * mm)
    canvas.setFont(SANS, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(left, footer, document.footer)
    canvas.drawRightString(right, footer, _page_label(canvas.getPageNumber(), total_pages))
    canvas.restoreState()


def _paint_brand(
    canvas: Any, *, logo: _Logo | None, brand: Brand, x: float, baseline: float
) -> None:
    """Logo, ou uma barra de cor sem ela, com o nome sempre ao lado."""
    if logo is not None:
        bottom = baseline - logo.height + 9
        canvas.drawImage(
            logo.image,
            x,
            bottom,
            width=logo.width,
            height=logo.height,
            preserveAspectRatio=True,
            anchor="sw",
            mask="auto",
        )
        mark_width = logo.width
        text_baseline = bottom + (logo.height - BRAND_SIZE * CAP_RATIO) / 2
    else:
        canvas.setFillColor(ACCENT)
        canvas.rect(x, baseline - 1, 3, BRAND_SIZE, stroke=0, fill=1)
        mark_width = 3
        text_baseline = baseline + 1

    canvas.setFont(SANS_BOLD, BRAND_SIZE)
    canvas.setFillColor(INK)
    canvas.drawString(x + mark_width + BRAND_GAP, text_baseline, brand.name)


def _page_label(page: int, total_pages: int | None) -> str:
    if total_pages is None:
        return f"Página {page}"
    return f"Página {page} de {total_pages}"
