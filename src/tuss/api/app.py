"""Aplicação FastAPI. Rodar com:

    uvicorn tuss.api.app:criar_app --factory

A API só lê: conecta com o papel `api` (sem permissão de escrita, 2 s por consulta)
e nunca chama a ANS. Sem nenhum token configurado, ela nem sobe.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tuss.api import erros, limites
from tuss.api.schemas import Saude
from tuss.api.v1 import rotas
from tuss.config import PAPEL_API, Config
from tuss.log import configurar as configurar_log

DESCRICAO = """
Consulta às tabelas TUSS da ANS com histórico de versões e vigência por data.

Toda rota `/v1` exige `Authorization: Bearer <token>`. Erros seguem o formato
Problem Details (RFC 9457) e trazem um `request_id` para rastrear no log.
"""


def criar_app(config: Config | None = None) -> FastAPI:
    config = config or Config()
    hashes_tokens = config.hashes_tokens()  # falha aqui, antes de subir, se não houver token
    configurar_log()

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
    limites.instalar(
        app, config.limite_consultas_por_minuto, config.limite_buscas_por_minuto
    )  # antes de erros: fica por dentro, e o log registra o 304/413 final
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


def openapi_json() -> str:
    """Contrato da API em JSON, para versionar em docs/openapi.json (não precisa de banco)."""
    app = criar_app(Config(api_tokens_sha256="0" * 64))  # token fictício: só para montar o app
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"
