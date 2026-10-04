"""Busca (`q`): por código ou prefixo, por texto sem acento e com erro de digitação."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import BancoDeTeste, auth

from tuss.config import PAPEL_API
from tuss.ingestion.importacao import importar
from tuss.ingestion.lote import ler_lote

# Descrições reais da tabela 22.
PROCEDIMENTOS = {
    "10101012": "Consulta em consultório (no horário normal ou preestabelecido)",
    "10101020": "Consulta em domicílio",
    "10101039": "Consulta em pronto socorro",
    "20101015": "Análise da proporcionalidade cineantropométrica",
    "40301010": "Ácido úrico - pesquisa e/ou dosagem",
}


@pytest.fixture
async def carregado(bd: BancoDeTeste, tmp_path: Path) -> None:
    arquivo = tmp_path / "tuss-22.json"
    arquivo.write_text(
        json.dumps(
            [
                {"id": codigo, "source": "tuss-22", "display_name": descricao, "extras": {}}
                for codigo, descricao in PROCEDIMENTOS.items()
            ]
        )
    )
    config = bd.config.model_copy(update={"snapshots_dir": tmp_path / "snapshots"})
    await importar(ler_lote(arquivo), config)


async def _buscar(cliente: httpx.AsyncClient, q: str, **params: Any) -> httpx.Response:
    return await cliente.get("/v1/tabelas/22/conceitos", params={"q": q, **params}, headers=auth())


async def _codigos(cliente: httpx.AsyncClient, q: str) -> list[str]:
    r = await _buscar(cliente, q)
    assert r.status_code == 200, r.text
    assert r.json()["proximo_cursor"] is None
    return [item["codigo"] for item in r.json()["itens"]]


# Texto


@pytest.mark.parametrize(
    "q", ["consulta consultorio", "CONSULTÓRIO", "  consultório  ", "consultorio consulta"]
)
async def test_acha_sem_acento_sem_caixa_e_em_qualquer_ordem(
    cliente: httpx.AsyncClient, carregado: None, q: str
) -> None:
    assert (await _codigos(cliente, q))[0] == "10101012"


async def test_acha_pela_raiz_da_palavra(cliente: httpx.AsyncClient, carregado: None) -> None:
    assert sorted(await _codigos(cliente, "consultas")) == ["10101012", "10101020", "10101039"]


@pytest.mark.parametrize("q", ["consluta", "acido urio", "domicilo"])
async def test_tolera_erro_de_digitacao(
    cliente: httpx.AsyncClient, carregado: None, q: str
) -> None:
    assert await _codigos(cliente, q) != []


async def test_corrige_cada_palavra_pelo_vocabulario_da_tabela(
    cliente: httpx.AsyncClient, carregado: None, bd: BancoDeTeste
) -> None:
    # A publicação montou o vocabulário (migration 0011).
    palavras = {r.palavra for r in await bd.executar(PAPEL_API, "SELECT palavra FROM vocabulario")}
    assert {"consulta", "domicilio", "pronto"} <= palavras

    # Duas palavras erradas: corrigidas, a busca por palavras acha quem tem as duas.
    assert (await _codigos(cliente, "consluta domicilo"))[0] == "10101020"
    assert (await _codigos(cliente, "consluta pronnto"))[0] == "10101039"


async def test_quem_tem_todas_as_palavras_vem_antes(
    cliente: httpx.AsyncClient, carregado: None
) -> None:
    codigos = await _codigos(cliente, "consulta domicilio")
    assert codigos[0] == "10101020"


async def test_acha_acido_urico_sem_acento(cliente: httpx.AsyncClient, carregado: None) -> None:
    assert await _codigos(cliente, "acido urico") == ["40301010"]


async def test_nada_encontrado_e_lista_vazia(cliente: httpx.AsyncClient, carregado: None) -> None:
    assert await _codigos(cliente, "xyzwkq") == []


# Código


async def test_prefixo_de_codigo_em_ordem(cliente: httpx.AsyncClient, carregado: None) -> None:
    assert await _codigos(cliente, "1010") == ["10101012", "10101020", "10101039"]


@pytest.mark.parametrize("q", ["10101012", "1.01.01.01-2", "1 01 01 01 2"])
async def test_codigo_com_ou_sem_pontuacao(
    cliente: httpx.AsyncClient, carregado: None, q: str
) -> None:
    assert await _codigos(cliente, q) == ["10101012"]


async def test_limite_vale_na_busca(cliente: httpx.AsyncClient, carregado: None) -> None:
    r = await _buscar(cliente, "1010", limite=2)
    assert [i["codigo"] for i in r.json()["itens"]] == ["10101012", "10101020"]


# Entrada inválida


@pytest.mark.parametrize(
    "q", ["ab", "   ab   ", "x" * 101], ids=["curta", "curta-espacos", "longa"]
)
async def test_q_fora_do_tamanho_e_422(cliente: httpx.AsyncClient, carregado: None, q: str) -> None:
    r = await _buscar(cliente, q)
    assert r.status_code == 422
    assert r.json()["erros"][0]["campo"] == "query.q"


async def test_busca_nao_aceita_cursor(cliente: httpx.AsyncClient, carregado: None) -> None:
    r = await _buscar(cliente, "consulta", cursor="MTAxMDEwMTI")
    assert r.status_code == 400
    assert "cursor não se aplica à busca" in r.json()["detail"]


@pytest.mark.parametrize("q", ["'; DROP TABLE conceito; --", "consulta & | ! :*", "a:b <-> c"])
async def test_caracteres_especiais_nao_quebram(
    cliente: httpx.AsyncClient, carregado: None, q: str
) -> None:
    assert (await _buscar(cliente, q)).status_code == 200
