"""Coleta de uma tabela pela API da ANS (ADR 0001).

Dois modos:

- **completa**: lê todas as páginas. Cada página vai para o rascunho numa transação
  própria, com `checkpoint` = última página gravada; se a fonte cair, a próxima
  execução retoma dali. No fim, publica como a importação de arquivo (com remoções e
  limite de anomalia). Se o número de páginas mudar no meio, a ANS publicou algo
  durante a leitura: a carga falha e a próxima começa do zero.
- **incremental**: lê a partir da página 1 (onde a ANS põe os códigos mais novos) e
  para depois de 2 páginas seguidas só com códigos já publicados e sem alteração.
  Publica como carga parcial: inclusões, alterações e reativações, sem remoções.

Nenhuma requisição à ANS acontece com transação aberta: uma página leva de 1 a 3 min.
Cada página recebida é guardada como veio (snapshot comprimido) antes de ser usada.
"""

from __future__ import annotations

import gzip
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from tuss.config import PAPEL_INGESTAO, Config
from tuss.db.conexao import criar_engine
from tuss.domain.conceito import Conceito, ConceitoInvalido, conceito_da_fonte
from tuss.ingestion.fonte import ClienteANS, FonteIndisponivel, Pagina
from tuss.ingestion.publicacao import Contagem, gravar_rascunho, publicar, travar

Modo = Literal["completa", "incremental"]
PAGINAS_CONHECIDAS_PARA_PARAR = 2
MAX_CARACTERES_ERRO = 2000


class ColetaRecusada(RuntimeError):
    """A coleta não pode começar agora; nada foi gravado."""


class DadosInvalidos(ValueError):
    """A ANS devolveu conceitos fora do formato esperado: a carga falha alto."""


@dataclass(frozen=True, slots=True)
class ResultadoColeta:
    carga_id: int
    tabela: str
    modo: Modo
    status: str  # publicada, sem_mudanca, retida ou em_andamento (parou; será retomada)
    paginas_lidas: int
    contagem: Contagem | None = None
    total: int | None = None
    motivo: str | None = None  # da retenção, ou por que a coleta parou no meio


async def coletar(
    tabela: str, cliente: ClienteANS, config: Config, modo: Modo | None = None
) -> ResultadoColeta:
    """Coleta a tabela. Sem `modo`: incremental se já tem carga publicada, senão completa."""
    engine = criar_engine(config, PAPEL_INGESTAO)
    try:
        async with engine.connect() as trava:
            if not await _pegar_trava_da_coleta(trava, tabela):
                raise ColetaRecusada(f"outra coleta de {tabela} está rodando agora")
            try:
                return await _coletar(engine, tabela, cliente, config, modo)
            finally:
                await trava.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": "coleta:" + tabela}
                )
    finally:
        await engine.dispose()


async def _pegar_trava_da_coleta(con: AsyncConnection, tabela: str) -> bool:
    """Trava que dura a coleta inteira (horas), numa conexão só dela.

    Chave diferente da trava de publicação (`travar`), que é por transação: as duas
    convivem sem uma esperar pela outra.
    """
    pegou: bool = (
        await con.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": "coleta:" + tabela}
        )
    ).scalar_one()
    await con.commit()
    return pegou


async def _coletar(
    engine: AsyncEngine, tabela: str, cliente: ClienteANS, config: Config, modo: Modo | None
) -> ResultadoColeta:
    carga_id, modo, ultima_pagina = await _abrir_ou_retomar(engine, tabela, modo)
    try:
        if modo == "completa":
            lidas = await _ler_tudo(engine, cliente, config, carga_id, tabela, ultima_pagina)
        else:
            lidas = await _ler_o_topo(engine, cliente, config, carga_id, tabela)
    except FonteIndisponivel as exc:
        if modo == "completa":  # fica em andamento: a próxima execução retoma
            return ResultadoColeta(carga_id, tabela, modo, "em_andamento", 0, motivo=str(exc))
        await _marcar_falha(engine, carga_id, exc)
        raise
    except Exception as exc:
        await _marcar_falha(engine, carga_id, exc)
        raise
    async with engine.begin() as con:
        await travar(con, tabela)
        tabela_id = await _tabela_id(con, tabela)
        await _registrar_assinatura(con, carga_id)
        publicado = await publicar(con, carga_id, tabela_id, tabela, parcial=modo == "incremental")
    return ResultadoColeta(
        carga_id,
        tabela,
        modo,
        publicado.status,
        lidas,
        publicado.contagem,
        publicado.total,
        publicado.motivo_retencao,
    )


async def _abrir_ou_retomar(
    engine: AsyncEngine, tabela: str, modo: Modo | None
) -> tuple[int, Modo, int]:
    """Carga a usar: a completa interrompida desta tabela, ou uma nova."""
    async with engine.begin() as con:
        await travar(con, tabela)
        await con.execute(
            text("INSERT INTO tabela_tuss (codigo) VALUES (:t) ON CONFLICT (codigo) DO NOTHING"),
            {"t": tabela},
        )
        tabela_id = await _tabela_id(con, tabela)
        pendente = (
            await con.execute(
                text(
                    "SELECT id, status, origem, modo, checkpoint FROM carga"
                    " WHERE tabela_id = :t AND status IN ('em_andamento', 'retida')"
                ),
                {"t": tabela_id},
            )
        ).one_or_none()
        if pendente is not None:
            if pendente.status == "retida":
                raise ColetaRecusada(
                    f"{tabela} tem a carga {pendente.id} retida esperando decisão:"
                    f" tuss aprovar {pendente.id} ou tuss descartar {pendente.id}"
                )
            if pendente.origem == "api" and pendente.modo == "completa" and modo != "incremental":
                return pendente.id, "completa", pendente.checkpoint or 0
            if pendente.origem == "api" and pendente.modo == "incremental":
                # Quem segura a trava da coleta somos nós: esta ficou para trás (processo
                # parado no meio). Incremental não se retoma; começa de novo.
                await _abandonar(con, pendente.id)
            else:
                raise ColetaRecusada(f"{tabela} já tem a carga {pendente.id} em andamento")
        publicada: int | None = (
            await con.execute(
                text("SELECT carga_atual_id FROM tabela_tuss WHERE id = :t"), {"t": tabela_id}
            )
        ).scalar_one()
        if modo is None:
            modo = "incremental" if publicada is not None else "completa"
        if modo == "incremental" and publicada is None:
            raise ColetaRecusada(f"{tabela} ainda não tem carga: a primeira precisa ser completa")
        carga_id: int = (
            await con.execute(
                text(
                    "INSERT INTO carga (tabela_id, origem, modo, checkpoint)"
                    " VALUES (:t, 'api', :modo, 0) RETURNING id"
                ),
                {"t": tabela_id, "modo": modo},
            )
        ).scalar_one()
        return carga_id, modo, 0


async def _ler_tudo(
    engine: AsyncEngine,
    cliente: ClienteANS,
    config: Config,
    carga_id: int,
    tabela: str,
    ultima_pagina: int,
) -> int:
    """Lê da página seguinte ao checkpoint até a última. Devolve quantas leu."""
    lidas = 0
    numero = ultima_pagina + 1
    total_esperado = await _total_paginas_da_carga(engine, carga_id)
    while True:
        pagina = await cliente.pagina(tabela, numero)
        if total_esperado is None:
            total_esperado = pagina.total_paginas
        elif pagina.total_paginas != total_esperado:
            raise DadosInvalidos(
                f"{tabela}: a ANS passou de {total_esperado} para {pagina.total_paginas} páginas"
                " durante a leitura; a próxima coleta começa do zero"
            )
        if pagina.total_paginas and numero > pagina.total_paginas:
            raise DadosInvalidos(f"{tabela}: página {numero} além do total {pagina.total_paginas}")
        await _gravar_pagina(engine, config, carga_id, tabela, pagina)
        lidas += 1
        if numero >= pagina.total_paginas:
            return lidas
        numero += 1


async def _ler_o_topo(
    engine: AsyncEngine, cliente: ClienteANS, config: Config, carga_id: int, tabela: str
) -> int:
    """Lê do começo até 2 páginas seguidas sem novidade (ou até o fim da tabela)."""
    sem_novidade = 0
    numero = 1
    while True:
        pagina = await cliente.pagina(tabela, numero)
        conceitos = await _gravar_pagina(engine, config, carga_id, tabela, pagina)
        if await _tem_novidade(engine, tabela, conceitos):
            sem_novidade = 0
        else:
            sem_novidade += 1
        if sem_novidade >= PAGINAS_CONHECIDAS_PARA_PARAR or numero >= pagina.total_paginas:
            return numero
        numero += 1


async def _gravar_pagina(
    engine: AsyncEngine, config: Config, carga_id: int, tabela: str, pagina: Pagina
) -> list[Conceito]:
    """Snapshot da página, validação e rascunho; avança o checkpoint na mesma transação."""
    _salvar_snapshot(config.snapshots_dir, tabela, carga_id, pagina)
    itens = _validar(tabela, pagina)
    async with engine.begin() as con:
        # A lista pode andar durante a leitura e repetir um código em outra página:
        # vale a leitura mais recente.
        codigos = [c.codigo for c, _ in itens]
        await con.execute(
            text(
                "DELETE FROM stg_conceito WHERE carga_id = :c"
                " AND codigo = ANY(CAST(:codigos AS text[]))"
            ),
            {"c": carga_id, "codigos": codigos},
        )
        await gravar_rascunho(con, carga_id, itens)
        await con.execute(
            text("UPDATE carga SET checkpoint = :n, paginas = :paginas WHERE id = :c"),
            {"c": carga_id, "n": pagina.numero, "paginas": pagina.total_paginas},
        )
    return [c for c, _ in itens]


def _validar(tabela: str, pagina: Pagina) -> list[tuple[Conceito, dict[str, Any]]]:
    itens = []
    vistos: set[str] = set()
    for bruto in pagina.registros:
        try:
            conceito = conceito_da_fonte(bruto)
        except ConceitoInvalido as exc:
            raise DadosInvalidos(f"{tabela} página {pagina.numero}: {exc}") from exc
        if conceito.tabela != tabela:
            raise DadosInvalidos(
                f"{tabela} página {pagina.numero}: veio conceito de {conceito.tabela}"
            )
        if conceito.codigo in vistos:
            raise DadosInvalidos(
                f"{tabela} página {pagina.numero}: código {conceito.codigo} repetido"
            )
        vistos.add(conceito.codigo)
        itens.append((conceito, bruto))
    return itens


async def _tem_novidade(engine: AsyncEngine, tabela: str, conceitos: list[Conceito]) -> bool:
    """Algum código da página é novo ou mudou em relação ao que está publicado?"""
    if not conceitos:
        return False
    async with engine.connect() as con:
        resultado = await con.execute(
            text(
                "SELECT c.codigo, v.hash_conteudo FROM conceito c"
                " JOIN tabela_tuss t ON t.id = c.tabela_id"
                " JOIN conceito_versao v ON v.conceito_id = c.id AND v.publicado_ate IS NULL"
                " WHERE t.codigo = :t AND c.codigo = ANY(CAST(:codigos AS text[]))"
            ),
            {"t": tabela, "codigos": [c.codigo for c in conceitos]},
        )
        publicados = {linha.codigo: linha.hash_conteudo for linha in resultado}
    return any(publicados.get(c.codigo) != c.hash_conteudo for c in conceitos)


def _salvar_snapshot(pasta: Path, tabela: str, carga_id: int, pagina: Pagina) -> None:
    destino = pasta / tabela / f"carga-{carga_id}" / f"pagina-{pagina.numero:05d}.json.gz"
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(destino.name + ".parcial")
    temporario.write_bytes(gzip.compress(pagina.bruto))
    temporario.replace(destino)


async def _registrar_assinatura(con: AsyncConnection, carga_id: int) -> None:
    """SHA-256 do conteúdo coletado (código + hash, em ordem de código): não depende
    da ordem das páginas, e duas coletas iguais têm a mesma assinatura."""
    linhas = await con.execute(
        text("SELECT codigo, hash_conteudo FROM stg_conceito WHERE carga_id = :c ORDER BY codigo"),
        {"c": carga_id},
    )
    h = hashlib.sha256()
    for codigo, hash_conteudo in linhas:
        h.update(f"{codigo}:{hash_conteudo}\n".encode())
    await con.execute(
        text("UPDATE carga SET sha256_snapshot = :sha WHERE id = :c"),
        {"c": carga_id, "sha": h.hexdigest()},
    )


async def _total_paginas_da_carga(engine: AsyncEngine, carga_id: int) -> int | None:
    """Páginas que a ANS informava quando a carga começou (para notar mudança no meio)."""
    async with engine.connect() as con:
        valor: int | None = (
            await con.execute(text("SELECT paginas FROM carga WHERE id = :c"), {"c": carga_id})
        ).scalar_one_or_none()
    return valor


async def _tabela_id(con: AsyncConnection, tabela: str) -> int:
    valor: int = (
        await con.execute(text("SELECT id FROM tabela_tuss WHERE codigo = :t"), {"t": tabela})
    ).scalar_one()
    return valor


async def _abandonar(con: AsyncConnection, carga_id: int) -> None:
    await con.execute(
        text(
            "UPDATE carga SET status = 'falhou', erro = 'interrompida no meio; refeita',"
            " finalizada_em = now() WHERE id = :c"
        ),
        {"c": carga_id},
    )
    await con.execute(text("DELETE FROM stg_conceito WHERE carga_id = :c"), {"c": carga_id})


async def _marcar_falha(engine: AsyncEngine, carga_id: int, exc: Exception) -> None:
    async with engine.begin() as con:
        await con.execute(
            text(
                "UPDATE carga SET status = 'falhou', erro = :erro, finalizada_em = now()"
                " WHERE id = :c"
            ),
            {"c": carga_id, "erro": f"{type(exc).__name__}: {exc}"[:MAX_CARACTERES_ERRO]},
        )
        await con.execute(text("DELETE FROM stg_conceito WHERE carga_id = :c"), {"c": carga_id})
