"""Vocabulário de cada tabela, para corrigir erro de digitação na busca.

Revisão: 0011
Anterior: 0010
Criada em: 2026-10-04

Nas tabelas 19 e 64, uma busca com erro de digitação ("fressa tungstenio") achava
milhares de descrições parecidas pelos trigramas da frase inteira, e as 1.000 que
entravam na ordenação eram quaisquer: "Brocas de tungstênio" vinha antes de "Fresas".
Um índice GiST para ordenar por parecença foi medido e descartado: 956 MB e de 1,7 a
11 s por busca (com descrições longas, ele quase não descarta nada).

Em vez de comparar a frase, a busca corrige palavra por palavra (`db/consultas.py`):
cada palavra que não existe na tabela vira a mais parecida e mais frequente do
vocabulário ("fressa" -> "fresa"). O vocabulário guarda as palavras só de letras, com
3 ou mais, e quantas vezes aparecem nas descrições atuais. Nas tabelas com mais de 50
mil conceitos (19 e 64), só as que aparecem 3 vezes ou mais: as raras são quase todas
nomes de modelo ou erros de digitação da própria fonte, e deixá-las de fora tornou a
correção 5 vezes mais rápida (~20 ms por palavra) sem piorar o resultado.

`tuss_reconstroi_vocabulario(tabela)` refaz o vocabulário de uma tabela; a publicação
a chama quando a carga muda algo (`ingestion/publicacao.py`), na mesma transação.
"""

from __future__ import annotations

from alembic import op

from tuss.config import PAPEL_API, PAPEL_INGESTAO

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for comando in [
        """
        CREATE TABLE vocabulario (
            tabela_id integer NOT NULL REFERENCES tabela_tuss (id),
            palavra   text COLLATE "C" NOT NULL,
            n         integer NOT NULL,
            PRIMARY KEY (tabela_id, palavra)
        )
        """,
        # Ordena por parecença (KNN): palavras curtas, onde o GiST funciona bem.
        """
        CREATE INDEX vocabulario_trigrama_idx ON vocabulario
            USING gist (tabela_id, palavra gist_trgm_ops)
        """,
        f"GRANT SELECT ON vocabulario TO {PAPEL_API}",
        f"GRANT SELECT, INSERT, DELETE ON vocabulario TO {PAPEL_INGESTAO}",
        """
        CREATE FUNCTION tuss_reconstroi_vocabulario(alvo integer) RETURNS void
        LANGUAGE sql AS $$
            DELETE FROM vocabulario WHERE tabela_id = alvo;
            INSERT INTO vocabulario (tabela_id, palavra, n)
            SELECT alvo, p, count(*)
            FROM conceito_versao v,
                 regexp_split_to_table(v.descricao_normalizada, '[^a-z]+') p
            WHERE v.tabela_id = alvo AND v.publicado_ate IS NULL AND length(p) >= 3
            GROUP BY p
            HAVING count(*) >= CASE
                WHEN (SELECT count(*) FROM conceito_versao
                      WHERE tabela_id = alvo AND publicado_ate IS NULL) > 50000 THEN 3
                ELSE 1 END;
        $$
        """,
        "SELECT tuss_reconstroi_vocabulario(id) FROM tabela_tuss",
        "ANALYZE vocabulario",
    ]:
        op.execute(comando)


def downgrade() -> None:
    op.execute("DROP FUNCTION tuss_reconstroi_vocabulario(integer)")
    op.execute("DROP TABLE vocabulario")
