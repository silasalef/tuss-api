"""`tuss importar` de ponta a ponta: arquivo real da ANS → banco → leitura pelo papel api."""

import gzip
import json
from pathlib import Path

import pytest
from conftest import BancoDeTeste

from tuss.config import PAPEL_API, Config
from tuss.ingestion.importacao import ImportacaoRecusada, importar
from tuss.ingestion.lote import ler_lote

PAGINA_TUSS_22 = Path(__file__).parents[1] / "fixtures" / "ans" / "concepts_tuss-22_page1.json"


@pytest.fixture
def config(bd: BancoDeTeste, tmp_path: Path) -> Config:
    # Depende de `bd` para o banco ser limpo depois de todo teste que importa.
    return bd.config.model_copy(update={"snapshots_dir": tmp_path / "snapshots"})


async def _contar(bd: BancoDeTeste, tabela: str, onde: str = "true") -> int:
    linhas = await bd.executar(PAPEL_API, f"SELECT count(*) FROM {tabela} WHERE {onde}")
    return int(linhas[0][0])


async def test_importa_e_publica_a_carga_inicial(bd: BancoDeTeste, config: Config) -> None:
    lote = ler_lote(PAGINA_TUSS_22)
    resultado = await importar(lote, config)

    assert (resultado.status, resultado.tabela, resultado.incluidos) == ("publicada", "tuss-22", 25)
    assert await _contar(bd, "conceito") == 25
    assert await _contar(bd, "conceito_versao", "publicado_ate IS NULL") == 25
    assert await _contar(bd, "evento_mudanca") == 0  # carga inicial é o ponto de partida

    carga = (
        await bd.executar(PAPEL_API, "SELECT status, total, incluidos, sha256_snapshot FROM carga")
    )[0]
    assert tuple(carga) == ("publicada", 25, 25, lote.fonte.sha256)
    tabela = (
        await bd.executar(PAPEL_API, "SELECT carga_atual_id, ultima_sync_em FROM tabela_tuss")
    )[0]
    assert tabela.carga_atual_id == resultado.carga_id
    assert tabela.ultima_sync_em is not None


async def test_conceito_publicado_tem_os_dados_do_arquivo(bd: BancoDeTeste, config: Config) -> None:
    await importar(ler_lote(PAGINA_TUSS_22), config)
    original = json.loads(PAGINA_TUSS_22.read_text(encoding="utf-8"))[0]
    versao = (
        await bd.executar(
            PAPEL_API,
            f"""SELECT v.descricao, v.inicio_vigencia::text, v.fim_vigencia, v.fim_implantacao::text
                FROM conceito_versao v JOIN conceito c ON c.id = v.conceito_id
                WHERE c.codigo = '{original["id"]}'""",
        )
    )[0]
    assert tuple(versao) == (
        original["display_name"],
        original["extras"]["inicio_vigencia"],
        None,  # "-" no arquivo = período aberto
        original["extras"]["fim_implantacao"],
    )


async def test_snapshot_guarda_o_arquivo_como_veio(config: Config) -> None:
    resultado = await importar(ler_lote(PAGINA_TUSS_22), config)
    assert resultado.snapshot is not None
    assert resultado.snapshot.parent == config.snapshots_dir / "tuss-22"
    assert gzip.decompress(resultado.snapshot.read_bytes()) == PAGINA_TUSS_22.read_bytes()


async def test_mesmo_arquivo_de_novo_so_registra_sem_mudanca(
    bd: BancoDeTeste, config: Config
) -> None:
    await importar(ler_lote(PAGINA_TUSS_22), config)
    resultado = await importar(ler_lote(PAGINA_TUSS_22), config)

    assert resultado.status == "sem_mudanca"
    assert await _contar(bd, "carga") == 2
    assert await _contar(bd, "conceito_versao") == 25


async def test_arquivo_diferente_para_tabela_ja_carregada_e_recusado(
    bd: BancoDeTeste, config: Config, tmp_path: Path
) -> None:
    await importar(ler_lote(PAGINA_TUSS_22), config)
    menor = tmp_path / "tuss-22-menor.json"
    menor.write_text(PAGINA_TUSS_22.read_text(encoding="utf-8").replace("Ablação", "Ablacao"))

    with pytest.raises(ImportacaoRecusada, match="Fase 3"):
        await importar(ler_lote(menor), config)
    assert await _contar(bd, "carga") == 1


async def test_arquivo_com_problema_nao_grava_nada(
    bd: BancoDeTeste, config: Config, tmp_path: Path
) -> None:
    registros = json.loads(PAGINA_TUSS_22.read_text(encoding="utf-8"))
    ruim = tmp_path / "tuss-22-ruim.json"
    ruim.write_text(json.dumps([*registros, registros[0]]))  # código duplicado

    with pytest.raises(ImportacaoRecusada, match="duplicado"):
        await importar(ler_lote(ruim), config)
    assert await _contar(bd, "carga") == 0


async def test_falha_no_meio_marca_carga_e_nao_publica_nada(
    bd: BancoDeTeste, config: Config, tmp_path: Path
) -> None:
    bloqueio = tmp_path / "nao-e-pasta"
    bloqueio.write_text("")  # snapshot não consegue criar pasta aqui
    quebrada = config.model_copy(update={"snapshots_dir": bloqueio})

    with pytest.raises(OSError):
        await importar(ler_lote(PAGINA_TUSS_22), quebrada)
    carga = (await bd.executar(PAPEL_API, "SELECT status, erro FROM carga"))[0]
    assert carga.status == "falhou"
    assert "nao-e-pasta" in carga.erro
    assert await _contar(bd, "conceito") == 0

    # A carga que falhou não trava a próxima tentativa.
    resultado = await importar(ler_lote(PAGINA_TUSS_22), config)
    assert resultado.status == "publicada"
