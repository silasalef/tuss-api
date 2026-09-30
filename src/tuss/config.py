"""Configuração lida de variáveis de ambiente com prefixo `TUSS_` (ex.: `TUSS_DB_HOST`).

Senhas nunca têm valor padrão: quem precisa de uma e não a recebeu falha com
mensagem clara em vez de tentar conectar sem senha.
"""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# Papéis do banco (ver docs/PLANEJAMENTO.md): o dono aplica migrations,
# `ingestao` escreve as cargas e `api` só lê.
PAPEL_DONO = "tuss"
PAPEL_INGESTAO = "ingestao"
PAPEL_API = "api"


class ConfigAusente(RuntimeError):
    """Falta uma variável de ambiente obrigatória para esta operação."""


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TUSS_")

    db_host: str = "postgres"  # nome do serviço no compose.yaml
    db_porta: int = 5432
    db_nome: str = "tuss"
    db_senha_dono: SecretStr | None = None
    db_senha_ingestao: SecretStr | None = None
    db_senha_api: SecretStr | None = None

    def url_banco(self, papel: str) -> URL:
        """Endereço de conexão (driver asyncpg) para um dos três papéis."""
        return URL.create(
            "postgresql+asyncpg",
            username=papel,
            password=self.senha(papel),
            host=self.db_host,
            port=self.db_porta,
            database=self.db_nome,
        )

    def senha(self, papel: str) -> str:
        campos = {
            PAPEL_DONO: self.db_senha_dono,
            PAPEL_INGESTAO: self.db_senha_ingestao,
            PAPEL_API: self.db_senha_api,
        }
        if papel not in campos:
            raise ValueError(f"papel desconhecido: {papel}")
        valor = campos[papel]
        if valor is None:
            raise ConfigAusente(f"defina TUSS_DB_SENHA_{papel.upper()}")
        return valor.get_secret_value()
