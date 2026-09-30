"""Base da API: token, formato de erro, saúde e /v1/status, com banco real."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from conftest import BancoDeTeste
from pydantic import SecretStr

from tuss.api.app import criar_app
from tuss.api.token import gerar_token, hash_token
from tuss.config import Config, ConfigAusente
from tuss.ingestion.importacao import importar
from tuss.ingestion.lote import ler_lote

PAGINA_TUSS_22 = Path(__file__).parents[1] / "fixtures" / "ans" / "concepts_tuss-22_page1.json"
TOKEN = gerar_token()
PROBLEMA = "application/problem+json"


async def _cliente(config: Config) -> AsyncIterator[httpx.AsyncClient]:
    app = criar_app(config.model_copy(update={"api_tokens_sha256": hash_token(TOKEN)}))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as c,
    ):
        yield c


@pytest.fixture
async def cliente(bd: BancoDeTeste) -> AsyncIterator[httpx.AsyncClient]:
    async for c in _cliente(bd.config):
        yield c


@pytest.fixture
async def cliente_sem_banco(bd: BancoDeTeste) -> AsyncIterator[httpx.AsyncClient]:
    senha_errada = SecretStr("senha-errada")
    async for c in _cliente(bd.config.model_copy(update={"db_senha_api": senha_errada})):
        yield c


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# Token


@pytest.mark.parametrize(
    "cabecalhos",
    [{}, _auth("tuss_errado"), {"Authorization": "Basic dXNlcjpzZW5oYQ=="}],
    ids=["sem-token", "token-errado", "outro-esquema"],
)
async def test_v1_exige_token(cliente: httpx.AsyncClient, cabecalhos: dict[str, str]) -> None:
    r = await cliente.get("/v1/status", headers=cabecalhos)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.headers["content-type"] == PROBLEMA
    assert r.json()["detail"] == "token ausente ou inválido"


def test_api_nao_sobe_sem_token_configurado(config_banco: Config) -> None:
    with pytest.raises(ConfigAusente):
        criar_app(config_banco.model_copy(update={"api_tokens_sha256": ""}))


# Formato de erro


async def test_erro_segue_problem_details_com_request_id(cliente: httpx.AsyncClient) -> None:
    r = await cliente.get("/v1/nao-existe", headers=_auth())
    corpo = r.json()
    assert r.status_code == 404
    assert r.headers["content-type"] == PROBLEMA
    assert corpo["title"] == "Not Found"
    assert corpo["status"] == 404
    assert corpo["instance"] == "/v1/nao-existe"
    assert corpo["request_id"] == r.headers["x-request-id"]


async def test_erro_interno_nao_vaza_detalhes(cliente_sem_banco: httpx.AsyncClient) -> None:
    r = await cliente_sem_banco.get("/v1/status", headers=_auth())
    assert r.status_code == 500
    assert r.headers["content-type"] == PROBLEMA
    assert "password" not in r.text.lower()
    assert r.json()["detail"] == "erro interno; informe o request_id para investigar"


# Saúde


async def test_saude_nao_exige_token(cliente: httpx.AsyncClient) -> None:
    assert (await cliente.get("/health/live")).json() == {"status": "ok"}
    assert (await cliente.get("/health/ready")).json() == {"status": "ok"}


async def test_pronto_avisa_quando_o_banco_nao_responde(
    cliente_sem_banco: httpx.AsyncClient,
) -> None:
    assert (await cliente_sem_banco.get("/health/live")).status_code == 200
    r = await cliente_sem_banco.get("/health/ready")
    assert r.status_code == 503
    assert r.headers["content-type"] == PROBLEMA


# Status


async def test_status_vazio_antes_da_primeira_carga(cliente: httpx.AsyncClient) -> None:
    r = await cliente.get("/v1/status", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"tabelas": []}


async def test_status_mostra_a_carga_publicada(
    cliente: httpx.AsyncClient, bd: BancoDeTeste, tmp_path: Path
) -> None:
    config = bd.config.model_copy(update={"snapshots_dir": tmp_path})
    resultado = await importar(ler_lote(PAGINA_TUSS_22), config)

    (tabela,) = (await cliente.get("/v1/status", headers=_auth())).json()["tabelas"]
    assert tabela["tabela"] == "tuss-22"
    assert tabela["conceitos"] == 25
    assert tabela["carga_id"] == resultado.carga_id
    assert tabela["sincronizado_em"] is not None
    assert tabela["ultima_carga"]["status"] == "publicada"
    assert tabela["ultima_carga"]["erro"] is None
