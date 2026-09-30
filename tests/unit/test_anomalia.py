import pytest

from tuss.domain.anomalia import motivo_para_reter


@pytest.mark.parametrize(
    ("antes", "removidos", "depois", "retem"),
    [
        (1000, 20, 1000, False),  # 2% removidos (no limite), com inclusões que repõem
        (1000, 10, 990, False),  # 1% a menos: no limite da queda, publica
        (1000, 20, 980, True),  # 2% removidos sem repor: a tabela cai 2%, passa de 1%
        (1000, 21, 979, True),  # acima de 2%
        (1000, 0, 989, True),  # sem remoção, mas a tabela caiu 1,1% (não deveria acontecer)
        (1000, 30, 1100, True),  # muitas inclusões não compensam remoções demais
        (1000, 5, 1000, False),
        (0, 0, 500, False),  # carga inicial: nada a comparar
    ],
)
def test_limite_de_anomalia(antes: int, removidos: int, depois: int, retem: bool) -> None:
    assert (motivo_para_reter(antes, removidos, depois) is not None) is retem


def test_motivo_explica_a_retencao() -> None:
    assert motivo_para_reter(25, 1, 25) == "remove 1 de 25 conceitos (4.0%; limite 2%)"
