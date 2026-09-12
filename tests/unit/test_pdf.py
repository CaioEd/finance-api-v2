"""O renderizador de PDF, verificado pelo texto que sai na folha (`tests.pdf_text`)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

import pytest

from core.pdf import (
    Align,
    Brand,
    Column,
    Document,
    NoteBlock,
    SummaryBlock,
    SummaryItem,
    TableBlock,
    Tone,
    render_pdf,
)
from tests.pdf_text import page_count, text_of

COLUMNS = [
    Column("Data", ratio=1.2),
    Column("Descrição", ratio=3.0, wrap=True),
    Column("Valor", ratio=1.4, align=Align.RIGHT),
]


def a_document(
    *,
    rows: int = 3,
    brand: Brand | None = None,
    blocks: list[object] | None = None,
    filters: Sequence[tuple[str, str]] = (("Tipo", "receitas e despesas"),),
) -> Document:
    table = TableBlock(
        columns=COLUMNS,
        rows=[[f"{day:02d}/09/2026", f"Compra {day}", "1.234,56"] for day in range(1, rows + 1)],
    )
    return Document(
        title="Lançamentos",
        subtitle="01/09/2026 a 30/09/2026",
        generated_at="Gerado em 12/09/2026 às 14:33",
        footer="Ana Ribeiro · ana@exemplo.com",
        brand=brand or Brand(name="finance-api"),
        filters=filters,
        blocks=blocks if blocks is not None else [table],  # type: ignore[arg-type]
    )


def a_png(path: Path) -> Path:
    from PIL import Image

    image = Image.new("RGB", (240, 80), (15, 118, 110))
    image.save(path)
    return path


# ------------------------------------------------------------------ o arquivo


def test_it_renders_a_pdf_file() -> None:
    content = render_pdf(a_document())

    assert content.startswith(b"%PDF-")
    assert content.rstrip().endswith(b"%%EOF")


def test_the_sheet_carries_title_period_and_filters() -> None:
    text = text_of(render_pdf(a_document()))

    assert "Lançamentos" in text
    assert "01/09/2026 a 30/09/2026" in text
    assert "Tipo" in text and "receitas e despesas" in text


def test_the_rows_reach_the_sheet() -> None:
    text = text_of(render_pdf(a_document(rows=2)))

    assert "Compra 1" in text
    assert "Compra 2" in text
    assert "1.234,56" in text


def test_a_document_without_filters_prints_none() -> None:
    text = text_of(render_pdf(a_document(filters=())))

    assert "Lançamentos" in text, "a folha sem filtros perdeu o título junto"
    assert "Tipo" not in text


def test_the_generation_stamp_and_the_owner_are_on_the_page() -> None:
    text = text_of(render_pdf(a_document()))

    assert "Gerado em 12/09/2026 às 14:33" in text
    assert "Ana Ribeiro · ana@exemplo.com" in text


# ----------------------------------------------------------------- paginação


def test_a_long_table_breaks_into_pages_repeating_the_header() -> None:
    content = render_pdf(a_document(rows=200))
    pages = page_count(content)
    text = text_of(content)

    assert pages > 1
    assert text.count("Descrição") == pages, "o cabeçalho da tabela não se repete em toda página"


def test_the_footer_numbers_every_page_with_the_real_total() -> None:
    content = render_pdf(a_document(rows=200))
    pages = page_count(content)
    text = text_of(content)

    assert pages > 1
    for page in range(1, pages + 1):
        assert f"Página {page} de {pages}" in text


def test_a_short_report_is_a_single_page() -> None:
    assert page_count(render_pdf(a_document(rows=1))) == 1


# ---------------------------------------------------------------- os blocos


def test_the_summary_prints_label_and_value() -> None:
    summary = SummaryBlock(
        [
            SummaryItem(label="Receitas", value="10.000,00", tone=Tone.POSITIVE),
            SummaryItem(label="Saldo", value="-1.500,00", tone=Tone.NEGATIVE),
        ]
    )
    text = text_of(render_pdf(a_document(blocks=[summary])))

    assert "Receitas" in text and "10.000,00" in text
    assert "Saldo" in text and "-1.500,00" in text


def test_a_note_is_printed_as_it_is() -> None:
    text = text_of(render_pdf(a_document(blocks=[NoteBlock("Nenhum lançamento neste recorte.")])))

    assert "Nenhum lançamento neste recorte." in text


def test_text_with_markup_characters_survives_intact() -> None:
    """Célula com `wrap` vira Paragraph, que interpreta marcação."""
    table = TableBlock(columns=COLUMNS, rows=[["01/09/2026", "Mercado & Cia <matriz>", "10,00"]])

    text = text_of(render_pdf(a_document(blocks=[table])))

    assert "Mercado" in text and "&" in text
    assert "matriz" in text, "o <matriz> foi lido como marcação e sumiu da folha"
    assert "&amp;" not in text and "&lt;" not in text, "a entidade vazou para a folha"


# ------------------------------------------------------------------- a marca


def test_without_a_logo_the_header_shows_the_brand_name() -> None:
    text = text_of(render_pdf(a_document(brand=Brand(name="minhas-financas"))))

    assert "minhas-financas" in text


def test_a_readable_logo_comes_with_the_name_beside_it(tmp_path: Path) -> None:
    brand = Brand(name="minhas-financas", logo_path=a_png(tmp_path / "logo.png"))

    content = render_pdf(a_document(brand=brand))

    assert b"/Subtype /Image" in content, "a logo não foi embutida no arquivo"
    assert "minhas-financas" in text_of(content)


def test_an_unreadable_logo_falls_back_to_the_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    broken = tmp_path / "logo.png"
    broken.write_bytes(b"isto nao e uma imagem")
    brand = Brand(name="minhas-financas", logo_path=broken)

    with caplog.at_level(logging.WARNING):
        content = render_pdf(a_document(brand=brand))

    assert "minhas-financas" in text_of(content)
    assert "logo de relatório ilegível" in caplog.text


def test_a_missing_logo_file_falls_back_to_the_name(tmp_path: Path) -> None:
    brand = Brand(name="minhas-financas", logo_path=tmp_path / "nao-existe.png")

    assert "minhas-financas" in text_of(render_pdf(a_document(brand=brand)))
