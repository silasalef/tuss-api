"""Consulta: tabelas, lista paginada de conceitos e conceito por código."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import BancoDeTeste, auth

from tuss.config import Config
from tuss.ingestion.importacao import importar
from tuss.ingestion.lote import ler_lote

PAGINA_TUSS_22 = Path(__file__).parents[1] / "fixtures" / "ans" / "concepts_tuss-22_page1.json"
REGISTROS_22: list[dict[str, Any]] = json.loads(PAGINA_TUSS_22.read_text(encoding="utf-8"))
PROBLEMA = "application/problem+json"


@pytest.fixture
async def carregado(bd: BancoDeTeste, tmp_path: Path) -> Config:
    """Banco com os 25 conceitos reais da tuss-22 e 2 medicamentos da tuss-20."""
    config = bd.config.model_copy(update={"snapshots_dir": tmp_path / "snapshots"})
    await importar(ler_lote(PAGINA_TUSS_22), config)
    medicamentos = tmp_path / "tuss-20.json"
    medicamentos.write_text(
        json.dumps(
            [
                {
                    "id": "90000021",
                    "source": "tuss-20",
                    "display_name": "ACTOS",
                    "extras": {"laboratorio": "ABBOTT", "inicio_vigencia": "2012-10-10"},
                },
                {"id": "00123", "source": "tuss-20", "display_name": "SEM DATA", "extras": {}},
            ]
        )
    )
    await importar(ler_lote(medicamentos), config)
    return config


async def _get(cliente: httpx.AsyncClient, url: str, **params: Any) -> httpx.Response:
    return await cliente.get(url, params=params, headers=auth())


# Tabelas


async def test_lista_tabelas_carregadas(cliente: httpx.AsyncClient, carregado: Config) -> None:
    corpo = (await _get(cliente, "/v1/tabelas")).json()
    assert [(t["tabela"], t["numero"], t["conceitos"]) for t in corpo["itens"]] == [
        ("tuss-20", "20", 2),
        ("tuss-22", "22", 25),
    ]


@pytest.mark.parametrize("tabela", ["22", "tuss-22", "TUSS-22"])
async def test_tabela_aceita_numero_ou_codigo(
    cliente: httpx.AsyncClient, carregado: Config, tabela: str
) -> None:
    r = await _get(cliente, f"/v1/tabelas/{tabela}/conceitos", limite=1)
    assert r.status_code == 200
    assert r.json()["tabela"] == "tuss-22"


@pytest.mark.parametrize("tabela", ["19", "tuss-abc", "22;drop"])
async def test_tabela_fora_do_catalogo_e_404(
    cliente: httpx.AsyncClient, carregado: Config, tabela: str
) -> None:
    r = await _get(cliente, f"/v1/tabelas/{tabela}/conceitos")
    assert r.status_code == 404
    assert r.headers["content-type"] == PROBLEMA


# Lista paginada


async def test_paginacao_percorre_tudo_em_ordem_sem_repetir(
    cliente: httpx.AsyncClient, carregado: Config
) -> None:
    codigos: list[str] = []
    tamanhos: list[int] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limite": 10} | ({"cursor": cursor} if cursor else {})
        corpo = (await _get(cliente, "/v1/tabelas/22/conceitos", **params)).json()
        codigos += [item["codigo"] for item in corpo["itens"]]
        tamanhos.append(len(corpo["itens"]))
        cursor = corpo["proximo_cursor"]
        if cursor is None:
            break

    assert tamanhos == [10, 10, 5]
    assert codigos == sorted(r["id"] for r in REGISTROS_22)


async def test_pagina_exata_nao_promete_proxima(
    cliente: httpx.AsyncClient, carregado: Config
) -> None:
    corpo = (await _get(cliente, "/v1/tabelas/22/conceitos", limite=25)).json()
    assert len(corpo["itens"]) == 25
    assert corpo["proximo_cursor"] is None


async def test_pagina_informa_a_carga(cliente: httpx.AsyncClient, carregado: Config) -> None:
    corpo = (await _get(cliente, "/v1/tabelas/22/conceitos")).json()
    assert corpo["carga_id"] == 1
    assert corpo["sincronizado_em"] is not None
    assert len(corpo["itens"]) == 25  # padrão é 50


@pytest.mark.parametrize(
    "params",
    [{"limite": 0}, {"limite": 201}, {"limite": "dez"}, {"limit": 10}],
    ids=["zero", "acima-do-maximo", "nao-numero", "parametro-desconhecido"],
)
async def test_parametro_invalido_e_422_com_campo(
    cliente: httpx.AsyncClient, carregado: Config, params: dict[str, Any]
) -> None:
    r = await _get(cliente, "/v1/tabelas/22/conceitos", **params)
    assert r.status_code == 422
    assert r.headers["content-type"] == PROBLEMA
    assert r.json()["erros"][0]["campo"].startswith("query.")


@pytest.mark.parametrize("cursor", ["!!!", "x" * 300, "gA"])
async def test_cursor_adulterado_e_400(
    cliente: httpx.AsyncClient, carregado: Config, cursor: str
) -> None:
    r = await _get(cliente, "/v1/tabelas/22/conceitos", cursor=cursor)
    assert r.status_code == 400
    assert "cursor inválido" in r.json()["detail"]


# Conceito por código


async def test_conceito_por_codigo_traz_dados_carga_e_criterio(
    cliente: httpx.AsyncClient, carregado: Config
) -> None:
    original = REGISTROS_22[0]
    r = await _get(cliente, f"/v1/tabelas/tuss-22/conceitos/{original['id']}")
    assert r.status_code == 200
    assert r.json() == {
        "tabela": "tuss-22",
        "codigo": original["id"],
        "descricao": original["display_name"],
        "inicio_vigencia": original["extras"]["inicio_vigencia"],
        "fim_vigencia": None,
        "fim_implantacao": original["extras"]["fim_implantacao"],
        "atributos": {},
        "criterio": "oficial",
        "carga_id": 1,
        "sincronizado_em": r.json()["sincronizado_em"],
        "em": datetime.now(UTC).date().isoformat(),
        "vigente": True,
    }


async def test_atributos_proprios_da_tabela(cliente: httpx.AsyncClient, carregado: Config) -> None:
    corpo = (await _get(cliente, "/v1/tabelas/20/conceitos/90000021")).json()
    assert corpo["atributos"] == {"laboratorio": "ABBOTT"}


async def test_sem_data_da_ans_o_criterio_e_observado(
    cliente: httpx.AsyncClient, carregado: Config
) -> None:
    corpo = (await _get(cliente, "/v1/tabelas/20/conceitos/00123")).json()
    assert corpo["criterio"] == "observado"


async def test_zeros_a_esquerda_importam(cliente: httpx.AsyncClient, carregado: Config) -> None:
    assert (await _get(cliente, "/v1/tabelas/20/conceitos/00123")).status_code == 200
    assert (await _get(cliente, "/v1/tabelas/20/conceitos/123")).status_code == 404


async def test_codigo_inexistente_e_404_com_mensagem(
    cliente: httpx.AsyncClient, carregado: Config
) -> None:
    r = await _get(cliente, "/v1/tabelas/22/conceitos/99999999")
    assert r.status_code == 404
    assert r.json()["detail"] == "código 99999999 não encontrado em tuss-22"
