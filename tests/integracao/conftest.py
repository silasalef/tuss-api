"""Postgres de verdade para os testes de integração (precisa de Docker).

Sobe um container temporário por execução do pytest, aplica as migrations e
entrega um `BancoDeTeste` que conecta com qualquer um dos três papéis. Cada
teste começa com as tabelas vazias.

Para rodar só os testes rápidos, sem Docker: `uv run pytest -m "not integracao"`.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config as ConfigAlembic
from sqlalchemy import Row, pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

from tuss.api.app import criar_app
from tuss.api.token import gerar_token, hash_token
from tuss.config import PAPEL_DONO, Config

POSTGRES_IMAGEM = "postgres:18-alpine"  # a mesma do compose.yaml
ALEMBIC_INI = Path(__file__).parents[2] / "alembic.ini"
TOKEN = gerar_token()
TABELAS = "tabela_tuss, carga, conceito, conceito_versao, evento_mudanca, stg_conceito"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integracao" in item.path.parts:
            item.add_marker(pytest.mark.integracao)


def migrar(destino: str) -> None:
    """Aplica (`head`) ou desfaz (`base`) as migrations, como o dono do banco."""
    config = ConfigAlembic(str(ALEMBIC_INI))
    if destino == "base":
        command.downgrade(config, destino)
    else:
        command.upgrade(config, destino)


class BancoDeTeste:
    def __init__(self, config: Config) -> None:
        self.config = config

    async def executar(
        self, papel: str, *comandos: str, senha: str | None = None
    ) -> list[Row[Any]]:
        """Roda os comandos numa única transação e devolve as linhas do último."""
        url = self.config.url_banco(papel)
        if senha is not None:
            url = url.set(password=senha)
        engine = create_async_engine(url, poolclass=pool.NullPool)
        try:
            linhas: list[Row[Any]] = []
            async with engine.begin() as conexao:
                for comando in comandos:
                    resultado = await conexao.execute(text(comando))
                    linhas = list(resultado) if resultado.returns_rows else []
            return linhas
        finally:
            await engine.dispose()


@pytest.fixture(scope="session")
def config_banco() -> Iterator[Config]:
    senha_dono = secrets.token_hex(24)
    with (
        pytest.MonkeyPatch.context() as mp,
        PostgresContainer(
            POSTGRES_IMAGEM, username=PAPEL_DONO, password=senha_dono, dbname="tuss", driver=None
        ) as pg,
    ):
        # A migration e o env.py do Alembic leem tudo das variáveis TUSS_DB_*.
        mp.setenv("TUSS_DB_HOST", pg.get_container_host_ip())
        mp.setenv("TUSS_DB_PORTA", str(pg.get_exposed_port(5432)))
        mp.setenv("TUSS_DB_SENHA_DONO", senha_dono)
        mp.setenv("TUSS_DB_SENHA_INGESTAO", secrets.token_hex(24))
        mp.setenv("TUSS_DB_SENHA_API", secrets.token_hex(24))
        migrar("head")
        yield Config()


@pytest.fixture
async def bd(config_banco: Config) -> AsyncIterator[BancoDeTeste]:
    banco = BancoDeTeste(config_banco)
    yield banco
    await banco.executar(PAPEL_DONO, f"TRUNCATE {TABELAS} RESTART IDENTITY CASCADE")


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def cliente_da_api(config: Config) -> AsyncIterator[httpx.AsyncClient]:
    """A API de verdade (com lifespan e pool), chamada sem rede, aceitando `TOKEN`."""
    app = criar_app(config.model_copy(update={"api_tokens_sha256": hash_token(TOKEN)}))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as c,
    ):
        yield c


@pytest.fixture
async def cliente(bd: BancoDeTeste) -> AsyncIterator[httpx.AsyncClient]:
    async for c in cliente_da_api(bd.config):
        yield c
