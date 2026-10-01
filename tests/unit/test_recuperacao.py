import pytest

from tuss import recuperacao
from tuss.recuperacao import FALHAS_PARA_AVISAR, Avisos


@pytest.fixture
def enviados(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    lista: list[bool] = []

    async def _avisar(url: str, *, ok: bool, resumo: str) -> None:
        lista.append(ok)

    monkeypatch.setattr(recuperacao, "avisar", _avisar)
    return lista


async def test_falha_isolada_nao_avisa(enviados: list[bool]) -> None:
    avisos = Avisos("https://hc.exemplo/x")
    for _ in range(FALHAS_PARA_AVISAR - 1):
        await avisos.falha("tuss-19", "timeout")
    await avisos.sucesso("tuss-19", "ok")
    assert enviados == [True]


async def test_falhas_seguidas_avisam_e_sucesso_da_outra_nao_esconde(
    enviados: list[bool],
) -> None:
    avisos = Avisos("https://hc.exemplo/x")
    for _ in range(FALHAS_PARA_AVISAR):
        await avisos.falha("tuss-19", "fora do ar")
    await avisos.sucesso("tuss-64", "ok")  # a 19 continua falhando: sem aviso de sucesso
    assert enviados == [False]

    await avisos.sucesso("tuss-19", "voltou")
    assert enviados == [False, True]
