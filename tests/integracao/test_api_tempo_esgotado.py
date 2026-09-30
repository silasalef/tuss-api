"""Consulta que passa do statement_timeout do papel api vira 503 com orientação, não 500."""

from collections.abc import AsyncIterator

import httpx
import pytest
from conftest import BancoDeTeste, auth, cliente_da_api

from tuss.config import PAPEL_DONO


@pytest.fixture
async def cliente_lento(bd: BancoDeTeste) -> AsyncIterator[httpx.AsyncClient]:
    # Tabela no catálogo e um limite de 1 ms só para este teste: qualquer consulta estoura.
    await bd.executar(
        PAPEL_DONO,
        "INSERT INTO tabela_tuss (codigo) VALUES ('tuss-22')",
        "ALTER ROLE api SET statement_timeout = '1ms'",
    )
    try:
        async for c in cliente_da_api(bd.config):
            yield c
    finally:
        await bd.executar(PAPEL_DONO, "ALTER ROLE api SET statement_timeout = '2s'")


async def test_consulta_demorada_responde_503(cliente_lento: httpx.AsyncClient) -> None:
    r = await cliente_lento.get(
        "/v1/tabelas/22/conceitos", params={"vigente_em": "2015-01-01"}, headers=auth()
    )
    assert r.status_code == 503
    assert r.headers["content-type"] == "application/problem+json"
    assert r.headers["retry-after"] == "60"
    assert "limite de 2 s" in r.json()["detail"]
