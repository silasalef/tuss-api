"""Linha de comando do projeto: `uv run tuss --help`."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

from tuss.domain.conceito import ConceitoInvalido, conceito_da_fonte
from tuss.ingestion import arquivo

app = typer.Typer(help="TUSS API: ingestão e operação.", no_args_is_help=True)

MAX_ERROS_EXIBIDOS = 5


@app.callback()
def _raiz() -> None:
    """Mantém `tuss <comando>` mesmo com um comando só."""


@app.command()
def inspecionar(
    caminho: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="JSON ou ZIP baixado do portal")
    ],
) -> None:
    """Valida um arquivo do portal sem gravar nada e mostra um resumo.

    Sai com código 1 se houver registro inválido ou código duplicado.
    """
    try:
        fonte = arquivo.abrir(caminho)
        brutos = list(arquivo.registros(fonte))
    except arquivo.ArquivoInvalido as exc:
        typer.echo(f"Arquivo recusado: {exc}", err=True)
        raise typer.Exit(1) from exc

    erros: list[str] = []
    codigos: Counter[tuple[str, str]] = Counter()
    inicios = []
    com_fim = 0
    for bruto in brutos:
        try:
            c = conceito_da_fonte(bruto)
        except ConceitoInvalido as exc:
            erros.append(str(exc))
            continue
        codigos[(c.tabela, c.codigo)] += 1
        if c.inicio_vigencia:
            inicios.append(c.inicio_vigencia)
        if c.fim_vigencia:
            com_fim += 1

    duplicados = [chave for chave, n in codigos.items() if n > 1]
    tabelas = Counter(tabela for tabela, _ in codigos)

    typer.echo(f"Arquivo:     {caminho.name}")
    typer.echo(f"SHA-256:     {fonte.sha256}")
    typer.echo(f"Registros:   {len(brutos)}")
    typer.echo("Tabelas:     " + ", ".join(f"{t} ({n})" for t, n in sorted(tabelas.items())))
    if inicios:
        typer.echo(f"Início vig.: {min(inicios)} a {max(inicios)}")
    typer.echo(f"Com fim de vigência: {com_fim}")
    typer.echo(f"Inválidos:   {len(erros)}")
    for erro in erros[:MAX_ERROS_EXIBIDOS]:
        typer.echo(f"  - {erro}")
    typer.echo(f"Duplicados:  {len(duplicados)}")
    for tabela, codigo in duplicados[:MAX_ERROS_EXIBIDOS]:
        typer.echo(f"  - {tabela}/{codigo}")

    if erros or duplicados:
        raise typer.Exit(1)
