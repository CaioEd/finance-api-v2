"""Lê de volta o texto de um PDF gerado, para o teste afirmar o que a folha diz.

Sem isto, teste de relatório só alcança "os bytes começam com `%PDF-`" — o que
passaria igual com o extrato de outra pessoa, com a tabela vazia ou com o total
errado. O que interessa verificar é o conteúdo, e o conteúdo está lá dentro.

Não é um leitor de PDF, e não tenta ser: extrai as strings literais dos fluxos
de conteúdo, que é onde o reportlab põe o texto desenhado. Basta para perguntar
"este número saiu na folha?" e não serve para mais nada — layout, posição e
ordem exata entre colunas não são assunto daqui.

Uma ressalva de uso: **onde a linha se parte em pedaços é decisão do reportlab**,
e os pedaços saem daqui unidos por espaço. Afirme a presença de trechos curtos
(um valor, um nome, o rótulo de uma coluna), não de frases compridas — uma frase
pode atravessar dois pedaços e ganhar um espaço que não existe na folha.

Duas decodificações no caminho, e as duas são do reportlab, não do PDF em geral:
o fluxo vem em ASCII85 sobre Flate, e o texto sai em WinAnsi com escape octal
(`\\347` é `ç`). Fluxo que não casar com isso — uma imagem, por exemplo — é
pulado, e não quebra a leitura do resto.
"""

from __future__ import annotations

import base64
import re
import zlib
from collections.abc import Callable

STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)
LITERAL = re.compile(rb"\((?:[^()\\]|\\.)*\)", re.DOTALL)
OCTAL = re.compile(rb"\\([0-7]{1,3})")
PAGE_OBJECT = re.compile(rb"/Type\s*/Page[^s]")
"""`/Type /Page`, mas não `/Type /Pages`, que é o nó que agrupa todas elas."""

DECODERS: tuple[Callable[[bytes], bytes], ...] = (
    lambda blob: base64.a85decode(blob, adobe=True),
    lambda blob: blob,
)
"""Como o reportlab embrulha o fluxo hoje, e o mesmo fluxo sem o embrulho."""


def text_of(pdf: bytes) -> str:
    """Todo o texto desenhado no arquivo, na ordem em que foi escrito."""
    fragments: list[str] = []
    for blob in STREAM.findall(pdf):
        for literal in LITERAL.findall(_inflated(blob.strip())):
            fragments.append(_unescaped(literal[1:-1]))
    return " ".join(fragments)


def page_count(pdf: bytes) -> int:
    """Quantas páginas o arquivo tem, contando os objetos de página."""
    return len(PAGE_OBJECT.findall(pdf))


def _inflated(blob: bytes) -> bytes:
    for decode in DECODERS:
        try:
            return zlib.decompress(decode(blob))
        except (ValueError, zlib.error):
            continue
    return b""


def _unescaped(raw: bytes) -> str:
    """O escape do PDF desfeito, e o resultado lido como WinAnsi (≈ cp1252)."""
    text = OCTAL.sub(lambda match: bytes([int(match.group(1), 8)]), raw)
    text = text.replace(rb"\(", b"(").replace(rb"\)", b")").replace(b"\\\\", b"\\")
    return text.decode("cp1252", errors="replace")
