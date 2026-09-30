"""Esquema inicial: catálogo, cargas, conceitos com histórico, eventos e papéis.

Revisão: 0001
Anterior: nenhuma
Criada em: 2026-09-30

Desenho em docs/PLANEJAMENTO.md ("Modelo de dados"). Regras que o próprio banco
garante, para não depender só do código Python:

- código é texto com collation "C" (ordem byte a byte): zeros à esquerda
  importam, e o mesmo índice serve para paginar por código e buscar por prefixo;
- nada é apagado: o papel `ingestao` não tem DELETE nas tabelas de histórico;
- duas versões do mesmo conceito nunca ficam publicadas ao mesmo tempo
  (restrição de exclusão com btree_gist);
- só uma carga em andamento por tabela;
- o papel `api` só lê, com limite de 2 s por consulta.

Busca por texto (tsvector, unaccent, pg_trgm) entra na migration da Fase 2.
"""

from __future__ import annotations

import re

from alembic import op

from tuss.config import PAPEL_API, PAPEL_INGESTAO, Config

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Senha entra no SQL como texto literal (CREATE ROLE não aceita parâmetro),
# então só aceitamos caracteres que não precisam de escape.
_RE_SENHA = re.compile(r"^[A-Za-z0-9_-]{24,}$")

_TABELAS = [
    # Catálogo das tabelas TUSS (tuss-22, tuss-20...).
    """
    CREATE TABLE tabela_tuss (
        id             integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        codigo         text NOT NULL UNIQUE CHECK (codigo ~ '^tuss-[0-9]+$'),
        numero         text GENERATED ALWAYS AS (substr(codigo, 6)) STORED,
        descricao      text NOT NULL,
        total_fonte    integer CHECK (total_fonte >= 0),  -- Total_sources da ANS: só aviso
        ultima_sync_em timestamptz,
        carga_atual_id bigint  -- chave estrangeira criada depois de `carga`
    )
    """,
    # Cada coleta pela API ou importação de arquivo.
    """
    CREATE TABLE carga (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tabela_id       integer NOT NULL REFERENCES tabela_tuss (id),
        origem          text NOT NULL CHECK (origem IN ('api', 'arquivo')),
        arquivo_nome    text,
        status          text NOT NULL DEFAULT 'em_andamento' CHECK (status IN (
                            'em_andamento', 'retida', 'publicada', 'sem_mudanca', 'falhou')),
        sha256_snapshot text CHECK (sha256_snapshot ~ '^[0-9a-f]{64}$'),
        checkpoint      integer CHECK (checkpoint >= 0),  -- última página coletada
        total           integer CHECK (total >= 0),
        incluidos       integer NOT NULL DEFAULT 0 CHECK (incluidos >= 0),
        alterados       integer NOT NULL DEFAULT 0 CHECK (alterados >= 0),
        removidos       integer NOT NULL DEFAULT 0 CHECK (removidos >= 0),
        reativados      integer NOT NULL DEFAULT 0 CHECK (reativados >= 0),
        erro            text,
        iniciada_em     timestamptz NOT NULL DEFAULT now(),
        finalizada_em   timestamptz,
        CHECK (finalizada_em >= iniciada_em),
        CHECK ((origem = 'arquivo') = (arquivo_nome IS NOT NULL))
    )
    """,
    "CREATE INDEX carga_tabela_idx ON carga (tabela_id, iniciada_em DESC)",
    # Reforça no banco o que o advisory lock faz no código.
    """
    CREATE UNIQUE INDEX carga_uma_em_andamento_idx ON carga (tabela_id)
        WHERE status = 'em_andamento'
    """,
    """
    ALTER TABLE tabela_tuss ADD CONSTRAINT tabela_tuss_carga_atual_fk
        FOREIGN KEY (carga_atual_id) REFERENCES carga (id)
    """,
    # Identidade estável de um código: nunca muda nem some.
    """
    CREATE TABLE conceito (
        id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        tabela_id integer NOT NULL REFERENCES tabela_tuss (id),
        codigo    text COLLATE "C" NOT NULL CHECK (codigo <> '' AND codigo = btrim(codigo)),
        UNIQUE (tabela_id, codigo)
    )
    """,
    # Cada estado de um conceito no tempo (SCD2 bitemporal).
    # Vigência oficial = datas da ANS; período publicado = quando esteve na nossa base.
    """
    CREATE TABLE conceito_versao (
        id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        conceito_id     bigint NOT NULL REFERENCES conceito (id),
        carga_id        bigint NOT NULL REFERENCES carga (id),
        descricao       text NOT NULL,
        atributos       jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(atributos) = 'object'),
        hash_conteudo   text NOT NULL CHECK (hash_conteudo ~ '^[0-9a-f]{64}$'),
        inicio_vigencia date,
        fim_vigencia    date,
        fim_implantacao date,
        publicado_de    timestamptz NOT NULL,
        publicado_ate   timestamptz,  -- nulo = versão atual
        CHECK (publicado_ate > publicado_de),
        EXCLUDE USING gist (
            conceito_id WITH =,
            tstzrange(publicado_de, publicado_ate) WITH &&
        )
    )
    """,
    # Acesso rápido à versão atual de cada conceito (a consulta mais comum da API).
    """
    CREATE UNIQUE INDEX conceito_versao_atual_idx ON conceito_versao (conceito_id)
        WHERE publicado_ate IS NULL
    """,
    # Feed de mudanças: gravado na mesma transação da publicação.
    """
    CREATE TABLE evento_mudanca (
        id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        carga_id         bigint NOT NULL REFERENCES carga (id),
        conceito_id      bigint NOT NULL REFERENCES conceito (id),
        tipo             text NOT NULL CHECK (tipo IN (
                             'incluido', 'alterado', 'removido', 'reativado')),
        campos_alterados text[],
        antes            jsonb,
        depois           jsonb,
        ocorrido_em      timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX evento_mudanca_feed_idx ON evento_mudanca (ocorrido_em, id)",
    "CREATE INDEX evento_mudanca_conceito_idx ON evento_mudanca (conceito_id, ocorrido_em)",
    # Rascunho da carga em andamento. UNLOGGED: mais rápido e sem ocupar o log de
    # recuperação; se o servidor cair, o conteúdo some, e tudo bem: a carga é refeita.
    """
    CREATE UNLOGGED TABLE stg_conceito (
        carga_id        bigint NOT NULL REFERENCES carga (id),
        codigo          text COLLATE "C" NOT NULL,
        bruto           jsonb NOT NULL,
        descricao       text NOT NULL,
        atributos       jsonb NOT NULL DEFAULT '{}',
        hash_conteudo   text NOT NULL,
        inicio_vigencia date,
        fim_vigencia    date,
        fim_implantacao date,
        PRIMARY KEY (carga_id, codigo)
    )
    """,
]

_HISTORICO = "tabela_tuss, carga, conceito, conceito_versao, evento_mudanca"

_PERMISSOES = [
    f"GRANT SELECT ON {_HISTORICO} TO {PAPEL_API}",
    # Sem DELETE: nada é apagado. UPDATE só onde o fluxo precisa
    # (status da carga, carga atual da tabela, fechar publicado_ate).
    f"GRANT SELECT, INSERT ON {_HISTORICO} TO {PAPEL_INGESTAO}",
    f"GRANT UPDATE ON tabela_tuss, carga, conceito_versao TO {PAPEL_INGESTAO}",
    f"GRANT SELECT, INSERT, DELETE ON stg_conceito TO {PAPEL_INGESTAO}",
    f"ALTER ROLE {PAPEL_API} SET statement_timeout = '2s'",
    f"ALTER ROLE {PAPEL_API} SET default_transaction_read_only = on",
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    config = Config()
    for papel in (PAPEL_API, PAPEL_INGESTAO):
        senha = config.senha(papel)
        if not _RE_SENHA.match(senha):
            raise RuntimeError(
                f"TUSS_DB_SENHA_{papel.upper()} precisa de 24+ caracteres entre A-Z, a-z, 0-9,"
                " '_' e '-' (gere com: openssl rand -hex 24)"
            )
        op.execute(f"CREATE ROLE {papel} LOGIN PASSWORD '{senha}'")
    # O asyncpg executa um comando por vez, por isso a lista em vez de um SQL único.
    for comando in _TABELAS + _PERMISSOES:
        op.execute(comando)


def downgrade() -> None:
    op.execute(
        "DROP TABLE stg_conceito, evento_mudanca, conceito_versao, conceito, carga, tabela_tuss"
        " CASCADE"
    )
    op.execute(f"DROP ROLE {PAPEL_API}")
    op.execute(f"DROP ROLE {PAPEL_INGESTAO}")
