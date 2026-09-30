"""Cliente da ANS com transporte falso: sem rede, respostas reais gravadas em fixtures."""

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from tuss.ingestion.fonte import (
    MAX_BYTES_RESPOSTA,
    TENTATIVAS,
    USER_AGENT,
    ClienteANS,
    FonteIndisponivel,
    RespostaInvalida,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ans"
CATALOGO = (FIXTURES / "source.json").read_bytes()
PAGINA_22 = (FIXTURES / "concepts_tuss-22_page1.json").read_bytes()


def _cliente(responder: Callable[[httpx.Request], httpx.Response]) -> ClienteANS:
    return ClienteANS(transporte=httpx.MockTransport(responder), espera_base_s=0)


async def test_catalogo_real() -> None:
    async with _cliente(lambda r: httpx.Response(200, content=CATALOGO)) as ans:
        catalogo = await ans.catalogo()
    assert len(catalogo) == 65
    tabela_22 = next(t for t in catalogo if t.codigo == "tuss-22")
    assert (tabela_22.descricao, tabela_22.total) == ("Procedimentos em saúde", 5964)


async def test_pagina_real_com_total_de_paginas() -> None:
    pedidos: list[httpx.Request] = []

    def responder(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        return httpx.Response(200, content=PAGINA_22, headers={"pages": "239"})

    async with _cliente(responder) as ans:
        pagina = await ans.pagina("tuss-22", 1)
    assert (pagina.total_paginas, len(pagina.registros), pagina.bruto) == (239, 25, PAGINA_22)
    assert pedidos[0].url.path.endswith("/ANS/concepts/tuss-22")
    assert pedidos[0].url.params["page"] == "1"
    assert pedidos[0].headers["user-agent"] == USER_AGENT


async def test_falha_temporaria_tenta_de_novo() -> None:
    respostas = iter([httpx.Response(503), httpx.Response(429)])

    def responder(pedido: httpx.Request) -> httpx.Response:
        return next(respostas, httpx.Response(200, content=PAGINA_22, headers={"pages": "1"}))

    async with _cliente(responder) as ans:
        assert len((await ans.pagina("tuss-22", 1)).registros) == 25


async def test_rede_fora_do_ar_desiste_depois_das_tentativas() -> None:
    chamadas = 0

    def responder(pedido: httpx.Request) -> httpx.Response:
        nonlocal chamadas
        chamadas += 1
        raise httpx.ReadTimeout("demorou", request=pedido)

    async with _cliente(responder) as ans:
        with pytest.raises(FonteIndisponivel, match="4 tentativas"):
            await ans.catalogo()
    assert chamadas == TENTATIVAS


@pytest.mark.parametrize("status", [302, 404])
async def test_erro_definitivo_nao_tenta_de_novo(status: int) -> None:
    chamadas = 0

    def responder(pedido: httpx.Request) -> httpx.Response:
        nonlocal chamadas
        chamadas += 1
        return httpx.Response(status, headers={"location": "https://outro.example/"})

    async with _cliente(responder) as ans:
        with pytest.raises(RespostaInvalida, match=f"HTTP {status}"):
            await ans.catalogo()
    assert chamadas == 1  # redirecionamento não é seguido


async def test_resposta_grande_demais_e_recusada() -> None:
    enorme = b"[" + b" " * MAX_BYTES_RESPOSTA + b"]"
    async with _cliente(lambda r: httpx.Response(200, content=enorme)) as ans:
        with pytest.raises(RespostaInvalida, match="maior que"):
            await ans.catalogo()


@pytest.mark.parametrize(
    ("corpo", "cabecalhos", "erro"),
    [
        (PAGINA_22, {}, "pages"),
        (PAGINA_22, {"pages": "muitas"}, "pages"),
        (b"<html>fora do ar</html>", {"pages": "1"}, "JSON"),
        (b'{"erro": 1}', {"pages": "1"}, "array"),
    ],
)
async def test_pagina_malformada_e_recusada(
    corpo: bytes, cabecalhos: dict[str, str], erro: str
) -> None:
    async with _cliente(lambda r: httpx.Response(200, content=corpo, headers=cabecalhos)) as ans:
        with pytest.raises(RespostaInvalida, match=erro):
            await ans.pagina("tuss-22", 1)


@pytest.mark.parametrize(
    "catalogo",
    [
        b'[{"Codigo": "tabela-x", "Descricao": "X", "Total_sources": 1}]',
        b'[{"Codigo": "tuss-1", "Descricao": null, "Total_sources": 1}]',
        b'[{"Codigo": "tuss-1", "Descricao": "X", "Total_sources": -1}]',
        b'["tuss-1"]',
    ],
)
async def test_catalogo_malformado_e_recusado(catalogo: bytes) -> None:
    async with _cliente(lambda r: httpx.Response(200, content=catalogo)) as ans:
        with pytest.raises(RespostaInvalida):
            await ans.catalogo()


@pytest.mark.parametrize(
    "url", ["http://consulta-ocl.apps.sa-1a.mendixcloud.com/x", "https://example.com/x"]
)
def test_so_aceita_a_ans_por_https(url: str) -> None:
    with pytest.raises(ValueError, match="fora da lista"):
        ClienteANS(base_url=url)


@pytest.mark.parametrize(("tabela", "numero"), [("22", 1), ("tuss-22/../x", 1), ("tuss-22", 0)])
async def test_parametros_invalidos_nem_chegam_a_rede(tabela: str, numero: int) -> None:
    async with _cliente(lambda r: pytest.fail("não deveria chamar a rede")) as ans:
        with pytest.raises(ValueError):
            await ans.pagina(tabela, numero)
