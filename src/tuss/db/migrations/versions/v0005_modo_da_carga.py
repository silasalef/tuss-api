"""Modo da carga (completa ou incremental) e páginas informadas pela ANS.

Revisão: 0005
Anterior: 0004
Criada em: 2026-09-30

A coleta pela API (Fase 4) tem dois modos (ADR 0001):

- completa: lê a tabela inteira; detecta remoções; se parar no meio, retoma da
  última página gravada (`checkpoint`);
- incremental: lê só o começo da lista (onde a ANS põe os códigos novos) e para
  quando só encontra conhecidos; não detecta remoções e não é retomada.

Importação de arquivo é sempre completa. `paginas` guarda quantas páginas a ANS
informava quando a coleta começou: se mudar no meio, a ANS publicou algo durante a
leitura e a coleta completa recomeça do zero.
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE carga ADD COLUMN modo text NOT NULL DEFAULT 'completa'"
        " CHECK (modo IN ('completa', 'incremental')),"
        " ADD COLUMN paginas integer CHECK (paginas >= 0)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE carga DROP COLUMN modo, DROP COLUMN paginas")
