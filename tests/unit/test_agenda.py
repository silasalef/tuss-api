from datetime import UTC, datetime, timedelta, timezone

import pytest

from tuss.ingestion.ciclo import SituacaoTabela, escolher_modo
from tuss.worker import proxima_execucao

AGORA = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _tabela(
    paginas: int, carregada: bool = True, dias: int | None = 1, em_dia: bool = False
) -> SituacaoTabela:
    ultima = None if dias is None else AGORA - timedelta(days=dias)
    return SituacaoTabela(
        "tuss-99", paginas, carregada, ultima, completa_interrompida=False, em_dia=em_dia
    )


@pytest.mark.parametrize(
    ("tabela", "modo"),
    [
        (_tabela(1), "completa"),  # pequena: ler tudo custa o mesmo que o topo
        (_tabela(239), "incremental"),  # tuss-22 com completa recente
        (_tabela(239, dias=31), "completa"),  # completa vencida
        (_tabela(239, dias=None), "completa"),  # nunca teve completa
        (_tabela(1783, dias=1), "incremental"),  # tuss-20 com completa recente
        (_tabela(1783, dias=31), "completa"),  # tuss-20: completa mensal, em trechos
        (_tabela(55592, dias=None), None),  # tuss-19 carregada: fica com `tuss recuperar`
        (_tabela(55592, dias=None, em_dia=True), "incremental"),  # recuperada: só o topo
        (_tabela(1783, carregada=False), None),  # primeira carga da 20: por arquivo
        (_tabela(144, carregada=False), "completa"),  # tuss-18: primeira carga pela API
        (_tabela(55592, carregada=False), None),  # tuss-19: primeira carga por arquivo
    ],
)
def test_escolher_modo(tabela: SituacaoTabela, modo: str | None) -> None:
    assert escolher_modo(tabela, AGORA)[0] == modo


def test_completa_interrompida_e_retomada_mesmo_em_tabela_grande() -> None:
    tabela = SituacaoTabela("tuss-20", 1783, True, None, completa_interrompida=True)
    assert escolher_modo(tabela, AGORA) == ("completa", "retomando coleta completa interrompida")


def test_grande_sem_carga_explica_o_que_fazer() -> None:
    _, motivo = escolher_modo(_tabela(55592, carregada=False), AGORA)
    assert "tuss importar" in motivo


@pytest.mark.parametrize(
    ("agora", "esperado"),
    [
        (datetime(2026, 10, 1, 5, 59, tzinfo=UTC), datetime(2026, 10, 1, 6, tzinfo=UTC)),
        (datetime(2026, 10, 1, 6, 0, tzinfo=UTC), datetime(2026, 10, 2, 6, tzinfo=UTC)),
        (datetime(2026, 10, 1, 23, tzinfo=UTC), datetime(2026, 10, 2, 6, tzinfo=UTC)),
        # 2 h em Brasília (-03:00) = 5 h UTC: ainda hoje
        (
            datetime(2026, 10, 1, 2, tzinfo=timezone(timedelta(hours=-3))),
            datetime(2026, 10, 1, 6, tzinfo=UTC),
        ),
    ],
)
def test_proxima_execucao(agora: datetime, esperado: datetime) -> None:
    assert proxima_execucao(agora, 6) == esperado
