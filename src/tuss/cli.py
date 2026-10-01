"""Linha de comando do projeto: `uv run tuss --help`."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from tuss.api.token import gerar_token, hash_token
from tuss.config import Config
from tuss.ingestion import arquivo, coleta, decisao
from tuss.ingestion.catalogo import sincronizar_catalogo
from tuss.ingestion.fonte import ClienteANS, FonteIndisponivel, RespostaInvalida
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
    if lote.total_erros or lote.duplicados:
        raise typer.Exit(1)


@app.command(name="importar")
def importar_arquivo(caminho: ArquivoDoPortal) -> None:
    """Importa um arquivo do portal: carga inicial ou atualização de uma tabela.

    Valida o arquivo inteiro antes de gravar; se houver qualquer problema, nada é gravado.
    Numa tabela já carregada, publica só o que mudou e registra cada mudança no histórico.
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
    if resultado.status == "retida":
        typer.echo(f"Carga {resultado.carga_id}: {resultado.tabela} RETIDA, nada foi publicado.")
        typer.echo(f"Motivo:      {resultado.motivo_retencao}")
        typer.echo(
            f"Confira e decida: tuss aprovar {resultado.carga_id}"
            f" ou tuss descartar {resultado.carga_id}"
        )
        raise typer.Exit(2)
    if resultado.status == "sem_mudanca":
        typer.echo(f"Carga {resultado.carga_id}: {resultado.tabela} sem mudança.")
    else:
        typer.echo(f"Carga {resultado.carga_id}: {resultado.tabela} publicada.")
        typer.echo(f"Incluídos:   {resultado.incluidos}")
        typer.echo(f"Alterados:   {resultado.alterados}")
        typer.echo(f"Removidos:   {resultado.removidos}")
        typer.echo(f"Reativados:  {resultado.reativados}")
    if resultado.snapshot is not None:
        typer.echo(f"Snapshot:    {resultado.snapshot}")


@app.command()
def coletar(
    tabela: Annotated[str, typer.Argument(help="`tuss-22` ou só `22`")],
    completa: Annotated[
        bool, typer.Option("--completa", help="Lê a tabela inteira (detecta remoções)")
    ] = False,
) -> None:
    """Coleta uma tabela pela API da ANS e publica o que mudou.

    Sem --completa: se a tabela já tem carga, lê só o começo da lista (onde a ANS põe os
    códigos novos); senão, lê tudo. Uma coleta completa interrompida é retomada de onde
    parou na próxima execução.
    """
    codigo = tabela if tabela.startswith("tuss-") else f"tuss-{tabela}"

    async def _rodar() -> coleta.ResultadoColeta:
        async with ClienteANS() as ans:
            return await coleta.coletar(
                codigo, ans, Config(), modo="completa" if completa else None
            )

    try:
        resultado = asyncio.run(_rodar())
    except (
        coleta.ColetaRecusada,
        coleta.DadosInvalidos,
        FonteIndisponivel,
        RespostaInvalida,
    ) as exc:
        typer.echo(f"Coleta não feita: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"Carga {resultado.carga_id}: {resultado.tabela} ({resultado.modo},"
        f" {resultado.paginas_lidas} páginas lidas) {resultado.status}."
    )
    if resultado.contagem is not None:
        c = resultado.contagem
        typer.echo(
            f"Incluídos {c['incluido']}, alterados {c['alterado']}, removidos {c['removido']},"
            f" reativados {c['reativado']}; {resultado.total} conceitos publicados."
        )
    if resultado.motivo:
        typer.echo(f"Motivo:      {resultado.motivo}")
    if resultado.status == "retida":
        typer.echo(
            f"Decida: tuss aprovar {resultado.carga_id} ou tuss descartar {resultado.carga_id}"
        )
        raise typer.Exit(2)
    if resultado.status == "em_andamento":
        typer.echo("A fonte parou de responder; rode de novo para continuar de onde parou.")
        raise typer.Exit(3)


@app.command()
def worker() -> None:
    """Roda o worker: um ciclo de coleta por dia, na hora TUSS_WORKER_HORA_UTC."""
    from tuss import worker as w
    from tuss.log import configurar as configurar_log

    configurar_log()
    w.main(Config())


@app.command()
def recuperar() -> None:
    """Roda o serviço de recuperação: incremental das tabelas gigantes (19 e 64), em trechos."""
    from tuss import recuperacao
    from tuss.log import configurar as configurar_log

    configurar_log()
    recuperacao.main(Config())


@app.command()
def ciclo() -> None:
    """Roda um ciclo de coleta agora (catálogo e todas as tabelas), sem esperar a hora."""
    from tuss import worker as w
    from tuss.log import configurar as configurar_log

    configurar_log()
    resultados = asyncio.run(w.rodar_ciclo(Config()))
    for r in resultados:
        typer.echo(f"{r.tabela:8} {r.status:13} {r.detalhe}")


CargaId = Annotated[int, typer.Argument(help="Número da carga retida")]
SemPerguntar = Annotated[bool, typer.Option("--sim", help="Não pede confirmação")]


@app.command()
def aprovar(carga_id: CargaId, sim: SemPerguntar = False) -> None:
    """Publica uma carga retida pelo limite de anomalia, depois de conferida."""
    carga = _carga_retida(carga_id)
    if not sim:
        typer.confirm("Publicar mesmo assim?", abort=True)
    try:
        resultado = asyncio.run(decisao.aprovar(carga_id, Config()))
    except decisao.DecisaoRecusada as exc:
        typer.echo(f"Não aprovada: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"Carga {carga_id}: {carga.tabela} {resultado.status} ({resultado.total} conceitos)."
    )


@app.command()
def descartar(carga_id: CargaId, sim: SemPerguntar = False) -> None:
    """Descarta uma carga retida: nada é publicado."""
    carga = _carga_retida(carga_id)
    if not sim:
        typer.confirm("Descartar esta carga?", abort=True)
    try:
        asyncio.run(decisao.descartar(carga_id, Config()))
    except decisao.DecisaoRecusada as exc:
        typer.echo(f"Não descartada: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Carga {carga_id}: {carga.tabela} descartada.")


def _carga_retida(carga_id: int) -> decisao.CargaRetida:
    try:
        carga = asyncio.run(decisao.consultar(carga_id, Config()))
    except decisao.DecisaoRecusada as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Carga {carga.id}: {carga.tabela}")
    typer.echo(f"Motivo:      {carga.motivo}")
    typer.echo(f"Incluídos:   {carga.incluidos}")
    typer.echo(f"Alterados:   {carga.alterados}")
    typer.echo(f"Removidos:   {carga.removidos}")
    typer.echo(f"Reativados:  {carga.reativados}")
    typer.echo(f"Conceitos depois: {carga.total}")
    return carga


@app.command()
def catalogo() -> None:
    """Atualiza o nome e o total de cada tabela a partir do catálogo da ANS."""

    async def _rodar() -> tuple[int, int]:
        async with ClienteANS() as ans:
            itens = await ans.catalogo()
        return len(itens), await sincronizar_catalogo(itens, Config())

    try:
        total, novas = asyncio.run(_rodar())
    except (FonteIndisponivel, RespostaInvalida) as exc:
        typer.echo(f"Catálogo não atualizado: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Catálogo: {total} tabelas ({novas} novas).")


@app.command()
def token() -> None:
    """Gera um token de acesso à API (um por dispositivo).

    O token aparece só agora: a API guarda apenas o hash. Para ativar, acrescente o
    hash em TUSS_API_TOKENS_SHA256 no .env (separado por vírgula) e reinicie a API.
    """
    novo = gerar_token()
    typer.echo(f"Token (guarde agora, não aparece de novo): {novo}")
    typer.echo(f"Hash para TUSS_API_TOKENS_SHA256:          {hash_token(novo)}")


@app.command()
def openapi() -> None:
    """Imprime o contrato da API (OpenAPI). Atualizar: tuss openapi > docs/openapi.json"""
    from tuss.api.app import openapi_json  # só carrega a API quando este comando é usado

    typer.echo(openapi_json(), nl=False)


def _ler(caminho: Path) -> Lote:
    try:
        return ler_lote(caminho)
    except arquivo.ArquivoInvalido as exc:
        typer.echo(f"Arquivo recusado: {exc}", err=True)
        raise typer.Exit(1) from exc


def _resumo(lote: Lote) -> None:
    typer.echo(f"Arquivo:     {lote.fonte.caminho.name}")
    typer.echo(f"SHA-256:     {lote.fonte.sha256}")
    typer.echo(f"Registros:   {lote.total_registros}")
    typer.echo("Tabelas:     " + ", ".join(f"{t} ({n})" for t, n in sorted(lote.tabelas.items())))
    if lote.inicio_vigencia_min:
        typer.echo(f"Início vig.: {lote.inicio_vigencia_min} a {lote.inicio_vigencia_max}")
    typer.echo(f"Com fim de vigência: {lote.com_fim_vigencia}")
    typer.echo(f"Inválidos:   {lote.total_erros}")
    for erro in lote.erros[:MAX_ERROS_EXIBIDOS]:
        typer.echo(f"  - {erro}")
    typer.echo(f"Duplicados:  {len(lote.duplicados)}")
    for tabela, codigo in lote.duplicados[:MAX_ERROS_EXIBIDOS]:
        typer.echo(f"  - {tabela}/{codigo}")
