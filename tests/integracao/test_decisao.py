"""Limite de anomalia: carga retida, aprovação e descarte pelo CLI."""

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import BancoDeTeste

from tuss.config import PAPEL_API, PAPEL_INGESTAO, Config
from tuss.ingestion.decisao import DecisaoRecusada, aprovar, descartar
from tuss.ingestion.importacao import ImportacaoRecusada, ResultadoImportacao, importar
from tuss.ingestion.lote import ler_lote

PAGINA_TUSS_22 = Path(__file__).parents[1] / "fixtures" / "ans" / "concepts_tuss-22_page1.json"


@pytest.fixture
def config(bd: BancoDeTeste, tmp_path: Path) -> Config:
    return bd.config.model_copy(update={"snapshots_dir": tmp_path / "snapshots"})


async def _retida(config: Config, tmp_path: Path) -> ResultadoImportacao:
    """Carga inicial com 25 conceitos e depois um arquivo sem 5 deles (20%)."""
    await importar(ler_lote(PAGINA_TUSS_22), config)
    registros: list[dict[str, Any]] = json.loads(PAGINA_TUSS_22.read_text(encoding="utf-8"))
    menor = tmp_path / "menor.json"
    menor.write_text(json.dumps(registros[5:], ensure_ascii=False), encoding="utf-8")
    resultado = await importar(ler_lote(menor), config)
    assert resultado.status == "retida"
    return resultado


async def _um(bd: BancoDeTeste, sql: str, papel: str = PAPEL_API) -> Any:
    return (await bd.executar(papel, sql))[0][0]


async def test_carga_retida_nao_publica_nada(
    bd: BancoDeTeste, config: Config, tmp_path: Path
) -> None:
    resultado = await _retida(config, tmp_path)

    assert resultado.removidos == 5
    assert resultado.motivo_retencao == "remove 5 de 25 conceitos (20.0%; limite 2%)"
    carga = (
        await bd.executar(
            PAPEL_API, f"SELECT status, erro, total FROM carga WHERE id = {resultado.carga_id}"
        )
    )[0]
    assert (carga.status, carga.total) == ("retida", 20)
    assert carga.erro.startswith("retida: remove 5 de 25")
    assert await _um(bd, "SELECT count(*) FROM conceito_versao WHERE publicado_ate IS NULL") == 25
    assert await _um(bd, "SELECT count(*) FROM evento_mudanca") == 0
    assert await _um(bd, "SELECT carga_atual_id FROM tabela_tuss") == 1
    # O rascunho fica guardado para a aprovação.
    assert await _um(bd, "SELECT count(*) FROM stg_conceito", PAPEL_INGESTAO) == 20


async def test_carga_retida_impede_outra_carga(config: Config, tmp_path: Path) -> None:
    resultado = await _retida(config, tmp_path)
    with pytest.raises(ImportacaoRecusada, match=f"tuss aprovar {resultado.carga_id}"):
        await importar(ler_lote(PAGINA_TUSS_22), config)


async def test_aprovar_publica_a_carga_retida(
    bd: BancoDeTeste, config: Config, tmp_path: Path
) -> None:
    resultado = await _retida(config, tmp_path)

    publicado = await aprovar(resultado.carga_id, config)

    assert (publicado.status, publicado.total, publicado.contagem["removido"]) == (
        "publicada",
        20,
        5,
    )
    assert await _um(bd, "SELECT count(*) FROM conceito_versao WHERE publicado_ate IS NULL") == 20
    assert await _um(bd, "SELECT count(*) FROM evento_mudanca WHERE tipo = 'removido'") == 5
    assert await _um(bd, "SELECT carga_atual_id FROM tabela_tuss") == resultado.carga_id
    erro = await _um(bd, f"SELECT erro FROM carga WHERE id = {resultado.carga_id}")
    assert erro.startswith("aprovada manualmente; retida: remove 5")
    assert await _um(bd, "SELECT count(*) FROM stg_conceito", PAPEL_INGESTAO) == 0
    with pytest.raises(DecisaoRecusada, match="não está retida"):
        await aprovar(resultado.carga_id, config)


async def test_descartar_nao_publica_e_libera_a_tabela(
    bd: BancoDeTeste, config: Config, tmp_path: Path
) -> None:
    resultado = await _retida(config, tmp_path)

    await descartar(resultado.carga_id, config)

    carga = (
        await bd.executar(
            PAPEL_API, f"SELECT status, erro FROM carga WHERE id = {resultado.carga_id}"
        )
    )[0]
    assert carga.status == "falhou"
    assert carga.erro.startswith("descartada; retida:")
    assert await _um(bd, "SELECT count(*) FROM conceito_versao WHERE publicado_ate IS NULL") == 25
    assert await _um(bd, "SELECT count(*) FROM stg_conceito", PAPEL_INGESTAO) == 0
    # A tabela volta a aceitar cargas.
    assert (await importar(ler_lote(PAGINA_TUSS_22), config)).status == "sem_mudanca"


async def test_aprovar_sem_rascunho_pede_para_refazer(
    bd: BancoDeTeste, config: Config, tmp_path: Path
) -> None:
    resultado = await _retida(config, tmp_path)
    await bd.executar(PAPEL_INGESTAO, "DELETE FROM stg_conceito")  # como se o banco tivesse caído

    with pytest.raises(DecisaoRecusada, match="rascunho .* se perdeu"):
        await aprovar(resultado.carga_id, config)
    assert await _um(bd, f"SELECT status FROM carga WHERE id = {resultado.carga_id}") == "retida"


async def test_so_decide_carga_retida(config: Config) -> None:
    await importar(ler_lote(PAGINA_TUSS_22), config)
    with pytest.raises(DecisaoRecusada, match="não está retida"):
        await descartar(1, config)
    with pytest.raises(DecisaoRecusada, match="não existe"):
        await aprovar(999, config)
