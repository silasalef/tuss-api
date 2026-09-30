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
