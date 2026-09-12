"""Extrai o texto de um PDF do reportlab, para o teste afirmar o que saiu na folha.

Não é um leitor genérico: decodifica ASCII85 + Flate e o escape octal do WinAnsi. Os pedaços saem
unidos por espaço, então afirme trechos curtos, não frases longas.
"""

from __future__ import annotations

import base64
import re
import zlib
from collections.abc import Callable

STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)
LITERAL = re.compile(rb"\((?:[^()\\]|\\.)*\)", re.DOTALL)
OCTAL = re.compile(rb"\\([0-7]{1,3})")
PAGE_OBJECT = re.compile(rb"/Type\s*/Page[^s]")  # sem casar com /Type /Pages

DECODERS: tuple[Callable[[bytes], bytes], ...] = (
    lambda blob: base64.a85decode(blob, adobe=True),
    lambda blob: blob,
)


def text_of(pdf: bytes) -> str:
    fragments: list[str] = []
    for blob in STREAM.findall(pdf):
        for literal in LITERAL.findall(_inflated(blob.strip())):
            fragments.append(_unescaped(literal[1:-1]))
    return " ".join(fragments)


def page_count(pdf: bytes) -> int:
    return len(PAGE_OBJECT.findall(pdf))


def _inflated(blob: bytes) -> bytes:
    for decode in DECODERS:
        try:
            return zlib.decompress(decode(blob))
        except (ValueError, zlib.error):
            continue
    return b""


def _unescaped(raw: bytes) -> str:
    text = OCTAL.sub(lambda match: bytes([int(match.group(1), 8)]), raw)
    text = text.replace(rb"\(", b"(").replace(rb"\)", b")").replace(b"\\\\", b"\\")
    return text.decode("cp1252", errors="replace")
