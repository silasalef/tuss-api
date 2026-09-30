"""Formatos de resposta da API.

Explícitos de propósito: a resposta nunca é "o que estiver no banco". Uma coluna
nova no banco só aparece na API quando alguém a coloca aqui.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tuss.domain.vigencia import Criterio

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


class Tabela(BaseModel):
    tabela: str = Field(examples=["tuss-22"])
    numero: str = Field(examples=["22"])
    descricao: str | None = Field(description="Vem do catálogo da ANS; vazia até a Fase 4")
    conceitos: int | None = Field(description="Conceitos publicados na carga atual")
    carga_id: int | None
    sincronizado_em: datetime | None


class ListaTabelas(BaseModel):
    itens: list[Tabela]


class Conceito(BaseModel):
    codigo: str = Field(examples=["10101012"])
    descricao: str = Field(
        examples=["Consulta em consultório (no horário normal ou preestabelecido)"]
    )
    inicio_vigencia: date | None
    fim_vigencia: date | None = Field(description="Vazio = período aberto")
    fim_implantacao: date | None
    atributos: dict[str, str] = Field(
        description="Campos próprios de cada tabela (ex.: laboratorio na tuss-20)"
    )
    criterio: Criterio = Field(
        description="oficial: datas informadas pela ANS; observado: período visto nas cargas"
    )


class ConceitoDetalhe(Conceito):
    tabela: str = Field(examples=["tuss-22"])
    carga_id: int = Field(description="Carga publicada de onde veio a resposta")
    sincronizado_em: datetime | None = Field(description="Última vez que a fonte foi conferida")


class PaginaConceitos(BaseModel):
    tabela: str
    carga_id: int | None
    sincronizado_em: datetime | None
    itens: list[Conceito]
    proximo_cursor: str | None = Field(
        description="Passe em `cursor` para a próxima página; vazio = acabou"
    )


class ParametrosLista(BaseModel):
    # Parâmetro desconhecido (ex.: `limit`) é erro; espaços nas pontas são ignorados.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    q: str | None = Field(
        None,
        min_length=3,
        max_length=100,
        description=(
            "Busca. Código ou começo de código (`1010`, `1.01.01.01-2`) ou texto da"
            " descrição, sem precisar de acento e tolerando erro de digitação. Devolve os"
            " mais relevantes primeiro, até `limite`, sem próxima página."
        ),
        examples=["consulta consultorio"],
    )
    cursor: str | None = Field(None, description="Valor de `proximo_cursor` da página anterior")
    limite: int = Field(50, ge=1, le=200, description="Itens por página")
