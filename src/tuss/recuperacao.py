"""Serviço de recuperação: a incremental das tabelas gigantes (19 e 64), o tempo todo.

O arquivo do portal dessas tabelas ficou meses atrás da API (mais de 5 mil páginas de
diferença em cada uma, a ~2,5 min por página). Ler tudo de uma vez prenderia o worker
por dias e só publicaria no fim. Aqui cada tabela tem sua própria tarefa, em paralelo
(uma requisição por vez por tabela, como pede a ANS):

- lê um trecho de `PAGINAS_POR_TRECHO` páginas, publica e continua no trecho seguinte
  (a página fica em `carga.continua_em`; se o processo parar, volta dali);
- quando alcança os códigos já conhecidos, a tabela está em dia: passa a ler só o topo
  da lista uma vez por dia, como o worker faz com as outras;
- se a ANS falhar, espera e tenta de novo.

O peso fica na espera pela ANS: o processo quase não usa CPU, e cada trecho publica
poucas centenas de conceitos.
"""

from __future__ import annotations

import asyncio
import signal

import structlog

from tuss.config import Config
from tuss.ingestion.ciclo import carregar_situacoes, gigante
from tuss.ingestion.coleta import coletar
from tuss.ingestion.fonte import ClienteANS

log = structlog.get_logger("tuss.recuperacao")

PAGINAS_POR_TRECHO = 20  # ~50 min de leitura entre uma publicação e outra
ESPERA_EM_DIA_S = 24 * 3600
ESPERA_APOS_FALHA_S = 15 * 60


async def acompanhar(tabela: str, config: Config) -> None:
    """Laço de uma tabela: trechos seguidos até ficar em dia, depois uma vez por dia."""
    while True:
        espera: float = 0
        try:
            async with ClienteANS() as ans:
                r = await coletar(
                    tabela, ans, config, modo="incremental", max_paginas=PAGINAS_POR_TRECHO
                )
            log.info(
                "trecho_fim",
                tabela=tabela,
                carga_id=r.carga_id,
                status=r.status,
                paginas=r.paginas_lidas,
                continua_em=r.continua_em,
                **(r.contagem or {}),
            )
            if r.continua_em is None:
                espera = ESPERA_EM_DIA_S
        except Exception:  # fonte fora, banco, dado inválido: registra e tenta depois
            log.exception("trecho_falhou", tabela=tabela)
            espera = ESPERA_APOS_FALHA_S
        if espera:
            log.info("aguardando", tabela=tabela, segundos=espera)
            await asyncio.sleep(espera)


async def rodar_para_sempre(config: Config) -> None:
    tabelas = [t.codigo for t in await carregar_situacoes(config) if t.carregada and gigante(t)]
    log.info("recuperacao_inicio", tabelas=tabelas)
    await asyncio.gather(*(acompanhar(t, config) for t in tabelas))


def main(config: Config) -> None:
    """Roda até receber SIGTERM (`docker compose stop`) ou Ctrl+C."""

    async def _principal() -> None:
        tarefa = asyncio.current_task()
        if tarefa is None:  # pragma: no cover (sempre existe dentro de asyncio.run)
            raise RuntimeError("sem tarefa atual")
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, tarefa.cancel)
        try:
            await rodar_para_sempre(config)
        except asyncio.CancelledError:
            # O trecho pela metade é refeito; os já publicados ficam.
            log.info("recuperacao_parada")

    asyncio.run(_principal())
