from tuss.api.limites import Limitador


class Relogio:
    def __init__(self) -> None:
        self.agora = 1000.0

    def __call__(self) -> float:
        return self.agora


def test_aceita_ate_o_limite_e_diz_quanto_esperar() -> None:
    relogio = Relogio()
    limitador = Limitador(relogio)
    for _ in range(3):
        assert limitador.registrar("a", limite=3) is None
        relogio.agora += 10
    # 3 chamadas em t=1000, 1010, 1020; agora t=1030: a primeira sai da janela em t=1060.
    assert limitador.registrar("a", limite=3) == 30


def test_janela_desliza_com_o_tempo() -> None:
    relogio = Relogio()
    limitador = Limitador(relogio)
    assert limitador.registrar("a", limite=1) is None
    relogio.agora += 59.9
    assert limitador.registrar("a", limite=1) is not None
    relogio.agora += 0.1  # exatamente 60 s depois da primeira
    assert limitador.registrar("a", limite=1) is None


def test_chamada_recusada_nao_conta() -> None:
    relogio = Relogio()
    limitador = Limitador(relogio)
    limitador.registrar("a", limite=1)
    for _ in range(10):
        limitador.registrar("a", limite=1)  # recusadas
    relogio.agora += 60
    assert limitador.registrar("a", limite=1) is None


def test_chaves_diferentes_tem_cotas_separadas() -> None:
    limitador = Limitador(Relogio())
    assert limitador.registrar("token-1:consulta", limite=1) is None
    assert limitador.registrar("token-2:consulta", limite=1) is None
    assert limitador.registrar("token-1:busca", limite=1) is None
    assert limitador.registrar("token-1:consulta", limite=1) is not None
