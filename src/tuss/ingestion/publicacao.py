"""Publicação de uma carga: do rascunho (`stg_conceito`) para o que a API lê.

Serve à importação de arquivo e à coleta pela API. Quem chama já gravou o rascunho
da carga e segura a trava da tabela; tudo aqui roda na transação de quem chama.

- carga inicial (tabela sem carga publicada): publica tudo e não gera eventos (ADR 0002);
- atualização: compara com as versões publicadas (`domain/diff.py`), fecha as versões
  alteradas e removidas, cria as novas e grava um evento por mudança. Nada é apagado;
- limite de anomalia (`domain/anomalia.py`): atualização estranha demais fica `retida`,
  com o rascunho guardado, até ser aprovada (`tuss aprovar`) ou descartada;
- carga parcial (coleta incremental, só o começo da lista da ANS): não detecta
  remoções, porque o que não foi lido não sumiu.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from tuss.domain.anomalia import motivo_para_reter
from tuss.domain.conceito import Conceito
from tuss.domain.diff import Mudanca, comparar_conceito

Contagem = dict[str, int]  # tipo de mudança (incluido, alterado...) -> quantos


@dataclass(frozen=True, slots=True)
class ResultadoPublicacao:
    status: str  # publicada, sem_mudanca ou retida
    contagem: Contagem
    total: int  # conceitos publicados na tabela depois desta carga
    motivo_retencao: str | None = None


def contagem_vazia() -> Contagem:
    return {"incluido": 0, "alterado": 0, "removido": 0, "reativado": 0}


async def travar(con: AsyncConnection, tabela: str) -> None:
    """Uma carga por tabela por vez; a trava é liberada no fim da transação."""
    await con.execute(text("SELECT pg_advisory_xact_lock(hashtext(:t))"), {"t": tabela})


async def gravar_rascunho(
    con: AsyncConnection, carga_id: int, itens: Iterable[tuple[Conceito, dict[str, Any]]]
) -> None:
    linhas = [_linha_rascunho(carga_id, conceito, bruto) for conceito, bruto in itens]
    if not linhas:
        return
    await con.execute(
        text(
            "INSERT INTO stg_conceito (carga_id, codigo, bruto, descricao, atributos,"
            " hash_conteudo, inicio_vigencia, fim_vigencia, fim_implantacao)"
            " VALUES (:carga, :codigo, CAST(:bruto AS jsonb), :descricao,"
            " CAST(:atributos AS jsonb), :hash, :inicio, :fim, :fim_implantacao)"
        ),
        linhas,
    )


async def publicar(
    con: AsyncConnection,
    carga_id: int,
    tabela_id: int,
    tabela: str,
    *,
    parcial: bool = False,
    aprovada: bool = False,
) -> ResultadoPublicacao:
    """Publica o rascunho da carga, ou a retém se passar do limite de anomalia.

    `aprovada`: o operador já viu a carga retida e mandou publicar mesmo assim.
    """
    atual = (
        await con.execute(
            text(
                "SELECT t.carga_atual_id, c.total FROM tabela_tuss t"
                " LEFT JOIN carga c ON c.id = t.carga_atual_id WHERE t.id = :tabela"
            ),
            {"tabela": tabela_id},
        )
    ).one()
    if atual.carga_atual_id is None:
        if parcial:
            raise ValueError(f"{tabela}: a primeira carga precisa ser completa")
        contagem = await _publicar_carga_inicial(con, carga_id, tabela_id)
        total = contagem["incluido"]
    else:
        mudancas = await _calcular_mudancas(con, carga_id, tabela_id, tabela, parcial)
        contagem = contagem_vazia()
        for mudanca, _, _ in mudancas:
            contagem[mudanca.tipo] += 1
        antes = atual.total or 0
        total = antes + contagem["incluido"] + contagem["reativado"] - contagem["removido"]
        motivo = None if aprovada else motivo_para_reter(antes, contagem["removido"], total)
        if motivo is not None:
            await _reter(con, carga_id, total, contagem, motivo)
            return ResultadoPublicacao("retida", contagem, total, motivo)
        await _aplicar_mudancas(con, carga_id, tabela_id, mudancas)
    await con.execute(text("DELETE FROM stg_conceito WHERE carga_id = :carga"), {"carga": carga_id})
    status = await _finalizar(con, carga_id, tabela_id, total, contagem)
    return ResultadoPublicacao(status, contagem, total)


async def _publicar_carga_inicial(con: AsyncConnection, carga_id: int, tabela_id: int) -> Contagem:
    """Tudo do rascunho vira conceito e versão atual, sem eventos (ADR 0002)."""
    params = {"carga": carga_id, "tabela": tabela_id}
    await con.execute(
        text(
            "INSERT INTO conceito (tabela_id, codigo)"
            " SELECT :tabela, codigo FROM stg_conceito WHERE carga_id = :carga"
        ),
        params,
    )
    await _inserir_versoes(con, carga_id, tabela_id, None)
    incluidos: int = (
        await con.execute(text("SELECT count(*) FROM stg_conceito WHERE carga_id = :carga"), params)
    ).scalar_one()
    return {**contagem_vazia(), "incluido": incluidos}


# now() é o mesmo instante para a transação inteira: todas as versões nascem juntas,
# no mesmo instante em que as anteriores foram fechadas.
_INSERIR_VERSOES = """
INSERT INTO conceito_versao (conceito_id, carga_id, descricao, atributos, hash_conteudo,
                             inicio_vigencia, fim_vigencia, fim_implantacao, publicado_de)
SELECT c.id, s.carga_id, s.descricao, s.atributos, s.hash_conteudo,
       s.inicio_vigencia, s.fim_vigencia, s.fim_implantacao, now()
FROM stg_conceito s
JOIN conceito c ON c.tabela_id = :tabela AND c.codigo = s.codigo
WHERE s.carga_id = :carga
"""


async def _inserir_versoes(
    con: AsyncConnection, carga_id: int, tabela_id: int, codigos: list[str] | None
) -> None:
    """Cria a versão atual dos `codigos` (todos, se `None`) a partir do rascunho."""
    params: dict[str, Any] = {"carga": carga_id, "tabela": tabela_id}
    sql = _INSERIR_VERSOES
    if codigos is not None:
        sql += " AND s.codigo = ANY(CAST(:codigos AS text[]))"
        params["codigos"] = codigos
    await con.execute(text(sql), params)


# Só os códigos em que algo mudou: o hash difere (ou falta de um dos lados).
# Códigos iguais nas duas cargas nem saem do banco, o que importa nas tabelas grandes.
_CANDIDATOS = """
WITH atual AS (
    SELECT c.id AS conceito_id, c.codigo, v.id AS versao_id, v.descricao, v.atributos,
           v.hash_conteudo, v.inicio_vigencia, v.fim_vigencia, v.fim_implantacao
    FROM conceito c
    LEFT JOIN conceito_versao v ON v.conceito_id = c.id AND v.publicado_ate IS NULL
    WHERE c.tabela_id = :tabela
), novo AS (
    SELECT * FROM stg_conceito WHERE carga_id = :carga
)
SELECT coalesce(n.codigo, a.codigo) AS codigo, a.conceito_id, a.versao_id,
       a.descricao AS a_descricao, a.atributos AS a_atributos,
       a.inicio_vigencia AS a_inicio, a.fim_vigencia AS a_fim,
       a.fim_implantacao AS a_fim_implantacao,
       n.codigo AS n_codigo, n.descricao AS n_descricao, n.atributos AS n_atributos,
       n.inicio_vigencia AS n_inicio, n.fim_vigencia AS n_fim,
       n.fim_implantacao AS n_fim_implantacao
FROM novo n
FULL JOIN atual a ON a.codigo = n.codigo
WHERE a.hash_conteudo IS DISTINCT FROM n.hash_conteudo
"""
# Carga parcial: só o que foi lido conta; ausência não é remoção.
_SO_O_QUE_FOI_LIDO = " AND n.codigo IS NOT NULL"


async def _calcular_mudancas(
    con: AsyncConnection, carga_id: int, tabela_id: int, tabela: str, parcial: bool
) -> list[tuple[Mudanca, int | None, int | None]]:
    """Mudanças entre o rascunho e as versões atuais, com conceito_id e versao_id."""
    sql = _CANDIDATOS + (_SO_O_QUE_FOI_LIDO if parcial else "") + " ORDER BY 1"
    mudancas = []
    for linha in await con.execute(text(sql), {"carga": carga_id, "tabela": tabela_id}):
        antes = _conceito_da_linha(linha, "a_", tabela) if linha.versao_id is not None else None
        depois = _conceito_da_linha(linha, "n_", tabela) if linha.n_codigo is not None else None
        mudanca = comparar_conceito(antes, depois, ja_existiu=linha.conceito_id is not None)
        if mudanca is not None:
            mudancas.append((mudanca, linha.conceito_id, linha.versao_id))
    return mudancas


async def _aplicar_mudancas(
    con: AsyncConnection,
    carga_id: int,
    tabela_id: int,
    mudancas: list[tuple[Mudanca, int | None, int | None]],
) -> None:
    if not mudancas:
        return
    ids_conceito = {m.codigo: cid for m, cid, _ in mudancas if cid is not None}
    novos = [m.codigo for m, cid, _ in mudancas if cid is None]
    if novos:
        inseridos = await con.execute(
            text(
                "INSERT INTO conceito (tabela_id, codigo)"
                " SELECT :tabela, unnest(CAST(:codigos AS text[])) RETURNING id, codigo"
            ),
            {"tabela": tabela_id, "codigos": novos},
        )
        ids_conceito.update({linha.codigo: linha.id for linha in inseridos})

    # Fecha primeiro e cria depois: só pode haver uma versão atual por conceito.
    fechar = [vid for _, _, vid in mudancas if vid is not None]
    if fechar:
        await con.execute(
            text(
                "UPDATE conceito_versao SET publicado_ate = now()"
                " WHERE id = ANY(CAST(:ids AS bigint[]))"
            ),
            {"ids": fechar},
        )
    criar = [m.codigo for m, _, _ in mudancas if m.depois is not None]
    if criar:
        await _inserir_versoes(con, carga_id, tabela_id, criar)

    await con.execute(
        text(
            "INSERT INTO evento_mudanca (carga_id, conceito_id, tipo, campos_alterados,"
            " antes, depois)"
            " VALUES (:carga, :conceito, :tipo, CAST(:campos AS text[]),"
            " CAST(:antes AS jsonb), CAST(:depois AS jsonb))"
        ),
        [_linha_evento(carga_id, ids_conceito[m.codigo], m) for m, _, _ in mudancas],
    )


async def _reter(
    con: AsyncConnection, carga_id: int, total: int, contagem: Contagem, motivo: str
) -> None:
    """Carga fica esperando decisão; o rascunho continua lá para a aprovação."""
    await con.execute(
        text(
            "UPDATE carga SET status = 'retida', total = :total, incluidos = :incluido,"
            " alterados = :alterado, removidos = :removido, reativados = :reativado,"
            " erro = :motivo, finalizada_em = now() WHERE id = :carga"
        ),
        {"carga": carga_id, "total": total, "motivo": f"retida: {motivo}", **contagem},
    )


async def _finalizar(
    con: AsyncConnection, carga_id: int, tabela_id: int, total: int, contagem: Contagem
) -> str:
    """Marca a carga como publicada; sem nenhuma mudança, como `sem_mudanca`.

    Em `sem_mudanca` a carga atual da tabela continua a anterior: é nela que estão
    as versões publicadas.
    """
    status = "publicada" if any(contagem.values()) else "sem_mudanca"
    await con.execute(
        text(
            "UPDATE carga SET status = :status, total = :total, incluidos = :incluido,"
            " alterados = :alterado, removidos = :removido, reativados = :reativado,"
            " finalizada_em = now() WHERE id = :carga"
        ),
        {"carga": carga_id, "status": status, "total": total, **contagem},
    )
    await con.execute(
        text(
            "UPDATE tabela_tuss SET ultima_sync_em = now(),"
            " carga_atual_id = CASE WHEN :publicada THEN :carga ELSE carga_atual_id END"
            " WHERE id = :tabela"
        ),
        {"carga": carga_id, "tabela": tabela_id, "publicada": status == "publicada"},
    )
    return status


def _conceito_da_linha(linha: Row[Any], prefixo: str, tabela: str) -> Conceito:
    valores = linha._mapping
    return Conceito(
        tabela=tabela,
        codigo=valores["codigo"],
        descricao=valores[prefixo + "descricao"],
        inicio_vigencia=valores[prefixo + "inicio"],
        fim_vigencia=valores[prefixo + "fim"],
        fim_implantacao=valores[prefixo + "fim_implantacao"],
        atributos=valores[prefixo + "atributos"],
    )


def _linha_evento(carga_id: int, conceito_id: int, mudanca: Mudanca) -> dict[str, Any]:
    def _json(conceito: Conceito | None) -> str | None:
        return None if conceito is None else json.dumps(conceito.como_dict(), ensure_ascii=False)

    return {
        "carga": carga_id,
        "conceito": conceito_id,
        "tipo": mudanca.tipo,
        "campos": list(mudanca.campos_alterados) if mudanca.tipo == "alterado" else None,
        "antes": _json(mudanca.antes),
        "depois": _json(mudanca.depois),
    }


def _linha_rascunho(carga_id: int, conceito: Conceito, bruto: dict[str, Any]) -> dict[str, Any]:
    return {
        "carga": carga_id,
        "codigo": conceito.codigo,
        "bruto": json.dumps(bruto, ensure_ascii=False),
        "descricao": conceito.descricao,
        "atributos": json.dumps(conceito.atributos, ensure_ascii=False, sort_keys=True),
        "hash": conceito.hash_conteudo,
        "inicio": conceito.inicio_vigencia,
        "fim": conceito.fim_vigencia,
        "fim_implantacao": conceito.fim_implantacao,
    }
