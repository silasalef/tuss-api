"""Índices de busca separados por tabela.

Revisão: 0007
Anterior: 0006
Criada em: 2026-10-01

Os índices de busca (full-text e trigramas) cobriam as versões de todas as tabelas
juntas. Com as tabelas 19 e 64 (3 milhões de conceitos), buscar "consulta" na tuss-22
fazia o índice devolver dezenas de milhares de candidatos das outras tabelas, filtrados
um a um depois: a busca passou de ~50 ms para até 2 s.

`conceito_versao` ganha `tabela_id` (cópia de `conceito.tabela_id`, que nunca muda;
uma chave estrangeira composta garante que a cópia não diverge) e
os dois índices passam a começar pela tabela (extensão btree_gin, que permite misturar
um número comum com texto num índice GIN). Assim a busca só olha a tabela pedida.
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for comando in [
        "CREATE EXTENSION IF NOT EXISTS btree_gin",
        # Sem os índices GIN, preencher a coluna nova nas 3 milhões de linhas é bem mais rápido.
        "DROP INDEX conceito_versao_busca_idx",
        "DROP INDEX conceito_versao_trigrama_idx",
        "ALTER TABLE conceito_versao ADD COLUMN tabela_id integer",
        """
        UPDATE conceito_versao v SET tabela_id = c.tabela_id
        FROM conceito c WHERE c.id = v.conceito_id
        """,
        "ALTER TABLE conceito_versao ALTER COLUMN tabela_id SET NOT NULL",
        # O banco garante que a cópia nunca diverge: (conceito, tabela) tem de existir junto.
        "ALTER TABLE conceito ADD CONSTRAINT conceito_id_tabela_key UNIQUE (id, tabela_id)",
        """
        ALTER TABLE conceito_versao ADD CONSTRAINT conceito_versao_conceito_tabela_fk
            FOREIGN KEY (conceito_id, tabela_id) REFERENCES conceito (id, tabela_id)
        """,
        "SET LOCAL maintenance_work_mem = '128MB'",
        """
        CREATE INDEX conceito_versao_busca_idx ON conceito_versao
            USING gin (tabela_id, busca) WHERE publicado_ate IS NULL
        """,
        """
        CREATE INDEX conceito_versao_trigrama_idx ON conceito_versao
            USING gin (tabela_id, descricao_normalizada gin_trgm_ops) WHERE publicado_ate IS NULL
        """,
    ]:
        op.execute(comando)


def downgrade() -> None:
    for comando in [
        "DROP INDEX conceito_versao_busca_idx",
        "DROP INDEX conceito_versao_trigrama_idx",
        "ALTER TABLE conceito_versao DROP COLUMN tabela_id",
        "ALTER TABLE conceito DROP CONSTRAINT conceito_id_tabela_key",
        """
        CREATE INDEX conceito_versao_busca_idx ON conceito_versao USING gin (busca)
            WHERE publicado_ate IS NULL
        """,
        """
        CREATE INDEX conceito_versao_trigrama_idx ON conceito_versao
            USING gin (descricao_normalizada gin_trgm_ops) WHERE publicado_ate IS NULL
        """,
        "DROP EXTENSION btree_gin",
    ]:
        op.execute(comando)
