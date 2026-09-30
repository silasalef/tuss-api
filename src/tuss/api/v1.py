"""Rotas /v1. Todas exigem token."""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncConnection

from tuss.api import cursor
from tuss.api.dependencias import conexao
from tuss.api.schemas import (
    Conceito,
    ConceitoDetalhe,
    ListaTabelas,
    PaginaConceitos,
    ParametrosLista,
    Status,
    StatusTabela,
    Tabela,
    UltimaCarga,
)
from tuss.api.seguranca import exigir_token
from tuss.db import consultas
from tuss.domain.vigencia import criterio

rotas = APIRouter(prefix="/v1", dependencies=[Depends(exigir_token)])

Conexao = Annotated[AsyncConnection, Depends(conexao)]
CodigoTabela = Annotated[
    str, Path(max_length=20, description="`tuss-22` ou só `22`", examples=["22"])
]
CodigoConceito = Annotated[str, Path(max_length=40, examples=["10101012"])]

_RE_TABELA = re.compile(r"^(?:tuss-)?([0-9]{1,3})$")
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
                ),
            )
            for linha in linhas
        ]
    )


@rotas.get("/tabelas", summary="Tabelas carregadas")
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
    """Paginado por cursor: para a próxima página, repita a chamada com `cursor=proximo_cursor`."""
    t = await _tabela(con, tabela)
    apos = cursor.decodificar(params.cursor) if params.cursor else None
    # Pede um a mais só para saber se existe próxima página.
    linhas = await consultas.listar_conceitos(con, t.id, apos, params.limite + 1)
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
    summary="Um conceito pelo código (estado atual)",
    responses=_NAO_ENCONTRADA,
)
async def consultar_conceito(
    tabela: CodigoTabela, codigo: CodigoConceito, con: Conexao
) -> ConceitoDetalhe:
    t = await _tabela(con, tabela)
    linha = await consultas.conceito(con, t.id, codigo)
    if linha is None:
        raise HTTPException(404, f"código {codigo} não encontrado em {t.codigo}")
    return ConceitoDetalhe(
        **_conceito(linha).model_dump(),
        tabela=t.codigo,
        carga_id=t.carga_atual_id,
        sincronizado_em=t.ultima_sync_em,
    )


async def _tabela(con: AsyncConnection, informada: str) -> Row[Any]:
    """Aceita `tuss-22` ou `22`. Fora do catálogo (ou formato estranho) = 404."""
    achado = _RE_TABELA.match(informada.strip().lower())
    linha = await consultas.tabela(con, f"tuss-{achado.group(1)}") if achado else None
    if linha is None:
        raise HTTPException(404, f"tabela {informada} não encontrada")
    return linha


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
