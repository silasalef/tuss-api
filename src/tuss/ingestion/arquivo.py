"""Leitura dos arquivos baixados do portal da ANS (JSON solto ou ZIP com vários JSON).

O arquivo é entrada não confiável: limitamos tamanho descompactado e número de
membros do ZIP antes de ler, para um arquivo malformado não derrubar a máquina.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# A tabela 19 (a maior com arquivo) tem 31 JSONs de até ~20 MB; folga de 5x.
MAX_BYTES_POR_JSON = 100 * 1024 * 1024
MAX_BYTES_TOTAL = 3 * 1024 * 1024 * 1024
MAX_MEMBROS_ZIP = 500


class ArquivoInvalido(ValueError):
    """O arquivo não é um export do portal que saibamos ler com segurança."""


@dataclass(frozen=True, slots=True)
class ArquivoFonte:
    caminho: Path
    sha256: str


def abrir(caminho: Path) -> ArquivoFonte:
    """Calcula o SHA-256 do arquivo como veio, antes de qualquer transformação."""
    if caminho.suffix.lower() not in {".json", ".zip"}:
        raise ArquivoInvalido(f"formato não suportado: {caminho.name} (use .json ou .zip)")
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return ArquivoFonte(caminho=caminho, sha256=h.hexdigest())


def registros(arquivo: ArquivoFonte) -> Iterator[Any]:
    """Devolve os registros brutos, um JSON por vez (no ZIP, em ordem de nome)."""
    if arquivo.caminho.suffix.lower() == ".json":
        tamanho = arquivo.caminho.stat().st_size
        _checar_tamanho(arquivo.caminho.name, tamanho)
        yield from _lista_json(arquivo.caminho.read_bytes(), arquivo.caminho.name)
        return

    try:
        zf = zipfile.ZipFile(arquivo.caminho)
    except zipfile.BadZipFile as exc:
        raise ArquivoInvalido(f"ZIP corrompido: {arquivo.caminho.name}") from exc
    with zf:
        membros = [m for m in zf.infolist() if not m.is_dir()]
        if not membros or len(membros) > MAX_MEMBROS_ZIP:
            raise ArquivoInvalido(
                f"ZIP com {len(membros)} arquivos; esperado entre 1 e {MAX_MEMBROS_ZIP}"
            )
        total = sum(m.file_size for m in membros)
        if total > MAX_BYTES_TOTAL:
            raise ArquivoInvalido(
                f"ZIP descompactado teria {total} bytes; limite {MAX_BYTES_TOTAL}"
            )
        for membro in sorted(membros, key=lambda m: m.filename):
            if not membro.filename.lower().endswith(".json"):
                raise ArquivoInvalido(f"membro inesperado no ZIP: {membro.filename}")
            _checar_tamanho(membro.filename, membro.file_size)
            with zf.open(membro) as f:
                # Lê no máximo o limite + 1: o tamanho declarado no ZIP pode mentir.
                conteudo = f.read(MAX_BYTES_POR_JSON + 1)
            _checar_tamanho(membro.filename, len(conteudo))
            yield from _lista_json(conteudo, membro.filename)


def _checar_tamanho(nome: str, tamanho: int) -> None:
    if tamanho > MAX_BYTES_POR_JSON:
        raise ArquivoInvalido(f"{nome} tem mais de {MAX_BYTES_POR_JSON} bytes")


def _lista_json(conteudo: bytes, nome: str) -> list[Any]:
    try:
        dados = json.loads(conteudo.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArquivoInvalido(f"{nome} não é JSON UTF-8 válido: {exc}") from exc
    if not isinstance(dados, list):
        raise ArquivoInvalido(f"{nome}: esperado um array JSON de conceitos")
    return dados
