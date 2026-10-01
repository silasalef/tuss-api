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

Avisa o heartbeat próprio (`TUSS_HEARTBEAT_RECUPERACAO_URL`) a cada trecho publicado,
e `/fail` quando uma tabela falha `FALHAS_PARA_AVISAR` vezes seguidas (falha isolada
da ANS é comum e se resolve sozinha). Enquanto uma tabela estiver falhando, o sucesso
da outra não manda aviso de sucesso, para não esconder a falha.

O peso fica na espera pela ANS: o processo quase não usa CPU, e cada trecho publica
poucas centenas de conceitos.
"""

from __future__ import annotations

import asyncio
import signal

import structlog

from tuss.config import Config
from tuss.heartbeat import avisar
from tuss.ingestion.ciclo import carregar_situacoes, gigante
from tuss.ingestion.coleta import coletar
from tuss.ingestion.fonte import ClienteANS

log = structlog.get_logger("tuss.recuperacao")

PAGINAS_POR_TRECHO = 20  # ~50 min de leitura entre uma publicação e outra
ESPERA_EM_DIA_S = 24 * 3600
ESPERA_APOS_FALHA_S = 15 * 60
FALHAS_PARA_AVISAR = 3


class Avisos:
    """Falhas seguidas de cada tabela, para decidir o aviso ao heartbeat."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.falhas: dict[str, int] = {}

    async def sucesso(self, tabela: str, resumo: str) -> None:
        self.falhas[tabela] = 0
        if all(n < FALHAS_PARA_AVISAR for n in self.falhas.values()):
            await avisar(self.url, ok=True, resumo=resumo)

    async def falha(self, tabela: str, resumo: str) -> None:
        self.falhas[tabela] = self.falhas.get(tabela, 0) + 1
        if self.falhas[tabela] >= FALHAS_PARA_AVISAR:
            await avisar(self.url, ok=False, resumo=resumo)


async def acompanhar(tabela: str, config: Config, avisos: Avisos) -> None:
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
            situacao = "em dia" if r.continua_em is None else f"continua na página {r.continua_em}"
            await avisos.sucesso(tabela, f"{tabela}: {r.status}, {situacao}")
        except Exception as exc:  # fonte fora, banco, dado inválido: registra e tenta depois
            log.exception("trecho_falhou", tabela=tabela)
            await avisos.falha(tabela, f"{tabela}: {exc!r}")
            espera = ESPERA_APOS_FALHA_S
        if espera:
            log.info("aguardando", tabela=tabela, segundos=espera)
            await asyncio.sleep(espera)


async def rodar_para_sempre(config: Config) -> None:
    tabelas = [t.codigo for t in await carregar_situacoes(config) if t.carregada and gigante(t)]
    log.info("recuperacao_inicio", tabelas=tabelas)
    avisos = Avisos(config.heartbeat_recuperacao_url)
    await asyncio.gather(*(acompanhar(t, config, avisos) for t in tabelas))


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
