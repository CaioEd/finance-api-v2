"""Geração de PDF: o desenho, e só ele.

Este é o **único** módulo do projeto que importa `reportlab`. O que entra aqui é
um `Document` — dataclasses de texto já formatado — e o que sai são bytes;
nenhum tipo de domínio atravessa a fronteira nas duas direções. É o que mantém
a decisão de biblioteca reversível: trocar o reportlab é reescrever este
arquivo, não caçar `canvas.drawString` espalhado pelos serviços.

A divisão de trabalho com `services/report_service.py` é essa mesma linha:

- **aqui** mora *como* a folha se parece — margens, fonte, faixa do cabeçalho,
  zebra da tabela, paginação, onde a logo é desenhada;
- **lá** mora *o que* a folha diz — título em português, quais colunas, como o
  dinheiro e a data são escritos, quais linhas entram.

Por isso `Document` não tem `Decimal`, nem `date`, nem `User`: recebe strings
prontas. Formatar dinheiro é decisão de apresentação do domínio (e o projeto
nem guarda moeda — ver `models.transaction`), e um renderizador que soubesse
formatar `Decimal` teria de saber também em que idioma.

Três escolhas que valem registro:

1. **`platypus`, não `canvas` cru.** A tabela de lançamentos pode passar de
   trinta páginas, e quebrar tabela à mão é reimplementar paginação. O cabeçalho
   da tabela se repete a cada página (`repeatRows=1`), e a faixa com a logo e a
   data é desenhada no canvas por página (`onPage`), que é o que faz toda folha
   solta continuar identificável.
2. **Duas passadas de renderização**, para o rodapé poder dizer "Página 2 de 7".
   O total só é conhecido depois de montar o documento, e o caminho usual —
   subclasse de `canvas.Canvas` que guarda as páginas — é impossível sob
   `mypy --strict`: o reportlab não publica tipos, a base viria como `Any` e
   `disallow_subclassing_any` reprova. A segunda passada custa CPU e nada mais:
   o rodapé é pintado no canvas, então mudar de "Página 2" para "Página 2 de 7"
   não mexe na paginação.
3. **Fontes embutidas do PDF** (Helvetica), sem arquivo de fonte no repositório.
   Acento português cabe no WinAnsi que elas usam; incorporar uma fonte custaria
   um binário versionado e alguns megabytes por relatório.
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
    """Alinhamento de uma coluna. Os valores são os que o reportlab entende."""

    LEFT = "LEFT"
    RIGHT = "RIGHT"


class Tone(StrEnum):
    """Peso semântico de um número em destaque.

    Existe para que o saldo negativo saia vermelho sem que este módulo precise
    olhar o sinal: quem sabe se o número é bom ou ruim é quem o calculou. Um
    renderizador que decidisse pela aparência do texto acertaria em `-10,00` e
    erraria em "10,00 a menos".
    """

    NEUTRAL = "neutral"
    POSITIVE = "positive"
    NEGATIVE = "negative"


@dataclass(frozen=True, slots=True)
class Column:
    """Uma coluna de tabela.

    `ratio` é proporção, não medida: a largura útil da folha muda com a margem,
    e coluna em pontos fixos estoura ou sobra quando ela muda.
    """

    header: str
    ratio: float = 1.0
    align: Align = Align.LEFT
    wrap: bool = False
    """Texto que pode passar da largura da célula (descrição, nome comprido).

    Quebra em várias linhas em vez de vazar por cima da coluna vizinha. Só as
    colunas de texto pagam o custo: número nenhum se beneficia de quebrar.
    """


@dataclass(frozen=True, slots=True)
class TableBlock:
    """Uma tabela. As linhas vêm formatadas, na ordem em que devem sair."""

    columns: Sequence[Column]
    rows: Sequence[Sequence[str]]


@dataclass(frozen=True, slots=True)
class SummaryItem:
    label: str
    value: str
    tone: Tone = Tone.NEUTRAL


@dataclass(frozen=True, slots=True)
class SummaryBlock:
    """Os números do recorte, em destaque no alto da folha.

    Fica antes da tabela de propósito: quem imprime um relatório de finanças
    quer o total primeiro, e a lista como prova dele.
    """

    items: Sequence[SummaryItem]


@dataclass(frozen=True, slots=True)
class NoteBlock:
    """Uma linha de texto corrido — o "nada a listar", um aviso de recorte."""

    text: str


type Block = TableBlock | SummaryBlock | NoteBlock


@dataclass(frozen=True, slots=True)
class Brand:
    """A identidade que aparece no topo de toda folha.

    `logo_path` é opcional e o caminho vem da configuração
    (`REPORT_LOGO_PATH`): imagem ausente, ilegível ou num formato que o
    reportlab não leia cai no nome em texto, e o relatório sai igual. Um
    relatório que falhasse por causa da logo trocaria um problema de aparência
    por um de indisponibilidade.
    """

    name: str
    logo_path: Path | None = None


@dataclass(frozen=True, slots=True)
class Document:
    """Tudo que uma folha precisa, já em texto.

    `generated_at` e `footer` chegam prontos porque a fonte deles é o domínio:
    a data vem do `Clock`, no fuso da aplicação (nunca de `date.today()`), e o
    rodapé identifica de quem é o extrato — folha impressa sem dono é folha que
    ninguém sabe conferir.
    """

    title: str
    subtitle: str
    generated_at: str
    footer: str
    brand: Brand
    filters: Sequence[tuple[str, str]] = ()
    """O recorte que produziu estes números, rótulo e valor.

    Vai impresso: um PDF sem os filtros que o geraram é um monte de números que
    não se pode reconferir depois.
    """

    blocks: Sequence[Block] = field(default=())


# ------------------------------------------------------------------ aparência

PAGE_SIZE = A4

SIDE_MARGIN = 18 * mm
TOP_MARGIN = 32 * mm
"""Alto o bastante para a faixa do cabeçalho, que é pintada fora do frame."""

BOTTOM_MARGIN = 18 * mm

LOGO_HEIGHT = 12 * mm
LOGO_MAX_WIDTH = 55 * mm

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

CELL_STYLES = {
    Align.LEFT: CELL_STYLE,
    Align.RIGHT: ParagraphStyle("celula-direita", parent=CELL_STYLE, alignment=2),
}
"""Um estilo por alinhamento, porque o do parágrafo é que vale.

A célula que quebra linha vira `Paragraph`, e aí o `ALIGN` da tabela não a
alcança mais: quem alinha o texto é o estilo dele. Sem este par, uma coluna
declarada à direita passaria a sair à esquerda no dia em que ganhasse `wrap`.
"""

BLOCK_GAP = 6 * mm


# ----------------------------------------------------------------- renderização


def render_pdf(document: Document) -> bytes:
    """O documento como um arquivo PDF.

    Chamada **fora do event loop** (`run_in_threadpool`, em `api/routes`):
    montar PDF é CPU, e segurar o loop numa tabela de mil linhas atrasaria toda
    requisição em voo — o servidor é um só.

    São duas passadas: a primeira só para contar as páginas, a segunda para
    escrever "de N" no rodapé. Ver o topo do módulo.
    """
    logo = _load_logo(document.brand)
    _, pages = _build(document, logo=logo, total_pages=None)
    content, _ = _build(document, logo=logo, total_pages=pages)
    return content


def _build(document: Document, *, logo: _Logo | None, total_pages: int | None) -> tuple[bytes, int]:
    """Uma passada de renderização: os bytes e quantas páginas saíram."""
    buffer = BytesIO()
    template = SimpleDocTemplate(
        buffer,
        pagesize=PAGE_SIZE,
        leftMargin=SIDE_MARGIN,
        rightMargin=SIDE_MARGIN,
        topMargin=TOP_MARGIN,
        bottomMargin=BOTTOM_MARGIN,
        # Metadados do arquivo: é o que o leitor de PDF mostra na aba e o que
        # aparece em "propriedades" depois que o arquivo sai daqui.
        title=document.title,
        subject=document.subtitle,
        author=document.brand.name,
    )

    def paint(canvas: Any, frame: Any) -> None:
        _paint_frame(canvas, frame, document=document, logo=logo, total_pages=total_pages)

    template.build(_story(document, template.width), onFirstPage=paint, onLaterPages=paint)
    return buffer.getvalue(), int(template.page)


def _story(document: Document, width: float) -> list[Any]:
    """Os elementos que fluem pela folha, na ordem.

    Título, recorte e filtros só existem na primeira página — repeti-los a cada
    folha comeria o espaço da tabela. O que se repete é a faixa do cabeçalho,
    que é pintada no canvas.
    """
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
    """A tabela de dados: cabeçalho que se repete por página, zebra e alinhamento.

    `LongTable` e não `Table`: o algoritmo de quebra dele é o que aguenta
    centenas de linhas sem medir a tabela inteira de uma vez.
    """
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
    """Os totais como painéis lado a lado: rótulo pequeno em cima, número embaixo."""
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
    """O conteúdo de uma célula: texto cru, ou um parágrafo quando pode quebrar."""
    if not column.wrap:
        return value
    return Paragraph(escape(value), CELL_STYLES[column.align])


# ------------------------------------------------------- faixa, logo e rodapé


@dataclass(frozen=True, slots=True)
class _Logo:
    """A imagem já lida e medida, para não reabrir o arquivo a cada página."""

    image: Any
    width: float
    height: float


def _load_logo(brand: Brand) -> _Logo | None:
    """Lê a logo uma vez por relatório, ou desiste dela em silêncio no log.

    Qualquer falha — arquivo ausente, permissão, formato que o reportlab não
    decodifica — vira aviso e cabeçalho em texto. Ver `Brand`.
    """
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
    """Pinta o que se repete em toda folha: faixa com logo e data, e o rodapé."""
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
        # A partir da segunda folha o título volta pequeno no alto: a primeira
        # página já o traz grande, e folha solta sem título não se identifica.
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
    if logo is not None:
        canvas.drawImage(
            logo.image,
            x,
            baseline - logo.height + 9,
            width=logo.width,
            height=logo.height,
            preserveAspectRatio=True,
            anchor="sw",
            mask="auto",
        )
        return

    # Sem imagem, a marca é o nome — com uma barra de cor ao lado, para o alto
    # da folha não ficar sendo uma linha de texto solta.
    canvas.setFillColor(ACCENT)
    canvas.rect(x, baseline - 1, 3, 13, stroke=0, fill=1)
    canvas.setFont(SANS_BOLD, 13)
    canvas.setFillColor(INK)
    canvas.drawString(x + 7, baseline + 1, brand.name)


def _page_label(page: int, total_pages: int | None) -> str:
    """ "Página 2 de 7" — ou só "Página 2" na passada que ainda não sabe o total."""
    if total_pages is None:
        return f"Página {page}"
    return f"Página {page} de {total_pages}"
