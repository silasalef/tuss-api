"""Descrição da tabela TUSS passa a ser opcional.

Revisão: 0002
Anterior: 0001
Criada em: 2026-09-30

O arquivo baixado do portal não traz o nome da tabela (só o código, ex.: tuss-22).
A descrição vem do catálogo da ANS (`/ANS/source`), sincronizado pelo worker na Fase 4.
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tabela_tuss ALTER COLUMN descricao DROP NOT NULL")


def downgrade() -> None:
    op.execute("UPDATE tabela_tuss SET descricao = codigo WHERE descricao IS NULL")
    op.execute("ALTER TABLE tabela_tuss ALTER COLUMN descricao SET NOT NULL")
