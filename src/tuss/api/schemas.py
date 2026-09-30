"""Formatos de resposta da API.

Explícitos de propósito: a resposta nunca é "o que estiver no banco". Uma coluna
nova no banco só aparece na API quando alguém a coloca aqui.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

StatusCarga = Literal["em_andamento", "retida", "publicada", "sem_mudanca", "falhou"]


class UltimaCarga(BaseModel):
    id: int
    status: StatusCarga
    iniciada_em: datetime
    finalizada_em: datetime | None
    erro: str | None = Field(description="Motivo, quando a carga falhou")


class StatusTabela(BaseModel):
    tabela: str = Field(examples=["tuss-22"])
    descricao: str | None = Field(description="Vem do catálogo da ANS; vazia até a Fase 4")
    conceitos: int | None = Field(description="Conceitos publicados na carga atual")
    carga_id: int | None = Field(description="Carga publicada que a API está servindo")
    sincronizado_em: datetime | None = Field(description="Última vez que a fonte foi conferida")
    ultima_carga: UltimaCarga | None = Field(
        description="Tentativa mais recente, com qualquer status"
    )


class Status(BaseModel):
    tabelas: list[StatusTabela]


class Saude(BaseModel):
    status: Literal["ok"]
