"""Conexão com o PostgreSQL, sempre com um dos papéis do projeto."""

from __future__ import annotations

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tuss.config import Config


def criar_engine(config: Config, papel: str) -> AsyncEngine:
    # Sem pool: o CLI abre poucas conexões e termina. A API terá pool próprio.
    return create_async_engine(config.url_banco(papel), poolclass=pool.NullPool)
