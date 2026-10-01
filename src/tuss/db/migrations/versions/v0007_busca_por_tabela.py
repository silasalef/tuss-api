"""Índices de busca separados por tabela.

Revisão: 0007
Anterior: 0006
Criada em: 2026-10-01

Os índices de busca (full-text e trigramas) cobriam as versões de todas as tabelas
juntas. Com as tabelas 19 e 64 (3 milhões de conceitos), buscar "consulta" na tuss-22
fazia o índice devolver dezenas de milhares de candidatos das outras tabelas, filtrados
um a um depois: a busca passou de ~50 ms para até 2 s.

`conceito_versao` ganha `tabela_id` (cópia de `conceito.tabela_id`, que nunca muda;
uma chave estrangeira composta garante que a cópia não diverge) e os dois índices
passam a começar pela tabela (extensão btree_gin, que permite misturar um número comum
com texto num índice GIN). Assim a busca só olha a tabela pedida.

Feita em passos com commit próprio: a primeira versão, numa transação só, preenchia as
3 milhões de linhas de uma vez e o Postgres estourou o limite de 512 MB do container
(nada se perdeu: a transação foi desfeita). Agora a coluna é preenchida em blocos de
200 mil linhas. Se parar no meio, rodar de novo continua de onde parou.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

BLOCO = 200_000


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin")
    op.execute("ALTER TABLE conceito_versao ADD COLUMN IF NOT EXISTS tabela_id integer")
    conexao = op.get_bind()
    with op.get_context().autocommit_block():
        maior = conexao.execute(text("SELECT coalesce(max(id), 0) FROM conceito_versao")).scalar()
        for inicio in range(0, int(maior or 0) + 1, BLOCO):
            conexao.execute(
                text(
                    "UPDATE conceito_versao v SET tabela_id = c.tabela_id FROM conceito c"
                    " WHERE c.id = v.conceito_id AND v.id >= :de AND v.id < :ate"
                    " AND v.tabela_id IS NULL"
                ),
                {"de": inicio, "ate": inicio + BLOCO},
            )
    for comando in [
        "ALTER TABLE conceito_versao ALTER COLUMN tabela_id SET NOT NULL",
        # O banco garante que a cópia nunca diverge: (conceito, tabela) tem de existir junto.
        "ALTER TABLE conceito ADD CONSTRAINT conceito_id_tabela_key UNIQUE (id, tabela_id)",
        """
        ALTER TABLE conceito_versao ADD CONSTRAINT conceito_versao_conceito_tabela_fk
            FOREIGN KEY (conceito_id, tabela_id) REFERENCES conceito (id, tabela_id)
        """,
        "DROP INDEX conceito_versao_busca_idx",
        "DROP INDEX conceito_versao_trigrama_idx",
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
