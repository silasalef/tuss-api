from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from tuss.domain.vigencia import criterio, situacao, versao_em


@dataclass(frozen=True)
class V:
    nome: str
    publicado_de: datetime
    publicado_ate: datetime | None = None
    inicio_vigencia: date | None = date(2020, 1, 1)
    fim_vigencia: date | None = None


def _em(dia: int, hora: int = 12) -> datetime:
    return datetime(2026, 9, dia, hora, tzinfo=UTC)


# Código publicado em 10/09, alterado em 20/09 e removido em 25/09.
V1 = V("v1", _em(10), _em(20))
V2 = V("v2", _em(20), _em(25))
HISTORICO = [V2, V1]  # fora de ordem de propósito


def test_criterio() -> None:
    assert criterio(date(2020, 1, 1)) == "oficial"
    assert criterio(None) == "observado"


def test_versao_em_cada_momento_do_historico() -> None:
    assert versao_em(HISTORICO, date(2026, 9, 1)) == V1  # antes da 1ª carga: a mais antiga
    assert versao_em(HISTORICO, date(2026, 9, 10)) == V1
    assert versao_em(HISTORICO, date(2026, 9, 19)) == V1
    assert versao_em(HISTORICO, date(2026, 9, 20)) == V2  # no dia da troca vale a nova
    assert versao_em(HISTORICO, date(2026, 9, 24)) == V2
    assert versao_em(HISTORICO, date(2026, 9, 25)) is None  # removido
    assert versao_em([], date(2026, 9, 25)) is None


def test_mais_de_uma_versao_no_mesmo_dia_vale_a_ultima() -> None:
    a = V("a", _em(10, 8), _em(10, 9))
    b = V("b", _em(10, 9))
    assert versao_em([a, b], date(2026, 9, 10)) == b


def test_codigo_reativado_volta_a_ter_versao() -> None:
    v3 = V("v3", _em(28))
    assert versao_em([*HISTORICO, v3], date(2026, 9, 26)) is None
    assert versao_em([*HISTORICO, v3], date(2026, 9, 28)) == v3


def test_vigencia_oficial_usa_as_datas_da_ans() -> None:
    versao = V("v", _em(10), inicio_vigencia=date(2021, 3, 1), fim_vigencia=date(2026, 12, 31))
    assert not situacao([versao], date(2021, 2, 28)).vigente
    assert situacao([versao], date(2021, 3, 1)).vigente
    assert situacao([versao], date(2026, 12, 31)).vigente  # o último dia ainda vale
    assert not situacao([versao], date(2027, 1, 1)).vigente
    s = situacao([versao], date(2024, 1, 1))
    assert (s.criterio, s.inicio, s.fim) == ("oficial", date(2021, 3, 1), date(2026, 12, 31))


def test_removido_nao_esta_vigente_e_o_criterio_e_observado() -> None:
    s = situacao(HISTORICO, date(2026, 9, 26))
    assert (s.vigente, s.criterio, s.inicio, s.fim) == (
        False,
        "observado",
        date(2020, 1, 1),
        date(2026, 9, 25),
    )


def test_sem_data_da_ans_vale_o_periodo_observado() -> None:
    versao = V("v", _em(10), inicio_vigencia=None)
    assert not situacao([versao], date(2026, 9, 9)).vigente  # antes de vermos o código
    s = situacao([versao], date(2026, 9, 10))
    assert (s.vigente, s.criterio, s.inicio, s.fim) == (True, "observado", date(2026, 9, 10), None)


def test_codigo_sem_versao() -> None:
    assert not situacao([], date(2026, 9, 10)).vigente


@st.composite
def _historicos(draw: st.DrawFn) -> list[V]:
    """Versões contíguas (cada uma começa quando a anterior termina); a última pode fechar."""
    inicio = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=draw(st.integers(0, 1000)))
    duracoes = draw(st.lists(st.integers(1, 2000), min_size=1, max_size=5))
    removido = draw(st.booleans())
    versoes = []
    momento = inicio
    for i, horas in enumerate(duracoes):
        fim: datetime | None = momento + timedelta(hours=horas)
        if i == len(duracoes) - 1 and not removido:
            fim = None
        versoes.append(V(str(i), momento, fim))
        momento = fim or momento
    return versoes


@given(versoes=_historicos(), d=st.dates(date(2025, 12, 1), date(2026, 12, 31)))
def test_versao_escolhida_estava_publicada_no_dia(versoes: list[V], d: date) -> None:
    escolhida = versao_em(versoes, d)
    primeira = versoes[0].publicado_de.date()
    if escolhida is None:
        # Só some quando o código já tinha saído da lista.
        ultima = versoes[-1].publicado_ate
        assert ultima is not None and ultima.date() <= d
    elif d >= primeira:
        assert escolhida.publicado_de.date() <= d
        assert escolhida.publicado_ate is None or d < escolhida.publicado_ate.date()
    else:
        assert escolhida == versoes[0]
