"""Tokens de acesso à API.

A API nunca guarda o token, só o SHA-256 dele (variável TUSS_API_TOKENS_SHA256,
separados por vírgula: um token por dispositivo). Revogar = tirar o hash da
variável e reiniciar a API. Como o token é aleatório e longo (256 bits), SHA-256
basta; bcrypt/argon2 servem para senhas escolhidas por pessoas, que são fracas.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

PREFIXO = "tuss_"  # facilita reconhecer o token se vazar num log ou print


def gerar_token() -> str:
    return PREFIXO + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_valido(token: str, hashes_aceitos: frozenset[str]) -> bool:
    """Compara em tempo constante com todos os hashes, sem parar no primeiro."""
    recebido = hash_token(token)
    valido = False
    for aceito in hashes_aceitos:
        valido |= hmac.compare_digest(recebido, aceito)
    return valido
