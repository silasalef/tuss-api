"""Catálogo da ANS gravado em `tabela_tuss`."""

import pytest
from conftest import BancoDeTeste

from tuss.config import PAPEL_API, Config
from tuss.ingestion.catalogo import sincronizar_catalogo
from tuss.ingestion.fonte import ItemCatalogo


@pytest.fixture
def config(bd: BancoDeTeste) -> Config:
    return bd.config


async def test_catalogo_insere_e_depois_atualiza(bd: BancoDeTeste, config: Config) -> None:
    itens = [
        ItemCatalogo("tuss-22", "Procedimentos em saúde", 5964),
        ItemCatalogo("tuss-23", "Caráter", 2),
    ]
    assert await sincronizar_catalogo(itens, config) == 2

    novos = [ItemCatalogo("tuss-22", "Procedimentos e eventos em saúde", 5970)]
    assert await sincronizar_catalogo(novos, config) == 0

    linhas = await bd.executar(
        PAPEL_API,
        "SELECT codigo, descricao, total_fonte, carga_atual_id FROM tabela_tuss ORDER BY 1",
    )
    assert [tuple(linha) for linha in linhas] == [
        ("tuss-22", "Procedimentos e eventos em saúde", 5970, None),
        ("tuss-23", "Caráter", 2, None),  # fora do catálogo novo, mas nada é apagado
    ]
