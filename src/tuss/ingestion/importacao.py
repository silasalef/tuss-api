"""Importação de um arquivo do portal: carga inicial ou atualização de uma tabela.

Passo a passo:

1. registra a carga (`em_andamento`), já com o SHA-256 do arquivo;
2. guarda uma cópia do arquivo como veio (snapshot), para poder refazer tudo depois;
3. numa única transação: grava os conceitos no rascunho (`stg_conceito`), publica
   a partir dele, limpa o rascunho e marca a carga como `publicada`.
   Se algo falhar, nada fica pela metade: a carga vira `falhou` com o erro.

Regras:

- mesmo arquivo de uma carga anterior (mesmo SHA-256): só registra `sem_mudanca`;
- carga inicial (tabela sem carga publicada): publica tudo e não gera eventos de
  mudança, porque ela é o ponto de partida do histórico, não uma mudança publicada
  pela ANS (ADR 0002). `carga.incluidos` registra quantos entraram;
- atualização (tabela já carregada): compara com as versões publicadas
  (`domain/diff.py`), fecha as versões alteradas e removidas, cria as novas e grava
  um evento por mudança. Arquivo diferente com o mesmo conteúdo (por exemplo, outra
  ordem) também vira `sem_mudanca`. Nada é apagado: versão antiga só ganha `publicado_ate`.
"""

from __future__ import annotations

import gzip
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from tuss.config import PAPEL_INGESTAO, Config
from tuss.db.conexao import criar_engine
from tuss.domain.conceito import Conceito
from tuss.domain.diff import Mudanca, comparar_conceito
from tuss.ingestion.arquivo import ArquivoFonte
from tuss.ingestion.lote import Lote

MAX_CARACTERES_ERRO = 2000


class ImportacaoRecusada(RuntimeError):
    """A importação não pode acontecer agora; nada foi gravado."""


@dataclass(frozen=True, slots=True)
class ResultadoImportacao:
    carga_id: int
    tabela: str
    status: str  # publicada ou sem_mudanca
    incluidos: int
    snapshot: Path | None
    alterados: int = 0
    removidos: int = 0
    reativados: int = 0


async def importar(lote: Lote, config: Config) -> ResultadoImportacao:
    if lote.problemas:
        raise ImportacaoRecusada("arquivo com problemas: " + "; ".join(lote.problemas))
    tabela = lote.tabela
    engine = criar_engine(config, PAPEL_INGESTAO)
    try:
        async with engine.begin() as con:
            await _travar(con, tabela)
            tabela_id, _ = await _garantir_tabela(con, tabela)
            await _recusar_se_houver_carga_em_andamento(con, tabela_id, tabela)
            if await _ultimo_sha256(con, tabela_id) == lote.fonte.sha256:
                carga_id = await _registrar_sem_mudanca(con, tabela_id, lote.fonte)
                return ResultadoImportacao(carga_id, tabela, "sem_mudanca", 0, None)
            carga_id = await _abrir_carga(con, tabela_id, lote.fonte)

        try:
            snapshot = salvar_snapshot(lote.fonte, tabela, config.snapshots_dir)
            async with engine.begin() as con:
                await _travar(con, tabela)
                _, carga_atual_id = await _garantir_tabela(con, tabela)
                await _gravar_rascunho(con, carga_id, lote)
                if carga_atual_id is None:
                    contagem = await _publicar_carga_inicial(con, carga_id, tabela_id)
                else:
                    contagem = await _publicar_atualizacao(con, carga_id, tabela_id, tabela)
                await con.execute(
                    text("DELETE FROM stg_conceito WHERE carga_id = :carga"), {"carga": carga_id}
                )
                status = await _finalizar(con, carga_id, tabela_id, len(lote.itens), contagem)
        except Exception as exc:
            async with engine.begin() as con:
                await _marcar_falha(con, carga_id, exc)
            raise
        return ResultadoImportacao(
            carga_id,
            tabela,
            status,
            contagem["incluido"],
            snapshot,
            alterados=contagem["alterado"],
            removidos=contagem["removido"],
            reativados=contagem["reativado"],
        )
    finally:
        await engine.dispose()


def salvar_snapshot(fonte: ArquivoFonte, tabela: str, pasta: Path) -> Path:
    """Guarda o arquivo como veio da ANS, com o SHA-256 no nome. JSON é comprimido (gzip)."""
    zipado = fonte.caminho.suffix.lower() == ".zip"
    destino = pasta / tabela / (fonte.sha256 + (".zip" if zipado else ".json.gz"))
    if destino.exists():
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(destino.name + ".parcial")
    with fonte.caminho.open("rb") as origem:
        if zipado:
            with temporario.open("wb") as saida:
                shutil.copyfileobj(origem, saida)
        else:
            with gzip.open(temporario, "wb") as saida_gz:
                shutil.copyfileobj(origem, saida_gz)
    temporario.replace(destino)  # só aparece com o nome final se foi gravado inteiro
    return destino


async def _travar(con: AsyncConnection, tabela: str) -> None:
    """Uma carga por tabela por vez; a trava é liberada no fim da transação."""
    await con.execute(text("SELECT pg_advisory_xact_lock(hashtext(:t))"), {"t": tabela})


async def _garantir_tabela(con: AsyncConnection, tabela: str) -> tuple[int, int | None]:
    await con.execute(
        text("INSERT INTO tabela_tuss (codigo) VALUES (:t) ON CONFLICT (codigo) DO NOTHING"),
        {"t": tabela},
    )
    linha = (
        await con.execute(
            text("SELECT id, carga_atual_id FROM tabela_tuss WHERE codigo = :t"), {"t": tabela}
        )
    ).one()
    return linha.id, linha.carga_atual_id


async def _recusar_se_houver_carga_em_andamento(
    con: AsyncConnection, tabela_id: int, tabela: str
) -> None:
    linha = (
        await con.execute(
            text(
                "SELECT id, iniciada_em FROM carga WHERE tabela_id = :t AND status = 'em_andamento'"
            ),
            {"t": tabela_id},
        )
    ).one_or_none()
    if linha is not None:
        raise ImportacaoRecusada(
            f"{tabela} já tem a carga {linha.id} em andamento"
            f" desde {linha.iniciada_em:%d/%m/%Y %H:%M} UTC"
        )


async def _ultimo_sha256(con: AsyncConnection, tabela_id: int) -> str | None:
    resultado = await con.execute(
        text(
            "SELECT sha256_snapshot FROM carga"
            " WHERE tabela_id = :t AND status IN ('publicada', 'sem_mudanca')"
            " ORDER BY id DESC LIMIT 1"
        ),
        {"t": tabela_id},
    )
    valor: str | None = resultado.scalar_one_or_none()
    return valor


async def _registrar_sem_mudanca(con: AsyncConnection, tabela_id: int, fonte: ArquivoFonte) -> int:
    carga_id: int = (
        await con.execute(
            text(
                "INSERT INTO carga (tabela_id, origem, arquivo_nome, sha256_snapshot, status,"
                " finalizada_em) VALUES (:t, 'arquivo', :nome, :sha, 'sem_mudanca', now())"
                " RETURNING id"
            ),
            {"t": tabela_id, "nome": fonte.caminho.name, "sha": fonte.sha256},
        )
    ).scalar_one()
    await con.execute(
        text("UPDATE tabela_tuss SET ultima_sync_em = now() WHERE id = :t"), {"t": tabela_id}
    )
    return carga_id


async def _abrir_carga(con: AsyncConnection, tabela_id: int, fonte: ArquivoFonte) -> int:
    carga_id: int = (
        await con.execute(
            text(
                "INSERT INTO carga (tabela_id, origem, arquivo_nome, sha256_snapshot)"
                " VALUES (:t, 'arquivo', :nome, :sha) RETURNING id"
            ),
            {"t": tabela_id, "nome": fonte.caminho.name, "sha": fonte.sha256},
        )
    ).scalar_one()
    return carga_id


async def _gravar_rascunho(con: AsyncConnection, carga_id: int, lote: Lote) -> None:
    await con.execute(
        text(
            "INSERT INTO stg_conceito (carga_id, codigo, bruto, descricao, atributos,"
            " hash_conteudo, inicio_vigencia, fim_vigencia, fim_implantacao)"
            " VALUES (:carga, :codigo, CAST(:bruto AS jsonb), :descricao,"
            " CAST(:atributos AS jsonb), :hash, :inicio, :fim, :fim_implantacao)"
        ),
        [_linha_rascunho(carga_id, conceito, bruto) for conceito, bruto in lote.itens],
    )


Contagem = dict[str, int]  # tipo de mudança (incluido, alterado...) -> quantos


def _contagem() -> Contagem:
    return {"incluido": 0, "alterado": 0, "removido": 0, "reativado": 0}


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
    return {**_contagem(), "incluido": incluidos}


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
ORDER BY 1
"""


async def _publicar_atualizacao(
    con: AsyncConnection, carga_id: int, tabela_id: int, tabela: str
) -> Contagem:
    """Compara o rascunho com as versões atuais e publica só o que mudou, com eventos."""
    params = {"carga": carga_id, "tabela": tabela_id}
    mudancas: list[tuple[Mudanca, int | None, int | None]] = []  # + conceito_id, versao_id
    for linha in await con.execute(text(_CANDIDATOS), params):
        antes = _conceito_da_linha(linha, "a_", tabela) if linha.versao_id is not None else None
        depois = _conceito_da_linha(linha, "n_", tabela) if linha.n_codigo is not None else None
        mudanca = comparar_conceito(antes, depois, ja_existiu=linha.conceito_id is not None)
        if mudanca is not None:
            mudancas.append((mudanca, linha.conceito_id, linha.versao_id))
    contagem = _contagem()
    if not mudancas:
        return contagem

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
    for mudanca, _, _ in mudancas:
        contagem[mudanca.tipo] += 1
    return contagem


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


async def _marcar_falha(con: AsyncConnection, carga_id: int, exc: Exception) -> None:
    await con.execute(
        text(
            "UPDATE carga SET status = 'falhou', erro = :erro, finalizada_em = now()"
            " WHERE id = :carga"
        ),
        {"carga": carga_id, "erro": f"{type(exc).__name__}: {exc}"[:MAX_CARACTERES_ERRO]},
    )
