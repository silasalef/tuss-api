"""Catálogo das tabelas TUSS: nome e total informado pela ANS (`GET /ANS/source`).

Grava em `tabela_tuss`. Tabela nova no catálogo entra sem carga (só aparece com
conceitos depois da primeira coleta ou importação). Tabela que sumiu do catálogo
não é apagada: nada é apagado, e o histórico dela continua consultável.
"""

from __future__ import annotations

from sqlalchemy import text

from tuss.config import PAPEL_INGESTAO, Config
from tuss.db.conexao import criar_engine
from tuss.ingestion.fonte import ItemCatalogo


async def sincronizar_catalogo(itens: list[ItemCatalogo], config: Config) -> int:
    """Insere ou atualiza cada tabela do catálogo; devolve quantas eram novas."""
    engine = criar_engine(config, PAPEL_INGESTAO)
    try:
        async with engine.begin() as con:
            antes: set[str] = set(
                (await con.execute(text("SELECT codigo FROM tabela_tuss"))).scalars().all()
            )
            await con.execute(
                text(
                    "INSERT INTO tabela_tuss (codigo, descricao, total_fonte)"
                    " VALUES (:codigo, :descricao, :total)"
                    " ON CONFLICT (codigo) DO UPDATE"
                    " SET descricao = excluded.descricao, total_fonte = excluded.total_fonte"
                ),
                [{"codigo": i.codigo, "descricao": i.descricao, "total": i.total} for i in itens],
            )
        return len({i.codigo for i in itens} - antes)
    finally:
        await engine.dispose()
