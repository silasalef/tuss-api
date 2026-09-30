"""Erros no formato Problem Details (RFC 9457) e identificador de cada requisição.

Toda resposta leva o cabeçalho X-Request-ID; todo erro traz o mesmo `request_id`
no corpo e no log. Assim um erro visto no cliente é achado no log do servidor.
Erro interno nunca mostra detalhes (mensagem de exceção, SQL) para quem chamou.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

log = structlog.get_logger("tuss.api")

TIPO_CONTEUDO = "application/problem+json"


def problema(
    request: Request,
    status: int,
    detalhe: str | None = None,
    cabecalhos: dict[str, str] | None = None,
    **extras: Any,
) -> JSONResponse:
    corpo: dict[str, Any] = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
    }
    if detalhe:
        corpo["detail"] = detalhe
    corpo["instance"] = request.url.path
    corpo["request_id"] = _request_id(request)
    corpo.update(extras)
    resposta = JSONResponse(corpo, status_code=status, media_type=TIPO_CONTEUDO)
    resposta.headers.update(cabecalhos or {})
    resposta.headers["X-Request-ID"] = corpo["request_id"]
    return resposta


def instalar(app: FastAPI) -> None:
    app.middleware("http")(_registrar_requisicao)
    app.add_exception_handler(HTTPException, _erro_http)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _erro_validacao)  # type: ignore[arg-type]


async def _registrar_requisicao(
    request: Request, proxima: Callable[[Request], Awaitable[Response]]
) -> Response:
    request.state.request_id = uuid.uuid4().hex
    inicio = time.perf_counter()
    try:
        resposta = await proxima(request)
    except Exception as exc:  # erro não previsto: responde 500 aqui, uma vez, e registra
        resposta = _erro_interno(request, exc)
    resposta.headers["X-Request-ID"] = request.state.request_id
    if request.url.path.startswith("/health/") and resposta.status_code == 200:
        return resposta  # checagem automática a cada 30 s: só registra quando falha
    log.info(
        "requisicao",
        request_id=request.state.request_id,
        metodo=request.method,
        rota=request.url.path,  # sem a query string: ela pode ter texto de busca
        status=resposta.status_code,
        ms=round((time.perf_counter() - inicio) * 1000, 1),
    )
    return resposta


async def _erro_http(request: Request, exc: HTTPException) -> JSONResponse:
    detalhe: str | None = exc.detail if isinstance(exc.detail, str) else None
    if detalhe == HTTPStatus(exc.status_code).phrase:
        detalhe = None  # "Not Found" repetido em title e detail não ajuda
    return problema(request, exc.status_code, detalhe, dict(exc.headers or {}))


async def _erro_validacao(request: Request, exc: RequestValidationError) -> JSONResponse:
    erros = [
        {"campo": ".".join(str(parte) for parte in e["loc"]), "mensagem": e["msg"]}
        for e in exc.errors()
    ]
    return problema(request, 422, "parâmetros inválidos", erros=erros)


def _erro_interno(request: Request, exc: Exception) -> JSONResponse:
    log.error(
        "erro_interno",
        request_id=_request_id(request),
        rota=request.url.path,
        erro=type(exc).__name__,
        exc_info=exc,
    )
    return problema(request, 500, "erro interno; informe o request_id para investigar")


def _request_id(request: Request) -> str:
    valor: str = getattr(request.state, "request_id", "") or uuid.uuid4().hex
    return valor
