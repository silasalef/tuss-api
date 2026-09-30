"""Decisão sobre uma carga retida pelo limite de anomalia: aprovar ou descartar.

Só pelo CLI (não existe rota HTTP de administração). A carga retida guarda o
rascunho no banco; aprovar publica esse rascunho, descartar joga fora.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tuss.config import PAPEL_INGESTAO, Config
from tuss.db.conexao import criar_engine
from tuss.ingestion.publicacao import ResultadoPublicacao, publicar, travar


class DecisaoRecusada(RuntimeError):
    """A carga não está esperando decisão (ou não dá para decidir); nada mudou."""


@dataclass(frozen=True, slots=True)
class CargaRetida:
    id: int
    tabela: str
    tabela_id: int
    motivo: str
    incluidos: int
    alterados: int
    removidos: int
    reativados: int
    total: int


async def consultar(carga_id: int, config: Config) -> CargaRetida:
    engine = criar_engine(config, PAPEL_INGESTAO)
    try:
        async with engine.begin() as con:
            return await _retida(con, carga_id)
    finally:
        await engine.dispose()


async def aprovar(carga_id: int, config: Config) -> ResultadoPublicacao:
    """Publica a carga retida mesmo acima do limite (o operador conferiu)."""
    engine = criar_engine(config, PAPEL_INGESTAO)
    try:
        async with engine.begin() as con:
            carga = await _retida(con, carga_id)
            await travar(con, carga.tabela)
            rascunho: int = (
                await con.execute(
                    text("SELECT count(*) FROM stg_conceito WHERE carga_id = :c"), {"c": carga_id}
                )
            ).scalar_one()
            if rascunho == 0:
                # stg_conceito é UNLOGGED: some se o Postgres cair. A carga precisa ser refeita.
                raise DecisaoRecusada(
                    f"o rascunho da carga {carga_id} se perdeu (o banco reiniciou?):"
                    f" descarte com tuss descartar {carga_id} e refaça a carga"
                )
            await con.execute(
                text("UPDATE carga SET erro = 'aprovada manualmente; ' || erro WHERE id = :c"),
                {"c": carga_id},
            )
            return await publicar(con, carga_id, carga.tabela_id, carga.tabela, aprovada=True)
    finally:
        await engine.dispose()


async def descartar(carga_id: int, config: Config) -> None:
    """Não publica: a carga vira `falhou` e o rascunho é apagado."""
    engine = criar_engine(config, PAPEL_INGESTAO)
    try:
        async with engine.begin() as con:
            carga = await _retida(con, carga_id)
            await travar(con, carga.tabela)
            await con.execute(
                text(
                    "UPDATE carga SET status = 'falhou', erro = 'descartada; ' || erro"
                    " WHERE id = :c"
                ),
                {"c": carga_id},
            )
            await con.execute(text("DELETE FROM stg_conceito WHERE carga_id = :c"), {"c": carga_id})
    finally:
        await engine.dispose()


async def _retida(con: AsyncConnection, carga_id: int) -> CargaRetida:
    linha = (
        await con.execute(
            text(
                "SELECT c.id, t.codigo AS tabela, t.id AS tabela_id, c.status, c.erro,"
                " c.incluidos, c.alterados, c.removidos, c.reativados, c.total"
                " FROM carga c JOIN tabela_tuss t ON t.id = c.tabela_id"
                " WHERE c.id = :c FOR UPDATE OF c"
            ),
            {"c": carga_id},
        )
    ).one_or_none()
    if linha is None:
        raise DecisaoRecusada(f"carga {carga_id} não existe")
    if linha.status != "retida":
        raise DecisaoRecusada(f"carga {carga_id} não está retida (status: {linha.status})")
    return CargaRetida(
        linha.id,
        linha.tabela,
        linha.tabela_id,
        linha.erro or "",
        linha.incluidos,
        linha.alterados,
        linha.removidos,
        linha.reativados,
        linha.total or 0,
    )
