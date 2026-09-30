"""Formatos de resposta da API.

Explícitos de propósito: a resposta nunca é "o que estiver no banco". Uma coluna
nova no banco só aparece na API quando alguém a coloca aqui.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tuss.domain.vigencia import Criterio, Motivo

StatusCarga = Literal["em_andamento", "retida", "publicada", "sem_mudanca", "falhou"]


class UltimaCarga(BaseModel):
    id: int
    status: StatusCarga
    iniciada_em: datetime
    finalizada_em: datetime | None
    erro: str | None = Field(
        description="Motivo da falha, da retenção pelo limite de anomalia ou da decisão manual"
    )
    incluidos: int
    alterados: int
    removidos: int
    reativados: int


class StatusTabela(BaseModel):
    tabela: str = Field(examples=["tuss-22"])
    descricao: str | None = Field(description="Nome da tabela no catálogo da ANS (`tuss catalogo`)")
    conceitos: int | None = Field(
        description="Conceitos publicados na carga atual; vazio = sem carga"
    )
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
    descricao: str | None = Field(description="Nome da tabela no catálogo da ANS (`tuss catalogo`)")
    conceitos: int | None = Field(
        description="Conceitos publicados na carga atual; vazio = sem carga"
    )
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
    em: date = Field(description="Data consultada: a de `?em=` ou, sem ela, hoje (UTC)")
    vigente: bool = Field(description="Se o código estava vigente na data consultada")


class Versao(BaseModel):
    descricao: str
    inicio_vigencia: date | None
    fim_vigencia: date | None = Field(description="Vazio = período aberto")
    fim_implantacao: date | None
    atributos: dict[str, str]
    criterio: Criterio
    carga_id: int = Field(description="Carga que publicou esta versão")
    publicado_de: datetime = Field(description="Quando esta versão entrou na nossa base")
    publicado_ate: datetime | None = Field(
        description="Quando foi substituída ou o código saiu da lista; vazio = versão atual"
    )


class Historico(BaseModel):
    tabela: str = Field(examples=["tuss-22"])
    codigo: str = Field(examples=["10101012"])
    carga_id: int = Field(description="Carga publicada que a API está servindo")
    sincronizado_em: datetime | None
    versoes: list[Versao] = Field(description="Da mais antiga para a mais nova")


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
    vigente_em: date | None = Field(
        None,
        description=(
            "Só os conceitos vigentes nesta data (AAAA-MM-DD), cada um como era nela."
            " Não se combina com `q`."
        ),
    )
    cursor: str | None = Field(None, description="Valor de `proximo_cursor` da página anterior")
    limite: int = Field(50, ge=1, le=200, description="Itens por página")


class ItemValidacao(BaseModel):
    # Só tabela, código e data: nenhum outro campo é aceito, para que ninguém mande
    # dado de beneficiário por engano (LGPD).
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tabela: str = Field(min_length=1, max_length=20, examples=["22"])
    codigo: str = Field(min_length=1, max_length=40, examples=["10101012"])
    data: date = Field(description="Data do atendimento (AAAA-MM-DD)", examples=["2026-09-01"])


class PedidoValidacao(BaseModel):
    model_config = ConfigDict(extra="forbid")

    itens: list[ItemValidacao] = Field(min_length=1, max_length=100)


class Periodo(BaseModel):
    inicio: date | None
    fim: date | None = Field(description="Vazio = período aberto")


MotivoValidacao = Literal[Motivo, "tabela_inexistente"]


class ResultadoValidacao(BaseModel):
    tabela: str = Field(description="Como veio no pedido, ou normalizada (`tuss-22`)")
    codigo: str
    data: date
    vigente: bool
    motivo: MotivoValidacao = Field(
        description=(
            "vigente; antes_do_inicio e apos_o_fim (fora do período de vigência);"
            " fora_da_lista (a ANS tinha retirado o código nessa data);"
            " inexistente (código nunca visto nesta tabela); tabela_inexistente"
        )
    )
    criterio: Criterio | None = Field(
        description="oficial: datas da ANS; observado: período visto nas cargas"
    )
    periodo: Periodo | None = Field(description="Período de vigência considerado")
    descricao: str | None = Field(description="Descrição do código como era na data")
    carga_id: int | None = Field(description="Carga publicada que a API está servindo")


class ResultadoValidacoes(BaseModel):
    vigentes: int
    nao_vigentes: int
    itens: list[ResultadoValidacao] = Field(description="Na mesma ordem do pedido")


TipoMudanca = Literal["incluido", "alterado", "removido", "reativado"]


class ConteudoConceito(BaseModel):
    descricao: str
    inicio_vigencia: date | None
    fim_vigencia: date | None
    fim_implantacao: date | None
    atributos: dict[str, str]


class Mudanca(BaseModel):
    ocorrido_em: datetime = Field(description="Quando a mudança foi publicada na nossa base")
    tabela: str = Field(examples=["tuss-22"])
    codigo: str = Field(examples=["10101012"])
    tipo: TipoMudanca
    campos_alterados: list[str] | None = Field(
        description="Só em `alterado`; atributos extras aparecem como `atributos.<nome>`"
    )
    antes: ConteudoConceito | None = Field(description="Vazio em incluido e reativado")
    depois: ConteudoConceito | None = Field(description="Vazio em removido")
    carga_id: int = Field(description="Carga que trouxe a mudança")


class PaginaMudancas(BaseModel):
    itens: list[Mudanca]
    proximo_cursor: str | None = Field(
        description="Passe em `cursor` para continuar; vazio = não há mais mudanças por ora"
    )


class ParametrosMudancas(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    desde: datetime | None = Field(
        None,
        description="A partir deste momento (AAAA-MM-DD ou data e hora; sem fuso = UTC)",
        examples=["2026-09-01"],
    )
    tabela: str | None = Field(None, max_length=20, description="`tuss-22` ou só `22`")
    tipo: TipoMudanca | None = None
    cursor: str | None = Field(None, description="Valor de `proximo_cursor` da página anterior")
    limite: int = Field(50, ge=1, le=200, description="Itens por página")
