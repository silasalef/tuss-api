"""Um arquivo do portal validado inteiro, antes de tocar no banco.

Usado por `tuss inspecionar` (só mostra) e `tuss importar` (grava se estiver tudo certo).

O arquivo é lido duas vezes, sem guardar os registros na memória: a primeira passada
(`ler_lote`) valida tudo e guarda só estatísticas e os códigos (para achar
duplicados); a segunda (`Lote.conceitos`) relê para gravar. Assim a tabela 19
(1,4 milhão de conceitos) cabe no limite de memória do container.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from tuss.domain.conceito import Conceito, ConceitoInvalido, conceito_da_fonte
from tuss.ingestion import arquivo

MAX_ERROS_GUARDADOS = 100  # a contagem é completa; as mensagens, só as primeiras


class ArquivoMudou(RuntimeError):
    """O arquivo mudou entre a validação e a gravação."""


@dataclass(frozen=True, slots=True)
class Lote:
    fonte: arquivo.ArquivoFonte
    total_registros: int
    total_validos: int
    erros: list[str]  # só as primeiras MAX_ERROS_GUARDADOS mensagens
    total_erros: int
    duplicados: list[tuple[str, str]]  # (tabela, código)
    tabelas: Counter[str]
    inicio_vigencia_min: date | None
    inicio_vigencia_max: date | None
    com_fim_vigencia: int

    @property
    def problemas(self) -> list[str]:
        """O que impede a importação; vazio = pode importar."""
        problemas = [f"{self.total_erros} registro(s) inválido(s)"] if self.total_erros else []
        if self.duplicados:
            problemas.append(f"{len(self.duplicados)} código(s) duplicado(s)")
        if len(self.tabelas) != 1:
            problemas.append(f"esperada uma tabela por arquivo, veio {len(self.tabelas)}")
        return problemas

    @property
    def tabela(self) -> str:
        (tabela,) = self.tabelas  # só chamar depois de conferir `problemas`
        return tabela

    def conceitos(self) -> Iterator[tuple[Conceito, dict[str, Any]]]:
        """Relê o arquivo: cada conceito validado com o registro bruto da ANS.

        Só chamar sem `problemas`. Confere no fim que o arquivo é o mesmo que foi validado.
        """
        atual = arquivo.abrir(self.fonte.caminho)
        if atual.sha256 != self.fonte.sha256:
            raise ArquivoMudou(f"{self.fonte.caminho.name} mudou depois de validado")
        for bruto in arquivo.registros(atual):
            yield conceito_da_fonte(bruto), bruto


def ler_lote(caminho: Path) -> Lote:
    """Lê o arquivo inteiro e valida cada registro. Levanta `ArquivoInvalido`."""
    fonte = arquivo.abrir(caminho)
    total = validos = total_erros = com_fim = 0
    erros: list[str] = []
    tabelas: Counter[str] = Counter()
    vistos: dict[str, set[str]] = {}
    duplicados: list[tuple[str, str]] = []
    inicio_min: date | None = None
    inicio_max: date | None = None
    for bruto in arquivo.registros(fonte):
        total += 1
        try:
            conceito = conceito_da_fonte(bruto)
        except ConceitoInvalido as exc:
            total_erros += 1
            if len(erros) < MAX_ERROS_GUARDADOS:
                erros.append(str(exc))
            continue
        validos += 1
        tabelas[conceito.tabela] += 1
        codigos = vistos.setdefault(conceito.tabela, set())
        if conceito.codigo in codigos:
            duplicados.append((conceito.tabela, conceito.codigo))
        codigos.add(conceito.codigo)
        if conceito.inicio_vigencia is not None:
            inicio_min = min(inicio_min or conceito.inicio_vigencia, conceito.inicio_vigencia)
            inicio_max = max(inicio_max or conceito.inicio_vigencia, conceito.inicio_vigencia)
        if conceito.fim_vigencia is not None:
            com_fim += 1
    return Lote(
        fonte,
        total,
        validos,
        erros,
        total_erros,
        sorted(set(duplicados)),
        tabelas,
        inicio_min,
        inicio_max,
        com_fim,
    )
