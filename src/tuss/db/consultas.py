"""Consultas de leitura usadas pela API (papel `api`, só leitura)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def status_das_tabelas(con: AsyncConnection) -> list[Row[Any]]:
    """Uma linha por tabela: carga publicada atual e a última tentativa de carga.

    `conceitos` vem de `carga.total` da carga atual (quantos a fonte trazia e foram
    publicados), sem contar linha por linha a cada chamada.
    """
    resultado = await con.execute(
        text("""
        SELECT t.codigo, t.descricao, t.ultima_sync_em, t.carga_atual_id,
               atual.total AS conceitos,
               ultima.id AS ultima_id, ultima.status AS ultima_status,
               ultima.iniciada_em AS ultima_iniciada_em,
               ultima.finalizada_em AS ultima_finalizada_em, ultima.erro AS ultima_erro,
               ultima.incluidos AS ultima_incluidos, ultima.alterados AS ultima_alterados,
               ultima.removidos AS ultima_removidos, ultima.reativados AS ultima_reativados
        FROM tabela_tuss t
        LEFT JOIN carga atual ON atual.id = t.carga_atual_id
        LEFT JOIN LATERAL (
            SELECT id, status, iniciada_em, finalizada_em, erro,
                   incluidos, alterados, removidos, reativados
            FROM carga
            WHERE tabela_id = t.id ORDER BY id DESC LIMIT 1
        ) ultima ON true
        ORDER BY t.numero::integer
        """)
    )
    return list(resultado)


_COLUNAS_CONCEITO = """
    c.codigo, v.descricao, v.inicio_vigencia, v.fim_vigencia, v.fim_implantacao, v.atributos,
    v.publicado_de, v.publicado_ate
    FROM conceito c
    JOIN conceito_versao v ON v.conceito_id = c.id AND v.publicado_ate IS NULL
"""


async def listar_tabelas(con: AsyncConnection) -> list[Row[Any]]:
    resultado = await con.execute(
        text("""
        SELECT t.codigo, t.numero, t.descricao, t.carga_atual_id, t.ultima_sync_em,
               atual.total AS conceitos
        FROM tabela_tuss t
        LEFT JOIN carga atual ON atual.id = t.carga_atual_id
        ORDER BY t.numero::integer
        """)
    )
    return list(resultado)


async def tabela(con: AsyncConnection, codigo: str) -> Row[Any] | None:
    resultado = await con.execute(
        text("""
        SELECT t.id, t.codigo, t.carga_atual_id, t.ultima_sync_em
        FROM tabela_tuss t WHERE t.codigo = :codigo
        """),
        {"codigo": codigo},
    )
    return resultado.one_or_none()


async def listar_conceitos(
    con: AsyncConnection, tabela_id: int, apos: str | None, limite: int
) -> list[Row[Any]]:
    """Versão atual dos conceitos, em ordem de código, a partir de `apos` (paginação keyset).

    Keyset em vez de OFFSET: "a partir do código X" usa o índice direto, e a página
    1.000 custa o mesmo que a primeira.
    """
    params: dict[str, Any] = {"tabela": tabela_id, "limite": limite}
    filtro = ""
    if apos is not None:
        filtro = "AND c.codigo > :apos"
        params["apos"] = apos
    resultado = await con.execute(
        text(f"""
        SELECT {_COLUNAS_CONCEITO}
        WHERE c.tabela_id = :tabela {filtro}
        ORDER BY c.codigo
        LIMIT :limite
        """),  # noqa: S608 (só trechos fixos entram no texto; valores vão como parâmetro)
        params,
    )
    return list(resultado)


async def conceito(con: AsyncConnection, tabela_id: int, codigo: str) -> Row[Any] | None:
    resultado = await con.execute(
        text(f"""
        SELECT {_COLUNAS_CONCEITO}
        WHERE c.tabela_id = :tabela AND c.codigo = :codigo
        """),  # noqa: S608 (só trechos fixos entram no texto; valores vão como parâmetro)
        {"tabela": tabela_id, "codigo": codigo},
    )
    return resultado.one_or_none()


async def versoes(con: AsyncConnection, tabela_id: int, codigo: str) -> list[Row[Any]]:
    """Todas as versões de um código, da mais antiga para a mais nova (vazio = nunca existiu).

    Inclui as fechadas: é o histórico, e também o que responde "como era em tal data".
    """
    resultado = await con.execute(
        text("""
        SELECT c.codigo, v.descricao, v.inicio_vigencia, v.fim_vigencia, v.fim_implantacao,
               v.atributos, v.carga_id, v.publicado_de, v.publicado_ate
        FROM conceito c
        JOIN conceito_versao v ON v.conceito_id = c.id
        WHERE c.tabela_id = :tabela AND c.codigo = :codigo
        ORDER BY v.publicado_de
        """),
        {"tabela": tabela_id, "codigo": codigo},
    )
    return list(resultado)


async def versoes_de_varios(
    con: AsyncConnection, tabela_id: int, codigos: list[str]
) -> list[Row[Any]]:
    """Como `versoes`, para vários códigos de uma vez (em ordem de código e publicação)."""
    resultado = await con.execute(
        text("""
        SELECT c.codigo, v.descricao, v.inicio_vigencia, v.fim_vigencia, v.fim_implantacao,
               v.atributos, v.carga_id, v.publicado_de, v.publicado_ate
        FROM conceito c
        JOIN conceito_versao v ON v.conceito_id = c.id
        WHERE c.tabela_id = :tabela AND c.codigo = ANY(CAST(:codigos AS text[]))
        ORDER BY c.codigo, v.publicado_de
        """),
        {"tabela": tabela_id, "codigos": codigos},
    )
    return list(resultado)


async def listar_vigentes(
    con: AsyncConnection, tabela_id: int, em: date, apos: str | None, limite: int
) -> list[Row[Any]]:
    """Conceitos vigentes na data `em`, cada um na versão que valia nela, em ordem de código.

    Mesma regra de `domain/vigencia.py` (os testes de integração conferem que as duas
    respondem igual): para cada código, a última versão publicada até o dia `em` (ou a
    mais antiga, se `em` é anterior a todas); fora se o código tinha saído da lista;
    depois, datas da ANS quando existem, senão o dia em que vimos o código.

    `c.desde <= :em` só descarta quem não poderia valer (migration 0010): sem ele, em
    datas antigas das tabelas 19 e 64, conferir a versão de cada código passava de 2 s.
    """
    params: dict[str, Any] = {"tabela": tabela_id, "em": em, "limite": limite}
    filtro = ""
    if apos is not None:
        filtro = "AND c.codigo > :apos"
        params["apos"] = apos
    resultado = await con.execute(
        text(f"""
        SELECT c.codigo, v.descricao, v.inicio_vigencia, v.fim_vigencia, v.fim_implantacao,
               v.atributos
        FROM conceito c
        CROSS JOIN LATERAL (
            SELECT v.*, (v.publicado_de AT TIME ZONE 'UTC')::date AS dia_de,
                   (v.publicado_ate AT TIME ZONE 'UTC')::date AS dia_ate
            FROM conceito_versao v
            WHERE v.conceito_id = c.id
            ORDER BY (v.publicado_de AT TIME ZONE 'UTC')::date <= :em DESC,
                     CASE WHEN (v.publicado_de AT TIME ZONE 'UTC')::date <= :em
                          THEN v.publicado_de END DESC NULLS LAST,
                     v.publicado_de
            LIMIT 1
        ) v
        WHERE c.tabela_id = :tabela {filtro}
          AND c.desde <= :em
          AND NOT (v.dia_de <= :em AND v.dia_ate IS NOT NULL AND v.dia_ate <= :em)
          AND CASE WHEN v.inicio_vigencia IS NOT NULL
                   THEN v.inicio_vigencia <= :em
                        AND (v.fim_vigencia IS NULL OR :em <= v.fim_vigencia)
                   ELSE v.dia_de <= :em END
        ORDER BY c.codigo
        LIMIT :limite
        """),  # noqa: S608 (só trechos fixos entram no texto; valores vão como parâmetro)
        params,
    )
    return list(resultado)


# Maior caractere possível: "começa com X" vira o intervalo [X, X + este caractere),
# que o índice de código (collation "C", ordem byte a byte) resolve direto.
_MAIOR_CARACTERE = chr(0x10FFFF)


async def buscar_por_codigo(
    con: AsyncConnection, tabela_id: int, prefixo: str, limite: int
) -> list[Row[Any]]:
    """Códigos que começam com `prefixo`, em ordem (o código exato, se existir, vem primeiro)."""
    resultado = await con.execute(
        text(f"""
        SELECT {_COLUNAS_CONCEITO}
        WHERE c.tabela_id = :tabela AND c.codigo >= :inicio AND c.codigo < :fim
        ORDER BY c.codigo
        LIMIT :limite
        """),  # noqa: S608 (só trechos fixos entram no texto; valores vão como parâmetro)
        {
            "tabela": tabela_id,
            "inicio": prefixo,
            "fim": prefixo + _MAIOR_CARACTERE,
            "limite": limite,
        },
    )
    return list(resultado)


# Quantos candidatos de cada tipo entram na ordenação por relevância. Um termo comum
# ("parafuso" está em 461 mil conceitos da tuss-19) não precisa ter a relevância de todos
# calculada para devolver 50: com uma palavra genérica eles empatam, e quem quer algo
# específico refina a busca. Termos raros ficam abaixo do teto e continuam exatos.
MAX_CANDIDATOS = 1000

# Correção de erro de digitação, palavra por palavra (migration 0011). Só palavras de
# letras com 3 ou mais; quem já existe na tabela fica como está. As outras viram a
# palavra do vocabulário que mais combina parecença e frequência, entre as 20 mais
# parecidas: "parafuzo" vira "parafuso" (comum), não "parafu" (rara, mais parecida).
_CORRIGIR = """
SELECT string_agg(coalesce(c.palavra, x.w), ' ' ORDER BY x.ordem)
FROM regexp_split_to_table(lower(tuss_sem_acento(:termo)), '\\s+') WITH ORDINALITY x(w, ordem)
LEFT JOIN LATERAL (
    SELECT k.palavra FROM (
        SELECT palavra, n FROM vocabulario
        WHERE tabela_id = :tabela AND x.w ~ '^[a-z]{3,}$'
          AND NOT EXISTS (SELECT 1 FROM vocabulario
                          WHERE tabela_id = :tabela AND palavra = x.w)
        ORDER BY palavra <-> x.w
        LIMIT 20
    ) k
    WHERE similarity(k.palavra, x.w) >= 0.4
    ORDER BY similarity(k.palavra, x.w) * ln(1 + k.n) DESC
    LIMIT 1
) c ON true
"""


async def buscar_por_texto(
    con: AsyncConnection, tabela_id: int, termo: str, limite: int
) -> list[Row[Any]]:
    """Descrições que batem com `termo`, das mais para as menos relevantes.

    Primeiro as que têm todas as palavras (full-text, pela raiz da palavra), do termo
    como veio ou com as palavras corrigidas (`_CORRIGIR`); depois as parecidas
    (trigramas da frase, para o que a correção não resolve). Empate: ordem de código.
    Cada tipo contribui com no máximo `MAX_CANDIDATOS` conceitos para a ordenação.
    """
    corrigido: str | None = (
        await con.execute(text(_CORRIGIR), {"tabela": tabela_id, "termo": termo})
    ).scalar_one()
    resultado = await con.execute(
        text(f"""
        WITH termo AS (
            SELECT websearch_to_tsquery('portuguese', tuss_sem_acento(:termo))
                   || websearch_to_tsquery('portuguese', :corrigido) AS consulta,
                   lower(tuss_sem_acento(:termo)) AS normalizado
        ), por_palavra AS (
            SELECT v.id FROM conceito_versao v, termo
            WHERE v.tabela_id = :tabela AND v.publicado_ate IS NULL
              AND v.busca @@ termo.consulta
            LIMIT :candidatos
        ), parecidos AS (
            -- Quem tem as palavras vem antes na ordem: se já são `limite`, nenhum
            -- parecido entraria na resposta, e o passo (o mais caro) é pulado.
            SELECT v.id FROM conceito_versao v, termo
            WHERE v.tabela_id = :tabela AND v.publicado_ate IS NULL
              AND termo.normalizado <% v.descricao_normalizada
              AND (SELECT count(*) FROM por_palavra) < :limite
            LIMIT :candidatos
        )
        SELECT {_COLUNAS_CONCEITO}
        CROSS JOIN termo
        WHERE v.id IN (SELECT id FROM por_palavra UNION SELECT id FROM parecidos)
        ORDER BY
            v.busca @@ termo.consulta DESC,
            ts_rank(v.busca, termo.consulta) DESC,
            word_similarity(termo.normalizado, v.descricao_normalizada) DESC,
            c.codigo
        LIMIT :limite
        """),  # noqa: S608 (só trechos fixos entram no texto; valores vão como parâmetro)
        {
            "tabela": tabela_id,
            "termo": termo,
            "corrigido": corrigido or termo,
            "limite": limite,
            "candidatos": MAX_CANDIDATOS,
        },
    )
    return list(resultado)


async def mudancas(
    con: AsyncConnection,
    desde: datetime | None,
    tabela_id: int | None,
    tipo: str | None,
    apos: tuple[datetime, int] | None,
    limite: int,
) -> list[Row[Any]]:
    """Eventos de mudança em ordem de acontecimento (instante da publicação, depois id).

    Paginação keyset em (ocorrido_em, id), que o índice `evento_mudanca_feed_idx` resolve.
    """
    params: dict[str, Any] = {"limite": limite}
    filtros = []
    if desde is not None:
        filtros.append("e.ocorrido_em >= :desde")
        params["desde"] = desde
    if tabela_id is not None:
        filtros.append("c.tabela_id = :tabela")
        params["tabela"] = tabela_id
    if tipo is not None:
        filtros.append("e.tipo = :tipo")
        params["tipo"] = tipo
    if apos is not None:
        filtros.append("(e.ocorrido_em, e.id) > (:apos_em, :apos_id)")
        params["apos_em"], params["apos_id"] = apos
    onde = ("WHERE " + " AND ".join(filtros)) if filtros else ""
    resultado = await con.execute(
        text(f"""
        SELECT e.id, e.ocorrido_em, t.codigo AS tabela, c.codigo, e.tipo, e.campos_alterados,
               e.antes, e.depois, e.carga_id
        FROM evento_mudanca e
        JOIN conceito c ON c.id = e.conceito_id
        JOIN tabela_tuss t ON t.id = c.tabela_id
        {onde}
        ORDER BY e.ocorrido_em, e.id
        LIMIT :limite
        """),  # noqa: S608 (só trechos fixos entram no texto; valores vão como parâmetro)
        params,
    )
    return list(resultado)
