"""Coleta pela API com uma ANS falsa (sem rede): completa, retomada e incremental."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import BancoDeTeste
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from tuss.config import PAPEL_API, PAPEL_INGESTAO, Config
from tuss.ingestion.coleta import ColetaRecusada, DadosInvalidos, coletar
from tuss.ingestion.fonte import ClienteANS

PAGINA_TUSS_22 = Path(__file__).parents[1] / "fixtures" / "ans" / "concepts_tuss-22_page1.json"
REGISTROS: list[dict[str, Any]] = json.loads(PAGINA_TUSS_22.read_text(encoding="utf-8"))


@dataclass
class ANSFalsa:
    """Pagina `registros` de 5 em 5, como a ANS faz de 25 em 25."""

    registros: list[dict[str, Any]]
    por_pagina: int = 5
    fora_do_ar: set[int] = field(default_factory=set)  # páginas que sempre dão timeout
    paginas_informadas: int | None = None  # para simular a lista mudando no meio
    pedidas: list[int] = field(default_factory=list)

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        numero = int(pedido.url.params["page"])
        self.pedidas.append(numero)
        if numero in self.fora_do_ar:
            raise httpx.ReadTimeout("fora do ar", request=pedido)
        total = -(-len(self.registros) // self.por_pagina)
        inicio = (numero - 1) * self.por_pagina
        corpo = json.dumps(self.registros[inicio : inicio + self.por_pagina]).encode()
        return httpx.Response(
            200, content=corpo, headers={"pages": str(self.paginas_informadas or total)}
        )

    def cliente(self) -> ClienteANS:
        return ClienteANS(transporte=httpx.MockTransport(self), espera_base_s=0)


@pytest.fixture
def config(bd: BancoDeTeste, tmp_path: Path) -> Config:
    return bd.config.model_copy(update={"snapshots_dir": tmp_path / "snapshots"})


async def _coletar(ans: ANSFalsa, config: Config, **kwargs: Any) -> Any:
    async with ans.cliente() as cliente:
        return await coletar("tuss-22", cliente, config, **kwargs)


async def _um(bd: BancoDeTeste, sql: str, papel: str = PAPEL_API) -> Any:
    return (await bd.executar(papel, sql))[0][0]


async def test_primeira_coleta_e_completa(bd: BancoDeTeste, config: Config) -> None:
    ans = ANSFalsa(REGISTROS)
    resultado = await _coletar(ans, config)

    assert (resultado.modo, resultado.status, resultado.paginas_lidas) == (
        "completa",
        "publicada",
        5,
    )
    assert (resultado.total, resultado.contagem["incluido"]) == (25, 25)
    assert ans.pedidas == [1, 2, 3, 4, 5]
    carga = (
        await bd.executar(
            PAPEL_API, "SELECT origem, modo, checkpoint, paginas, sha256_snapshot FROM carga"
        )
    )[0]
    assert (carga.origem, carga.modo, carga.checkpoint, carga.paginas) == ("api", "completa", 5, 5)
    assert len(carga.sha256_snapshot) == 64
    snapshots = sorted((config.snapshots_dir / "tuss-22" / "carga-1").iterdir())
    assert [s.name for s in snapshots] == [f"pagina-0000{n}.json.gz" for n in range(1, 6)]
    assert await _um(bd, "SELECT count(*) FROM evento_mudanca") == 0  # carga inicial


async def test_coleta_completa_retoma_de_onde_parou(bd: BancoDeTeste, config: Config) -> None:
    ans = ANSFalsa(REGISTROS, fora_do_ar={3})
    parou = await _coletar(ans, config)
    assert (parou.status, parou.carga_id) == ("em_andamento", 1)
    assert "4 tentativas" in (parou.motivo or "")
    assert await _um(bd, "SELECT checkpoint FROM carga") == 2
    assert await _um(bd, "SELECT count(*) FROM stg_conceito", PAPEL_INGESTAO) == 10

    ans.fora_do_ar.clear()
    ans.pedidas.clear()
    resultado = await _coletar(ans, config)
    assert (resultado.carga_id, resultado.status, resultado.total) == (1, "publicada", 25)
    assert ans.pedidas == [3, 4, 5]  # não relê o que já tinha


async def test_lista_mudando_no_meio_faz_recomecar(bd: BancoDeTeste, config: Config) -> None:
    ans = ANSFalsa(REGISTROS, fora_do_ar={3})
    await _coletar(ans, config)
    ans.fora_do_ar.clear()
    ans.paginas_informadas = 6  # a ANS publicou algo enquanto isso

    with pytest.raises(DadosInvalidos, match="de 5 para 6 páginas"):
        await _coletar(ans, config)
    assert await _um(bd, "SELECT status FROM carga WHERE id = 1") == "falhou"
    assert await _um(bd, "SELECT count(*) FROM stg_conceito", PAPEL_INGESTAO) == 0

    ans.paginas_informadas = None
    resultado = await _coletar(ans, config)
    assert (resultado.carga_id, resultado.status) == (2, "publicada")


async def test_incremental_le_so_o_topo_e_nao_remove(bd: BancoDeTeste, config: Config) -> None:
    await _coletar(ANSFalsa(REGISTROS), config)

    novos = [{**REGISTROS[0], "id": f"9999999{n}", "display_name": f"Novo {n}"} for n in (1, 2)]
    alterado = {**REGISTROS[0], "display_name": REGISTROS[0]["display_name"] + " (revisado)"}
    # Dois novos no topo, o primeiro alterado, e o último código fora da lista.
    ans = ANSFalsa([*novos, alterado, *REGISTROS[1:-1]])
    resultado = await _coletar(ans, config)

    assert (resultado.modo, resultado.status) == ("incremental", "publicada")
    assert ans.pedidas == [1, 2, 3]  # página 1 com novidade; 2 e 3 sem: para
    contagem = resultado.contagem
    assert (contagem["incluido"], contagem["alterado"], contagem["removido"]) == (2, 1, 0)
    assert resultado.total == 27
    ultimo = REGISTROS[-1]["id"]
    assert (
        await _um(
            bd,
            f"""SELECT count(*) FROM conceito c JOIN conceito_versao v ON v.conceito_id = c.id
                WHERE c.codigo = '{ultimo}' AND v.publicado_ate IS NULL""",
        )
        == 1
    )  # não lido não é removido
    assert await _um(bd, "SELECT modo FROM carga WHERE id = 2") == "incremental"


async def test_incremental_sem_novidade(bd: BancoDeTeste, config: Config) -> None:
    await _coletar(ANSFalsa(REGISTROS), config)
    ans = ANSFalsa(REGISTROS)
    resultado = await _coletar(ans, config)
    assert (resultado.status, ans.pedidas) == ("sem_mudanca", [1, 2])
    assert await _um(bd, "SELECT carga_atual_id FROM tabela_tuss") == 1


async def test_incremental_exige_carga_anterior(config: Config) -> None:
    with pytest.raises(ColetaRecusada, match="primeira precisa ser completa"):
        await _coletar(ANSFalsa(REGISTROS), config, modo="incremental")


async def test_completa_pedida_explicitamente_detecta_remocao(config: Config) -> None:
    await _coletar(ANSFalsa(REGISTROS), config)
    resultado = await _coletar(ANSFalsa(REGISTROS[:-1]), config, modo="completa")
    # 1 de 25 removido (4%): passa do limite de anomalia.
    assert (resultado.status, resultado.contagem["removido"]) == ("retida", 1)
    with pytest.raises(ColetaRecusada, match="retida"):
        await _coletar(ANSFalsa(REGISTROS), config)


@pytest.mark.parametrize(
    "estragar",
    [
        lambda r: {**r, "source": "tuss-20"},  # conceito de outra tabela
        lambda r: {**r, "extras": {"inicio_vigencia": "31/12/2020"}},  # data fora do formato
    ],
)
async def test_dado_invalido_da_ans_falha_alto(
    bd: BancoDeTeste, config: Config, estragar: Any
) -> None:
    registros = [*REGISTROS[:7], estragar(REGISTROS[7]), *REGISTROS[8:]]
    with pytest.raises(DadosInvalidos, match="página 2"):
        await _coletar(ANSFalsa(registros), config)
    assert await _um(bd, "SELECT status FROM carga") == "falhou"
    assert await _um(bd, "SELECT count(*) FROM stg_conceito", PAPEL_INGESTAO) == 0
    assert await _um(bd, "SELECT count(*) FROM conceito") == 0


async def test_duas_coletas_da_mesma_tabela_ao_mesmo_tempo(config: Config) -> None:
    engine = create_async_engine(config.url_banco(PAPEL_INGESTAO), poolclass=pool.NullPool)
    try:
        async with engine.connect() as outra:  # como se outra coleta estivesse rodando
            await outra.execute(text("SELECT pg_advisory_lock(hashtext('coleta:tuss-22'))"))
            with pytest.raises(ColetaRecusada, match="outra coleta de tuss-22"):
                await _coletar(ANSFalsa(REGISTROS), config)
    finally:
        await engine.dispose()
    # Solta a trava ao terminar: a próxima coleta roda.
    assert (await _coletar(ANSFalsa(REGISTROS), config)).status == "publicada"


async def test_incremental_interrompida_e_refeita(bd: BancoDeTeste, config: Config) -> None:
    await _coletar(ANSFalsa(REGISTROS), config)
    # Processo parado no meio de uma incremental: a carga ficou em andamento.
    await bd.executar(
        PAPEL_INGESTAO,
        "INSERT INTO carga (tabela_id, origem, modo, checkpoint)"
        " VALUES (1, 'api', 'incremental', 1)",
    )

    resultado = await _coletar(ANSFalsa(REGISTROS), config)

    assert (resultado.carga_id, resultado.status) == (3, "sem_mudanca")
    abandonada = (await bd.executar(PAPEL_API, "SELECT status, erro FROM carga WHERE id = 2"))[0]
    assert tuple(abandonada) == ("falhou", "interrompida no meio; refeita")


async def test_completa_em_trechos(bd: BancoDeTeste, config: Config) -> None:
    ans = ANSFalsa(REGISTROS)
    primeiro = await _coletar(ans, config, max_paginas=2)
    assert (primeiro.status, primeiro.paginas_lidas) == ("em_andamento", 2)
    assert "limite desta execução" in (primeiro.motivo or "")
    assert await _um(bd, "SELECT checkpoint FROM carga") == 2

    segundo = await _coletar(ans, config, max_paginas=2)
    assert (segundo.carga_id, segundo.status, segundo.paginas_lidas) == (1, "em_andamento", 2)

    terceiro = await _coletar(ans, config, max_paginas=2)
    assert (terceiro.carga_id, terceiro.status, terceiro.total) == (1, "publicada", 25)
    assert ans.pedidas == [1, 2, 3, 4, 5]


async def test_incremental_em_trechos_continua_de_onde_parou(
    bd: BancoDeTeste, config: Config
) -> None:
    # Arquivo atrasado: 15 códigos novos (3 páginas) antes dos já conhecidos.
    await _coletar(ANSFalsa(REGISTROS), config)
    novos = [
        {**REGISTROS[0], "id": f"900000{n:02d}", "display_name": f"Novo {n}"} for n in range(15)
    ]
    ans = ANSFalsa([*novos, *REGISTROS])

    primeiro = await _coletar(ans, config, max_paginas=2)
    assert (primeiro.status, primeiro.continua_em) == ("publicada", 3)
    assert primeiro.contagem["incluido"] == 10
    assert ans.pedidas == [1, 2]

    # A ANS inclui mais um no topo: a lista anda uma posição, e o trecho seguinte relê
    # um código já publicado em vez de pular algum.
    mais_novo = {**REGISTROS[0], "id": "90000099", "display_name": "Mais novo"}
    ans = ANSFalsa([mais_novo, *novos, *REGISTROS])
    segundo = await _coletar(ans, config, max_paginas=2)
    assert (segundo.status, segundo.continua_em, ans.pedidas) == ("publicada", 5, [3, 4])
    assert segundo.contagem["incluido"] == 5

    ans.pedidas.clear()
    terceiro = await _coletar(ans, config, max_paginas=2)  # 2 páginas conhecidas: em dia
    assert (terceiro.status, terceiro.continua_em, ans.pedidas) == ("sem_mudanca", None, [5, 6])

    ans.pedidas.clear()
    quarto = await _coletar(ans, config, max_paginas=5)  # em dia: volta ao topo
    assert (quarto.status, quarto.continua_em, ans.pedidas) == ("publicada", None, [1, 2, 3])
    assert (quarto.contagem["incluido"], quarto.total) == (1, 41)
    continua = await bd.executar(PAPEL_API, "SELECT id, continua_em FROM carga ORDER BY id")
    assert [tuple(c) for c in continua] == [(1, None), (2, 3), (3, 5), (4, None), (5, None)]
