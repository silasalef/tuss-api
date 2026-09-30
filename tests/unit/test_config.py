import pytest
from pydantic import SecretStr

from tuss.config import PAPEL_API, PAPEL_INGESTAO, Config, ConfigAusente


def test_senha_ausente_diz_qual_variavel_definir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TUSS_DB_SENHA_INGESTAO", raising=False)
    with pytest.raises(ConfigAusente, match="TUSS_DB_SENHA_INGESTAO"):
        Config().url_banco(PAPEL_INGESTAO)


def test_url_nao_expoe_a_senha_quando_impressa() -> None:
    url = Config(db_senha_api=SecretStr("segredo-nao-pode-vazar")).url_banco(PAPEL_API)
    assert url.username == PAPEL_API
    assert url.password == "segredo-nao-pode-vazar"
    assert "segredo" not in str(url)  # str() vai para log e mensagem de erro


def test_papel_desconhecido_e_recusado() -> None:
    with pytest.raises(ValueError, match="papel desconhecido"):
        Config().senha("postgres")
