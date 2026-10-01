"""Aviso a um serviço de heartbeat (ex.: healthchecks.io), usado pelo worker e pela
recuperação: sucesso, ou `/fail`. Se o aviso não chegar no prazo, o serviço manda
e-mail. O aviso sai do servidor para fora: nenhuma porta aberta."""

from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger("tuss.heartbeat")


async def avisar(url: str, *, ok: bool, resumo: str) -> None:
    """Avisa `url` (vazio = não avisa). Falha no aviso só vai para o log."""
    if not url:
        return
    destino = url.rstrip("/") + ("" if ok else "/fail")
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            await http.post(destino, content=resumo[:10_000].encode())
    except httpx.HTTPError as exc:  # sem aviso, o serviço de heartbeat alerta sozinho
        log.warning("heartbeat_falhou", erro=repr(exc))
