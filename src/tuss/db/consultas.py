"""Consultas de leitura usadas pela API (papel `api`, só leitura)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def status_das_tabelas(con: AsyncConnection) -> list[Row[Any]]:
    """Uma linha por tabela: carga publicada atual e a última tentativa de carga.

    `conceitos` vem de `carga.total` da carga atual (quantos a fonte trazia e foram
    publicados), sem contar linha por linha a cada chamada.
    """
    resultado = await con.execute(
        text("""
        SELECT t.codigo, t.descricao, t.ultima_sync_em, t.carga_atual_id,
               atual.total AS conceitos,
               ultima.id AS ultima_id, ultima.status AS ultima_status,
               ultima.iniciada_em AS ultima_iniciada_em,
               ultima.finalizada_em AS ultima_finalizada_em, ultima.erro AS ultima_erro
        FROM tabela_tuss t
        LEFT JOIN carga atual ON atual.id = t.carga_atual_id
        LEFT JOIN LATERAL (
            SELECT id, status, iniciada_em, finalizada_em, erro FROM carga
            WHERE tabela_id = t.id ORDER BY id DESC LIMIT 1
        ) ultima ON true
        ORDER BY t.numero::integer
        """)
    )
    return list(resultado)


_COLUNAS_CONCEITO = """
    c.codigo, v.descricao, v.inicio_vigencia, v.fim_vigencia, v.fim_implantacao, v.atributos
    FROM conceito c
    JOIN conceito_versao v ON v.conceito_id = c.id AND v.publicado_ate IS NULL
"""


async def listar_tabelas(con: AsyncConnection) -> list[Row[Any]]:
    resultado = await con.execute(
        text("""
        SELECT t.codigo, t.numero, t.descricao, t.carga_atual_id, t.ultima_sync_em,
               atual.total AS conceitos
        FROM tabela_tuss t
        LEFT JOIN carga atual ON atual.id = t.carga_atual_id
        ORDER BY t.numero::integer
        """)
    )
    return list(resultado)


async def tabela(con: AsyncConnection, codigo: str) -> Row[Any] | None:
    resultado = await con.execute(
        text("""
        SELECT t.id, t.codigo, t.carga_atual_id, t.ultima_sync_em
        FROM tabela_tuss t WHERE t.codigo = :codigo
        """),
        {"codigo": codigo},
    )
    return resultado.one_or_none()


async def listar_conceitos(
    con: AsyncConnection, tabela_id: int, apos: str | None, limite: int
) -> list[Row[Any]]:
    """Versão atual dos conceitos, em ordem de código, a partir de `apos` (paginação keyset).

    Keyset em vez de OFFSET: "a partir do código X" usa o índice direto, e a página
    1.000 custa o mesmo que a primeira.
    """
    params: dict[str, Any] = {"tabela": tabela_id, "limite": limite}
    filtro = ""
    if apos is not None:
        filtro = "AND c.codigo > :apos"
        params["apos"] = apos
    resultado = await con.execute(
        text(f"""
        SELECT {_COLUNAS_CONCEITO}
        WHERE c.tabela_id = :tabela {filtro}
        ORDER BY c.codigo
        LIMIT :limite
        """),  # noqa: S608 (só trechos fixos entram no texto; valores vão como parâmetro)
        params,
    )
    return list(resultado)


async def conceito(con: AsyncConnection, tabela_id: int, codigo: str) -> Row[Any] | None:
    resultado = await con.execute(
        text(f"""
        SELECT {_COLUNAS_CONCEITO}
        WHERE c.tabela_id = :tabela AND c.codigo = :codigo
        """),  # noqa: S608 (só trechos fixos entram no texto; valores vão como parâmetro)
        {"tabela": tabela_id, "codigo": codigo},
    )
    return resultado.one_or_none()
