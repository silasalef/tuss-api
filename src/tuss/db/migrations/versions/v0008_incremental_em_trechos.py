"""Incremental em trechos: onde a próxima coleta continua.

Revisão: 0008
Anterior: 0007
Criada em: 2026-10-01

Nas tabelas 19 e 64 o arquivo do portal ficou meses atrás da API: a diferença passa
de 5 mil páginas (mais de uma semana de leitura). O serviço de recuperação lê em
trechos e publica cada um; `continua_em` guarda a página em que o próximo trecho
começa. Nulo = a incremental chegou aos códigos já conhecidos (está em dia).
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE carga ADD COLUMN continua_em integer CHECK (continua_em >= 1)")


def downgrade() -> None:
    op.execute("ALTER TABLE carga DROP COLUMN continua_em")
