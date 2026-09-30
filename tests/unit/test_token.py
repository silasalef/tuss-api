import pytest
from pydantic import SecretStr

from tuss.api.token import PREFIXO, gerar_token, hash_token, token_valido
from tuss.config import Config, ConfigAusente


def test_token_gerado_e_longo_unico_e_reconhecivel() -> None:
    a, b = gerar_token(), gerar_token()
    assert a != b
    assert a.startswith(PREFIXO)
    assert len(a) >= 40


def test_so_o_token_certo_e_aceito() -> None:
    token = gerar_token()
    aceitos = frozenset({hash_token(gerar_token()), hash_token(token)})
    assert token_valido(token, aceitos)
    assert not token_valido(token + "x", aceitos)
    assert not token_valido("", aceitos)
    assert not token_valido(token, frozenset())


def test_hashes_da_config_aceitam_espacos_e_maiusculas() -> None:
    h = hash_token("t")
    config = Config(api_tokens_sha256=f" {h.upper()} , {hash_token('u')}")
    assert config.hashes_tokens() == {h, hash_token("u")}


@pytest.mark.parametrize("valor", ["", " , ", "nao-e-hash"])
def test_config_sem_hash_valido_impede_a_api_de_subir(valor: str) -> None:
    with pytest.raises(ConfigAusente, match="TUSS_API_TOKENS_SHA256"):
        Config(api_tokens_sha256=valor, db_senha_api=SecretStr("x")).hashes_tokens()
