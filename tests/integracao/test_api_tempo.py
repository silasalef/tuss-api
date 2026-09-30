"""Tempo na API: histórico, `?em=`, `vigente_em`, validação em lote e feed de mudanças.

Cenário (as mesmas três cargas do teste manual da etapa 1), com as datas de
publicação levadas para dias diferentes de setembro de 2026:

- 10/09, carga 1: os 25 conceitos reais da página 1 da tuss-22;
- 20/09, carga 2: 30918090 alterado, 30914175 removido, 99999999 incluído;
- 25/09, carga 3: volta o arquivo original (30918090 alterado de novo,
  30914175 reativado, 99999999 removido).
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import BancoDeTeste, auth, cliente_da_api

from tuss.config import PAPEL_DONO
from tuss.ingestion.decisao import aprovar
from tuss.ingestion.importacao import importar
from tuss.ingestion.lote import ler_lote

PAGINA_TUSS_22 = Path(__file__).parents[1] / "fixtures" / "ans" / "concepts_tuss-22_page1.json"
ALTERADO, REMOVIDO, INCLUIDO = "30918090", "30914175", "99999999"
DIAS = ["2026-09-10 12:00+00", "2026-09-20 12:00+00", "2026-09-25 12:00+00"]


@pytest.fixture
async def cenario(bd: BancoDeTeste, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    config = bd.config.model_copy(
        update={"snapshots_dir": tmp_path / "snapshots", "limite_consultas_por_minuto": 1000}
    )
    registros: list[dict[str, Any]] = json.loads(PAGINA_TUSS_22.read_text(encoding="utf-8"))
    variante = [dict(r) for r in registros]
    variante[0] = {**variante[0], "display_name": variante[0]["display_name"] + " (teste)"}
    variante.pop(1)
    variante.append({**variante[2], "id": INCLUIDO, "display_name": "Código de teste"})
    v2 = tmp_path / "v2.json"
    v2.write_text(json.dumps(variante, ensure_ascii=False), encoding="utf-8")

    for arquivo in (PAGINA_TUSS_22, v2, PAGINA_TUSS_22):
        resultado = await importar(ler_lote(arquivo), config)
        if resultado.status == "retida":  # remover 1 de 25 passa do limite de anomalia
            await aprovar(resultado.carga_id, config)

    # As três cargas aconteceram agora; cada uma passa para o seu dia, numa instrução só
    # (início e fim de cada versão mudam juntos). Os dias novos são todos anteriores aos
    # de agora, então nenhuma linha trocada se sobrepõe a uma que ainda não foi trocada.
    instantes = await bd.executar(
        PAPEL_DONO, "SELECT DISTINCT publicado_de FROM conceito_versao ORDER BY 1"
    )
    casos = " ".join(
        f"WHEN '{instante.isoformat()}' THEN timestamptz '{dia}'"
        for (instante,), dia in zip(instantes, DIAS, strict=True)
    )
    await bd.executar(
        PAPEL_DONO,
        f"UPDATE conceito_versao SET publicado_de = CASE publicado_de {casos} END,"
        f" publicado_ate = CASE publicado_ate {casos} END",
        # Eventos são gravados na transação da publicação: mesmo instante das versões.
        f"UPDATE evento_mudanca SET ocorrido_em = CASE ocorrido_em {casos} END",
    )
    async for cliente in cliente_da_api(config):
        yield cliente


async def _get(cliente: httpx.AsyncClient, url: str, **params: Any) -> httpx.Response:
    return await cliente.get(url, params=params, headers=auth())


# Histórico


async def test_historico_mostra_todas_as_versoes(cenario: httpx.AsyncClient) -> None:
    corpo = (await _get(cenario, f"/v1/tabelas/22/conceitos/{ALTERADO}/historico")).json()
    assert (corpo["tabela"], corpo["codigo"], corpo["carga_id"]) == ("tuss-22", ALTERADO, 3)
    versoes = corpo["versoes"]
    assert [(v["carga_id"], v["descricao"].endswith("(teste)")) for v in versoes] == [
        (1, False),
        (2, True),
        (3, False),
    ]
    assert [v["publicado_de"][:10] for v in versoes] == ["2026-09-10", "2026-09-20", "2026-09-25"]
    assert [(v["publicado_ate"] or "")[:10] for v in versoes] == ["2026-09-20", "2026-09-25", ""]
    assert versoes[0]["criterio"] == "oficial"


async def test_historico_de_codigo_que_saiu_da_lista(cenario: httpx.AsyncClient) -> None:
    corpo = (await _get(cenario, f"/v1/tabelas/22/conceitos/{INCLUIDO}/historico")).json()
    assert [(v["publicado_de"][:10], v["publicado_ate"][:10]) for v in corpo["versoes"]] == [
        ("2026-09-20", "2026-09-25")
    ]
    # Sem `em`, a consulta é o estado atual: código fora da lista.
    assert (await _get(cenario, f"/v1/tabelas/22/conceitos/{INCLUIDO}")).status_code == 404


async def test_historico_de_codigo_inexistente_e_404(cenario: httpx.AsyncClient) -> None:
    r = await _get(cenario, "/v1/tabelas/22/conceitos/00000000/historico")
    assert r.status_code == 404
    assert r.json()["detail"] == "código 00000000 não encontrado em tuss-22"


# Consulta numa data


@pytest.mark.parametrize(
    ("em", "com_teste"),
    [("2026-09-15", False), ("2026-09-20", True), ("2026-09-24", True), ("2026-09-30", False)],
)
async def test_em_devolve_a_versao_daquele_dia(
    cenario: httpx.AsyncClient, em: str, com_teste: bool
) -> None:
    corpo = (await _get(cenario, f"/v1/tabelas/22/conceitos/{ALTERADO}", em=em)).json()
    assert corpo["descricao"].endswith("(teste)") is com_teste
    assert (corpo["em"], corpo["vigente"], corpo["criterio"]) == (em, True, "oficial")


async def test_em_antes_do_inicio_oficial_nao_esta_vigente(cenario: httpx.AsyncClient) -> None:
    # 30918090 começa a valer em 01/08/2026 pela ANS; em julho ainda não valia.
    corpo = (await _get(cenario, f"/v1/tabelas/22/conceitos/{ALTERADO}", em="2026-07-01")).json()
    assert (corpo["vigente"], corpo["inicio_vigencia"]) == (False, "2026-08-01")
    assert not corpo["descricao"].endswith("(teste)")  # a versão mais antiga que conhecemos


@pytest.mark.parametrize(
    ("codigo", "em", "status"),
    [
        (REMOVIDO, "2026-09-19", 200),
        (REMOVIDO, "2026-09-20", 404),  # saiu da lista na carga de 20/09
        (REMOVIDO, "2026-09-25", 200),  # voltou em 25/09
        (INCLUIDO, "2026-09-22", 200),
        (INCLUIDO, "2026-09-26", 404),
    ],
)
async def test_em_respeita_saida_e_volta_da_lista(
    cenario: httpx.AsyncClient, codigo: str, em: str, status: int
) -> None:
    r = await _get(cenario, f"/v1/tabelas/22/conceitos/{codigo}", em=em)
    assert r.status_code == status
    if status == 404:
        assert "fora da lista em" in r.json()["detail"]


async def test_em_com_data_invalida_e_422(cenario: httpx.AsyncClient) -> None:
    r = await _get(cenario, f"/v1/tabelas/22/conceitos/{ALTERADO}", em="30/09/2026")
    assert r.status_code == 422


# Lista vigente numa data


async def _vigentes_pela_lista(cliente: httpx.AsyncClient, em: str) -> dict[str, str]:
    """Percorre todas as páginas (de 7 em 7, para testar o cursor junto)."""
    achados: dict[str, str] = {}
    params: dict[str, Any] = {"vigente_em": em, "limite": 7}
    while True:
        corpo = (await _get(cliente, "/v1/tabelas/22/conceitos", **params)).json()
        achados.update({item["codigo"]: item["descricao"] for item in corpo["itens"]})
        if corpo["proximo_cursor"] is None:
            return achados
        params["cursor"] = corpo["proximo_cursor"]


@pytest.mark.parametrize(
    "em", ["2009-01-01", "2020-06-01", "2026-09-01", "2026-09-15", "2026-09-22", "2026-09-30"]
)
async def test_vigente_em_bate_com_a_consulta_codigo_a_codigo(
    cenario: httpx.AsyncClient, em: str
) -> None:
    """A lista (SQL) e a consulta de um código (regra em domain/vigencia.py) concordam."""
    codigos = [
        item["codigo"]
        for item in (await _get(cenario, "/v1/tabelas/22/conceitos", limite=200)).json()["itens"]
    ] + [INCLUIDO]
    esperado: dict[str, str] = {}
    for codigo in codigos:
        r = await _get(cenario, f"/v1/tabelas/22/conceitos/{codigo}", em=em)
        if r.status_code == 200 and r.json()["vigente"]:
            esperado[codigo] = r.json()["descricao"]

    assert await _vigentes_pela_lista(cenario, em) == esperado


async def test_vigente_em_exclui_quem_estava_fora_da_lista(cenario: httpx.AsyncClient) -> None:
    em_22 = await _vigentes_pela_lista(cenario, "2026-09-22")
    assert INCLUIDO in em_22 and REMOVIDO not in em_22
    assert em_22[ALTERADO].endswith("(teste)")
    em_26 = await _vigentes_pela_lista(cenario, "2026-09-26")
    assert INCLUIDO not in em_26 and REMOVIDO in em_26


async def test_vigente_em_nao_se_combina_com_busca(cenario: httpx.AsyncClient) -> None:
    r = await _get(cenario, "/v1/tabelas/22/conceitos", q="consulta", vigente_em="2026-09-22")
    assert r.status_code == 400


# Validação em lote


async def _validar(cliente: httpx.AsyncClient, itens: list[dict[str, Any]]) -> httpx.Response:
    return await cliente.post("/v1/validacoes", json={"itens": itens}, headers=auth())


async def test_validacao_diz_se_estava_vigente_e_por_que(cenario: httpx.AsyncClient) -> None:
    pedido = [
        {"tabela": "22", "codigo": ALTERADO, "data": "2026-09-15"},
        {"tabela": "tuss-22", "codigo": ALTERADO, "data": "2026-07-01"},
        {"tabela": "22", "codigo": REMOVIDO, "data": "2026-09-22"},
        {"tabela": "22", "codigo": INCLUIDO, "data": "2026-09-22"},
        {"tabela": "22", "codigo": "00000000", "data": "2026-09-22"},
        {"tabela": "99", "codigo": ALTERADO, "data": "2026-09-22"},
    ]
    r = await _validar(cenario, pedido)
    assert r.status_code == 200
    corpo = r.json()
    assert (corpo["vigentes"], corpo["nao_vigentes"]) == (2, 4)
    resumo = [(i["tabela"], i["codigo"], i["vigente"], i["motivo"]) for i in corpo["itens"]]
    assert resumo == [
        ("tuss-22", ALTERADO, True, "vigente"),
        ("tuss-22", ALTERADO, False, "antes_do_inicio"),
        ("tuss-22", REMOVIDO, False, "fora_da_lista"),
        ("tuss-22", INCLUIDO, True, "vigente"),
        ("tuss-22", "00000000", False, "inexistente"),
        ("99", ALTERADO, False, "tabela_inexistente"),
    ]
    primeiro, _, removido, incluido, inexistente, _ = corpo["itens"]
    assert primeiro["criterio"] == "oficial"
    assert primeiro["periodo"] == {"inicio": "2026-08-01", "fim": None}
    assert primeiro["carga_id"] == 3
    # Fora da lista: o período é o observado, até o dia em que o código saiu.
    assert (removido["criterio"], removido["periodo"]["fim"]) == ("observado", "2026-09-20")
    assert removido["descricao"] is None
    assert incluido["descricao"] == "Código de teste"
    assert (inexistente["criterio"], inexistente["periodo"]) == (None, None)


async def test_validacao_so_aceita_tabela_codigo_e_data(cenario: httpx.AsyncClient) -> None:
    item = {"tabela": "22", "codigo": ALTERADO, "data": "2026-09-15", "beneficiario": "Fulano"}
    r = await _validar(cenario, [item])
    assert r.status_code == 422
    assert r.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize("quantidade", [0, 101])
async def test_validacao_aceita_de_1_a_100_itens(
    cenario: httpx.AsyncClient, quantidade: int
) -> None:
    item = {"tabela": "22", "codigo": ALTERADO, "data": "2026-09-15"}
    assert (await _validar(cenario, [item] * quantidade)).status_code == 422
    assert (await _validar(cenario, [item] * 100)).status_code == 200


async def test_validacao_tem_limite_proprio_por_minuto(cenario: httpx.AsyncClient) -> None:
    item = {"tabela": "22", "codigo": ALTERADO, "data": "2026-09-15"}
    for _ in range(20):
        assert (await _validar(cenario, [item])).status_code == 200
    r = await _validar(cenario, [item])
    assert r.status_code == 429
    assert "validação" in r.json()["detail"]
    assert "Retry-After" in r.headers
    # Consultas comuns continuam liberadas: cada tipo tem a sua cota.
    assert (await _get(cenario, f"/v1/tabelas/22/conceitos/{ALTERADO}")).status_code == 200


# Feed de mudanças


async def _mudancas(cliente: httpx.AsyncClient, **params: Any) -> list[tuple[str, str, str]]:
    corpo = (await _get(cliente, "/v1/mudancas", **params)).json()
    return [(m["ocorrido_em"][:10], m["codigo"], m["tipo"]) for m in corpo["itens"]]


async def test_feed_traz_as_mudancas_em_ordem(cenario: httpx.AsyncClient) -> None:
    assert await _mudancas(cenario) == [
        ("2026-09-20", REMOVIDO, "removido"),
        ("2026-09-20", ALTERADO, "alterado"),
        ("2026-09-20", INCLUIDO, "incluido"),
        ("2026-09-25", REMOVIDO, "reativado"),
        ("2026-09-25", ALTERADO, "alterado"),
        ("2026-09-25", INCLUIDO, "removido"),
    ]


async def test_feed_mostra_antes_e_depois(cenario: httpx.AsyncClient) -> None:
    corpo = (await _get(cenario, "/v1/mudancas", tipo="alterado", limite=1)).json()
    (mudanca,) = corpo["itens"]
    assert (mudanca["tabela"], mudanca["carga_id"]) == ("tuss-22", 2)
    assert mudanca["campos_alterados"] == ["descricao"]
    assert mudanca["depois"]["descricao"] == mudanca["antes"]["descricao"] + " (teste)"
    assert mudanca["antes"]["inicio_vigencia"] == "2026-08-01"

    incluido = (await _get(cenario, "/v1/mudancas", tipo="incluido")).json()["itens"][0]
    assert (incluido["antes"], incluido["campos_alterados"]) == (None, None)
    assert incluido["depois"]["descricao"] == "Código de teste"


async def test_feed_filtra_por_data_tipo_e_tabela(cenario: httpx.AsyncClient) -> None:
    assert [c for _, c, _ in await _mudancas(cenario, desde="2026-09-21")] == [
        REMOVIDO,
        ALTERADO,
        INCLUIDO,
    ]
    assert await _mudancas(cenario, desde="2026-09-25T12:00:01Z") == []
    assert await _mudancas(cenario, tipo="removido") == [
        ("2026-09-20", REMOVIDO, "removido"),
        ("2026-09-25", INCLUIDO, "removido"),
    ]
    assert len(await _mudancas(cenario, tabela="tuss-22")) == 6
    assert (await _get(cenario, "/v1/mudancas", tabela="20")).status_code == 404
    assert (await _get(cenario, "/v1/mudancas", tipo="apagado")).status_code == 422


async def test_feed_pagina_por_cursor_sem_repetir(cenario: httpx.AsyncClient) -> None:
    primeira = (await _get(cenario, "/v1/mudancas", limite=4)).json()
    assert len(primeira["itens"]) == 4
    segunda = (
        await _get(cenario, "/v1/mudancas", limite=4, cursor=primeira["proximo_cursor"])
    ).json()
    assert segunda["proximo_cursor"] is None
    vistos = [(m["carga_id"], m["codigo"]) for m in primeira["itens"] + segunda["itens"]]
    assert len(vistos) == len(set(vistos)) == 6


@pytest.mark.parametrize("cursor", ["xyz", "MTAxMDEwMTI"])  # lixo; cursor de lista de conceitos
async def test_feed_com_cursor_invalido_e_400(cenario: httpx.AsyncClient, cursor: str) -> None:
    assert (await _get(cenario, "/v1/mudancas", cursor=cursor)).status_code == 400


async def test_status_mostra_o_que_a_ultima_carga_mudou(cenario: httpx.AsyncClient) -> None:
    (tabela,) = (await _get(cenario, "/v1/status")).json()["tabelas"]
    ultima = tabela["ultima_carga"]
    assert (ultima["id"], ultima["status"]) == (3, "publicada")
    assert [ultima[k] for k in ("incluidos", "alterados", "removidos", "reativados")] == [
        0,
        1,
        1,
        1,
    ]
