"""Ponto de entrada do Alembic: conecta como dono do banco e aplica as migrations.

As migrations são escritas à mão, em SQL, sem autogerar a partir de modelos:
assim cada mudança no banco fica legível num arquivo só.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from tuss.config import PAPEL_DONO, Config

if context.is_offline_mode():
    raise SystemExit("modo offline não é usado neste projeto: rode com o banco disponível")

if context.config.config_file_name:
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)


def _aplicar(conexao: Connection) -> None:
    context.configure(connection=conexao, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def _principal() -> None:
    # Endereço e senha vêm das variáveis TUSS_DB_* (também nos testes).
    engine = create_async_engine(Config().url_banco(PAPEL_DONO), poolclass=pool.NullPool)
    try:
        async with engine.connect() as conexao:
            await conexao.run_sync(_aplicar)
    finally:
        await engine.dispose()


asyncio.run(_principal())
