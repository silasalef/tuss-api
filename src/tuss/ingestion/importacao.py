"""Importação de um arquivo do portal: a carga inicial de uma tabela (Fase 1).

Passo a passo:

1. registra a carga (`em_andamento`), já com o SHA-256 do arquivo;
2. guarda uma cópia do arquivo como veio (snapshot), para poder refazer tudo depois;
3. numa única transação: grava os conceitos no rascunho (`stg_conceito`), publica
   conceitos e versões a partir dele, limpa o rascunho e marca a carga como `publicada`.
   Se algo falhar, nada fica pela metade: a carga vira `falhou` com o erro.

Regras desta fase:

- mesmo arquivo de uma carga anterior (mesmo SHA-256): só registra `sem_mudanca`;
- tabela que já tem carga publicada e arquivo diferente: recusa. Comparar e versionar
  (o que mudou, o que saiu, o que voltou) chega na Fase 3;
- a carga inicial não gera eventos de mudança: ela é o ponto de partida do histórico,
  não uma mudança publicada pela ANS. `carga.incluidos` registra quantos entraram.
"""

from __future__ import annotations

import gzip
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tuss.config import PAPEL_INGESTAO, Config
from tuss.db.conexao import criar_engine
from tuss.domain.conceito import Conceito
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


async def importar(lote: Lote, config: Config) -> ResultadoImportacao:
    if lote.problemas:
        raise ImportacaoRecusada("arquivo com problemas: " + "; ".join(lote.problemas))
    tabela = lote.tabela
    engine = criar_engine(config, PAPEL_INGESTAO)
    try:
        async with engine.begin() as con:
            await _travar(con, tabela)
            tabela_id, carga_atual_id = await _garantir_tabela(con, tabela)
            await _recusar_se_houver_carga_em_andamento(con, tabela_id, tabela)
            if await _ultimo_sha256(con, tabela_id) == lote.fonte.sha256:
                carga_id = await _registrar_sem_mudanca(con, tabela_id, lote.fonte)
                return ResultadoImportacao(carga_id, tabela, "sem_mudanca", 0, None)
            if carga_atual_id is not None:
                raise ImportacaoRecusada(
                    f"{tabela} já tem carga publicada (carga {carga_atual_id}) e este arquivo"
                    " é diferente. Atualizar uma tabela já carregada, com histórico do que"
                    " mudou, chega na Fase 3."
                )
            carga_id = await _abrir_carga(con, tabela_id, lote.fonte)

        try:
            snapshot = salvar_snapshot(lote.fonte, tabela, config.snapshots_dir)
            async with engine.begin() as con:
                await _travar(con, tabela)
                await _publicar_carga_inicial(con, carga_id, tabela_id, lote)
        except Exception as exc:
            async with engine.begin() as con:
                await _marcar_falha(con, carga_id, exc)
            raise
        return ResultadoImportacao(carga_id, tabela, "publicada", len(lote.itens), snapshot)
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


async def _publicar_carga_inicial(
    con: AsyncConnection, carga_id: int, tabela_id: int, lote: Lote
) -> None:
    params = {"carga": carga_id, "tabela": tabela_id}
    await con.execute(
        text(
            "INSERT INTO stg_conceito (carga_id, codigo, bruto, descricao, atributos,"
            " hash_conteudo, inicio_vigencia, fim_vigencia, fim_implantacao)"
            " VALUES (:carga, :codigo, CAST(:bruto AS jsonb), :descricao,"
            " CAST(:atributos AS jsonb), :hash, :inicio, :fim, :fim_implantacao)"
        ),
        [_linha_rascunho(carga_id, conceito, bruto) for conceito, bruto in lote.itens],
    )
    await con.execute(
        text(
            "INSERT INTO conceito (tabela_id, codigo)"
            " SELECT :tabela, codigo FROM stg_conceito WHERE carga_id = :carga"
        ),
        params,
    )
    # now() é o mesmo instante para a transação inteira: todas as versões nascem juntas.
    await con.execute(
        text(
            "INSERT INTO conceito_versao (conceito_id, carga_id, descricao, atributos,"
            " hash_conteudo, inicio_vigencia, fim_vigencia, fim_implantacao, publicado_de)"
            " SELECT c.id, s.carga_id, s.descricao, s.atributos, s.hash_conteudo,"
            " s.inicio_vigencia, s.fim_vigencia, s.fim_implantacao, now()"
            " FROM stg_conceito s"
            " JOIN conceito c ON c.tabela_id = :tabela AND c.codigo = s.codigo"
            " WHERE s.carga_id = :carga"
        ),
        params,
    )
    await con.execute(text("DELETE FROM stg_conceito WHERE carga_id = :carga"), params)
    await con.execute(
        text(
            "UPDATE carga SET status = 'publicada', total = :n, incluidos = :n,"
            " finalizada_em = now() WHERE id = :carga"
        ),
        {**params, "n": len(lote.itens)},
    )
    await con.execute(
        text(
            "UPDATE tabela_tuss SET carga_atual_id = :carga, ultima_sync_em = now()"
            " WHERE id = :tabela"
        ),
        params,
    )


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
