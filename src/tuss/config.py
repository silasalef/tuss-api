"""Configuração lida de variáveis de ambiente com prefixo `TUSS_` (ex.: `TUSS_DB_HOST`).

Senhas nunca têm valor padrão: quem precisa de uma e não a recebeu falha com
mensagem clara em vez de tentar conectar sem senha.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field, SecretStr
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
    # Cópia de cada arquivo importado, como veio da ANS (volume `snapshots` no compose.yaml)
    snapshots_dir: Path = Path("/var/lib/tuss/snapshots")
    # SHA-256 dos tokens aceitos pela API, separados por vírgula (gere com `tuss token`)
    api_tokens_sha256: str = ""
    # Chamadas por minuto, por token. Só o teste de carga (numa cópia temporária da API) muda isto.
    limite_consultas_por_minuto: int = 60
    limite_buscas_por_minuto: int = 20
    # Worker: hora (UTC) do ciclo diário; 6 h UTC = 3 h em Brasília, sem horário de verão.
    worker_hora_utc: int = Field(default=6, ge=0, le=23)
    # Endereço de heartbeat (ex.: healthchecks.io) avisado ao fim de cada ciclo; vazio = não avisa.
    heartbeat_url: str = ""

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

    def hashes_tokens(self) -> frozenset[str]:
        """Hashes aceitos pela API. Sem nenhum, a API não sobe (falha fechada)."""
        hashes = frozenset(
            h.strip().lower() for h in self.api_tokens_sha256.split(",") if h.strip()
        )
        if not hashes:
            raise ConfigAusente("defina TUSS_API_TOKENS_SHA256 (gere com: tuss token)")
        invalidos = [h for h in hashes if not _RE_SHA256.match(h)]
        if invalidos:
            raise ConfigAusente(
                f"TUSS_API_TOKENS_SHA256 tem {len(invalidos)} valor(es) que não são SHA-256"
            )
        return hashes


_RE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
