"""Ciclo diário do worker com uma ANS falsa: catálogo e várias tabelas."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import BancoDeTeste

from tuss.config import PAPEL_API, Config
from tuss.ingestion.ciclo import AVISO_ARQUIVO_NOVO, executar_ciclo
from tuss.ingestion.coleta import coletar
from tuss.ingestion.fonte import ClienteANS

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ans"
TUSS_22: list[dict[str, Any]] = json.loads(
    (FIXTURES / "concepts_tuss-22_page1.json").read_text(encoding="utf-8")
)
TUSS_23: list[dict[str, Any]] = json.loads(
    (FIXTURES / "tuss-23_20260929.json").read_text(encoding="utf-8")
)
CATALOGO = [
    {"Codigo": "tuss-19", "Descricao": "OPME", "Total_sources": 1389786},
    {"Codigo": "tuss-22", "Descricao": "Procedimentos em saúde", "Total_sources": 25},
    {"Codigo": "tuss-23", "Descricao": "Caráter do atendimento", "Total_sources": len(TUSS_23)},
]
AGORA = datetime(2026, 10, 1, 6, tzinfo=UTC)


class ANSFalsa:
    def __init__(self, tabelas: dict[str, list[dict[str, Any]]], fora_do_ar: bool = False):
        self.tabelas = tabelas
        self.fora_do_ar = fora_do_ar
        self.pedidas: list[str] = []

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        if pedido.url.path.endswith("/ANS/source"):
            return httpx.Response(200, content=json.dumps(CATALOGO).encode())
        tabela = pedido.url.path.rsplit("/", 1)[1]
        self.pedidas.append(tabela)
        if self.fora_do_ar:
            raise httpx.ConnectError("fora do ar", request=pedido)
        registros = self.tabelas[tabela]
        numero = int(pedido.url.params["page"])
        pagina = registros[(numero - 1) * 25 : numero * 25]
        paginas = str(-(-len(registros) // 25))
        return httpx.Response(200, content=json.dumps(pagina).encode(), headers={"pages": paginas})


@pytest.fixture
def config(bd: BancoDeTeste, tmp_path: Path) -> Config:
    return bd.config.model_copy(update={"snapshots_dir": tmp_path / "snapshots"})


async def _ciclo(ans: ANSFalsa, config: Config) -> list[tuple[str, str]]:
    async with ClienteANS(transporte=httpx.MockTransport(ans), espera_base_s=0) as cliente:
        return [(r.tabela, r.status) for r in await executar_ciclo(cliente, config, AGORA)]


async def test_primeiro_ciclo_carrega_as_tabelas_possiveis(
    bd: BancoDeTeste, config: Config
) -> None:
    ans = ANSFalsa({"tuss-22": TUSS_22, "tuss-23": TUSS_23})

    assert await _ciclo(ans, config) == [
        ("tuss-19", "pulada"),  # grande demais: carga inicial por arquivo
        ("tuss-22", "publicada"),
        ("tuss-23", "publicada"),
    ]
    assert "tuss-19" not in ans.pedidas
    linhas = await bd.executar(
        PAPEL_API,
        "SELECT t.codigo, t.descricao, c.total FROM tabela_tuss t"
        " LEFT JOIN carga c ON c.id = t.carga_atual_id ORDER BY 1",
    )
    assert [tuple(linha) for linha in linhas] == [
        ("tuss-19", "OPME", None),
        ("tuss-22", "Procedimentos em saúde", 25),
        ("tuss-23", "Caráter do atendimento", len(TUSS_23)),
    ]

    # No dia seguinte, sem novidade na ANS.
    assert await _ciclo(ans, config) == [
        ("tuss-19", "pulada"),
        ("tuss-22", "sem_mudanca"),
        ("tuss-23", "sem_mudanca"),
    ]


async def test_ans_fora_do_ar_nao_derruba_o_ciclo(bd: BancoDeTeste, config: Config) -> None:
    ans = ANSFalsa({}, fora_do_ar=True)
    resultados = await _ciclo(ans, config)
    # Tabelas novas vão em modo completo: com a fonte fora, ficam em andamento para retomar.
    assert resultados == [
        ("tuss-19", "pulada"),
        ("tuss-22", "em_andamento"),
        ("tuss-23", "em_andamento"),
    ]
    status = await bd.executar(PAPEL_API, "SELECT status, checkpoint FROM carga ORDER BY id")
    assert [tuple(s) for s in status] == [("em_andamento", 0), ("em_andamento", 0)]

    # A ANS volta: o ciclo seguinte retoma as duas.
    ans.fora_do_ar = False
    ans.tabelas = {"tuss-22": TUSS_22, "tuss-23": TUSS_23}
    assert await _ciclo(ans, config) == [
        ("tuss-19", "pulada"),
        ("tuss-22", "publicada"),
        ("tuss-23", "publicada"),
    ]


async def test_novidade_em_tabela_gigante_avisa_para_conferir_o_portal(
    config: Config, tmp_path: Path
) -> None:
    # tuss-19 carregada (aqui, com poucos registros); o catálogo diz 1,4 milhão.
    opme = [{**r, "source": "tuss-19"} for r in TUSS_22]
    ans = ANSFalsa({"tuss-19": opme})
    async with ClienteANS(transporte=httpx.MockTransport(ans), espera_base_s=0) as cliente:
        await coletar("tuss-19", cliente, config, modo="completa")

    novo = {**opme[0], "id": "99999999", "display_name": "OPME nova"}
    ans.tabelas = {"tuss-19": [novo, *opme], "tuss-22": TUSS_22, "tuss-23": TUSS_23}
    async with ClienteANS(transporte=httpx.MockTransport(ans), espera_base_s=0) as cliente:
        resultados = await executar_ciclo(cliente, config, AGORA)

    opme_resultado = next(r for r in resultados if r.tabela == "tuss-19")
    assert (opme_resultado.status, opme_resultado.detalhe) == ("publicada", AVISO_ARQUIVO_NOVO)
    assert all(r.detalhe != AVISO_ARQUIVO_NOVO for r in resultados if r.tabela != "tuss-19")
