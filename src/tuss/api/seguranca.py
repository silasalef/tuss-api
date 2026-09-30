"""Exige `Authorization: Bearer <token>` em toda rota /v1."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tuss.api.token import hash_token, token_valido

# auto_error=False: a resposta 401 sai no nosso formato (Problem Details), não no padrão do FastAPI.
_bearer = HTTPBearer(auto_error=False, description="Token gerado com `tuss token`")


async def exigir_token(
    request: Request,
    credenciais: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    hashes: frozenset[str] = request.app.state.hashes_tokens
    if credenciais is None or not token_valido(credenciais.credentials, hashes):
        # Mesma resposta para "sem token" e "token errado": não ajuda quem está tentando adivinhar.
        raise HTTPException(
            status_code=401,
            detail="token ausente ou inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.token_hash = hash_token(credenciais.credentials)  # chave dos limites por token
