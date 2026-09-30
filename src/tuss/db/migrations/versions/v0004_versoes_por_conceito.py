"""Índice para ler todas as versões de um conceito em ordem.

Revisão: 0004
Anterior: 0003
Criada em: 2026-09-30

Até a Fase 2 a API só lia a versão atual (índice `conceito_versao_atual_idx`). Na
Fase 3 ela também responde "como era o código na data D" e o histórico completo:
as duas consultas procuram as versões de um conceito em ordem de publicação.
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX conceito_versao_conceito_idx ON conceito_versao (conceito_id, publicado_de)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX conceito_versao_conceito_idx")
