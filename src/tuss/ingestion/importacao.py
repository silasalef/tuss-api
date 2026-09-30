"""Importação de um arquivo do portal: carga inicial ou atualização de uma tabela.

Passo a passo:

1. registra a carga (`em_andamento`), já com o SHA-256 do arquivo;
2. guarda uma cópia do arquivo como veio (snapshot), para poder refazer tudo depois;
3. numa única transação: grava os conceitos no rascunho (`stg_conceito`) e publica
   (`publicacao.py`: carga inicial, atualização com eventos ou retenção por anomalia).
   Se algo falhar, nada fica pela metade: a carga vira `falhou` com o erro.

Mesmo arquivo de uma carga anterior (mesmo SHA-256): só registra `sem_mudanca`.
Enquanto a tabela tem carga em andamento ou retida, nenhuma outra começa.
"""

from __future__ import annotations

import gzip
import shutil
from dataclasses import dataclass
from itertools import batched
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tuss.config import PAPEL_INGESTAO, Config
from tuss.db.conexao import criar_engine
from tuss.ingestion.arquivo import ArquivoFonte
from tuss.ingestion.lote import Lote
from tuss.ingestion.publicacao import gravar_rascunho, publicar, travar

MAX_CARACTERES_ERRO = 2000
TAMANHO_DO_BLOCO = 5000  # conceitos por INSERT no rascunho: memória constante


class ImportacaoRecusada(RuntimeError):
    """A importação não pode acontecer agora; nada foi gravado."""


@dataclass(frozen=True, slots=True)
class ResultadoImportacao:
    carga_id: int
    tabela: str
    status: str  # publicada, sem_mudanca ou retida
    incluidos: int
    snapshot: Path | None
    alterados: int = 0
    removidos: int = 0
    reativados: int = 0
    motivo_retencao: str | None = None


async def importar(lote: Lote, config: Config) -> ResultadoImportacao:
    if lote.problemas:
        raise ImportacaoRecusada("arquivo com problemas: " + "; ".join(lote.problemas))
    tabela = lote.tabela
    engine = criar_engine(config, PAPEL_INGESTAO)
    try:
        async with engine.begin() as con:
            await travar(con, tabela)
            tabela_id, _ = await _garantir_tabela(con, tabela)
            await recusar_se_houver_carga_pendente(con, tabela_id, tabela)
            if await _ultimo_sha256(con, tabela_id) == lote.fonte.sha256:
                carga_id = await _registrar_sem_mudanca(con, tabela_id, lote.fonte)
                return ResultadoImportacao(carga_id, tabela, "sem_mudanca", 0, None)
            carga_id = await _abrir_carga(con, tabela_id, lote.fonte)

        try:
            snapshot = salvar_snapshot(lote.fonte, tabela, config.snapshots_dir)
            async with engine.begin() as con:
                await travar(con, tabela)
                for bloco in batched(lote.conceitos(), TAMANHO_DO_BLOCO, strict=False):
                    await gravar_rascunho(con, carga_id, bloco)
                publicado = await publicar(con, carga_id, tabela_id, tabela)
        except Exception as exc:
            async with engine.begin() as con:
                await _marcar_falha(con, carga_id, exc)
            raise
        contagem = publicado.contagem
        return ResultadoImportacao(
            carga_id,
            tabela,
            publicado.status,
            contagem["incluido"],
            snapshot,
            alterados=contagem["alterado"],
            removidos=contagem["removido"],
            reativados=contagem["reativado"],
            motivo_retencao=publicado.motivo_retencao,
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


async def recusar_se_houver_carga_pendente(
    con: AsyncConnection, tabela_id: int, tabela: str
) -> None:
    linha = (
        await con.execute(
            text(
                "SELECT id, status, iniciada_em FROM carga"
                " WHERE tabela_id = :t AND status IN ('em_andamento', 'retida')"
            ),
            {"t": tabela_id},
        )
    ).one_or_none()
    if linha is not None and linha.status == "retida":
        raise ImportacaoRecusada(
            f"{tabela} tem a carga {linha.id} retida esperando decisão:"
            f" tuss aprovar {linha.id} ou tuss descartar {linha.id}"
        )
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


async def _marcar_falha(con: AsyncConnection, carga_id: int, exc: Exception) -> None:
    await con.execute(
        text(
            "UPDATE carga SET status = 'falhou', erro = :erro, finalizada_em = now()"
            " WHERE id = :carga"
        ),
        {"carga": carga_id, "erro": f"{type(exc).__name__}: {exc}"[:MAX_CARACTERES_ERRO]},
    )
