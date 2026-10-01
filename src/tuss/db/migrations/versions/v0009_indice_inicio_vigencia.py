"""Índice de início de vigência, para a lista `vigente_em` em datas antigas.

Revisão: 0009
Anterior: 0008
Criada em: 2026-10-01

Em data antiga quase nenhum código das tabelas 19 e 64 valia, e os que valiam ficam
concentrados em poucas faixas de código. A lista `vigente_em` percorria a tabela em
ordem de código atrás deles e passava do limite de 2 s (503). Com este índice, ela
conta os candidatos (só lendo o índice) e, se forem poucos, parte deles
(`db/consultas.py`, `listar_vigentes`).

Criado com CONCURRENTLY: não trava as escritas da coleta enquanto é construído
(~12 s e 20 MB com 3,1 milhões de versões).
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS conceito_versao_inicio_idx"
            " ON conceito_versao (tabela_id, inicio_vigencia)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS conceito_versao_inicio_idx")
