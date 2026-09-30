"""Aplicação FastAPI. Rodar com:

    uvicorn tuss.api.app:criar_app --factory

A API só lê: conecta com o papel `api` (sem permissão de escrita, 2 s por consulta)
e nunca chama a ANS. Sem nenhum token configurado, ela nem sobe.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tuss.api import erros, limites
from tuss.api.schemas import Saude
from tuss.api.v1 import rotas
from tuss.config import PAPEL_API, Config

DESCRICAO = """
Consulta às tabelas TUSS da ANS com histórico de versões e vigência por data.

Toda rota `/v1` exige `Authorization: Bearer <token>`. Erros seguem o formato
Problem Details (RFC 9457) e trazem um `request_id` para rastrear no log.
"""


def criar_app(config: Config | None = None) -> FastAPI:
    config = config or Config()
    hashes_tokens = config.hashes_tokens()  # falha aqui, antes de subir, se não houver token
    _configurar_log()

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_async_engine(
            config.url_banco(PAPEL_API),
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,  # descarta conexão que o banco fechou (ex.: reinício do Postgres)
        )
        try:
            yield
        finally:
            await app.state.engine.dispose()

    app = FastAPI(
        title="TUSS API",
        version="0.1.0",
        description=DESCRICAO,
        lifespan=ciclo_de_vida,
    )
    app.state.hashes_tokens = hashes_tokens
    limites.instalar(app)  # antes de erros: fica por dentro, e o log registra o 304/413 final
    erros.instalar(app)
    app.include_router(rotas)

    @app.get("/health/live", tags=["saúde"], summary="O processo está no ar")
    async def vivo() -> Saude:
        return Saude(status="ok")

    @app.get(
        "/health/ready",
        tags=["saúde"],
        summary="O banco responde",
        responses={503: {"description": "Banco indisponível"}},
    )
    async def pronto(request: Request) -> Saude:
        try:
            async with request.app.state.engine.connect() as con:
                await con.execute(text("SELECT 1"))
        except Exception as exc:
            erros.log.warning("banco_indisponivel", erro=type(exc).__name__)
            raise HTTPException(503, "banco indisponível") from exc
        return Saude(status="ok")

    return app


def _configurar_log() -> None:
    """Uma linha JSON por evento, com data em UTC."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
