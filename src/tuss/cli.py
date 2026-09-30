"""Linha de comando do projeto: `uv run tuss --help`."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from tuss.api.token import gerar_token, hash_token
from tuss.config import Config
from tuss.ingestion import arquivo
from tuss.ingestion.importacao import ImportacaoRecusada, importar
from tuss.ingestion.lote import Lote, ler_lote

app = typer.Typer(help="TUSS API: ingestão e operação.", no_args_is_help=True)

MAX_ERROS_EXIBIDOS = 5

ArquivoDoPortal = Annotated[
    Path, typer.Argument(exists=True, dir_okay=False, help="JSON ou ZIP baixado do portal")
]


@app.callback()
def _raiz() -> None:
    """Mantém `tuss <comando>` mesmo com um comando só."""


@app.command()
def inspecionar(caminho: ArquivoDoPortal) -> None:
    """Valida um arquivo do portal sem gravar nada e mostra um resumo.

    Sai com código 1 se houver registro inválido ou código duplicado.
    """
    lote = _ler(caminho)
    _resumo(lote)
    if lote.erros or lote.duplicados:
        raise typer.Exit(1)


@app.command(name="importar")
def importar_arquivo(caminho: ArquivoDoPortal) -> None:
    """Importa um arquivo do portal: carga inicial de uma tabela.

    Valida o arquivo inteiro antes de gravar; se houver qualquer problema, nada é gravado.
    Importar de novo o mesmo arquivo só registra que não houve mudança.
    """
    lote = _ler(caminho)
    _resumo(lote)
    try:
        resultado = asyncio.run(importar(lote, Config()))
    except ImportacaoRecusada as exc:
        typer.echo(f"Importação recusada: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo("")
    if resultado.status == "sem_mudanca":
        typer.echo(
            f"Carga {resultado.carga_id}: {resultado.tabela} sem mudança"
            " (mesmo arquivo da última carga)."
        )
    else:
        typer.echo(
            f"Carga {resultado.carga_id}: {resultado.tabela} publicada com"
            f" {resultado.incluidos} conceitos."
        )
        typer.echo(f"Snapshot:    {resultado.snapshot}")


@app.command()
def token() -> None:
    """Gera um token de acesso à API (um por dispositivo).

    O token aparece só agora: a API guarda apenas o hash. Para ativar, acrescente o
    hash em TUSS_API_TOKENS_SHA256 no .env (separado por vírgula) e reinicie a API.
    """
    novo = gerar_token()
    typer.echo(f"Token (guarde agora, não aparece de novo): {novo}")
    typer.echo(f"Hash para TUSS_API_TOKENS_SHA256:          {hash_token(novo)}")


def _ler(caminho: Path) -> Lote:
    try:
        return ler_lote(caminho)
    except arquivo.ArquivoInvalido as exc:
        typer.echo(f"Arquivo recusado: {exc}", err=True)
        raise typer.Exit(1) from exc


def _resumo(lote: Lote) -> None:
    inicios = [c.inicio_vigencia for c, _ in lote.itens if c.inicio_vigencia]
    com_fim = sum(1 for c, _ in lote.itens if c.fim_vigencia)

    typer.echo(f"Arquivo:     {lote.fonte.caminho.name}")
    typer.echo(f"SHA-256:     {lote.fonte.sha256}")
    typer.echo(f"Registros:   {lote.total_registros}")
    typer.echo("Tabelas:     " + ", ".join(f"{t} ({n})" for t, n in sorted(lote.tabelas.items())))
    if inicios:
        typer.echo(f"Início vig.: {min(inicios)} a {max(inicios)}")
    typer.echo(f"Com fim de vigência: {com_fim}")
    typer.echo(f"Inválidos:   {len(lote.erros)}")
    for erro in lote.erros[:MAX_ERROS_EXIBIDOS]:
        typer.echo(f"  - {erro}")
    typer.echo(f"Duplicados:  {len(lote.duplicados)}")
    for tabela, codigo in lote.duplicados[:MAX_ERROS_EXIBIDOS]:
        typer.echo(f"  - {tabela}/{codigo}")
