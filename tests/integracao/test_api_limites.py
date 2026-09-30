"""Limites por token (429), tamanho do corpo e cache HTTP (ETag/304)."""

from pathlib import Path

import httpx
import pytest
from conftest import BancoDeTeste, auth

from tuss.api.limites import CACHE_CONTROL, MAX_BYTES_CORPO
from tuss.config import Config
from tuss.ingestion.importacao import importar
from tuss.ingestion.lote import ler_lote

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ans"
PROBLEMA = "application/problem+json"
LIMITE_CONSULTAS_POR_MINUTO = Config().limite_consultas_por_minuto
LIMITE_BUSCAS_POR_MINUTO = Config().limite_buscas_por_minuto


@pytest.fixture
async def carregado(bd: BancoDeTeste, tmp_path: Path) -> Path:
    snapshots = tmp_path / "snapshots"
    config = bd.config.model_copy(update={"snapshots_dir": snapshots})
    await importar(ler_lote(FIXTURES / "concepts_tuss-22_page1.json"), config)
    return snapshots


# Limites por minuto


async def test_consultas_acima_do_limite_recebem_429_com_retry_after(
    cliente: httpx.AsyncClient, carregado: Path
) -> None:
    for _ in range(LIMITE_CONSULTAS_POR_MINUTO):
        assert (await cliente.get("/v1/tabelas", headers=auth())).status_code == 200
    r = await cliente.get("/v1/tabelas", headers=auth())
    assert r.status_code == 429
    assert r.headers["content-type"] == PROBLEMA
    assert 1 <= int(r.headers["retry-after"]) <= 60
    assert r.json()["detail"] == "limite de 60 chamadas de consulta por minuto atingido"


async def test_busca_tem_limite_proprio_sem_gastar_o_das_consultas(
    cliente: httpx.AsyncClient, carregado: Path
) -> None:
    url = "/v1/tabelas/22/conceitos"
    for _ in range(LIMITE_BUSCAS_POR_MINUTO):
        assert (await cliente.get(url, params={"q": "ablação"}, headers=auth())).status_code == 200
    assert (await cliente.get(url, params={"q": "ablação"}, headers=auth())).status_code == 429
    assert (await cliente.get(url, headers=auth())).status_code == 200  # consulta segue liberada


async def test_saude_nao_tem_limite(cliente: httpx.AsyncClient) -> None:
    for _ in range(LIMITE_CONSULTAS_POR_MINUTO + 5):
        assert (await cliente.get("/health/live")).status_code == 200


# Corpo


async def test_corpo_grande_e_recusado_com_413(cliente: httpx.AsyncClient) -> None:
    r = await cliente.request(
        "GET", "/v1/tabelas", headers=auth(), content=b"x" * (MAX_BYTES_CORPO + 1)
    )
    assert r.status_code == 413
    assert r.headers["content-type"] == PROBLEMA


# Cache HTTP


async def test_get_traz_etag_e_cache_control(cliente: httpx.AsyncClient, carregado: Path) -> None:
    r = await cliente.get("/v1/tabelas/22/conceitos/30918090", headers=auth())
    assert r.headers["etag"].startswith('"')
    assert r.headers["cache-control"] == CACHE_CONTROL
    assert r.headers["vary"] == "Authorization"


async def test_mesma_versao_responde_304_sem_corpo(
    cliente: httpx.AsyncClient, carregado: Path
) -> None:
    url = "/v1/tabelas/22/conceitos"
    etag = (await cliente.get(url, headers=auth())).headers["etag"]
    r = await cliente.get(url, headers=auth() | {"If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == etag


async def test_etag_muda_quando_os_dados_mudam(
    cliente: httpx.AsyncClient, carregado: Path, bd: BancoDeTeste
) -> None:
    antes = (await cliente.get("/v1/tabelas", headers=auth())).headers["etag"]
    config = bd.config.model_copy(update={"snapshots_dir": carregado})
    await importar(ler_lote(FIXTURES / "tuss-23_20260929.json"), config)

    r = await cliente.get("/v1/tabelas", headers=auth() | {"If-None-Match": antes})
    assert r.status_code == 200
    assert r.headers["etag"] != antes


async def test_erro_nao_leva_etag(cliente: httpx.AsyncClient, carregado: Path) -> None:
    r = await cliente.get("/v1/tabelas/22/conceitos/99999999", headers=auth())
    assert r.status_code == 404
    assert "etag" not in r.headers
