"""Busca por texto nas descrições, no próprio Postgres.

Revisão: 0003
Anterior: 0002
Criada em: 2026-09-30

Duas formas de achar uma descrição, combinadas na consulta:

- full-text em português (`busca`): acha pela raiz da palavra, então "consultas"
  encontra "consulta" e "consultório" não precisa de acento;
- trigramas (`descricao_normalizada` + pg_trgm): tolera erro de digitação
  ("consluta" ainda acha "consulta").

As duas colunas são geradas pelo banco a partir de `descricao`: ninguém precisa
lembrar de preenchê-las, e não há como ficarem desatualizadas.

`unaccent` não pode ser usada direto numa coluna gerada (o Postgres não a considera
IMMUTABLE, porque o dicionário poderia mudar), então usamos uma função própria que
fixa o dicionário. É a solução recomendada na documentação do Postgres.
"""

from __future__ import annotations

from alembic import op

from tuss.config import PAPEL_API

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for comando in [
        "CREATE EXTENSION IF NOT EXISTS unaccent",
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        """
        CREATE FUNCTION tuss_sem_acento(texto text) RETURNS text
            LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
            RETURN public.unaccent('public.unaccent'::regdictionary, texto)
        """,
        """
        ALTER TABLE conceito_versao
            ADD COLUMN descricao_normalizada text
                GENERATED ALWAYS AS (lower(tuss_sem_acento(descricao))) STORED,
            ADD COLUMN busca tsvector
                GENERATED ALWAYS AS (to_tsvector('portuguese', tuss_sem_acento(descricao))) STORED
        """,
        # Índices só das versões atuais: é nelas que a API busca.
        """
        CREATE INDEX conceito_versao_busca_idx ON conceito_versao USING gin (busca)
            WHERE publicado_ate IS NULL
        """,
        """
        CREATE INDEX conceito_versao_trigrama_idx ON conceito_versao
            USING gin (descricao_normalizada gin_trgm_ops) WHERE publicado_ate IS NULL
        """,
        # Quão parecida uma palavra digitada precisa ser (0 a 1) para contar como achado.
        # O padrão (0,6) não perdoa nem uma letra trocada numa palavra curta; com 0,4 "consluta"
        # (0,44 de semelhança) ainda acha "consulta". Os achados só parecidos vêm depois dos exatos.
        f"ALTER ROLE {PAPEL_API} SET pg_trgm.word_similarity_threshold = 0.4",
    ]:
        op.execute(comando)


def downgrade() -> None:
    for comando in [
        f"ALTER ROLE {PAPEL_API} RESET pg_trgm.word_similarity_threshold",
        "ALTER TABLE conceito_versao DROP COLUMN busca, DROP COLUMN descricao_normalizada",
        "DROP FUNCTION tuss_sem_acento(text)",
        "DROP EXTENSION pg_trgm",
        "DROP EXTENSION unaccent",
    ]:
        op.execute(comando)
