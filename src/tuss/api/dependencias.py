"""Recursos compartilhados pelas rotas."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


async def conexao(request: Request) -> AsyncIterator[AsyncConnection]:
    """Uma conexão do pool por requisição, devolvida ao pool no fim."""
    engine: AsyncEngine = request.app.state.engine
    async with engine.connect() as con:
        yield con
