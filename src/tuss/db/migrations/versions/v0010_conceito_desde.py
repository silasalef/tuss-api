"""Data mais antiga em que cada conceito pode ter valido, para a lista `vigente_em`.

Revisão: 0010
Anterior: 0009
Criada em: 2026-10-04

Nas tabelas 19 e 64, a lista `vigente_em` em datas entre 2017 e 2020 dava 503: em
ordem de código, os vigentes daquela época ficam espalhados, e conferir a versão de
cada código até achar 50 passava de 2 s. O índice da migration 0009 só ajudava quando
os candidatos eram poucos.

`conceito.desde` é a menor data entre as versões do conceito: o início de vigência da
ANS ou, sem ele, o dia em que publicamos a versão. Um conceito só pode estar vigente
em D se `desde <= D` (a versão que vale em D começou até D por um dos dois critérios),
então a lista descarta os outros sem abrir as versões. O índice por código já traz
`desde`: a lista percorre só o índice. Medido com 1,7 milhão de conceitos: 50 a 230 ms
em qualquer data.

Um gatilho mantém `desde` em dia: a cada versão nova (importação ou coleta), o
conceito fica com a menor data entre a que tinha e a da versão. A coluna é preenchida
em blocos, com commit próprio (como na 0007); se parar no meio, rodar de novo continua.
O índice da 0009 deixa de ser usado e sai.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from tuss.config import PAPEL_INGESTAO

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

BLOCO = 200_000

# Dia da versão pelo critério da regra (domain/vigencia.py): data da ANS ou, sem ela,
# o dia em que a publicamos (em UTC).
_DIA_DA_VERSAO = "coalesce(v.inicio_vigencia, (v.publicado_de AT TIME ZONE 'UTC')::date)"


def upgrade() -> None:
    for comando in [
        "ALTER TABLE conceito ADD COLUMN IF NOT EXISTS desde date",
        # O gatilho roda com o papel de quem insere a versão: a ingestão só pode mexer
        # nesta coluna do conceito.
        f"GRANT UPDATE (desde) ON conceito TO {PAPEL_INGESTAO}",
        f"""
        CREATE OR REPLACE FUNCTION tuss_atualiza_desde() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE conceito c SET desde = least(c.desde, n.dia)
            FROM (SELECT v.conceito_id, min({_DIA_DA_VERSAO}) AS dia
                  FROM novas v GROUP BY v.conceito_id) n
            WHERE c.id = n.conceito_id AND (c.desde IS NULL OR n.dia < c.desde);
            RETURN NULL;
        END
        $$
        """,  # noqa: S608 (só trechos fixos entram no texto)
        """
        CREATE OR REPLACE TRIGGER conceito_versao_atualiza_desde
            AFTER INSERT ON conceito_versao
            REFERENCING NEW TABLE AS novas
            FOR EACH STATEMENT EXECUTE FUNCTION tuss_atualiza_desde()
        """,
    ]:
        op.execute(comando)

    # Gatilho antes do preenchimento: versão que entrar no meio já atualiza `desde`.
    # `least` nos dois lados: a ordem entre gatilho e bloco não importa.
    conexao = op.get_bind()
    with op.get_context().autocommit_block():
        maior = conexao.execute(text("SELECT coalesce(max(id), 0) FROM conceito")).scalar()
        for inicio in range(0, int(maior or 0) + 1, BLOCO):
            conexao.execute(
                text(f"""
                UPDATE conceito c SET desde = least(c.desde, m.dia)
                FROM (SELECT v.conceito_id, min({_DIA_DA_VERSAO}) AS dia
                      FROM conceito_versao v
                      WHERE v.conceito_id >= :de AND v.conceito_id < :ate
                      GROUP BY v.conceito_id) m
                WHERE c.id = m.conceito_id AND (c.desde IS NULL OR m.dia < c.desde)
                """),  # noqa: S608 (só trechos fixos entram no texto)
                {"de": inicio, "ate": inicio + BLOCO},
            )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS conceito_desde_idx"
            " ON conceito (tabela_id, codigo) INCLUDE (desde, id)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS conceito_versao_inicio_idx")
        # Marca as páginas como visíveis: a lista lê só o índice, sem abrir a tabela.
        op.execute("VACUUM (ANALYZE) conceito")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS conceito_versao_inicio_idx"
            " ON conceito_versao (tabela_id, inicio_vigencia)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS conceito_desde_idx")
    for comando in [
        "DROP TRIGGER IF EXISTS conceito_versao_atualiza_desde ON conceito_versao",
        "DROP FUNCTION IF EXISTS tuss_atualiza_desde()",
        f"REVOKE UPDATE (desde) ON conceito FROM {PAPEL_INGESTAO}",
        "ALTER TABLE conceito DROP COLUMN IF EXISTS desde",
    ]:
        op.execute(comando)
