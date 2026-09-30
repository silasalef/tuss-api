from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tuss.domain.conceito import Conceito
from tuss.domain.diff import aplicar, campos_alterados, comparar, comparar_conceito


def _conceito(codigo: str = "10101012", descricao: str = "Consulta", **extra: object) -> Conceito:
    campos: dict[str, object] = {
        "tabela": "tuss-22",
        "codigo": codigo,
        "descricao": descricao,
        "inicio_vigencia": date(2020, 1, 1),
        "fim_vigencia": None,
        "fim_implantacao": None,
        "atributos": {},
    }
    campos.update(extra)
    return Conceito(**campos)  # type: ignore[arg-type]


def test_igual_nao_e_mudanca() -> None:
    assert comparar_conceito(_conceito(), _conceito()) is None


def test_espaco_e_caixa_nao_sao_mudanca() -> None:
    assert comparar_conceito(_conceito(), _conceito(descricao="  CONSULTA ")) is None


def test_codigo_novo_e_incluido() -> None:
    mudanca = comparar_conceito(None, _conceito())
    assert mudanca is not None
    assert (mudanca.tipo, mudanca.antes, mudanca.campos_alterados) == ("incluido", None, ())


def test_codigo_que_ja_existiu_e_reativado() -> None:
    mudanca = comparar_conceito(None, _conceito(), ja_existiu=True)
    assert mudanca is not None
    assert mudanca.tipo == "reativado"


def test_codigo_que_saiu_e_removido() -> None:
    mudanca = comparar_conceito(_conceito(), None)
    assert mudanca is not None
    assert (mudanca.tipo, mudanca.depois) == ("removido", None)


def test_alterado_lista_os_campos_que_mudaram() -> None:
    antes = _conceito(atributos={"laboratorio": "ACME", "apresentacao": "cx 10"})
    depois = _conceito(
        descricao="Consulta médica",
        fim_vigencia=date(2026, 12, 31),
        atributos={"laboratorio": "acme", "registro": "123"},  # só caixa: não conta
    )
    mudanca = comparar_conceito(antes, depois)
    assert mudanca is not None
    assert mudanca.tipo == "alterado"
    assert mudanca.campos_alterados == (
        "descricao",
        "fim_vigencia",
        "atributos.apresentacao",
        "atributos.registro",
    )


def test_codigos_diferentes_e_erro_de_programacao() -> None:
    with pytest.raises(ValueError, match="códigos diferentes"):
        comparar_conceito(_conceito("1"), _conceito("2"))


def test_comparar_tabela_inteira() -> None:
    publicados = {c.codigo: c for c in [_conceito("1"), _conceito("2"), _conceito("3")]}
    novos = {
        c.codigo: c
        for c in [_conceito("1"), _conceito("2", "Outra"), _conceito("4"), _conceito("5")]
    }
    mudancas = comparar(publicados, novos, ja_existiram={"5"})
    assert [(m.codigo, m.tipo) for m in mudancas] == [
        ("2", "alterado"),
        ("3", "removido"),
        ("4", "incluido"),
        ("5", "reativado"),
    ]


# Tabelas pequenas geradas ao acaso: códigos de "0" a "9" e poucas descrições, para
# que códigos em comum e descrições repetidas (sem mudança) apareçam com frequência.
_descricoes = st.sampled_from(["Consulta", "CONSULTA ", "Exame", "Exame de sangue", "Cirurgia"])
_datas = st.one_of(st.none(), st.dates(date(2000, 1, 1), date(2030, 12, 31)))


@st.composite
def _tabelas(draw: st.DrawFn) -> dict[str, Conceito]:
    codigos = draw(st.sets(st.sampled_from("0123456789"), max_size=10))
    return {
        codigo: _conceito(
            codigo,
            draw(_descricoes),
            fim_vigencia=draw(_datas),
            atributos=draw(
                st.dictionaries(st.sampled_from("ab"), st.sampled_from(["x", "X", "y"]))
            ),
        )
        for codigo in codigos
    }


@given(a=_tabelas(), b=_tabelas())
def test_aplicar_o_diff_de_a_para_b_sobre_a_resulta_em_b(
    a: dict[str, Conceito], b: dict[str, Conceito]
) -> None:
    resultado = aplicar(a, comparar(a, b))
    assert {k: v.hash_conteudo for k, v in resultado.items()} == {
        k: v.hash_conteudo for k, v in b.items()
    }


@given(a=_tabelas())
def test_sem_mudanca_de_a_para_a(a: dict[str, Conceito]) -> None:
    assert comparar(a, a) == []


@given(a=_tabelas(), b=_tabelas())
def test_alterado_sempre_tem_campo_alterado(a: dict[str, Conceito], b: dict[str, Conceito]) -> None:
    for mudanca in comparar(a, b):
        if mudanca.tipo == "alterado":
            assert mudanca.antes is not None and mudanca.depois is not None
            assert mudanca.campos_alterados == campos_alterados(mudanca.antes, mudanca.depois)
            assert mudanca.campos_alterados
