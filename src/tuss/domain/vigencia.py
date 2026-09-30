"""Regras de vigência. Sem banco, HTTP nem framework.

Duas perguntas sobre um código numa data D:

1. Qual versão vale em D? A que estava publicada na nossa base naquele dia (a última
   do dia, se houve mais de uma). Para D anterior à primeira carga, a versão mais
   antiga que conhecemos: é o melhor retrato que temos do código antes de observá-lo.
   Se em D o código já tinha saído da lista da ANS, nenhuma versão vale.
2. Essa versão está vigente em D? Com data de início da ANS (critério `oficial`):
   `inicio_vigencia <= D <= fim_vigencia` (fim vazio = sem fim). Sem data da ANS
   (critério `observado`): vigente se o código estava na nossa base em D. Código fora
   da lista em D não está vigente, e o critério é `observado`: a ANS não preenche
   `fim_vigencia`, ela retira o código, e quem viu a retirada fomos nós.

Os dias do período publicado são contados em UTC, como os carimbos do sistema.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal, Protocol

Criterio = Literal["oficial", "observado"]
# Por que está (ou não está) vigente. `inexistente`: o código nunca apareceu nas cargas.
Motivo = Literal["vigente", "antes_do_inicio", "apos_o_fim", "fora_da_lista", "inexistente"]


class Versao(Protocol):
    """O que a regra precisa saber de uma versão (vem do banco ou de um teste)."""

    @property
    def inicio_vigencia(self) -> date | None: ...
    @property
    def fim_vigencia(self) -> date | None: ...
    @property
    def publicado_de(self) -> datetime: ...
    @property
    def publicado_ate(self) -> datetime | None: ...


@dataclass(frozen=True, slots=True)
class Situacao:
    vigente: bool
    criterio: Criterio
    inicio: date | None  # período considerado: oficial ou observado (dias em UTC)
    fim: date | None  # vazio = aberto
    motivo: Motivo


def criterio(inicio_vigencia: date | None) -> Criterio:
    """De onde vem a vigência de uma versão.

    `oficial`: a ANS informou a data de início. `observado`: a ANS não informou,
    então a vigência é o período em que o código apareceu nas nossas cargas.
    """
    return "oficial" if inicio_vigencia is not None else "observado"


def versao_em[V: Versao](versoes: Sequence[V], d: date) -> V | None:
    """A versão que vale em `d`, ou `None` se o código estava fora da lista nesse dia."""
    if not versoes:
        return None
    ordenadas = sorted(versoes, key=lambda v: v.publicado_de)
    ate_d = [v for v in ordenadas if _dia(v.publicado_de) <= d]
    if not ate_d:
        return ordenadas[0]  # antes da primeira carga: a mais antiga que conhecemos
    escolhida = ate_d[-1]
    # Versões de um código são contíguas; se a última até D já tinha fechado,
    # não veio outra depois: o código saiu da lista.
    if escolhida.publicado_ate is not None and _dia(escolhida.publicado_ate) <= d:
        return None
    return escolhida


def situacao(versoes: Sequence[Versao], d: date) -> Situacao:
    """Se o código estava vigente em `d`, por qual critério e, se não, por quê."""
    versao = versao_em(versoes, d)
    ordenadas = sorted(versoes, key=lambda v: v.publicado_de)
    if versao is None:
        if not ordenadas:
            return Situacao(False, "observado", None, None, "inexistente")
        # Fora da lista em D: quem decide é a saída observada, não a data da ANS.
        # O período é o da última versão antes de D (não a mais nova: o código pode
        # ter voltado depois de D).
        saiu = [v for v in ordenadas if _dia(v.publicado_de) <= d][-1]
        return Situacao(
            False,
            "observado",
            saiu.inicio_vigencia or _dia(ordenadas[0].publicado_de),
            _dia_opcional(saiu.publicado_ate),
            "fora_da_lista",
        )
    if versao.inicio_vigencia is not None:
        inicio, fim = versao.inicio_vigencia, versao.fim_vigencia
        return Situacao(
            inicio <= d and (fim is None or d <= fim),
            "oficial",
            inicio,
            fim,
            _motivo(d, inicio, fim),
        )
    # Observado: da primeira vez que vimos o código até a saída da lista (se saiu).
    inicio_obs = _dia(ordenadas[0].publicado_de)
    fim_obs = _dia_opcional(ordenadas[-1].publicado_ate)
    return Situacao(inicio_obs <= d, "observado", inicio_obs, fim_obs, _motivo(d, inicio_obs, None))


def _motivo(d: date, inicio: date, fim: date | None) -> Motivo:
    if d < inicio:
        return "antes_do_inicio"
    if fim is not None and d > fim:
        return "apos_o_fim"
    return "vigente"


def _dia(momento: datetime) -> date:
    return momento.astimezone(UTC).date()


def _dia_opcional(momento: datetime | None) -> date | None:
    return None if momento is None else _dia(momento)
