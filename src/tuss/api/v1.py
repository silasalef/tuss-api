"""Rotas /v1. Todas exigem token."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncConnection

from tuss.api import cursor
from tuss.api.dependencias import conexao
from tuss.api.limites import limitar
from tuss.api.schemas import (
    Conceito,
    ConceitoDetalhe,
    Historico,
    ListaTabelas,
    Mudanca,
    PaginaConceitos,
    PaginaMudancas,
    ParametrosLista,
    ParametrosMudancas,
    PedidoValidacao,
    Periodo,
    ResultadoValidacao,
    ResultadoValidacoes,
    Status,
    StatusTabela,
    Tabela,
    UltimaCarga,
    Versao,
)
from tuss.api.seguranca import exigir_token
from tuss.db import consultas
from tuss.domain.vigencia import criterio, situacao, versao_em

# Ordem importa: primeiro o token (sabe quem é), depois o limite (conta por token).
rotas = APIRouter(prefix="/v1", dependencies=[Depends(exigir_token), Depends(limitar)])

Conexao = Annotated[AsyncConnection, Depends(conexao)]
CodigoTabela = Annotated[
    str, Path(max_length=20, description="`tuss-22` ou só `22`", examples=["22"])
]
CodigoConceito = Annotated[str, Path(max_length=40, examples=["10101012"])]

_RE_TABELA = re.compile(r"^(?:tuss-)?([0-9]{1,3})$")
_RE_PONTUACAO_DE_CODIGO = re.compile(r"[.\-\s]")
_NAO_ENCONTRADA: dict[int | str, dict[str, Any]] = {
    404: {"description": "Tabela ou código não encontrado"}
}


@rotas.get("/status", summary="Última sincronização de cada tabela")
async def status(con: Conexao) -> Status:
    """Mostra, por tabela, a carga que a API está servindo e a tentativa de carga mais recente.

    É a forma de saber se os dados estão atualizados e se alguma carga falhou.
    """
    linhas = await consultas.status_das_tabelas(con)
    return Status(
        tabelas=[
            StatusTabela(
                tabela=linha.codigo,
                descricao=linha.descricao,
                conceitos=linha.conceitos,
                carga_id=linha.carga_atual_id,
                sincronizado_em=linha.ultima_sync_em,
                ultima_carga=None
                if linha.ultima_id is None
                else UltimaCarga(
                    id=linha.ultima_id,
                    status=linha.ultima_status,
                    iniciada_em=linha.ultima_iniciada_em,
                    finalizada_em=linha.ultima_finalizada_em,
                    erro=linha.ultima_erro,
                    incluidos=linha.ultima_incluidos,
                    alterados=linha.ultima_alterados,
                    removidos=linha.ultima_removidos,
                    reativados=linha.ultima_reativados,
                ),
            )
            for linha in linhas
        ]
    )


@rotas.get("/tabelas", summary="Tabelas do catálogo (conceitos vazio = ainda não carregada)")
async def listar_tabelas(con: Conexao) -> ListaTabelas:
    linhas = await consultas.listar_tabelas(con)
    return ListaTabelas(
        itens=[
            Tabela(
                tabela=linha.codigo,
                numero=linha.numero,
                descricao=linha.descricao,
                conceitos=linha.conceitos,
                carga_id=linha.carga_atual_id,
                sincronizado_em=linha.ultima_sync_em,
            )
            for linha in linhas
        ]
    )


@rotas.get(
    "/tabelas/{tabela}/conceitos",
    summary="Conceitos de uma tabela, em ordem de código",
    responses=_NAO_ENCONTRADA,
)
async def listar_conceitos(
    tabela: CodigoTabela, params: Annotated[ParametrosLista, Query()], con: Conexao
) -> PaginaConceitos:
    """Sem `q`: todos, paginados por cursor (repita a chamada com `cursor=proximo_cursor`).

    Com `q`: busca, dos mais relevantes para os menos, até `limite` resultados.
    """
    t = await _tabela(con, tabela)
    if params.q is not None:
        if params.vigente_em is not None:
            raise HTTPException(400, "vigente_em não se combina com q; use um dos dois")
        return await _buscar(con, t, params.q, params.cursor, params.limite)
    apos = cursor.decodificar(params.cursor) if params.cursor else None
    # Pede um a mais só para saber se existe próxima página.
    if params.vigente_em is None:
        linhas = await consultas.listar_conceitos(con, t.id, apos, params.limite + 1)
    else:
        linhas = await consultas.listar_vigentes(
            con, t.id, params.vigente_em, apos, params.limite + 1
        )
    tem_mais = len(linhas) > params.limite
    itens = [_conceito(linha) for linha in linhas[: params.limite]]
    return PaginaConceitos(
        tabela=t.codigo,
        carga_id=t.carga_atual_id,
        sincronizado_em=t.ultima_sync_em,
        itens=itens,
        proximo_cursor=cursor.codificar(itens[-1].codigo) if tem_mais else None,
    )


@rotas.get(
    "/tabelas/{tabela}/conceitos/{codigo}",
    summary="Um conceito pelo código (estado atual ou numa data)",
    responses=_NAO_ENCONTRADA,
)
async def consultar_conceito(
    tabela: CodigoTabela,
    codigo: CodigoConceito,
    con: Conexao,
    em: Annotated[
        date | None,
        Query(
            description=(
                "Data (AAAA-MM-DD): devolve o código como era nesse dia e se estava vigente."
                " 404 se nesse dia o código estava fora da lista."
            )
        ),
    ] = None,
) -> ConceitoDetalhe:
    """Sem `em`: a versão atual, e se está vigente hoje. Com `em`: a versão que valia na data.

    Vigência usa as datas da ANS (`criterio: oficial`); sem elas, o período em que o
    código apareceu nas cargas (`criterio: observado`). Antes da primeira carga, a
    versão mais antiga que conhecemos.
    """
    t = await _tabela(con, tabela)
    if em is None:
        # Estado atual: só a versão atual importa, e a consulta usa o índice dela.
        em = datetime.now(UTC).date()
        linha = await consultas.conceito(con, t.id, codigo)
        versoes = [] if linha is None else [linha]
    else:
        versoes = await consultas.versoes(con, t.id, codigo)
    versao = versao_em(versoes, em)
    if versao is None:
        detalhe = "não encontrado" if not versoes else f"fora da lista em {em:%d/%m/%Y}"
        raise HTTPException(404, f"código {codigo} {detalhe} em {t.codigo}")
    return ConceitoDetalhe(
        **_conceito(versao).model_dump(),
        tabela=t.codigo,
        carga_id=t.carga_atual_id,
        sincronizado_em=t.ultima_sync_em,
        em=em,
        vigente=situacao(versoes, em).vigente,
    )


@rotas.get(
    "/tabelas/{tabela}/conceitos/{codigo}/historico",
    summary="Todas as versões de um código",
    responses=_NAO_ENCONTRADA,
)
async def historico(tabela: CodigoTabela, codigo: CodigoConceito, con: Conexao) -> Historico:
    """Cada estado que o código já teve na nossa base, com o período em que valeu.

    Inclui códigos que saíram da lista (a última versão tem `publicado_ate`).
    """
    t = await _tabela(con, tabela)
    linhas = await consultas.versoes(con, t.id, codigo)
    if not linhas:
        raise HTTPException(404, f"código {codigo} não encontrado em {t.codigo}")
    return Historico(
        tabela=t.codigo,
        codigo=codigo,
        carga_id=t.carga_atual_id,
        sincronizado_em=t.ultima_sync_em,
        versoes=[
            Versao(
                descricao=linha.descricao,
                inicio_vigencia=linha.inicio_vigencia,
                fim_vigencia=linha.fim_vigencia,
                fim_implantacao=linha.fim_implantacao,
                atributos=linha.atributos,
                criterio=criterio(linha.inicio_vigencia),
                carga_id=linha.carga_id,
                publicado_de=linha.publicado_de,
                publicado_ate=linha.publicado_ate,
            )
            for linha in linhas
        ],
    )


@rotas.post("/validacoes", summary="Confere se códigos estavam vigentes em datas")
async def validar(pedido: PedidoValidacao, con: Conexao) -> ResultadoValidacoes:
    """Até 100 itens de tabela + código + data; para cada um, se estava vigente e por quê.

    Um item com problema (tabela ou código que não existe) não derruba os outros: vem
    com `vigente: false` e o `motivo`. Conta no limite de buscas por minuto.
    """
    tabelas: dict[str, Row[Any] | None] = {}
    for informada in {item.tabela for item in pedido.itens}:
        tabelas[informada] = await _resolver_tabela(con, informada)

    # Uma consulta por tabela, com todos os códigos pedidos dela.
    codigos_por_tabela: dict[int, set[str]] = defaultdict(set)
    for item in pedido.itens:
        t = tabelas[item.tabela]
        if t is not None:
            codigos_por_tabela[t.id].add(item.codigo)
    versoes: dict[tuple[int, str], list[Row[Any]]] = defaultdict(list)
    for tabela_id, codigos in codigos_por_tabela.items():
        for linha in await consultas.versoes_de_varios(con, tabela_id, sorted(codigos)):
            versoes[(tabela_id, linha.codigo)].append(linha)

    itens: list[ResultadoValidacao] = []
    for item in pedido.itens:
        t = tabelas[item.tabela]
        if t is None:
            itens.append(
                ResultadoValidacao(
                    tabela=item.tabela,
                    codigo=item.codigo,
                    data=item.data,
                    vigente=False,
                    motivo="tabela_inexistente",
                    criterio=None,
                    periodo=None,
                    descricao=None,
                    carga_id=None,
                )
            )
            continue
        do_codigo = versoes[(t.id, item.codigo)]
        s = situacao(do_codigo, item.data)
        versao = versao_em(do_codigo, item.data)
        itens.append(
            ResultadoValidacao(
                tabela=t.codigo,
                codigo=item.codigo,
                data=item.data,
                vigente=s.vigente,
                motivo=s.motivo,
                criterio=None if s.motivo == "inexistente" else s.criterio,
                periodo=None if s.motivo == "inexistente" else Periodo(inicio=s.inicio, fim=s.fim),
                descricao=None if versao is None else versao.descricao,
                carga_id=t.carga_atual_id,
            )
        )
    vigentes = sum(item.vigente for item in itens)
    return ResultadoValidacoes(vigentes=vigentes, nao_vigentes=len(itens) - vigentes, itens=itens)


@rotas.get("/mudancas", summary="Feed de mudanças publicadas", responses=_NAO_ENCONTRADA)
async def mudancas(params: Annotated[ParametrosMudancas, Query()], con: Conexao) -> PaginaMudancas:
    """Inclusões, alterações, remoções e reativações, da mais antiga para a mais nova.

    Para acompanhar: guarde o `proximo_cursor` da última página e volte com ele depois.
    A carga inicial de cada tabela não gera mudanças (é o ponto de partida do histórico).
    """
    tabela_id = None if params.tabela is None else (await _tabela(con, params.tabela)).id
    desde = params.desde
    if desde is not None and desde.tzinfo is None:
        desde = desde.replace(tzinfo=UTC)
    apos = cursor.decodificar_evento(params.cursor) if params.cursor else None
    linhas = await consultas.mudancas(con, desde, tabela_id, params.tipo, apos, params.limite + 1)
    tem_mais = len(linhas) > params.limite
    linhas = linhas[: params.limite]
    return PaginaMudancas(
        itens=[
            Mudanca(
                ocorrido_em=linha.ocorrido_em,
                tabela=linha.tabela,
                codigo=linha.codigo,
                tipo=linha.tipo,
                campos_alterados=linha.campos_alterados,
                antes=linha.antes,
                depois=linha.depois,
                carga_id=linha.carga_id,
            )
            for linha in linhas
        ],
        proximo_cursor=cursor.codificar_evento(linhas[-1].ocorrido_em, linhas[-1].id)
        if tem_mais
        else None,
    )


async def _buscar(
    con: AsyncConnection, t: Row[Any], q: str, cursor_informado: str | None, limite: int
) -> PaginaConceitos:
    if cursor_informado:
        # Resultado por relevância não tem "próxima página" estável: refine a busca.
        raise HTTPException(400, "cursor não se aplica à busca; refine o texto de q")
    # "1.01.01.01-2" (como aparece em guia e tabela impressa) vira "10101012".
    como_codigo = _RE_PONTUACAO_DE_CODIGO.sub("", q)
    if como_codigo.isascii() and como_codigo.isdigit():  # isdigit sozinho aceita "²"
        linhas = await consultas.buscar_por_codigo(con, t.id, como_codigo, limite)
    else:
        linhas = await consultas.buscar_por_texto(con, t.id, q, limite)
    return PaginaConceitos(
        tabela=t.codigo,
        carga_id=t.carga_atual_id,
        sincronizado_em=t.ultima_sync_em,
        itens=[_conceito(linha) for linha in linhas],
        proximo_cursor=None,
    )


async def _tabela(con: AsyncConnection, informada: str) -> Row[Any]:
    """Aceita `tuss-22` ou `22`. Fora do catálogo (ou formato estranho) = 404."""
    linha = await _resolver_tabela(con, informada)
    if linha is None:
        raise HTTPException(404, f"tabela {informada} não encontrada")
    return linha


async def _resolver_tabela(con: AsyncConnection, informada: str) -> Row[Any] | None:
    achado = _RE_TABELA.match(informada.strip().lower())
    return await consultas.tabela(con, f"tuss-{achado.group(1)}") if achado else None


def _conceito(linha: Row[Any]) -> Conceito:
    return Conceito(
        codigo=linha.codigo,
        descricao=linha.descricao,
        inicio_vigencia=linha.inicio_vigencia,
        fim_vigencia=linha.fim_vigencia,
        fim_implantacao=linha.fim_implantacao,
        atributos=linha.atributos,
        criterio=criterio(linha.inicio_vigencia),
    )
