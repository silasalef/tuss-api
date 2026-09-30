"""Papel `ingestao` pode atualizar as estatísticas (ANALYZE) das tabelas que carrega.

Revisão: 0006
Anterior: 0005
Criada em: 2026-09-30

O Postgres escolhe como executar cada consulta pelas estatísticas de cada tabela.
Numa carga grande, as linhas novas ainda não estão nas estatísticas: na primeira
carga da tuss-19 (1,4 milhão de conceitos) ele achou que a tabela tinha 1 linha e
escolheu um plano que relia o rascunho inteiro para cada conceito (horas em vez de
minutos). A publicação passa a rodar ANALYZE no meio da própria transação, que já
enxerga as linhas novas. MAINTAIN (Postgres 17+) é o privilégio só para isso: não
permite ler, gravar nem apagar nada além do que o papel já podia.
"""

from __future__ import annotations

from alembic import op

from tuss.config import PAPEL_INGESTAO

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"GRANT MAINTAIN ON stg_conceito, conceito, conceito_versao TO {PAPEL_INGESTAO}")


def downgrade() -> None:
    op.execute(f"REVOKE MAINTAIN ON stg_conceito, conceito, conceito_versao FROM {PAPEL_INGESTAO}")
