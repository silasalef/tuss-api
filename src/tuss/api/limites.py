"""Limites de consumo: chamadas por minuto por token, tamanho do corpo e cache HTTP.

A API é privada: os limites não barram o público, protegem o servidor de um script
em loop (inclusive um nosso) e mostram o controle funcionando.

Por que um limitador próprio em vez de slowapi: a busca e a listagem são a mesma
rota (a diferença é o parâmetro `q`), e os limites são diferentes para cada uma.
Contar chamadas numa janela de 60 s cabe em poucas linhas e fica fácil de testar.
Tudo em memória, então vale para um processo só da API (é o nosso caso).
"""

from __future__ import annotations

import hashlib
import math
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response

from tuss.api import erros

MAX_BYTES_CORPO = 64 * 1024
CACHE_CONTROL = "private, max-age=60"  # dados só mudam quando uma carga é publicada


class Limitador:
    """Janela deslizante: no máximo `limite` chamadas nos últimos `janela` segundos, por chave."""

    def __init__(self, relogio: Callable[[], float] = time.monotonic) -> None:
        self._relogio = relogio
        self._chamadas: defaultdict[str, deque[float]] = defaultdict(deque)

    def registrar(self, chave: str, limite: int, janela: float = 60.0) -> float | None:
        """Conta a chamada e devolve None; se passou do limite, devolve quantos segundos esperar."""
        agora = self._relogio()
        chamadas = self._chamadas[chave]
        while chamadas and chamadas[0] <= agora - janela:
            chamadas.popleft()
        if len(chamadas) >= limite:
            return chamadas[0] + janela - agora
        chamadas.append(agora)
        return None


async def limitar(request: Request) -> None:
    """Dependência das rotas /v1, depois do token: conta por token e por tipo de chamada."""
    busca = "q" in request.query_params
    limites: tuple[int, int] = request.app.state.limites_por_minuto  # (consultas, buscas)
    tipo, limite = ("busca", limites[1]) if busca else ("consulta", limites[0])
    limitador: Limitador = request.app.state.limitador
    espera = limitador.registrar(f"{request.state.token_hash}:{tipo}", limite)
    if espera is not None:
        raise HTTPException(
            429,
            f"limite de {limite} chamadas de {tipo} por minuto atingido",
            headers={"Retry-After": str(max(1, math.ceil(espera)))},
        )


def instalar(app: FastAPI, consultas_por_minuto: int, buscas_por_minuto: int) -> None:
    app.state.limitador = Limitador()
    app.state.limites_por_minuto = (consultas_por_minuto, buscas_por_minuto)
    app.middleware("http")(_cache_http)
    app.middleware("http")(_limitar_corpo)


async def _limitar_corpo(
    request: Request, proxima: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Recusa corpo grande antes de ler, pelo cabeçalho Content-Length."""
    tamanho = request.headers.get("content-length")
    if tamanho is not None and (not tamanho.isdigit() or int(tamanho) > MAX_BYTES_CORPO):
        return erros.problema(request, 413, f"corpo maior que {MAX_BYTES_CORPO // 1024} KB")
    if tamanho is None and "chunked" in request.headers.get("transfer-encoding", ""):
        return erros.problema(request, 411, "informe Content-Length")
    return await proxima(request)


async def _cache_http(
    request: Request, proxima: Callable[[Request], Awaitable[Response]]
) -> Response:
    """ETag nas respostas GET /v1: se o cliente já tem esta versão, responde 304 sem corpo.

    O ETag é o SHA-256 do próprio corpo: muda exatamente quando a resposta muda.
    """
    resposta = await proxima(request)
    if request.method != "GET" or not request.url.path.startswith("/v1/"):
        return resposta
    partes = getattr(resposta, "body_iterator", None)  # a resposta chega aqui em partes
    if resposta.status_code != 200 or partes is None:
        return resposta
    corpo = b"".join([parte async for parte in partes])
    etag = '"' + hashlib.sha256(corpo).hexdigest()[:32] + '"'
    cabecalhos = {
        "ETag": etag,
        "Cache-Control": CACHE_CONTROL,
        "Vary": "Authorization",  # a resposta depende de quem pergunta: não misturar entre tokens
    }
    if etag in _etags_do_cliente(request):
        return Response(status_code=304, headers=cabecalhos)
    # O tipo (application/json) vem do cabeçalho original: o atributo media_type chega vazio aqui.
    cabecalhos["Content-Type"] = resposta.headers["content-type"]
    return Response(corpo, 200, headers=cabecalhos)


def _etags_do_cliente(request: Request) -> set[str]:
    valor = request.headers.get("if-none-match", "")
    return {parte.strip().removeprefix("W/") for parte in valor.split(",") if parte.strip()}
