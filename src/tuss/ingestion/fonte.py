"""Cliente da API da ANS (OCL). A fonte é lenta e é entrada não confiável.

Regras (docs/PLANEJAMENTO.md, "A fonte oficial"):

- só o host da ANS, só HTTPS com certificado verificado, sem seguir redirecionamento;
- resposta com tamanho máximo (lida aos pedaços: o cabeçalho pode mentir);
- timeout de 5 minutos por requisição (uma página leva de 1 a 3 minutos);
- cliente educado: uma requisição por vez, User-Agent com o nome do projeto e, em
  falha temporária (rede, 429, 5xx), nova tentativa com espera crescente e aleatória.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

BASE_URL = "https://consulta-ocl.apps.sa-1a.mendixcloud.com/rest/oclservice"
HOSTS_PERMITIDOS = frozenset({"consulta-ocl.apps.sa-1a.mendixcloud.com"})
USER_AGENT = "tuss-api/0.1 (+https://github.com/silasalef/tuss-api; sem fins comerciais)"
TIMEOUT_S = 300.0
MAX_BYTES_RESPOSTA = 5 * 1024 * 1024  # uma página tem ~5 KB; o catálogo, ~5 KB
TENTATIVAS = 4
MAX_PAGINAS = 100_000  # a maior tabela (tuss-64) tem ~65,5 mil

_RE_TABELA = re.compile(r"^tuss-[0-9]{1,3}$")


class FonteIndisponivel(RuntimeError):
    """A ANS não respondeu direito depois de todas as tentativas."""


class RespostaInvalida(ValueError):
    """A ANS respondeu, mas com algo que não sabemos ler com segurança."""


class _FalhaTemporaria(Exception):
    """Vale tentar de novo (rede, 429, 5xx)."""


@dataclass(frozen=True, slots=True)
class ItemCatalogo:
    codigo: str  # tuss-22
    descricao: str
    total: int  # Total_sources: só aviso, pode estar desatualizado


@dataclass(frozen=True, slots=True)
class Pagina:
    tabela: str
    numero: int
    total_paginas: int
    registros: list[Any]  # conceitos brutos, validados depois por `conceito_da_fonte`
    bruto: bytes  # a resposta como veio, para o snapshot


class ClienteANS:
    """Use com `async with ClienteANS() as ans:`."""

    def __init__(
        self,
        transporte: httpx.AsyncBaseTransport | None = None,
        espera_base_s: float = 30.0,
        base_url: str = BASE_URL,
    ) -> None:
        if urlsplit(base_url).scheme != "https" or urlsplit(base_url).hostname not in (
            HOSTS_PERMITIDOS
        ):
            raise ValueError(f"fonte fora da lista permitida: {base_url}")
        self._base_url = base_url
        self._espera_base_s = espera_base_s
        self._http = httpx.AsyncClient(
            transport=transporte,
            timeout=TIMEOUT_S,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    async def __aenter__(self) -> ClienteANS:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._http.aclose()

    async def catalogo(self) -> list[ItemCatalogo]:
        """As tabelas TUSS publicadas (`GET /ANS/source`)."""
        _, corpo = await self._get("/ANS/source")
        dados = _json(corpo, "catálogo")
        if not isinstance(dados, list):
            raise RespostaInvalida("catálogo: esperado um array JSON")
        itens = []
        for item in dados:
            if not isinstance(item, dict):
                raise RespostaInvalida(f"catálogo: item não é objeto: {item!r}")
            codigo, descricao, total = (
                item.get("Codigo"),
                item.get("Descricao"),
                item.get("Total_sources"),
            )
            if not isinstance(codigo, str) or not _RE_TABELA.match(codigo):
                raise RespostaInvalida(f"catálogo: código de tabela inválido: {codigo!r}")
            if not isinstance(descricao, str) or not isinstance(total, int) or total < 0:
                raise RespostaInvalida(f"catálogo: {codigo} com descrição ou total inválidos")
            itens.append(ItemCatalogo(codigo, descricao.strip(), total))
        return itens

    async def pagina(self, tabela: str, numero: int) -> Pagina:
        """Uma página de conceitos (25 por página; a 1 traz os mais recentes)."""
        if not _RE_TABELA.match(tabela):
            raise ValueError(f"tabela inválida: {tabela!r}")
        if not 1 <= numero <= MAX_PAGINAS:
            raise ValueError(f"página fora do intervalo: {numero}")
        cabecalhos, corpo = await self._get(f"/ANS/concepts/{tabela}", {"page": str(numero)})
        paginas = cabecalhos.get("pages", "")
        if not paginas.isdigit() or not 0 <= int(paginas) <= MAX_PAGINAS:
            raise RespostaInvalida(f"{tabela} página {numero}: cabeçalho pages inválido")
        dados = _json(corpo, f"{tabela} página {numero}")
        if not isinstance(dados, list):
            raise RespostaInvalida(f"{tabela} página {numero}: esperado um array JSON")
        return Pagina(tabela, numero, int(paginas), dados, corpo)

    async def _get(
        self, caminho: str, params: dict[str, str] | None = None
    ) -> tuple[httpx.Headers, bytes]:
        tentativas = AsyncRetrying(
            stop=stop_after_attempt(TENTATIVAS),
            wait=wait_random_exponential(multiplier=self._espera_base_s, max=600),
            retry=retry_if_exception_type(_FalhaTemporaria),
            reraise=True,
        )
        try:
            async for tentativa in tentativas:
                with tentativa:
                    return await self._get_uma_vez(caminho, params)
        except _FalhaTemporaria as exc:
            raise FonteIndisponivel(f"{caminho}: {exc} ({TENTATIVAS} tentativas)") from exc
        raise AssertionError("inalcançável")  # pragma: no cover

    async def _get_uma_vez(
        self, caminho: str, params: dict[str, str] | None
    ) -> tuple[httpx.Headers, bytes]:
        try:
            async with self._http.stream(
                "GET", self._base_url + caminho, params=params
            ) as resposta:
                if resposta.status_code == 429 or resposta.status_code >= 500:
                    raise _FalhaTemporaria(f"HTTP {resposta.status_code}")
                if resposta.status_code != 200:
                    raise RespostaInvalida(f"{caminho}: HTTP {resposta.status_code}")
                corpo = bytearray()
                async for pedaco in resposta.aiter_bytes():
                    corpo.extend(pedaco)
                    if len(corpo) > MAX_BYTES_RESPOSTA:
                        raise RespostaInvalida(
                            f"{caminho}: resposta maior que {MAX_BYTES_RESPOSTA} bytes"
                        )
                return resposta.headers, bytes(corpo)
        except httpx.TransportError as exc:  # rede, timeout, conexão recusada
            raise _FalhaTemporaria(repr(exc)) from exc


def _json(corpo: bytes, onde: str) -> Any:
    try:
        return json.loads(corpo.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RespostaInvalida(f"{onde}: não é JSON UTF-8 válido") from exc
