"""Worker: roda o ciclo de coleta uma vez por dia, de madrugada (docs/PLANEJAMENTO.md).

Um laço simples (esperar até a hora, rodar, repetir) em vez de um agendador: há uma
tarefa só, uma vez por dia, e o próprio ciclo sabe retomar o que ficou pela metade.

Ao fim de cada ciclo avisa um serviço de heartbeat (ex.: healthchecks.io), se
configurado: sucesso, ou `/fail` se alguma tabela falhou. Se o aviso não chegar,
o serviço manda e-mail. O aviso sai do servidor para fora: nenhuma porta aberta.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import UTC, datetime, timedelta

import structlog

from tuss.config import Config
from tuss.heartbeat import avisar
from tuss.ingestion.ciclo import ResultadoTabela, executar_ciclo
from tuss.ingestion.fonte import ClienteANS

log = structlog.get_logger("tuss.worker")


def proxima_execucao(agora: datetime, hora_utc: int) -> datetime:
    """Próxima vez que o relógio (UTC) marca `hora_utc` em ponto, depois de `agora`."""
    alvo = agora.astimezone(UTC).replace(hour=hora_utc, minute=0, second=0, microsecond=0)
    return alvo if alvo > agora else alvo + timedelta(days=1)


async def rodar_ciclo(config: Config) -> list[ResultadoTabela]:
    """Um ciclo com aviso de heartbeat no fim (também usado por `tuss ciclo`)."""
    inicio = datetime.now(UTC)
    log.info("ciclo_inicio")
    try:
        async with ClienteANS() as ans:
            resultados = await executar_ciclo(ans, config, inicio)
    except Exception:
        log.exception("ciclo_falhou")
        await avisar(config.heartbeat_url, ok=False, resumo="ciclo falhou antes de terminar")
        raise
    falhas = [r for r in resultados if r.status == "falhou"]
    resumo = "\n".join(f"{r.tabela}: {r.status} {r.detalhe}".strip() for r in resultados)
    minutos = (datetime.now(UTC) - inicio).total_seconds() / 60
    log.info(
        "ciclo_fim",
        tabelas=len(resultados),
        falhas=len(falhas),
        minutos=round(minutos),
    )
    await avisar(config.heartbeat_url, ok=not falhas, resumo=resumo)
    return resultados


async def rodar_para_sempre(config: Config) -> None:
    while True:
        proxima = proxima_execucao(datetime.now(UTC), config.worker_hora_utc)
        log.info("aguardando", proxima=proxima.isoformat())
        await asyncio.sleep((proxima - datetime.now(UTC)).total_seconds())
        try:
            await rodar_ciclo(config)
        except Exception:  # já registrado e avisado pelo heartbeat
            log.info("proximo_ciclo_amanha")


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
            # Coleta completa pela metade é retomada no próximo ciclo; incremental é refeita.
            log.info("worker_parado")

    asyncio.run(_principal())
