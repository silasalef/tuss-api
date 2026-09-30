"""Um arquivo do portal lido e validado inteiro, antes de tocar no banco.

Usado por `tuss inspecionar` (só mostra) e `tuss importar` (grava se estiver tudo certo).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tuss.domain.conceito import Conceito, ConceitoInvalido, conceito_da_fonte
from tuss.ingestion import arquivo


@dataclass(frozen=True, slots=True)
class Lote:
    fonte: arquivo.ArquivoFonte
    total_registros: int
    itens: list[tuple[Conceito, dict[str, Any]]]  # conceito validado + registro bruto da ANS
    erros: list[str]
    duplicados: list[tuple[str, str]]  # (tabela, código)

    @property
    def tabelas(self) -> Counter[str]:
        return Counter(conceito.tabela for conceito, _ in self.itens)

    @property
    def problemas(self) -> list[str]:
        """O que impede a importação; vazio = pode importar."""
        problemas = [f"{len(self.erros)} registro(s) inválido(s)"] if self.erros else []
        if self.duplicados:
            problemas.append(f"{len(self.duplicados)} código(s) duplicado(s)")
        if len(self.tabelas) != 1:
            problemas.append(f"esperada uma tabela por arquivo, veio {len(self.tabelas)}")
        return problemas

    @property
    def tabela(self) -> str:
        (tabela,) = self.tabelas  # só chamar depois de conferir `problemas`
        return tabela


def ler_lote(caminho: Path) -> Lote:
    """Lê o arquivo inteiro e valida cada registro. Levanta `ArquivoInvalido`."""
    fonte = arquivo.abrir(caminho)
    total = 0
    itens: list[tuple[Conceito, dict[str, Any]]] = []
    erros: list[str] = []
    codigos: Counter[tuple[str, str]] = Counter()
    for bruto in arquivo.registros(fonte):
        total += 1
        try:
            conceito = conceito_da_fonte(bruto)
        except ConceitoInvalido as exc:
            erros.append(str(exc))
            continue
        itens.append((conceito, bruto))
        codigos[(conceito.tabela, conceito.codigo)] += 1
    duplicados = [chave for chave, n in codigos.items() if n > 1]
    return Lote(fonte, total, itens, erros, duplicados)
