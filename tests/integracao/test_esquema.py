"""Regras que o próprio banco garante (migration 0001), testadas num Postgres real."""

import pytest
from conftest import BancoDeTeste, migrar
from sqlalchemy.exc import DBAPIError

from tuss.config import PAPEL_API, PAPEL_INGESTAO, Config

# Uma tabela, uma carga e um conceito com sua primeira versão publicada em 01/09.
CENARIO = (
    "INSERT INTO tabela_tuss (codigo, descricao) VALUES ('tuss-22', 'Procedimentos')",
    "INSERT INTO carga (tabela_id, origem, arquivo_nome) VALUES (1, 'arquivo', 'tuss-22.zip')",
    "INSERT INTO conceito (tabela_id, codigo) VALUES (1, '00010014')",
    """INSERT INTO conceito_versao (conceito_id, tabela_id, carga_id, descricao, hash_conteudo,
       publicado_de) VALUES (1, 1, 1, 'Consulta', repeat('a', 64), '2026-09-01')""",
)
NOVA_VERSAO = """
    INSERT INTO conceito_versao (conceito_id, tabela_id, carga_id, descricao, hash_conteudo,
    publicado_de) VALUES (1, 1, 1, 'Consulta em consultório', repeat('b', 64), '2026-09-15')"""
FECHA_VERSAO_ATUAL = """
    UPDATE conceito_versao SET publicado_ate = '2026-09-15'
    WHERE conceito_id = 1 AND publicado_ate IS NULL"""


# Histórico


async def test_codigo_guarda_zeros_a_esquerda_e_numero_vem_do_nome(bd: BancoDeTeste) -> None:
    await bd.executar(PAPEL_INGESTAO, *CENARIO)
    linhas = await bd.executar(
        PAPEL_INGESTAO,
        "SELECT t.numero, c.codigo FROM conceito c JOIN tabela_tuss t ON t.id = c.tabela_id",
    )
    assert [tuple(linha) for linha in linhas] == [("22", "00010014")]


async def test_nova_versao_sem_fechar_a_atual_e_recusada(bd: BancoDeTeste) -> None:
    await bd.executar(PAPEL_INGESTAO, *CENARIO)
    with pytest.raises(DBAPIError, match="exclusion constraint"):
        await bd.executar(PAPEL_INGESTAO, NOVA_VERSAO)


async def test_fechar_a_atual_e_abrir_a_nova_na_mesma_transacao(bd: BancoDeTeste) -> None:
    await bd.executar(PAPEL_INGESTAO, *CENARIO)
    await bd.executar(PAPEL_INGESTAO, FECHA_VERSAO_ATUAL, NOVA_VERSAO)
    linhas = await bd.executar(
        PAPEL_API,
        "SELECT descricao, publicado_ate IS NULL FROM conceito_versao ORDER BY publicado_de",
    )
    assert [tuple(linha) for linha in linhas] == [
        ("Consulta", False),
        ("Consulta em consultório", True),
    ]


async def test_versao_nao_termina_antes_de_comecar(bd: BancoDeTeste) -> None:
    await bd.executar(PAPEL_INGESTAO, *CENARIO)
    with pytest.raises(DBAPIError, match="conceito_versao_check"):
        await bd.executar(PAPEL_INGESTAO, "UPDATE conceito_versao SET publicado_ate = '2026-08-01'")


@pytest.mark.parametrize("codigo", ["", " 123", "123 "])
async def test_codigo_vazio_ou_com_espaco_e_recusado(bd: BancoDeTeste, codigo: str) -> None:
    await bd.executar(PAPEL_INGESTAO, *CENARIO)
    with pytest.raises(DBAPIError, match="conceito_codigo_check"):
        await bd.executar(
            PAPEL_INGESTAO, f"INSERT INTO conceito (tabela_id, codigo) VALUES (1, '{codigo}')"
        )


async def test_tabela_fora_do_padrao_tuss_n_e_recusada(bd: BancoDeTeste) -> None:
    with pytest.raises(DBAPIError, match="tabela_tuss_codigo_check"):
        await bd.executar(
            PAPEL_INGESTAO, "INSERT INTO tabela_tuss (codigo, descricao) VALUES ('22', 'x')"
        )


async def test_uma_carga_em_andamento_por_tabela(bd: BancoDeTeste) -> None:
    await bd.executar(PAPEL_INGESTAO, *CENARIO)
    nova_carga = "INSERT INTO carga (tabela_id, origem) VALUES (1, 'api')"
    with pytest.raises(DBAPIError, match="carga_uma_em_andamento_idx"):
        await bd.executar(PAPEL_INGESTAO, nova_carga)
    # Terminada a primeira, a próxima pode começar.
    await bd.executar(PAPEL_INGESTAO, "UPDATE carga SET status = 'publicada'", nova_carga)


# Papéis


@pytest.mark.parametrize(
    "tabela", ["tabela_tuss", "carga", "conceito", "conceito_versao", "evento_mudanca"]
)
async def test_ingestao_nao_apaga_historico(bd: BancoDeTeste, tabela: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await bd.executar(PAPEL_INGESTAO, f"DELETE FROM {tabela}")


async def test_api_le_o_que_foi_publicado(bd: BancoDeTeste) -> None:
    await bd.executar(PAPEL_INGESTAO, *CENARIO)
    linhas = await bd.executar(PAPEL_API, "SELECT count(*) FROM conceito_versao")
    assert linhas[0][0] == 1


async def test_api_nao_escreve(bd: BancoDeTeste) -> None:
    await bd.executar(PAPEL_INGESTAO, *CENARIO)
    with pytest.raises(DBAPIError, match="read-only transaction"):
        await bd.executar(PAPEL_API, "UPDATE carga SET status = 'falhou'")


async def test_api_nao_ve_o_rascunho_da_carga(bd: BancoDeTeste) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await bd.executar(PAPEL_API, "SELECT * FROM stg_conceito")


async def test_api_tem_consulta_cancelada_apos_2_segundos(bd: BancoDeTeste) -> None:
    with pytest.raises(DBAPIError, match="statement timeout"):
        await bd.executar(PAPEL_API, "SELECT pg_sleep(3)")


async def test_senha_errada_e_recusada(bd: BancoDeTeste) -> None:
    with pytest.raises(DBAPIError, match="password authentication failed"):
        await bd.executar(PAPEL_API, "SELECT 1", senha="senha-errada")


# Migration (síncronos: o Alembic cria o próprio loop de eventos)


def test_migration_desfaz_e_refaz(config_banco: Config) -> None:
    migrar("base")
    migrar("head")


def test_migration_recusa_senha_fraca(
    config_banco: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrar("base")
    monkeypatch.setenv("TUSS_DB_SENHA_API", "curta")
    try:
        with pytest.raises(RuntimeError, match="TUSS_DB_SENHA_API precisa de 24"):
            migrar("head")
    finally:
        monkeypatch.undo()
        migrar("head")


async def test_versao_e_sempre_da_mesma_tabela_do_conceito(bd: BancoDeTeste) -> None:
    await bd.executar(PAPEL_INGESTAO, *CENARIO)
    await bd.executar(
        PAPEL_INGESTAO, "INSERT INTO tabela_tuss (codigo, descricao) VALUES ('tuss-20', 'x')"
    )
    with pytest.raises(DBAPIError, match="foreign key"):
        await bd.executar(
            PAPEL_INGESTAO,
            FECHA_VERSAO_ATUAL,
            NOVA_VERSAO.replace("(1, 1, 1,", "(1, 2, 1,"),  # conceito da tuss-22 na tuss-20
        )
