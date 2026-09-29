import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tuss.domain.conceito import ConceitoInvalido, conceito_da_fonte

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ans"


def _registro(**extras: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "fim_vigencia": "-",
        "fim_implantacao": "2014-08-31",
        "inicio_vigencia": "2012-10-10",
    }
    base.update(extras)
    return {"id": "1", "source": "tuss-23", "display_name": "Eletivo", "extras": base}


@pytest.mark.parametrize("arquivo", ["concepts_tuss-22_page1.json", "tuss-23_20260929.json"])
def test_fixtures_reais_da_ans_sao_aceitas(arquivo: str) -> None:
    registros = json.loads((FIXTURES / arquivo).read_text(encoding="utf-8"))
    conceitos = [conceito_da_fonte(r) for r in registros]
    assert len(conceitos) == len(registros)
    assert all(c.inicio_vigencia is not None for c in conceitos)


def test_traco_em_fim_de_vigencia_e_periodo_aberto() -> None:
    c = conceito_da_fonte(_registro())
    assert c.fim_vigencia is None
    assert c.inicio_vigencia == date(2012, 10, 10)
    assert c.fim_implantacao == date(2014, 8, 31)


def test_codigo_mantem_zeros_a_esquerda() -> None:
    registro = _registro()
    registro["id"] = "00123"
    assert conceito_da_fonte(registro).codigo == "00123"


def test_extras_desconhecidos_viram_atributos() -> None:
    c = conceito_da_fonte(_registro(laboratorio="  ACME   LTDA ", registro_anvisa="123"))
    assert c.atributos == {"laboratorio": "ACME LTDA", "registro_anvisa": "123"}


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("id", 123), ("id", ""), ("display_name", None), ("source", "  ")],
)
def test_campo_obrigatorio_invalido_falha(campo: str, valor: Any) -> None:
    registro = _registro()
    registro[campo] = valor
    with pytest.raises(ConceitoInvalido):
        conceito_da_fonte(registro)


@pytest.mark.parametrize("valor", ["31/12/2020", "2020-02-30", "amanhã", 20200101])
def test_data_invalida_falha(valor: Any) -> None:
    with pytest.raises(ConceitoInvalido):
        conceito_da_fonte(_registro(inicio_vigencia=valor))


def test_registro_que_nao_e_objeto_falha() -> None:
    with pytest.raises(ConceitoInvalido):
        conceito_da_fonte(["não", "é", "objeto"])


def test_hash_ignora_espaco_e_caixa() -> None:
    a = conceito_da_fonte(_registro())
    registro = _registro()
    registro["display_name"] = "  ELETIVO "
    assert conceito_da_fonte(registro).hash_conteudo == a.hash_conteudo


def test_hash_muda_com_acento_data_ou_atributo() -> None:
    base = conceito_da_fonte(_registro()).hash_conteudo
    com_acento = _registro()
    com_acento["display_name"] = "Eletívo"
    assert conceito_da_fonte(com_acento).hash_conteudo != base
    assert conceito_da_fonte(_registro(fim_vigencia="2026-01-01")).hash_conteudo != base
    assert conceito_da_fonte(_registro(fabricante="X")).hash_conteudo != base


def test_hash_nao_depende_do_codigo_nem_da_ordem_dos_extras() -> None:
    a = _registro(b="2", a="1")
    b = _registro(a="1", b="2")
    b["id"] = "999"
    assert conceito_da_fonte(a).hash_conteudo == conceito_da_fonte(b).hash_conteudo


texto = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc", "Zl", "Zp")), min_size=1
).filter(lambda s: s.strip())


@given(descricao=texto, esquerda=st.text(" \t\n", max_size=3), direita=st.text(" \t", max_size=3))
def test_hash_estavel_com_espacos_nas_pontas(descricao: str, esquerda: str, direita: str) -> None:
    registro = _registro()
    registro["display_name"] = descricao
    original = conceito_da_fonte(registro).hash_conteudo
    registro["display_name"] = esquerda + descricao + direita
    assert conceito_da_fonte(registro).hash_conteudo == original
