"""Cursor da paginação: o último código da página, codificado.

O cliente trata o cursor como opaco (só devolve o que recebeu). Assim podemos
mudar o formato por dentro sem quebrar quem usa a API.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime

from fastapi import HTTPException

MAX_BYTES = 200


def codificar(ultimo_codigo: str) -> str:
    return base64.urlsafe_b64encode(ultimo_codigo.encode("utf-8")).decode("ascii").rstrip("=")


def decodificar(cursor: str) -> str:
    try:
        if len(cursor) > MAX_BYTES:
            raise ValueError("cursor longo demais")
        preenchido = cursor + "=" * (-len(cursor) % 4)
        codigo = base64.urlsafe_b64decode(preenchido.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise HTTPException(400, "cursor inválido; use o proximo_cursor de uma resposta") from exc
    if not codigo:
        raise HTTPException(400, "cursor inválido; use o proximo_cursor de uma resposta")
    return codigo


def codificar_evento(ocorrido_em: datetime, evento_id: int) -> str:
    """Cursor do feed de mudanças: instante e id do último evento da página."""
    return codificar(f"{ocorrido_em.isoformat()}|{evento_id}")


def decodificar_evento(cursor: str) -> tuple[datetime, int]:
    instante, _, evento_id = decodificar(cursor).partition("|")
    try:
        momento = datetime.fromisoformat(instante)
        if momento.tzinfo is None or not evento_id.isdigit():
            raise ValueError("cursor incompleto")
        return momento, int(evento_id)
    except ValueError as exc:
        raise HTTPException(400, "cursor inválido; use o proximo_cursor de uma resposta") from exc
